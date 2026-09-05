from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.databento_cache import CACHE_FORMAT_VERSION, DatabentoCacheError
from tools.research_rolls import RawContractRollResolver


@pytest.fixture
def raw_roll_cache(tmp_path):
    instant = datetime(2024, 3, 10, 22, tzinfo=timezone.utc)
    timestamp = int(instant.timestamp()) * 1_000_000_000
    directory = tmp_path / "versions" / "test"
    data = directory / "parquet" / "ohlcv_1m" / "root=MNQ" / "year=2024" / "month=03"
    data.mkdir(parents=True)
    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "version_dir": "versions/test",
        "source_fingerprint": "verified-source",
        "roll_policy_version": "prior-session",
        "raw_symbol_codes": {"MNQ": {"MNQH4@2024": 1, "MNQM4@2024": 2}},
    }
    (tmp_path / "current.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Old and new contract coexist at the same time at very different prices;
    # the old contract also has a later minute that must never be substituted.
    rows = []
    for offset, instrument, code, price in ((0, 101, 1, 100), (0, 102, 2, 200), (2, 101, 1, 300)):
        rows.append({
            "timestamp_ns": timestamp + offset * 60_000_000_000,
            "instrument_id": instrument, "raw_symbol_code": code,
            "open_nano": price * 1_000_000_000,
            "high_nano": (price + 1) * 1_000_000_000,
            "low_nano": (price - 1) * 1_000_000_000,
            "close_nano": price * 1_000_000_000, "volume": 10,
        })
    pq.write_table(pa.Table.from_pylist(rows), data / "part-00000.parquet")
    previous = SimpleNamespace(
        symbol="MNQ", source_raw_symbol="MNQH4", source_instrument_id=101,
        source_file_sha256="verified-source", user_id="research", contract_id="MNQ",
    )
    return RawContractRollResolver(tmp_path), previous, instant


def test_roll_resolver_reads_old_delivery_at_exact_decision_time(raw_roll_cache):
    resolver, previous, instant = raw_roll_cache
    result = resolver(previous, instant)
    assert result is not None
    assert result.candle_timestamp == instant
    assert result.open_price == 100
    assert result.source_raw_symbol == "MNQH4"
    assert result.source_instrument_id == 101


def test_roll_resolver_does_not_use_later_or_new_delivery_bar(raw_roll_cache):
    resolver, previous, instant = raw_roll_cache
    assert resolver(previous, instant + timedelta(minutes=1)) is None
    previous.source_instrument_id = 999
    assert resolver(previous, instant) is None


def test_roll_resolver_refuses_mixed_cache_versions(raw_roll_cache):
    resolver, previous, instant = raw_roll_cache
    previous.source_file_sha256 = "different-source"
    with pytest.raises(DatabentoCacheError, match="source_fingerprint_mismatch"):
        resolver(previous, instant)


def test_roll_resolver_rejects_time_rounding(raw_roll_cache):
    resolver, previous, instant = raw_roll_cache
    with pytest.raises(DatabentoCacheError, match="aware_minute_boundary"):
        resolver(previous, instant + timedelta(seconds=1))
