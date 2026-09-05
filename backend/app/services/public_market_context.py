"""Manual, bounded daily reference data; never an execution-price source.

Primary sources checked 2026-09-05:
https://www.federalreserve.gov/datadownload/Choose.aspx?rel=H15
https://www.federalreserve.gov/releases/h15/ (Treasury source; yields % p.a.)
https://www.federalreserve.gov/disclaimer.htm (public domain unless indicated)
https://www.cboe.com/tradable_products/vix/vix_historical_data
https://www.cboe.com/terms (operator must establish permitted data use)

H.15 is released on business days at 16:15, but the CSV gives observation
dates, not each historical release/vintage timestamp. We never impute those
publication timestamps. Availability is the later of actual first collection
and the end of the observation's New York calendar day. Revised observations
are counted as conflicts and cannot overwrite values already known locally.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import io
import os
from time import monotonic
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..market_data_schemas import (PublicMarketRefreshItemOut, PublicMarketRefreshOut,
                                   PublicMarketSourceOut, PublicMarketStatusOut)
from ..models import ProjectXMarketCandle


UTC = timezone.utc
ET = ZoneInfo("America/New_York")
MAX_BYTES = 2_000_000
MAX_CSV_ROWS = 15_000
FED_URL = "https://www.federalreserve.gov/datadownload/Output.aspx"
FED_PACKAGE = "bf17364827e38702b42a58cf8eaa3f78"
FED_SERIES = "RIFLGFCY10_N.B"
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
PUBLIC_SOURCES = frozenset({"federal_reserve_h15", "cboe"})


class PublicDataError(ValueError):
    """Fixed non-sensitive failure code; never a provider response body."""


@dataclass(frozen=True)
class PublicFeed:
    symbol: str
    source: str
    label: str
    contract_id: str
    source_url: str
    data_notice: str
    observation_kind: str
    value_unit: str


FEEDS = {
    "US10Y": PublicFeed("US10Y", "federal_reserve_h15", "Federal Reserve H.15 · 10-year Treasury yield",
        "PUBLIC.FED.H15.US10Y", "https://www.federalreserve.gov/releases/h15/",
        "Federal Reserve Board H.15, original source U.S. Treasury. Daily constant-maturity yield in percent per year; not a tradable bond price or real-time quote. Historical publication times are unavailable; first local collection controls availability.",
        "daily_yield_observation", "percent_per_year"),
    "VIX": PublicFeed("VIX", "cboe", "Cboe VIX · daily history", "PUBLIC.CBOE.VIX",
        "https://www.cboe.com/tradable_products/vix/vix_historical_data",
        "Copyright Cboe Exchange, Inc. All rights reserved. Disabled by default. Set CBOE_VIX_ENABLED=true only with rights appropriate to your intended storage and use; see https://www.cboe.com/terms. Daily index observations, not real-time quotes.",
        "daily_index_ohlc", "index_points"),
}


@dataclass(frozen=True)
class DailyObservation:
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def observation_bounds(day: date) -> tuple[datetime, datetime]:
    """Calendar-day bounds, including 23/25-hour DST dates; not market hours."""
    return (datetime.combine(day, time.min, ET).astimezone(UTC),
            datetime.combine(day + timedelta(days=1), time.min, ET).astimezone(UTC))


def enabled(symbol: str) -> bool:
    return symbol != "VIX" or os.getenv("CBOE_VIX_ENABLED", "false").strip().lower() == "true"


def _csv_rows(body: bytes) -> list[list[str]]:
    if len(body) > MAX_BYTES:
        raise PublicDataError("response_too_large")
    try:
        decoded = body.decode("utf-8-sig")
        if "\x00" in decoded:
            raise PublicDataError("invalid_csv")
        rows = []
        for row in csv.reader(io.StringIO(decoded, newline=""), strict=True):
            if not row or not any(value.strip() for value in row):
                continue
            if len(rows) >= MAX_CSV_ROWS or len(row) > 64 or any(len(value) > 4000 for value in row):
                raise PublicDataError("csv_limit_exceeded")
            rows.append([value.strip() for value in row])
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise PublicDataError("invalid_csv") from exc


def _number(value: str, *, yield_value: bool = False) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise PublicDataError("invalid_observation") from exc
    if not result.is_finite() or result < (-10 if yield_value else 0) or result > (100 if yield_value else 10000):
        raise PublicDataError("invalid_observation")
    if result != result.quantize(Decimal("0.000001")):
        raise PublicDataError("unsupported_precision")
    return result


def _select(rows: list[DailyObservation], start: date, end_exclusive: date) -> list[DailyObservation]:
    selected = {}
    for row in rows:
        if row.day in selected and row != selected[row.day]:
            raise PublicDataError("conflicting_source_dates")
        if start <= row.day < end_exclusive:
            selected[row.day] = row
    return sorted(selected.values(), key=lambda row: row.day)


def parse_h15(body: bytes, *, start: date, end_exclusive: date) -> list[DailyObservation]:
    rows = _csv_rows(body)
    headers = [(i, row) for i, row in enumerate(rows) if row[0] == "Time Period"]
    if len(headers) != 1 or headers[0][1].count(FED_SERIES) != 1:
        raise PublicDataError("unexpected_h15_schema")
    header_index, header = headers[0]
    column = header.index(FED_SERIES)
    units = [row for row in rows[:header_index] if row[0] == "Unit:"]
    multipliers = [row for row in rows[:header_index] if row[0] == "Multiplier:"]
    if (len(units) != 1 or len(multipliers) != 1 or len(units[0]) != len(header)
            or len(multipliers[0]) != len(header) or units[0][column] != "Percent:_Per_Year"
            or multipliers[0][column] != "1"):
        raise PublicDataError("unexpected_h15_units")
    parsed = []
    for row in rows[header_index + 1:]:
        if len(row) != len(header):
            raise PublicDataError("unexpected_h15_schema")
        try:
            day = date.fromisoformat(row[0])
        except ValueError as exc:
            raise PublicDataError("invalid_observation_date") from exc
        if not start <= day < end_exclusive:
            continue
        if row[column] in {"ND", "NA", "N/A", "n.a.", ""}:
            continue  # Holidays and unavailable observations are never filled.
        value = _number(row[column], yield_value=True)
        # Existing storage requires four prices. These identical fields hold
        # one yield observation, explicitly tagged and never exposed as OHLC.
        parsed.append(DailyObservation(day, value, value, value, value))
    return _select(parsed, start, end_exclusive)


def parse_vix(body: bytes, *, start: date, end_exclusive: date) -> list[DailyObservation]:
    rows = _csv_rows(body)
    if not rows or rows[0] != ["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]:
        raise PublicDataError("unexpected_vix_schema")
    parsed = []
    for row in rows[1:]:
        if len(row) != 5:
            raise PublicDataError("unexpected_vix_schema")
        try:
            day = datetime.strptime(row[0], "%m/%d/%Y").date()
        except ValueError as exc:
            raise PublicDataError("invalid_observation_date") from exc
        if not start <= day < end_exclusive:
            continue
        o, h, lo, c = [_number(value) for value in row[1:]]
        if not lo <= min(o, c) <= max(o, c) <= h:
            raise PublicDataError("invalid_ohlc")
        parsed.append(DailyObservation(day, o, h, lo, c))
    return _select(parsed, start, end_exclusive)


def _download(symbol: str, *, start: date, end_exclusive: date) -> bytes:
    """One fixed-endpoint request per enabled source; no redirects or retries."""
    if symbol == "US10Y":
        url = FED_URL
        params = {"rel": "H15", "series": FED_PACKAGE, "lastobs": "",
                  "from": start.strftime("%m/%d/%Y"), "to": (end_exclusive - timedelta(days=1)).strftime("%m/%d/%Y"),
                  "filetype": "csv", "label": "include", "layout": "seriescolumn", "type": "package"}
    elif symbol == "VIX" and enabled(symbol):
        url, params = CBOE_URL, None
    else:
        raise PublicDataError("source_not_enabled")
    started = monotonic()
    try:
        with requests.get(url, params=params, stream=True, allow_redirects=False,
                          timeout=(5, 12), headers={"User-Agent": "TopSignal/1.0 public daily reference data", "Accept": "text/csv"}) as response:
            if response.status_code != 200:
                raise PublicDataError("source_http_error")
            if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
                raise PublicDataError("response_too_large")
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise PublicDataError("response_too_large")
                if monotonic() - started > 25:
                    raise PublicDataError("source_timeout")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as exc:
        raise PublicDataError("source_transport_error") from exc
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PublicDataError):
            raise
        raise PublicDataError("invalid_source_response") from exc


def merge_observations(db: Session, *, user_id: str, symbol: str, observations: list[DailyObservation], collected_at: datetime) -> tuple[int, int, int]:
    feed = FEEDS[symbol]
    collected_at = _utc(collected_at)
    if len(observations) > 365 or len({row.day for row in observations}) != len(observations):
        raise PublicDataError("invalid_observation_window")
    if any(observation_bounds(row.day)[1] > collected_at for row in observations):
        raise PublicDataError("observation_day_not_complete")
    c = ProjectXMarketCandle
    existing = db.query(c).filter(c.user_id == user_id, c.contract_id == feed.contract_id,
        c.live.is_(False), c.unit == "day", c.unit_number == 1).all()
    indexed = {_utc(row.candle_timestamp): row for row in existing}
    inserted = unchanged = conflicting = 0
    for value in observations:
        start, end = observation_bounds(value.day)
        old = indexed.get(start)
        prices = (value.open, value.high, value.low, value.close)
        if old is not None:
            if (old.source == feed.source and (old.open_price, old.high_price, old.low_price, old.close_price) == prices
                    and public_observation_details(old, cutoff=collected_at) is not None):
                unchanged += 1
            else:
                conflicting += 1  # No revision backfill into a prior decision.
            continue
        db.add(c(user_id=user_id, contract_id=feed.contract_id, symbol=symbol, live=False,
            unit="day", unit_number=1, candle_timestamp=start, open_price=value.open,
            high_price=value.high, low_price=value.low, close_price=value.close, volume=0,
            is_partial=False, source=feed.source, fetched_at=collected_at,
            raw_payload={"schema": "public_observation_v1", "observation_date": value.day.isoformat(),
                "observation_kind": feed.observation_kind, "value_unit": feed.value_unit,
                "source_time_precision": "date", "published_at": None,
                "source_day_end_bound": end.isoformat(), "first_collected_at": collected_at.isoformat(),
                "source_url": feed.source_url, "data_notice": feed.data_notice,
                "storage_semantics": "yield_point_in_required_price_columns" if symbol == "US10Y" else "source_daily_index_ohlc",
                "series_id": FED_SERIES if symbol == "US10Y" else "VIX"}))
        inserted += 1
    db.commit()
    return inserted, unchanged, conflicting


def public_observation_details(row: ProjectXMarketCandle, *, cutoff: datetime) -> dict | None:
    """Validate persisted provenance and collection before any context read."""
    feed = FEEDS.get(row.symbol)
    payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
    if (feed is None or row.source != feed.source or row.contract_id != feed.contract_id
            or row.live or row.unit != "day" or row.unit_number != 1 or row.is_partial
            or payload.get("schema") != "public_observation_v1"
            or payload.get("observation_kind") != feed.observation_kind
            or payload.get("value_unit") != feed.value_unit):
        return None
    try:
        day = date.fromisoformat(payload["observation_date"])
        start, end = observation_bounds(day)
        receipt = _utc(row.fetched_at)
        if (start != _utc(row.candle_timestamp) or receipt > cutoff or end > cutoff
                or _utc(datetime.fromisoformat(payload["first_collected_at"])) != receipt
                or _utc(datetime.fromisoformat(payload["source_day_end_bound"])) != end):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return {"observation_date": day, "observation_kind": feed.observation_kind,
            "value_unit": feed.value_unit, "source_url": feed.source_url, "data_notice": feed.data_notice,
            "data_mode": "public_daily", "source_day_end": end, "available_at": max(end, receipt)}


def public_market_status(db: Session, *, user_id: str) -> PublicMarketStatusOut:
    c = ProjectXMarketCandle
    items = []
    for symbol, feed in FEEDS.items():
        count, last_day, fetched = db.query(func.count(c.id), func.max(c.candle_timestamp), func.max(c.fetched_at)).filter(
            c.user_id == user_id, c.contract_id == feed.contract_id, c.source == feed.source,
            c.live.is_(False), c.unit == "day", c.unit_number == 1).one()
        active = enabled(symbol)
        items.append(PublicMarketSourceOut(symbol=symbol, source=feed.source, label=feed.label,
            status=("stored" if count else "ready") if active else "disabled", enabled=active,
            stored_rows=count, latest_observation_date=_utc(last_day).astimezone(ET).date() if last_day else None,
            last_collected_at=_utc(fetched) if fetched else None, source_url=feed.source_url, data_notice=feed.data_notice))
    return PublicMarketStatusOut(generated_at=datetime.now(UTC), sources=items)


def refresh_public_market_context(db: Session, *, user_id: str, symbols: list[str], days: int = 365) -> PublicMarketRefreshOut:
    if not 1 <= days <= 365 or not symbols or len(symbols) > 2 or set(symbols) - set(FEEDS):
        raise PublicDataError("invalid_public_refresh_request")
    started = datetime.now(UTC)
    end = started.astimezone(ET).date()  # Only completed observation dates.
    start = end - timedelta(days=days)
    items = []
    for symbol in dict.fromkeys(symbols):
        feed = FEEDS[symbol]
        common = {"symbol": symbol, "source": feed.source, "data_notice": feed.data_notice}
        if not enabled(symbol):
            items.append(PublicMarketRefreshItemOut(**common, status="disabled", detail="Cboe VIX collection is disabled; review data-use rights before enabling CBOE_VIX_ENABLED."))
            continue
        try:
            db.commit()  # Release read transactions before bounded network I/O.
            body = _download(symbol, start=start, end_exclusive=end)
            collected_at = datetime.now(UTC)  # Receipt is after all response bytes.
            parser = parse_h15 if symbol == "US10Y" else parse_vix
            observations = parser(body, start=start, end_exclusive=end)
            inserted, unchanged, conflicting = merge_observations(db, user_id=user_id, symbol=symbol,
                observations=observations, collected_at=collected_at)
            items.append(PublicMarketRefreshItemOut(**common, status="updated" if observations else "unavailable",
                received_rows=len(observations), inserted_rows=inserted, unchanged_rows=unchanged, conflicting_rows=conflicting,
                detail="Daily observations stored with original collection timestamps. Conflicting later revisions are counted and not imported." if observations else "The source returned no observations for the requested completed dates."))
        except PublicDataError as exc:
            db.rollback()
            items.append(PublicMarketRefreshItemOut(**common, status="failed", detail=str(exc)))
    return PublicMarketRefreshOut(started_at=started, finished_at=datetime.now(UTC), items=items)
