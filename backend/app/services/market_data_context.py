from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..market_data_schemas import MarketContextItemOut, MarketContextOut, MarketRefreshItemOut, MarketRefreshOut
from ..models import ProjectXMarketCandle
from .market_data_inventory import as_utc, database_streams, merge_candles_without_overwrite, symbol_root
from .projectx_client import ProjectXClient, ProjectXClientError
from .public_market_context import PUBLIC_SOURCES, public_observation_details


CONTEXT_SYMBOLS = ("MNQ", "MES", "NQ", "ES", "QQQ", "VIX", "NVDA", "AAPL", "MSFT", "US10Y", "DXY")
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def stored_market_context(db: Session, *, user_id: str, live: bool = False, as_of: datetime | None = None, collected_by_as_of: bool = False) -> MarketContextOut:
    now = datetime.now(timezone.utc)
    cutoff = as_utc(as_of) if as_of is not None else now
    if cutoff > now:
        raise ValueError("as_of_must_not_be_in_the_future")
    c = ProjectXMarketCandle
    streams = database_streams(db, user_id=user_id)
    candidates: dict[str, list[tuple[datetime, int, list[ProjectXMarketCandle]]]] = {}
    for stream in streams:
        public_daily = stream.source in PUBLIC_SOURCES
        if (not public_daily and stream.live != live) or stream.root_symbol not in CONTEXT_SYMBOLS or stream.unit not in _UNIT_SECONDS:
            continue
        duration = _UNIT_SECONDS[stream.unit] * stream.unit_number
        query = db.query(c).filter(
            c.user_id == user_id, c.contract_id == stream.contract_id, c.live == stream.live,
            c.unit == stream.unit, c.unit_number == stream.unit_number, c.source == stream.source,
            c.is_partial.is_(False), c.candle_timestamp <= (cutoff if public_daily else cutoff - timedelta(seconds=duration)),
        )
        if collected_by_as_of or public_daily:
            query = query.filter(c.fetched_at <= cutoff)
        rows = query.order_by(c.candle_timestamp.desc()).limit(3 if public_daily else 2).all()
        if public_daily:
            rows = [row for row in rows if public_observation_details(row, cutoff=cutoff) is not None][:2]
        if rows:
            available = (public_observation_details(rows[0], cutoff=cutoff)["source_day_end"] if public_daily
                         else as_utc(rows[0].candle_timestamp) + timedelta(seconds=duration))
            candidates.setdefault(stream.root_symbol, []).append((available, duration, rows))
    items = []
    for symbol in CONTEXT_SYMBOLS:
        options = candidates.get(symbol)
        if not options:
            items.append(MarketContextItemOut(symbol=symbol, status="missing", live=live))
            continue
        available, duration, rows = max(options, key=lambda item: (item[0], -item[1]))
        current = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        age = max(0.0, (cutoff - available).total_seconds())
        close = float(current.close_price)
        previous_close = float(previous.close_price) if previous is not None else None
        details = public_observation_details(current, cutoff=cutoff) if current.source in PUBLIC_SOURCES else None
        public_fields = {key: value for key, value in details.items() if key not in {"available_at", "source_day_end"}} if details else {}
        yield_observation = details is not None and details["observation_kind"] == "daily_yield_observation"
        items.append(MarketContextItemOut(
            symbol=symbol, status="fresh" if age <= max(300, duration * 2) else "stale",
            contract_id=current.contract_id, source=current.source, live=current.live,
            timeframe=f"{current.unit_number} {current.unit}", candle_timestamp=as_utc(current.candle_timestamp),
            available_at=details["available_at"] if details else available, fetched_at=as_utc(current.fetched_at), age_seconds=age,
            close=close, volume=None if details else float(current.volume), previous_close=previous_close,
            change_pct=((close / previous_close) - 1) * 100 if previous_close and not yield_observation else None,
            change_bps=(close - previous_close) * 100 if yield_observation and previous_close is not None else None,
            change_period_seconds=(as_utc(current.candle_timestamp) - as_utc(previous.candle_timestamp)).total_seconds() if previous is not None else None,
            **public_fields,
        ))
    return MarketContextOut(as_of=cutoff, generated_at=now, items=items,
        missing_symbols=[item.symbol for item in items if item.status == "missing"],
        note="Closed candles from the selected ProjectX live mode, plus public daily reference observations independent of that mode. Changes compare consecutive observations of the same contract, source and timeframe; elapsed periods may differ. Age is wall-clock time and can be stale while markets are closed. "
            + ("Candle closes and collection timestamps are both at or before the decision cutoff. " if collected_by_as_of else "Historical as-of limits candle close times, not collection times; fetched_at shows when history was collected. ")
            + "Public daily observations always require collection before the cutoff; source dates do not establish historical publication timestamps. US10Y is a yield in percent per year and its change is in basis points. These observations are not calibrated prediction probabilities.")


def refresh_market_context(db: Session, *, user_id: str, client: ProjectXClient, symbols: list[str], days: int, live: bool) -> MarketRefreshOut:
    """At most four symbol searches and four bounded history calls; no order APIs."""
    if not 1 <= days <= 10 or not symbols or len(symbols) > 4 or set(symbols) - {"MNQ", "MES", "NQ", "ES"}:
        raise ValueError("invalid_refresh_request")
    started = datetime.now(timezone.utc)
    end = started.replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    result = []
    for symbol in dict.fromkeys(symbols):
        try:
            available = [row for row in client.search_contracts(search_text=symbol, live=live)
                         if row.get("active_contract") is True and symbol_root(row.get("symbol_id"), str(row.get("id", ""))) == symbol]
            # Refuse ambiguous active deliveries instead of choosing by lexical sort.
            if len(available) != 1:
                result.append(MarketRefreshItemOut(symbol=symbol, status="unavailable", detail="No unique active contract is available for this symbol and live mode."))
                continue
            contract = available[0]
            bars = client.retrieve_bars(contract_id=contract["id"], live=live, start=start, end=end,
                unit=2, unit_number=1, limit=20_000, include_partial_bar=False)
            selected: dict[datetime, dict[str, Any]] = {}
            for bar in bars:
                stamp = as_utc(bar["timestamp"])
                if not start <= stamp or stamp + timedelta(minutes=1) > end or bar.get("is_partial"):
                    continue
                if stamp in selected and selected[stamp] != bar:
                    raise ValueError("conflicting_provider_timestamps")
                selected[stamp] = bar
            fetched = datetime.now(timezone.utc)
            inserted, unchanged, conflicts = merge_candles_without_overwrite(db, user_id=user_id,
                contract_id=contract["id"], symbol=contract.get("symbol_id") or f"F.US.{symbol}", live=live,
                bars=list(selected.values()), fetched_at=fetched,
                provenance={"operation": "market_data_refresh", "requested_start": start.isoformat(), "requested_end_exclusive": end.isoformat()},
            )
            result.append(MarketRefreshItemOut(symbol=symbol, status="updated" if selected else "unavailable",
                contract_id=contract["id"], received_rows=len(selected), inserted_rows=inserted,
                unchanged_rows=unchanged, conflicting_rows=conflicts,
                detail="Closed one-minute candles merged; existing rows preserved." if selected else "The provider returned no completed candles for this interval."))
        except ProjectXClientError:
            db.rollback()
            result.append(MarketRefreshItemOut(symbol=symbol, status="failed", detail="ProjectX market data request failed. Check connection and data entitlements."))
        except (ValueError, TypeError, KeyError):
            db.rollback()
            result.append(MarketRefreshItemOut(symbol=symbol, status="failed", detail="Provider candle or contract data was invalid; this symbol was not imported."))
    return MarketRefreshOut(started_at=started, finished_at=datetime.now(timezone.utc), live=live, items=result)
