"""Read observed old-contract minutes for causal research roll liquidation.

The continuous series switches deliveries at a session boundary. This adapter
reads the unstitched outright Parquet rows without rebuilding or changing the
cache. It never substitutes the new contract, an earlier close, or a later bar.
The caller must execute only against the returned bar's opening price at the
requested timestamp; its later high/low/close are not decision-time information.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from app.services.databento_cache import (
    CACHE_FORMAT_VERSION,
    PRICE_SCALE,
    CachedReplayCandle,
    DatabentoCacheError,
)
from app.services.trading_day import trading_day_date


class RawContractRollResolver:
    """Callable ``(previous_delivery_candle, roll_time) -> candle | None``.

    Construct after the normal replay store has verified the source archives.
    This pins one immutable cache version and requires its fingerprint on every
    previous candle, preventing accidental mixing of baseline and corrected data.
    """

    def __init__(self, cache_root: str | Path) -> None:
        root = Path(cache_root).resolve()
        manifest = json.loads((root / "current.json").read_text(encoding="utf-8"))
        if manifest.get("cache_format_version") != CACHE_FORMAT_VERSION:
            raise DatabentoCacheError("raw_roll_cache_format_changed")
        self.version_dir = (root / manifest["version_dir"]).resolve()
        if not self.version_dir.is_relative_to(root):
            raise DatabentoCacheError("raw_roll_version_outside_cache")
        saved = json.loads(
            (self.version_dir / "manifest.json").read_text(encoding="utf-8")
        )
        if saved != manifest:
            raise DatabentoCacheError("raw_roll_manifest_mismatch")
        self.fingerprint = str(manifest["source_fingerprint"])
        self.policy = str(manifest["roll_policy_version"])
        self.symbol_codes = manifest["raw_symbol_codes"]

    def __call__(self, previous: Any, roll_time: datetime) -> CachedReplayCandle | None:
        if roll_time.tzinfo is None or roll_time.second or roll_time.microsecond:
            raise DatabentoCacheError("raw_roll_time_must_be_aware_minute_boundary")
        if getattr(previous, "source_file_sha256", None) != self.fingerprint:
            raise DatabentoCacheError("raw_roll_source_fingerprint_mismatch")
        root = str(previous.symbol).upper()
        raw_symbol = str(previous.source_raw_symbol)
        codes = [
            int(code)
            for contract, code in self.symbol_codes.get(root, {}).items()
            if contract.split("@", 1)[0] == raw_symbol
        ]
        if not codes:
            raise DatabentoCacheError("raw_roll_contract_mapping_missing")
        instant = roll_time.astimezone(timezone.utc)
        session = trading_day_date(instant)
        directory = (
            self.version_dir / "parquet" / "ohlcv_1m" / f"root={root}"
            / f"year={session.year:04d}" / f"month={session.month:02d}"
        )
        files = sorted(directory.glob("*.parquet"))
        if not files:
            return None
        timestamp_ns = int(instant.timestamp()) * 1_000_000_000
        table = pq.read_table(
            files,
            filters=[
                ("timestamp_ns", "=", timestamp_ns),
                ("instrument_id", "=", int(previous.source_instrument_id)),
                ("raw_symbol_code", "in", codes),
            ],
            columns=[
                "timestamp_ns", "instrument_id", "raw_symbol_code", "open_nano",
                "high_nano", "low_nano", "close_nano", "volume",
            ],
        )
        if table.num_rows == 0:
            return None
        if table.num_rows != 1:
            raise DatabentoCacheError("raw_roll_duplicate_contract_minute")
        row = table.to_pylist()[0]
        return CachedReplayCandle(
            user_id=previous.user_id,
            contract_id=previous.contract_id,
            symbol=root,
            live=False,
            unit="minute",
            unit_number=1,
            candle_timestamp=instant,
            open_price=int(row["open_nano"]) / PRICE_SCALE,
            high_price=int(row["high_nano"]) / PRICE_SCALE,
            low_price=int(row["low_nano"]) / PRICE_SCALE,
            close_price=int(row["close_nano"]) / PRICE_SCALE,
            volume=int(row["volume"]),
            is_partial=False,
            raw_payload=None,
            fetched_at=None,
            source="databento_raw_contract_cache",
            source_instrument_id=int(row["instrument_id"]),
            source_raw_symbol=raw_symbol,
            source_file_sha256=self.fingerprint,
            roll_policy_version=self.policy,
            nominal_close_time=instant + timedelta(minutes=1),
        )
