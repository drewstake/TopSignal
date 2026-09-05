from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..market_data_models import LOCAL_MNQ_CAPTURE, LocalCapture
from ..market_data_schemas import (
    ArchiveOut, ArchiveSeriesOut, CandleImportOut, CandleImportTimeframeOut, CandleStreamOut, CoverageWindowOut,
    FeedStatusOut, LocalCaptureOut, MarketDataInventoryOut,
)
from ..models import ProjectXMarketCandle
from .databento_cache import default_cache_root
from .databento_market_data import _bucket_bounds, _bucket_has_complete_open_minute_coverage
from .trading_day import futures_session_is_open


class CaptureIntegrityError(ValueError):
    """A fixed, non-sensitive reason category safe for API responses."""


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def symbol_root(symbol: str | None, contract_id: str = "") -> str:
    # ProjectX uses CQG native roots for the E-minis. Preserve actual contract
    # IDs in storage while comparing the documented display roots exactly.
    # https://gateway.docs.projectx.com/docs/api-reference/market-data/search-contracts/
    # https://gateway.docs.projectx.com/docs/realtime/ (F.US.EP -> /ES)
    aliases = {"ENQ": "NQ", "EP": "ES"}
    contract = re.fullmatch(r"CON\.F\.[A-Z]+\.([A-Z0-9]+)\.[A-Z]\d+", contract_id.upper())
    if contract:
        return aliases.get(contract.group(1), contract.group(1))
    normalized = (symbol or contract_id).upper().strip()
    if normalized.startswith("F.US."):
        normalized = normalized[5:]
    return aliases.get(normalized, normalized)


def database_streams(db: Session, *, user_id: str, include_recent_gaps: bool = False) -> list[CandleStreamOut]:
    c = ProjectXMarketCandle
    grouping = [c.contract_id, c.symbol, c.live, c.unit, c.unit_number, c.source]
    rows = db.query(
        *grouping, func.count(c.id), func.sum(case((c.is_partial.is_(False), 1), else_=0)),
        func.min(c.candle_timestamp), func.max(c.candle_timestamp), func.max(c.fetched_at),
    ).filter(c.user_id == user_id).group_by(*grouping).order_by(c.contract_id, c.unit, c.unit_number, c.live).all()
    output = [CandleStreamOut(
        contract_id=row[0], symbol=row[1], root_symbol=symbol_root(row[1], row[0]),
        live=row[2], unit=row[3], unit_number=row[4], source=row[5], rows=row[6],
        complete_rows=int(row[7]), first_timestamp=as_utc(row[8]), last_timestamp=as_utc(row[9]),
        last_fetched_at=as_utc(row[10]),
    ) for row in rows]
    if include_recent_gaps:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        for stream in output:
            if stream.unit != "minute" or stream.unit_number != 1 or stream.root_symbol not in {"MNQ", "MES", "NQ", "ES"}:
                continue
            end = min(now, stream.last_timestamp + timedelta(minutes=1))
            start = max(stream.first_timestamp, end - timedelta(days=3))
            if end <= start:
                continue
            timestamps = db.query(c.candle_timestamp).filter(
                c.user_id == user_id, c.contract_id == stream.contract_id, c.live == stream.live,
                c.unit == "minute", c.unit_number == 1, c.source == stream.source,
                c.is_partial.is_(False), c.candle_timestamp >= start, c.candle_timestamp < end,
            ).all()
            observed = {as_utc(row[0]) for row in timestamps}
            expected = set()
            cursor = start
            while cursor < end:
                if futures_session_is_open(cursor, symbol=stream.root_symbol):
                    expected.add(cursor)
                cursor += timedelta(minutes=1)
            stream.recent_gap_check = CoverageWindowOut(start=start, end_exclusive=end,
                expected_open_minutes=len(expected), observed_open_minutes=len(expected & observed),
                missing_open_minutes=len(expected - observed),
                note="Up to three days ending at this stream's latest observation. Scheduled-open minutes use the app's exchange calendar, including documented historical exceptions. Missing candles can mean no trades or unavailable data; this is not proof of a feed outage.")
    return output


def _ns_date(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value) / 1_000_000_000, timezone.utc) if value is not None else None


def _bounded_json(path: Path, max_bytes: int) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise CaptureIntegrityError("source_file_too_large")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise CaptureIntegrityError("source_schema_invalid")
    return value


def archive_inventory(cache_root: Path | None = None) -> ArchiveOut:
    root = (cache_root or default_cache_root()).resolve()
    pointer = root / "current.json"
    note = "Local continuous replay series; coverage comes from the current manifest. Counts do not certify exchange-feed completeness."
    if not pointer.is_file():
        return ArchiveOut(status="missing", note="No local Databento manifest is installed.")
    try:
        manifest = _bounded_json(pointer, 2_000_000)
        version = (root / manifest["version_dir"]).resolve()
        if not version.is_relative_to(root):
            raise ValueError("invalid version directory")
        output = []
        for key, meta in manifest["series"].items():
            symbol, timeframe = key.split(":", 1)
            path = (version / meta["path"]).resolve()
            if not path.is_relative_to(version):
                raise ValueError("invalid series directory")
            output.append(ArchiveSeriesOut(
                symbol=symbol, timeframe=timeframe, rows=int(meta["rows"]),
                first_timestamp=_ns_date(meta.get("first_timestamp_ns")),
                end_exclusive=_ns_date(meta.get("source_end_ns")),
                files_present=all((path / name).is_file() for name in (
                    "timestamp_ns.npy", "close_timestamp_ns.npy", "open_nano.npy", "high_nano.npy",
                    "low_nano.npy", "close_nano.npy", "volume.npy", "instrument_id.npy",
                )),
            ))
        return ArchiveOut(
            status="available" if output and all(row.files_present for row in output) else "invalid",
            fingerprint=manifest.get("source_fingerprint"), built_at=manifest.get("built_at"),
            series=sorted(output, key=lambda row: (row.symbol, row.timeframe)),
            schemas={str(key): int(value) for key, value in manifest.get("records_by_schema", {}).items()}, note=note,
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return ArchiveOut(status="invalid", note="The local Databento manifest or its series is incomplete or invalid.")


def _capture_manifest(capture: LocalCapture) -> dict[str, Any]:
    directory = capture.directory.resolve()
    # Resolve both sides to reject a capture directory redirected outside storage.
    storage_root = Path(__file__).resolve().parents[2] / "storage"
    if capture == LOCAL_MNQ_CAPTURE and not directory.is_relative_to(storage_root.resolve()):
        raise CaptureIntegrityError("source_path_invalid")
    path = directory / "manifest.json"
    if not path.resolve().is_relative_to(directory) or path.stat().st_size > 1_000_000:
        raise CaptureIntegrityError("source_path_invalid")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != capture.manifest_sha256:
        raise CaptureIntegrityError("source_manifest_hash_mismatch")
    manifest = json.loads(payload)
    if manifest.get("contract_id") != capture.contract_id or manifest.get("coverage", {}).get("total_rows") != capture.rows:
        raise CaptureIntegrityError("source_metadata_mismatch")
    return manifest


def local_capture_inventory(db: Session, *, user_id: str, capture: LocalCapture = LOCAL_MNQ_CAPTURE) -> LocalCaptureOut:
    base = dict(capture_id=capture.capture_id, contract_id=capture.contract_id, symbol=capture.symbol, live=capture.live, rows=capture.rows)
    try:
        manifest = _capture_manifest(capture)
        for name, meta in manifest["files"].items():
            path = (capture.directory / name).resolve()
            if not path.is_relative_to(capture.directory.resolve()) or not path.is_file() or path.stat().st_size != meta["bytes"]:
                raise CaptureIntegrityError("source_file_invalid")
        first = as_utc(datetime.fromisoformat(manifest["coverage"]["first_utc"]))
        last = as_utc(datetime.fromisoformat(manifest["coverage"]["last_utc"]))
        c = ProjectXMarketCandle
        count = db.query(func.count(c.id)).filter(
            c.user_id == user_id, c.contract_id == capture.contract_id, c.live == capture.live,
            c.unit == "minute", c.unit_number == 1, c.source == "projectx", c.is_partial.is_(False),
            c.candle_timestamp >= first, c.candle_timestamp <= last,
        ).scalar()
        return LocalCaptureOut(**base, status="available", first_timestamp=first, last_timestamp=last,
            matching_database_rows=count or 0,
            note="Pinned capture manifest verified. Import rechecks all file hashes and every bar; existing candles are preserved. Database count indicates timestamps in this window, not price equality.")
    except FileNotFoundError:
        return LocalCaptureOut(**base, status="missing", note="This verified local capture is not installed on this server.")
    except (OSError, ValueError, KeyError, TypeError):
        return LocalCaptureOut(**base, status="invalid", note="The local capture failed integrity checks and cannot be imported.")


def market_data_inventory(db: Session, *, user_id: str) -> MarketDataInventoryOut:
    streams = database_streams(db, user_id=user_id, include_recent_gaps=True)
    archive = archive_inventory()
    present = sorted({stream.root_symbol for stream in streams})
    return MarketDataInventoryOut(
        generated_at=datetime.now(timezone.utc), database_rows=sum(row.rows for row in streams),
        streams=streams, archive=archive, local_capture=local_capture_inventory(db, user_id=user_id),
        feeds=[
            FeedStatusOut(key="candles", label="Stored candles", status="available" if streams else "missing", detail=", ".join(present) or "No candles stored for this user."),
            FeedStatusOut(key="cross_market", label="Cross-market context", status="available" if len(present) > 1 else "partial", detail="Uses closed stored bars with explicit timestamps. Missing symbols are never synthesized."),
            FeedStatusOut(key="archive", label="Historical replay archive", status=archive.status, detail=archive.note),
            FeedStatusOut(key="statistics", label="Exchange statistics archive", status="available" if archive.schemas.get("statistics", 0) else "missing", detail="Settlement/open-interest records require a statistics feed or imported archive."),
        ],
        note="Database coverage is scoped to your user; local replay archives are server resources. Providers, contracts, live modes and timeframes remain separate. Timestamp bounds are not a guarantee of complete sessions.",
    )


def _validated_capture_bar(raw: dict[str, Any], *, start: datetime, end: datetime) -> dict[str, Any]:
    try:
        if set(raw) != {"t", "o", "h", "l", "c", "v"}:
            raise ValueError()
        timestamp = datetime.fromisoformat(raw["t"])
        if timestamp.tzinfo is None:
            raise ValueError()
        timestamp = as_utc(timestamp)
        values = [Decimal(str(raw[key])) for key in ("o", "h", "l", "c", "v")]
        o, h, l, c, v = values
        if (not all(value.is_finite() for value in values) or min(o, h, l, c) <= 0
                or l > min(o, c) or h < max(o, c) or h < l or v < 0 or v != v.to_integral_value()
                or any(value % Decimal("0.25") for value in (o, h, l, c))
                or timestamp.second or timestamp.microsecond or not start <= timestamp < end):
            raise ValueError()
        return dict(timestamp=timestamp, open=o, high=h, low=l, close=c, volume=v, is_partial=False, raw_payload=raw)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise CaptureIntegrityError("source_bar_invalid") from None


def load_verified_capture(capture: LocalCapture = LOCAL_MNQ_CAPTURE) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fully verify all bytes and rows before opening a database write transaction."""
    manifest = _capture_manifest(capture)
    all_bars: dict[datetime, dict[str, Any]] = {}
    payloads = {}
    for name, meta in manifest["files"].items():
        path = (capture.directory / name).resolve()
        if Path(name).name != name or not path.is_relative_to(capture.directory.resolve()):
            raise CaptureIntegrityError("source_path_invalid")
        if path.stat().st_size > 4_000_000:
            raise CaptureIntegrityError("source_file_too_large")
        payload = path.read_bytes()
        if len(payload) != meta["bytes"] or hashlib.sha256(payload).hexdigest() != meta["sha256"]:
            raise CaptureIntegrityError("source_file_hash_mismatch")
        payloads[name] = json.loads(payload)
    for window in manifest["windows"]:
        payload = payloads[window["file"]]
        request = payload["request"]
        start = as_utc(datetime.fromisoformat(window["start_utc"]))
        end = as_utc(datetime.fromisoformat(window["end_exclusive_utc"]))
        if (request["contractId"] != capture.contract_id or request["live"] is not capture.live
                or request["unit"] != 2 or request["unitNumber"] != 1 or request["includePartialBar"] is not False
                or as_utc(datetime.fromisoformat(request["startTime"])) != start
                or as_utc(datetime.fromisoformat(request["endTime"])) != end):
            raise CaptureIntegrityError("source_request_mismatch")
        bars = payload["response"]["bars"]
        if len(bars) != window["rows"]:
            raise CaptureIntegrityError("source_row_count_mismatch")
        for raw in bars:
            bar = _validated_capture_bar(raw, start=start, end=end)
            if bar["timestamp"] in all_bars:
                raise CaptureIntegrityError("source_duplicate_timestamp")
            all_bars[bar["timestamp"]] = bar
    if len(all_bars) != capture.rows:
        raise CaptureIntegrityError("source_row_count_mismatch")
    return manifest, [all_bars[key] for key in sorted(all_bars)]


def merge_candles_without_overwrite(
    db: Session, *, user_id: str, contract_id: str, symbol: str, live: bool,
    bars: list[dict[str, Any]], fetched_at: datetime, provenance: dict[str, Any],
    unit: str = "minute", unit_number: int = 1, commit: bool = True,
) -> tuple[int, int, int]:
    """Atomic insert-only merge, including a database uniqueness guard for races."""
    if not bars:
        return 0, 0, 0
    c = ProjectXMarketCandle
    existing = db.query(c).filter(
        c.user_id == user_id, c.contract_id == contract_id, c.live == live,
        c.unit == unit, c.unit_number == unit_number,
        c.candle_timestamp >= min(bar["timestamp"] for bar in bars),
        c.candle_timestamp <= max(bar["timestamp"] for bar in bars),
    ).all()
    by_time = {as_utc(row.candle_timestamp): row for row in existing}
    unchanged = conflicts = inserted = 0
    values = []
    fields = [("open_price", "open"), ("high_price", "high"), ("low_price", "low"), ("close_price", "close"), ("volume", "volume")]
    for bar in bars:
        row = by_time.get(as_utc(bar["timestamp"]))
        if row is not None:
            if row.source == "projectx" and not row.is_partial and all(Decimal(str(getattr(row, model_field))) == Decimal(str(bar[field])) for model_field, field in fields):
                unchanged += 1
            else:
                conflicts += 1
            continue
        payload = dict(bar.get("raw_payload") or {})
        payload["_topsignal_provenance"] = provenance
        values.append(dict(
            user_id=user_id, contract_id=contract_id, symbol=symbol, live=live, unit=unit, unit_number=unit_number,
            candle_timestamp=bar["timestamp"], open_price=bar["open"], high_price=bar["high"],
            low_price=bar["low"], close_price=bar["close"], volume=bar["volume"],
            is_partial=False, source="projectx", raw_payload=payload, fetched_at=fetched_at,
        ))
    dialect = db.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise ValueError("unsupported_database")
    factory = pg_insert if dialect == "postgresql" else sqlite_insert
    try:
        for offset in range(0, len(values), 300):
            chunk = values[offset:offset + 300]
            statement = factory(c).values(chunk).on_conflict_do_nothing(index_elements=[
                "user_id", "contract_id", "live", "unit", "unit_number", "candle_timestamp",
            ]).returning(c.id)
            count = len(db.execute(statement).all())
            inserted += count
            conflicts += len(chunk) - count  # Concurrent inserts are preserved, never silently overwritten.
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return inserted, unchanged, conflicts


def aggregate_complete_minutes(bars: list[dict[str, Any]], *, root_symbol: str, unit: str, unit_number: int, closed_by: datetime) -> list[dict[str, Any]]:
    """Reuse replay's session alignment and strict scheduled-minute coverage."""
    buckets: dict[tuple[datetime, datetime], list[dict[str, Any]]] = {}
    for bar in sorted(bars, key=lambda item: item["timestamp"]):
        key = _bucket_bounds(bar["timestamp"], unit=unit, unit_number=unit_number)
        buckets.setdefault(key, []).append(bar)
    output = []
    for (start, end), rows in sorted(buckets.items()):
        bucket = dict(start=start, end=end, timestamps=[row["timestamp"] for row in rows])
        if end > closed_by or not _bucket_has_complete_open_minute_coverage(bucket, root_symbol=root_symbol):
            continue
        output.append(dict(timestamp=start, open=rows[0]["open"], high=max(row["high"] for row in rows),
            low=min(row["low"] for row in rows), close=rows[-1]["close"],
            volume=sum(row["volume"] for row in rows), is_partial=False,
            raw_payload={"aggregation": "strict_scheduled_minutes", "source_minutes": len(rows), "nominal_close_time": end.isoformat()}))
    return output


def materialize_capture_timeframes(db: Session, *, user_id: str, capture: LocalCapture, bars: list[dict[str, Any]], fetched_at: datetime, provenance: dict[str, Any]) -> list[CandleImportTimeframeOut]:
    base_counts = merge_candles_without_overwrite(db, user_id=user_id, contract_id=capture.contract_id,
        symbol=f"F.US.{capture.symbol}", live=capture.live, bars=bars, fetched_at=fetched_at, provenance=provenance, commit=False)
    outcomes = [CandleImportTimeframeOut(timeframe="1m", available_rows=len(bars), inserted_rows=base_counts[0], unchanged_rows=base_counts[1], conflicting_rows=base_counts[2])]
    # A conflicting original minute cannot become an apparently compatible
    # derived signal candle. Filter against the canonical DB values after merge.
    c = ProjectXMarketCandle
    canonical = db.query(c).filter(c.user_id == user_id, c.contract_id == capture.contract_id,
        c.live == capture.live, c.unit == "minute", c.unit_number == 1,
        c.candle_timestamp >= bars[0]["timestamp"], c.candle_timestamp <= bars[-1]["timestamp"]).all()
    by_time = {as_utc(row.candle_timestamp): row for row in canonical}
    fields = [("open_price", "open"), ("high_price", "high"), ("low_price", "low"), ("close_price", "close"), ("volume", "volume")]
    compatible = [bar for bar in bars if (row := by_time.get(bar["timestamp"])) is not None
        and row.source == "projectx" and not row.is_partial
        and all(Decimal(str(getattr(row, model_field))) == Decimal(str(bar[field])) for model_field, field in fields)]
    for unit, number, timeframe in (("minute", 5, "5m"), ("minute", 15, "15m"), ("hour", 1, "1h")):
        aggregates = aggregate_complete_minutes(compatible, root_symbol=capture.symbol, unit=unit, unit_number=number, closed_by=fetched_at)
        counts = merge_candles_without_overwrite(db, user_id=user_id, contract_id=capture.contract_id,
            symbol=f"F.US.{capture.symbol}", live=capture.live, bars=aggregates, fetched_at=fetched_at,
            provenance={**provenance, "derived_from": "verified_1m", "calendar_policy": "shared_replay_session_alignment"},
            unit=unit, unit_number=number, commit=False)
        outcomes.append(CandleImportTimeframeOut(timeframe=timeframe, available_rows=len(aggregates),
            inserted_rows=counts[0], unchanged_rows=counts[1], conflicting_rows=counts[2]))
    return outcomes


def import_local_history(db: Session, *, user_id: str, capture: LocalCapture = LOCAL_MNQ_CAPTURE) -> CandleImportOut:
    manifest, bars = load_verified_capture(capture)
    try:
        outcomes = materialize_capture_timeframes(db, user_id=user_id, capture=capture, bars=bars,
            fetched_at=as_utc(datetime.fromisoformat(manifest["finished_at"])),
            provenance={"capture_id": capture.capture_id, "manifest_sha256": capture.manifest_sha256,
                "imported_at": datetime.now(timezone.utc).isoformat(), "research_exposure": "previously_evaluated"})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return CandleImportOut(capture_id=capture.capture_id, verified_rows=len(bars),
        inserted_rows=sum(row.inserted_rows for row in outcomes), unchanged_rows=sum(row.unchanged_rows for row in outcomes),
        conflicting_rows=sum(row.conflicting_rows for row in outcomes), timeframes=outcomes,
        note="Verified ProjectX minutes and complete 5m/15m/1h bars added to your candle history. Existing rows were preserved; aggregates skip conflicting or missing source minutes. Totals include all timeframes. The separate Databento replay archive is unchanged.")
