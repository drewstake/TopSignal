from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from zipfile import ZipFile

import numpy as np
import pyarrow.parquet as pq
import pytest
import zstandard
from databento_dbn import (
    InstrumentClass,
    InstrumentDefMsg,
    Metadata,
    OHLCVMsg,
    RType,
    SType,
    Schema,
    SecurityUpdateAction,
)

import app.services.databento_cache as databento_cache
from tools import benchmark_databento_cache
from app.services.databento_cache import (
    CachedCandleList,
    DatabentoCacheError,
    DatabentoCacheStaleError,
    DatabentoReplayStore,
    MmapCandleSequence,
    MmapReplayCandle,
    build_databento_cache,
    inspect_archive,
    parse_timeframe,
    _Instrument,
    _MappingResolver,
    _build_roll_schedule,
    _contract_key,
    _resolve_contract_code,
)


DATASET = "GLBX.MDP3"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
CONTRACT_ID = "CON.F.US.MNQ.M24"
ROOT_CONTRACTS = {
    "MNQ": (101, "MNQM4", 2_000_000_000),
    "MES": (201, "MESM4", 5_000_000_000),
    "NQ": (301, "NQM4", 20_000_000_000),
    "ES": (401, "ESM4", 50_000_000_000),
}
ARRAY_COLUMNS = (
    "timestamp_ns",
    "close_timestamp_ns",
    "open_nano",
    "high_nano",
    "low_nano",
    "close_nano",
    "volume",
    "instrument_id",
    "raw_symbol_code",
    "session_ordinal",
)


@dataclass(frozen=True)
class TinyCache:
    archives: tuple[Path, Path]
    cache_root: Path
    version_dir: Path
    source_fingerprint: str
    start: datetime


def _unix_nanos(value: datetime) -> int:
    normalized = value.astimezone(timezone.utc)
    return int(normalized.timestamp()) * 1_000_000_000 + normalized.microsecond * 1_000


def _definition(
    root_symbol: str,
    *,
    undefined_timestamps: bool = False,
) -> InstrumentDefMsg:
    instrument_id, raw_symbol, unit_quantity = ROOT_CONTRACTS[root_symbol]
    definition_time = datetime(2024, 1, 2, tzinfo=timezone.utc)
    values: dict[str, Any] = {
        "publisher_id": 1,
        "instrument_id": instrument_id,
        "ts_event": _unix_nanos(definition_time),
        "ts_recv": _unix_nanos(definition_time),
        "min_price_increment": 250_000_000,
        "display_factor": 1_000_000_000,
        "raw_symbol": raw_symbol,
        "asset": root_symbol,
        "security_type": "FUT",
        "instrument_class": InstrumentClass.FUTURE,
        "security_update_action": SecurityUpdateAction.ADD,
        "unit_of_measure_qty": unit_quantity,
    }
    if not undefined_timestamps:
        values.update(
            expiration=_unix_nanos(
                datetime(2024, 6, 21, 13, 30, tzinfo=timezone.utc)
            ),
            activation=_unix_nanos(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        )
    return InstrumentDefMsg(**values)


def _ohlcv(
    timestamp: datetime,
    *,
    instrument_id: int,
    index: int,
) -> OHLCVMsg:
    open_nano = 100_000_000_000 + index * 1_000_000_000
    return OHLCVMsg(
        rtype=RType.OHLCV_1M,
        publisher_id=1,
        instrument_id=instrument_id,
        ts_event=_unix_nanos(timestamp),
        open=open_nano,
        high=open_nano + 2_000_000_000,
        low=open_nano - 1_000_000_000,
        close=open_nano + 500_000_000,
        volume=10 + index,
    )


def _write_dbn_archive(
    path: Path,
    *,
    root_symbol: str,
    schema_name: str,
    records: Iterable[InstrumentDefMsg | OHLCVMsg],
    mappings: Iterable[Any] | None = None,
) -> Path:
    materialized = list(records)
    schema = Schema.DEFINITION if schema_name == "definition" else Schema.OHLCV_1M
    record_times = [
        int(record.ts_recv if schema_name == "definition" else record.ts_event)
        for record in materialized
    ]
    start = min(record_times)
    end = max(record_times) + 60_000_000_000
    metadata = Metadata(
        dataset=DATASET,
        start=start,
        end=end,
        stype_in=SType.PARENT,
        stype_out=SType.INSTRUMENT_ID,
        schema=schema,
        symbols=[f"{root_symbol}.FUT"],
        mappings=list(mappings) if mappings is not None else None,
    )
    uncompressed = bytes(metadata) + b"".join(bytes(record) for record in materialized)
    payload = zstandard.ZstdCompressor().compress(uncompressed)
    payload_name = f"tiny.{schema_name}.dbn.zst"
    payload_hash = hashlib.sha256(payload).hexdigest()
    job_id = f"tiny-{root_symbol.lower()}-{schema_name}"
    metadata_json = {
        "version": 1,
        "job_id": job_id,
        "query": {
            "dataset": DATASET,
            "schema": schema_name,
            "symbols": [f"{root_symbol}.FUT"],
            "stype_in": "parent",
            "stype_out": "instrument_id",
            "encoding": "dbn",
            "compression": "zstd",
            "start": start,
            "end": end,
        },
    }
    manifest_json = {
        "job_id": job_id,
        "files": [
            {
                "filename": payload_name,
                "size": len(payload),
                "hash": f"sha256:{payload_hash}",
            }
        ],
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps(metadata_json))
        archive.writestr("manifest.json", json.dumps(manifest_json))
        archive.writestr(payload_name, payload)
    return path


def _tiny_mnq_archives(
    directory: Path,
    *,
    bar_count: int = 6,
    start: datetime | None = None,
) -> tuple[tuple[Path, Path], datetime]:
    directory.mkdir(parents=True, exist_ok=True)
    instrument_id, _raw_symbol, _unit_quantity = ROOT_CONTRACTS["MNQ"]
    start = start or datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    definitions = _write_dbn_archive(
        directory / "mnq-definition.zip",
        root_symbol="MNQ",
        schema_name="definition",
        records=[_definition("MNQ")],
    )
    ohlcv = _write_dbn_archive(
        directory / "mnq-ohlcv.zip",
        root_symbol="MNQ",
        schema_name="ohlcv-1m",
        records=[
            _ohlcv(
                start + timedelta(minutes=index),
                instrument_id=instrument_id,
                index=index,
            )
            for index in range(bar_count)
        ],
    )
    return (definitions, ohlcv), start


@pytest.fixture(scope="module")
def tiny_cache(tmp_path_factory: pytest.TempPathFactory) -> TinyCache:
    directory = tmp_path_factory.mktemp("databento-local-cache")
    archives, start = _tiny_mnq_archives(directory / "archives")
    cache_root = directory / "cache"
    result = build_databento_cache(
        archives,
        cache_root=cache_root,
        timeframes=("1m", "5m"),
    )
    return TinyCache(
        archives=archives,
        cache_root=cache_root,
        version_dir=Path(result.version_dir),
        source_fingerprint=result.source_fingerprint,
        start=start,
    )


def _load(
    store: DatabentoReplayStore,
    cache: TinyCache,
    *,
    unit_number: int = 1,
    start: datetime | None = None,
    end: datetime | None = None,
    closed_by: datetime | None = None,
    user_id: str = OWNER_ID,
    contract_id: str = CONTRACT_ID,
) -> CachedCandleList:
    requested_start = start or cache.start
    requested_end = end or cache.start + timedelta(minutes=6)
    return store.load_candles(
        user_id=user_id,
        contract_id=contract_id,
        root_symbol="MNQ",
        unit="minute",
        unit_number=unit_number,
        start=requested_start,
        end=requested_end,
        closed_by=closed_by or requested_end,
    )


def _open(
    store: DatabentoReplayStore,
    cache: TinyCache,
    *,
    unit_number: int = 1,
    start: datetime | None = None,
    end: datetime | None = None,
    closed_by: datetime | None = None,
    user_id: str = OWNER_ID,
    contract_id: str = CONTRACT_ID,
) -> MmapCandleSequence:
    requested_start = start or cache.start
    requested_end = end or cache.start + timedelta(minutes=6)
    return store.open_candles(
        user_id=user_id,
        contract_id=contract_id,
        root_symbol="MNQ",
        unit="minute",
        unit_number=unit_number,
        start=requested_start,
        end=requested_end,
        closed_by=closed_by or requested_end,
    )


def test_build_creates_partitioned_parquet_and_persistent_memmaps(
    tiny_cache: TinyCache,
):
    definition_files = list(
        (
            tiny_cache.version_dir
            / "parquet"
            / "definitions"
            / "root=MNQ"
            / "year=2024"
            / "month=01"
        ).glob("*.parquet")
    )
    ohlcv_files = list(
        (
            tiny_cache.version_dir
            / "parquet"
            / "ohlcv_1m"
            / "root=MNQ"
            / "year=2024"
            / "month=03"
        ).glob("*.parquet")
    )
    assert len(definition_files) == 1
    assert len(ohlcv_files) == 1
    assert pq.ParquetFile(definition_files[0]).metadata.num_rows == 1
    assert pq.ParquetFile(ohlcv_files[0]).metadata.num_rows == 6

    manifest = json.loads((tiny_cache.cache_root / "current.json").read_text())
    assert manifest["source_fingerprint"] == tiny_cache.source_fingerprint
    assert manifest["records_by_schema"] == {"definition": 1, "ohlcv-1m": 6}
    assert set(manifest["series"]) == {"MNQ:1m", "MNQ:5m"}
    for timeframe, expected_rows in (("1m", 6), ("5m", 2)):
        entry = manifest["series"][f"MNQ:{timeframe}"]
        series_dir = tiny_cache.version_dir / entry["path"]
        assert entry["rows"] == expected_rows
        assert (series_dir / "metadata.json").is_file()
        for name in ARRAY_COLUMNS:
            assert (series_dir / f"{name}.npy").is_file()
        timestamps = np.load(series_dir / "timestamp_ns.npy", mmap_mode="r")
        try:
            assert isinstance(timestamps, np.memmap)
            assert timestamps.shape == (expected_rows,)
        finally:
            timestamps._mmap.close()

    reused = build_databento_cache(
        tiny_cache.archives,
        cache_root=tiny_cache.cache_root,
        timeframes=("1m", "5m"),
    )
    assert reused.reused is True
    assert reused.source_fingerprint == tiny_cache.source_fingerprint


def test_failed_forced_rebuild_preserves_published_cache(
    tiny_cache: TinyCache,
    monkeypatch: pytest.MonkeyPatch,
):
    current_path = tiny_cache.cache_root / "current.json"
    before = current_path.read_bytes()

    def fail_build(*_args: Any, **_kwargs: Any):
        raise RuntimeError("synthetic rebuild failure")

    monkeypatch.setattr(databento_cache, "_build_parquet_and_rolls", fail_build)
    with pytest.raises(RuntimeError, match="synthetic rebuild failure"):
        build_databento_cache(
            tiny_cache.archives,
            cache_root=tiny_cache.cache_root,
            timeframes=("1m", "5m"),
            force=True,
        )

    assert current_path.read_bytes() == before
    assert tiny_cache.version_dir.is_dir()
    assert (tiny_cache.version_dir / "manifest.json").is_file()
    assert not list((tiny_cache.cache_root / "versions").glob("*.tmp"))


def test_forced_rebuild_publishes_a_unique_generation_with_open_nondefault_mmap(
    tmp_path: Path,
):
    archives, start = _tiny_mnq_archives(tmp_path / "archives")
    cache_root = tmp_path / "cache"
    first = build_databento_cache(
        archives,
        cache_root=cache_root,
        timeframes=("1m", "5m"),
    )
    store = DatabentoReplayStore(cache_root)
    cache = TinyCache(
        archives=archives,
        cache_root=cache_root,
        version_dir=Path(first.version_dir),
        source_fingerprint=first.source_fingerprint,
        start=start,
    )
    try:
        _load(store, cache)
        assert store.stats()["mapped_series"] == 1

        rebuilt = build_databento_cache(
            archives,
            cache_root=cache_root,
            timeframes=("1m", "5m"),
            force=True,
        )

        assert Path(rebuilt.version_dir) != Path(first.version_dir)
        assert Path(first.version_dir).is_dir()
        assert Path(rebuilt.version_dir).is_dir()
        pointer = json.loads((cache_root / "current.json").read_text())
        assert (cache_root / pointer["version_dir"]).resolve() == Path(
            rebuilt.version_dir
        ).resolve()
        refreshed = _load(store, cache)
        assert refreshed
        assert store.stats()["mapped_series"] == 1
    finally:
        store.clear()


def test_valid_dbn_undefined_timestamps_are_normalized(tmp_path: Path):
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    start = datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    definition = _write_dbn_archive(
        archive_dir / "definition.zip",
        root_symbol="MNQ",
        schema_name="definition",
        records=[_definition("MNQ", undefined_timestamps=True)],
    )
    ohlcv = _write_dbn_archive(
        archive_dir / "ohlcv.zip",
        root_symbol="MNQ",
        schema_name="ohlcv-1m",
        records=[_ohlcv(start, instrument_id=101, index=0)],
    )

    result = build_databento_cache(
        [definition, ohlcv],
        cache_root=tmp_path / "cache",
        timeframes=("1m",),
    )
    definition_file = next(
        (Path(result.version_dir) / "parquet" / "definitions").rglob("*.parquet")
    )
    table = pq.read_table(
        definition_file,
        columns=["activation_ns", "expiration_ns"],
    )
    assert table.to_pydict() == {"activation_ns": [-1], "expiration_ns": [-1]}


def test_one_and_five_minute_fields_and_closed_bar_boundary(tiny_cache: TinyCache):
    store = DatabentoReplayStore(tiny_cache.cache_root)
    try:
        one_minute = _load(
            store,
            tiny_cache,
            closed_by=tiny_cache.start + timedelta(minutes=5),
        )
        assert isinstance(one_minute, CachedCandleList)
        assert len(one_minute) == 5
        first = one_minute[0]
        assert first.candle_timestamp == tiny_cache.start
        assert first.nominal_close_time == tiny_cache.start + timedelta(minutes=1)
        assert (
            first.open_price,
            first.high_price,
            first.low_price,
            first.close_price,
            first.volume,
        ) == (100.0, 102.0, 99.0, 100.5, 10)
        assert first.source_instrument_id == 101
        assert first.source_raw_symbol == "MNQM4"
        assert first.source_file_sha256 == tiny_cache.source_fingerprint

        not_yet_closed = _load(
            store,
            tiny_cache,
            unit_number=5,
            end=tiny_cache.start + timedelta(minutes=5),
            closed_by=tiny_cache.start + timedelta(minutes=5, seconds=-1),
        )
        assert not not_yet_closed

        five_minute = _load(
            store,
            tiny_cache,
            unit_number=5,
            end=tiny_cache.start + timedelta(minutes=5),
            closed_by=tiny_cache.start + timedelta(minutes=5),
        )
        assert len(five_minute) == 1
        candle = five_minute[0]
        assert candle.candle_timestamp == tiny_cache.start
        assert candle.nominal_close_time == tiny_cache.start + timedelta(minutes=5)
        assert (
            candle.open_price,
            candle.high_price,
            candle.low_price,
            candle.close_price,
            candle.volume,
        ) == (100.0, 106.0, 99.0, 104.5, 60)
    finally:
        store.clear()


def test_lazy_mmap_view_matches_eager_fields_and_exposes_o1_slices(
    tiny_cache: TinyCache,
):
    store = DatabentoReplayStore(tiny_cache.cache_root)
    fields = (
        "user_id",
        "contract_id",
        "symbol",
        "live",
        "unit",
        "unit_number",
        "candle_timestamp",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
        "is_partial",
        "raw_payload",
        "fetched_at",
        "source",
        "source_instrument_id",
        "source_raw_symbol",
        "source_file_sha256",
        "roll_policy_version",
        "nominal_close_time",
    )
    try:
        eager = _load(
            store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=5),
            closed_by=tiny_cache.start + timedelta(minutes=5),
        )
        lazy = _open(
            store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=5),
            closed_by=tiny_cache.start + timedelta(minutes=5),
        )

        assert isinstance(lazy, MmapCandleSequence)
        assert len(lazy) == len(eager) == 5
        assert isinstance(lazy[0], MmapReplayCandle)
        assert lazy[0] is lazy[0]
        assert lazy[0] == lazy[0]
        assert lazy[0] == eager[0]
        assert lazy[0] in lazy
        with pytest.raises(TypeError):
            hash(lazy[0])
        assert lazy[-1].candle_timestamp == eager[-1].candle_timestamp
        for eager_row, lazy_row in zip(eager, lazy):
            assert {
                field: getattr(lazy_row, field) for field in fields
            } == {
                field: getattr(eager_row, field) for field in fields
            }

        assert lazy._topsignal_mmap_backed is True
        assert lazy._topsignal_lazy_replay is True
        assert lazy._topsignal_storage_bytes_per_row == 68
        assert lazy._topsignal_physical_row_count == 5
        assert lazy._topsignal_input_fingerprint == eager._topsignal_input_fingerprint
        assert lazy.search_start(tiny_cache.start + timedelta(minutes=2)) == 2
        assert (
            lazy.search_start(
                tiny_cache.start + timedelta(minutes=2), side="right"
            )
            == 3
        )
        assert lazy.search_close(tiny_cache.start + timedelta(minutes=2)) == 2
        assert lazy.search_close(_unix_nanos(tiny_cache.start), side="left") == 0

        sliced = lazy[1:4]
        assert isinstance(sliced, MmapCandleSequence)
        assert sliced._lease is lazy._lease
        assert sliced._topsignal_slice_start == lazy._topsignal_slice_start + 1
        assert sliced._topsignal_slice_end == lazy._topsignal_slice_start + 4
        assert sliced._topsignal_physical_row_count == 3
        assert np.shares_memory(sliced.start_ns, lazy.start_ns)
        assert np.shares_memory(sliced.close_nano_values, lazy.close_nano_values)
        assert sliced.materialize() == eager[1:4]
        assert lazy.materialize(1, 4) == eager[1:4]

        assert np.array_equal(lazy.start_ns, np.asarray([
            _unix_nanos(row.candle_timestamp) for row in eager
        ]))
        assert np.array_equal(lazy.close_ns, np.asarray([
            _unix_nanos(row.nominal_close_time) for row in eager
        ]))
        assert np.array_equal(
            lazy.volume_values,
            np.asarray([row.volume for row in eager], dtype=np.uint64),
        )
        assert np.array_equal(
            lazy.close_nano_values,
            np.asarray(
                [round(row.close_price * databento_cache.PRICE_SCALE) for row in eager],
                dtype=np.int64,
            ),
        )
        assert lazy.start_ns.flags.writeable is False
        with pytest.raises(ValueError):
            lazy.start_ns[0] = 0
        with pytest.raises(IndexError):
            _ = lazy[len(lazy)]
        assert len(lazy[3:2]) == 0
        assert lazy.materialize(3, 2) == []
    finally:
        store.clear()


def test_nonaligned_source_start_exposes_first_partial_resampled_bucket(
    tmp_path: Path,
):
    raw_start = datetime(2024, 3, 4, 14, 32, tzinfo=timezone.utc)
    archives, _ = _tiny_mnq_archives(
        tmp_path / "archives",
        start=raw_start,
    )
    cache_root = tmp_path / "cache"
    build_databento_cache(
        archives,
        cache_root=cache_root,
        timeframes=("1m", "5m"),
    )
    store = DatabentoReplayStore(cache_root)
    try:
        bounds = store.history_bounds("MNQ")
        assert bounds is not None
        assert bounds[0] == datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
        candles = store.load_candles(
            user_id=OWNER_ID,
            contract_id=CONTRACT_ID,
            root_symbol="MNQ",
            unit="minute",
            unit_number=5,
            start=bounds[0],
            end=raw_start + timedelta(minutes=8),
            closed_by=raw_start + timedelta(minutes=8),
        )
        assert [row.candle_timestamp for row in candles] == [
            datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc),
        ]
        assert (
            candles[0].open_price,
            candles[0].high_price,
            candles[0].low_price,
            candles[0].close_price,
            candles[0].volume,
        ) == (100.0, 104.0, 99.0, 102.5, 33)
    finally:
        store.clear()


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("dtype", "databento_series_dtype_mismatch"),
        ("shape", "databento_series_shape_mismatch"),
        ("monotonic", "databento_series_timestamps_not_strictly_increasing"),
        ("fingerprint", "databento_series_metadata_mismatch"),
    ],
)
def test_mmap_series_integrity_is_validated_before_replay(
    tmp_path: Path,
    corruption: str,
    message: str,
):
    archives, start = _tiny_mnq_archives(tmp_path / "archives")
    cache_root = tmp_path / "cache"
    result = build_databento_cache(
        archives,
        cache_root=cache_root,
        timeframes=("1m",),
    )
    manifest = json.loads((cache_root / "current.json").read_text())
    series_dir = Path(result.version_dir) / manifest["series"]["MNQ:1m"]["path"]
    if corruption == "dtype":
        path = series_dir / "volume.npy"
        values = np.asarray(np.load(path)).astype(np.float64)
        np.save(path, values)
    elif corruption == "shape":
        path = series_dir / "volume.npy"
        values = np.asarray(np.load(path))[:-1]
        np.save(path, values)
    elif corruption == "monotonic":
        path = series_dir / "timestamp_ns.npy"
        values = np.load(path, mmap_mode="r+")
        try:
            values[1] = values[0]
            values.flush()
        finally:
            values._mmap.close()
    else:
        path = series_dir / "metadata.json"
        metadata = json.loads(path.read_text())
        metadata["series_fingerprint"] = "0" * 64
        path.write_text(json.dumps(metadata), encoding="utf-8")

    store = DatabentoReplayStore(cache_root)
    try:
        with pytest.raises(DatabentoCacheError, match=message):
            store.load_candles(
                user_id=OWNER_ID,
                contract_id=CONTRACT_ID,
                root_symbol="MNQ",
                unit="minute",
                unit_number=1,
                start=start,
                end=start + timedelta(minutes=6),
                closed_by=start + timedelta(minutes=6),
            )
    finally:
        store.clear()


def test_series_directory_shortens_only_the_on_disk_fingerprint(tmp_path: Path):
    archives, _ = _tiny_mnq_archives(tmp_path / "archives")
    cache_root = tmp_path / "cache"

    result = build_databento_cache(
        archives,
        cache_root=cache_root,
        timeframes=("1m",),
    )

    manifest = json.loads((cache_root / "current.json").read_text())
    entry = manifest["series"]["MNQ:1m"]
    series_fingerprint = str(entry["series_fingerprint"])
    fingerprint_segment = Path(str(entry["path"])).parts[-1]
    metadata = json.loads(
        (Path(result.version_dir) / str(entry["path"]) / "metadata.json").read_text()
    )

    assert len(series_fingerprint) == 64
    assert fingerprint_segment == f"fingerprint={series_fingerprint[:20]}"
    assert metadata["series_fingerprint"] == series_fingerprint


def test_source_mtime_change_invalidates_slices_and_open_mappings(tmp_path: Path):
    archives, start = _tiny_mnq_archives(tmp_path / "archives")
    cache_root = tmp_path / "cache"
    build_databento_cache(archives, cache_root=cache_root, timeframes=("1m",))
    store = DatabentoReplayStore(cache_root)
    cache = TinyCache(
        archives=archives,
        cache_root=cache_root,
        version_dir=Path(),
        source_fingerprint="",
        start=start,
    )
    _load(store, cache)
    assert store.stats()["slice_entries"] == 1
    assert store.stats()["mapped_series"] == 1

    changed = archives[0]
    prior = changed.stat()
    os.utime(
        changed,
        ns=(prior.st_atime_ns, prior.st_mtime_ns + 2_000_000_000),
    )
    with pytest.raises(DatabentoCacheStaleError, match="databento_source_changed"):
        _load(store, cache)
    assert store.stats()["slice_entries"] == 0
    assert store.stats()["mapped_series"] == 0


def test_source_invalidation_retires_mapping_after_lazy_view_lease_is_released(
    tmp_path: Path,
):
    archives, start = _tiny_mnq_archives(tmp_path / "archives")
    cache_root = tmp_path / "cache"
    build_databento_cache(archives, cache_root=cache_root, timeframes=("1m",))
    store = DatabentoReplayStore(cache_root)
    cache = TinyCache(
        archives=archives,
        cache_root=cache_root,
        version_dir=Path(),
        source_fingerprint="",
        start=start,
    )
    lazy = _open(store, cache)
    child = lazy[1:3]
    assert isinstance(child, MmapCandleSequence)
    mapped = lazy._lease.mapped
    expected_close = lazy[0].close_price
    expected_child_close = child[0].close_price
    assert mapped._lease_count == 1
    assert mapped._closed is False

    changed = archives[0]
    prior = changed.stat()
    os.utime(
        changed,
        ns=(prior.st_atime_ns, prior.st_mtime_ns + 2_000_000_000),
    )
    with pytest.raises(DatabentoCacheStaleError, match="databento_source_changed"):
        _open(store, cache)

    assert store.stats()["mapped_series"] == 0
    assert mapped._retired is True
    assert mapped._closed is False
    assert lazy[0].close_price == expected_close

    del lazy
    gc.collect()
    assert mapped._lease_count == 1
    assert mapped._closed is False
    assert child[0].close_price == expected_child_close

    del child
    gc.collect()
    assert mapped._lease_count == 0
    assert mapped._closed is True
    store.clear()


def test_lazy_proxy_cache_is_shared_by_slices_and_row_bounded(
    tiny_cache: TinyCache,
    monkeypatch,
):
    monkeypatch.setenv("TOPSIGNAL_BACKTEST_PROXY_CACHE_ROWS", "2")
    store = DatabentoReplayStore(tiny_cache.cache_root)
    try:
        lazy = _open(store, tiny_cache)
        child = lazy[1:4]
        assert isinstance(child, MmapCandleSequence)
        assert child._proxy_cache is lazy._proxy_cache
        assert child[0] is lazy[1]
        for index in range(len(lazy)):
            _ = lazy[index].candle_timestamp
        assert len(lazy._proxy_cache) == 2
        assert list(lazy._proxy_cache) == [len(lazy) - 2, len(lazy) - 1]
    finally:
        store.clear()


def test_raw_numpy_view_survives_sequence_release_and_store_clear_in_subprocess(
    tiny_cache: TinyCache,
):
    # A regression here can terminate the interpreter instead of raising a
    # Python exception, so isolate it from the pytest worker.
    script = """
import gc
import sys
from datetime import datetime, timedelta
from app.services.databento_cache import DatabentoReplayStore

root, start_text = sys.argv[1:]
start = datetime.fromisoformat(start_text)
store = DatabentoReplayStore(root)
sequence = store.open_candles(
    user_id="11111111-1111-1111-1111-111111111111",
    contract_id="CON.F.US.MNQ.M24",
    root_symbol="MNQ",
    unit="minute",
    unit_number=1,
    start=start,
    end=start + timedelta(minutes=5),
    closed_by=start + timedelta(minutes=5),
)
view = sequence.start_ns
expected = int(view[0])
del sequence
gc.collect()
store.clear()
gc.collect()
assert int(view[0]) == expected
print(expected)
"""
    backend_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(backend_root), environment.get("PYTHONPATH", ""))
        if value
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tiny_cache.cache_root),
            tiny_cache.start.isoformat(),
        ],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_same_size_same_mtime_source_edit_invalidates_cache(tmp_path: Path):
    archives, start = _tiny_mnq_archives(tmp_path / "archives")
    cache_root = tmp_path / "cache"
    build_databento_cache(archives, cache_root=cache_root, timeframes=("1m",))
    store = DatabentoReplayStore(cache_root)
    cache = TinyCache(
        archives=archives,
        cache_root=cache_root,
        version_dir=Path(),
        source_fingerprint="",
        start=start,
    )
    try:
        _load(store, cache)
        changed = archives[0]
        prior = changed.stat()
        payload = bytearray(changed.read_bytes())
        payload[len(payload) // 2] ^= 0x01
        changed.write_bytes(payload)
        os.utime(changed, ns=(prior.st_atime_ns, prior.st_mtime_ns))
        current = changed.stat()
        assert current.st_size == prior.st_size
        assert current.st_mtime_ns == prior.st_mtime_ns

        with pytest.raises(DatabentoCacheStaleError, match="databento_source_changed"):
            _load(store, cache)
        assert store.stats()["slice_entries"] == 0
        assert store.stats()["mapped_series"] == 0
    finally:
        store.clear()


def test_local_roll_schedule_is_prefix_invariant_and_uses_only_prior_volume():
    def contract(
        instrument_id: int,
        raw_symbol: str,
        expiration: datetime,
    ) -> _Instrument:
        return _Instrument(
            root_symbol="MNQ",
            instrument_id=instrument_id,
            raw_symbol=raw_symbol,
            contract_key=f"{raw_symbol}@2024",
            instrument_class="F",
            security_type="FUT",
            activation_ns=_unix_nanos(datetime(2024, 1, 1, tzinfo=timezone.utc)),
            expiration_ns=_unix_nanos(expiration),
            min_price_increment_nano=250_000_000,
            unit_of_measure_qty_nano=2_000_000_000,
            definition_ts_ns=_unix_nanos(
                datetime(2024, 1, 2, tzinfo=timezone.utc)
            ),
            source_sha256="a" * 64,
        )

    contracts = [
        contract(101, "MNQM4", datetime(2024, 6, 21, 13, 30, tzinfo=timezone.utc)),
        contract(102, "MNQU4", datetime(2024, 9, 20, 13, 30, tzinfo=timezone.utc)),
    ]
    monday = date(2024, 3, 4)
    volumes = {
        monday: {"MNQM4@2024": 100, "MNQU4@2024": 90},
        monday + timedelta(days=1): {"MNQM4@2024": 100, "MNQU4@2024": 100},
        monday + timedelta(days=2): {"MNQM4@2024": 90, "MNQU4@2024": 120},
        monday + timedelta(days=3): {"MNQM4@2024": 1_000, "MNQU4@2024": 1},
    }
    prefix = _build_roll_schedule(
        root_symbol="MNQ", contracts=contracts, daily_volumes=volumes
    )
    extended = _build_roll_schedule(
        root_symbol="MNQ",
        contracts=contracts,
        daily_volumes={
            **volumes,
            monday + timedelta(days=4): {
                "MNQM4@2024": 999_999,
                "MNQU4@2024": 0,
            },
        },
    )

    assert extended[: len(prefix)] == prefix
    assert [decision.raw_symbol for decision in prefix] == [
        "MNQM4",
        "MNQM4",
        "MNQM4",
        "MNQU4",
    ]
    assert prefix[2].reason == "kept_current_contract"
    assert (prefix[2].current_volume, prefix[2].candidate_volume) == (100, 100)
    assert prefix[3].decision_session_date == monday + timedelta(days=2)
    assert (prefix[3].current_volume, prefix[3].candidate_volume) == (90, 120)


def test_roll_eligibility_uses_only_definition_state_known_at_session_open():
    def contract(
        instrument_id: int,
        raw_symbol: str,
        expiration: datetime,
        *,
        definition_time: datetime,
        activation: datetime,
        action: str = "A",
    ) -> _Instrument:
        return _Instrument(
            root_symbol="MNQ",
            instrument_id=instrument_id,
            raw_symbol=raw_symbol,
            contract_key=f"{raw_symbol}@2024",
            instrument_class="F",
            security_type="FUT",
            activation_ns=_unix_nanos(activation),
            expiration_ns=_unix_nanos(expiration),
            min_price_increment_nano=250_000_000,
            unit_of_measure_qty_nano=2_000_000_000,
            definition_ts_ns=_unix_nanos(definition_time),
            source_sha256="a" * 64,
            security_update_action=action,
        )

    monday = date(2024, 3, 4)
    wednesday_start, _ = databento_cache.trading_day_bounds_utc(
        monday + timedelta(days=2)
    )
    original_front = contract(
        101,
        "MNQM4",
        datetime(2024, 6, 21, 13, 30, tzinfo=timezone.utc),
        definition_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        activation=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    future_modification = contract(
        101,
        "MNQM4",
        datetime(2024, 6, 21, 13, 30, tzinfo=timezone.utc),
        definition_time=wednesday_start,
        activation=datetime(2024, 5, 1, tzinfo=timezone.utc),
        action="M",
    )
    next_contract = contract(
        102,
        "MNQU4",
        datetime(2024, 9, 20, 13, 30, tzinfo=timezone.utc),
        definition_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        activation=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    volumes = {
        monday + timedelta(days=offset): {
            "MNQM4@2024": 100,
            "MNQU4@2024": 10,
        }
        for offset in range(3)
    }

    prefix = _build_roll_schedule(
        root_symbol="MNQ",
        contracts=[original_front, next_contract],
        daily_volumes={key: value for key, value in list(volumes.items())[:2]},
    )
    extended = _build_roll_schedule(
        root_symbol="MNQ",
        contracts=[original_front, future_modification, next_contract],
        daily_volumes=volumes,
    )

    assert extended[:2] == prefix
    assert [decision.raw_symbol for decision in extended] == [
        "MNQM4",
        "MNQM4",
        "MNQU4",
    ]
    assert extended[2].reason == "expiration_fallback"


def test_time_aware_mapping_resolves_a_reused_instrument_id_by_calendar_day():
    first_day = date(2024, 3, 4)
    metadata = Metadata(
        dataset=DATASET,
        start=_unix_nanos(datetime(2024, 3, 4, tzinfo=timezone.utc)),
        end=_unix_nanos(datetime(2024, 3, 7, tzinfo=timezone.utc)),
        stype_in=SType.PARENT,
        stype_out=SType.INSTRUMENT_ID,
        schema=Schema.OHLCV_1M,
        symbols=["MNQ.FUT"],
        mappings=[
            SimpleNamespace(
                raw_symbol="MNQM4",
                intervals=[
                    SimpleNamespace(
                        start_date=first_day,
                        end_date=first_day + timedelta(days=1),
                        symbol="101",
                    )
                ],
            ),
            SimpleNamespace(
                raw_symbol="MNQU4",
                intervals=[
                    SimpleNamespace(
                        start_date=first_day + timedelta(days=1),
                        end_date=first_day + timedelta(days=3),
                        symbol="101",
                    )
                ],
            ),
        ],
    )
    resolver = _MappingResolver(metadata, root_symbol="MNQ")

    assert resolver.resolve(
        _unix_nanos(datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)), 101
    ) == "MNQM4"
    assert resolver.resolve(
        _unix_nanos(datetime(2024, 3, 5, 14, 30, tzinfo=timezone.utc)), 101
    ) == "MNQU4"


def test_unresolved_valid_outright_is_rejected_instead_of_silently_dropped(
    tmp_path: Path,
):
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    start = datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    definitions = _write_dbn_archive(
        archive_dir / "definition.zip",
        root_symbol="MNQ",
        schema_name="definition",
        records=[_definition("MNQ")],
    )
    ohlcv = _write_dbn_archive(
        archive_dir / "ohlcv.zip",
        root_symbol="MNQ",
        schema_name="ohlcv-1m",
        records=[_ohlcv(start, instrument_id=102, index=0)],
        mappings=[
            SimpleNamespace(
                raw_symbol="MNQU4",
                intervals=[
                    SimpleNamespace(
                        start_date=start.date(),
                        end_date=start.date() + timedelta(days=1),
                        symbol="102",
                    )
                ],
            )
        ],
    )

    with pytest.raises(
        DatabentoCacheError,
        match="databento_outright_definition_missing:MNQ:MNQU4:102",
    ):
        build_databento_cache(
            [definitions, ohlcv],
            cache_root=tmp_path / "cache",
            timeframes=("1m",),
        )


def test_parent_spread_rows_remain_intentionally_excluded(tmp_path: Path):
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    start = datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    definitions = _write_dbn_archive(
        archive_dir / "definition.zip",
        root_symbol="MNQ",
        schema_name="definition",
        records=[_definition("MNQ")],
    )
    ohlcv = _write_dbn_archive(
        archive_dir / "ohlcv.zip",
        root_symbol="MNQ",
        schema_name="ohlcv-1m",
        records=[
            _ohlcv(start, instrument_id=101, index=0),
            _ohlcv(start + timedelta(minutes=1), instrument_id=102, index=1),
        ],
        mappings=[
            SimpleNamespace(
                raw_symbol="MNQM4",
                intervals=[
                    SimpleNamespace(
                        start_date=start.date(),
                        end_date=start.date() + timedelta(days=1),
                        symbol="101",
                    )
                ],
            ),
            SimpleNamespace(
                raw_symbol="MNQM4-MNQU4",
                intervals=[
                    SimpleNamespace(
                        start_date=start.date(),
                        end_date=start.date() + timedelta(days=1),
                        symbol="102",
                    )
                ],
            ),
        ],
    )

    result = build_databento_cache(
        [definitions, ohlcv],
        cache_root=tmp_path / "cache",
        timeframes=("1m",),
    )
    manifest = json.loads((Path(result.version_dir) / "manifest.json").read_text())
    assert manifest["parquet_rows"]["ohlcv_1m"] == 1
    assert manifest["series"]["MNQ:1m"]["rows"] == 1


def test_repeated_one_digit_delivery_symbols_keep_distinct_decade_identities():
    assert _contract_key(
        "ESZ1",
        expiration_ns=_unix_nanos(
            datetime(2011, 12, 16, 14, 30, tzinfo=timezone.utc)
        ),
        reference_date=date(2011, 1, 1),
    ) == "ESZ1@2011"
    assert _contract_key(
        "ESZ1",
        expiration_ns=_unix_nanos(
            datetime(2021, 12, 17, 14, 30, tzinfo=timezone.utc)
        ),
        reference_date=date(2021, 1, 1),
    ) == "ESZ1@2021"

    candidates = [
        _Instrument(
            root_symbol="ES",
            instrument_id=instrument_id,
            raw_symbol="ESZ1",
            contract_key=contract_key,
            instrument_class="F",
            security_type="FUT",
            activation_ns=-1,
            expiration_ns=-1,
            min_price_increment_nano=250_000_000,
            unit_of_measure_qty_nano=50_000_000_000,
            definition_ts_ns=_unix_nanos(
                datetime(year - 2, 1, 1, tzinfo=timezone.utc)
            ),
            source_sha256="a" * 64,
        )
        for instrument_id, contract_key, year in (
            (1, "ESZ1@2011", 2011),
            (2, "ESZ1@2021", 2021),
        )
    ]
    assert _resolve_contract_code(
        raw_symbol="ESZ1",
        timestamp_ns=_unix_nanos(
            datetime(2021, 6, 1, tzinfo=timezone.utc)
        ),
        candidates=candidates,
        codes={"ESZ1@2011": 1, "ESZ1@2021": 2},
        cache={},
    ) == ("ESZ1@2021", 2)


def test_bounded_lru_reports_hits_and_evicts_least_recent_slice(tiny_cache: TinyCache):
    store = DatabentoReplayStore(
        tiny_cache.cache_root,
        max_entries=1,
        max_bytes=1024 * 1024,
    )
    try:
        first = _load(
            store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=3),
            closed_by=tiny_cache.start + timedelta(minutes=3),
        )
        repeated = _load(
            store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=3),
            closed_by=tiny_cache.start + timedelta(minutes=3),
        )
        assert repeated is first
        assert store.stats() == {
            "hits": 1,
            "misses": 1,
            "evictions": 0,
            "slice_entries": 1,
            "slice_bytes": 3 * 640,
            "mapped_series": 1,
        }

        _load(
            store,
            tiny_cache,
            start=tiny_cache.start + timedelta(minutes=3),
            end=tiny_cache.start + timedelta(minutes=6),
            closed_by=tiny_cache.start + timedelta(minutes=6),
        )
        after_eviction = store.stats()
        assert after_eviction["hits"] == 1
        assert after_eviction["misses"] == 2
        assert after_eviction["evictions"] == 1
        assert after_eviction["slice_entries"] == 1

        loaded_again = _load(
            store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=3),
            closed_by=tiny_cache.start + timedelta(minutes=3),
        )
        assert loaded_again is not first
        assert store.stats()["misses"] == 3
        assert store.stats()["evictions"] == 2
    finally:
        store.clear()


def test_eager_slice_lru_charges_at_least_640_bytes_per_bar(
    tiny_cache: TinyCache,
):
    store = DatabentoReplayStore(
        tiny_cache.cache_root,
        max_entries=8,
        max_bytes=3 * 640 - 1,
    )
    try:
        first = _load(
            store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=3),
            closed_by=tiny_cache.start + timedelta(minutes=3),
        )
        repeated = _load(
            store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=3),
            closed_by=tiny_cache.start + timedelta(minutes=3),
        )
        assert repeated == first
        assert repeated is not first
        assert store.stats()["slice_entries"] == 0
        assert store.stats()["slice_bytes"] == 0
        assert store.stats()["misses"] == 2
    finally:
        store.clear()


def test_slice_fingerprint_is_stable_for_the_same_binary_search_bounds(
    tiny_cache: TinyCache,
):
    first_store = DatabentoReplayStore(tiny_cache.cache_root)
    second_store = DatabentoReplayStore(tiny_cache.cache_root)
    try:
        first = _load(
            first_store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=5),
            closed_by=tiny_cache.start + timedelta(minutes=5),
        )
        same_bounds = _load(
            first_store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=5, seconds=30),
            closed_by=tiny_cache.start + timedelta(minutes=5, seconds=30),
        )
        reopened = _load(
            second_store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=5),
            closed_by=tiny_cache.start + timedelta(minutes=5),
        )
        other_contract = _load(
            second_store,
            tiny_cache,
            end=tiny_cache.start + timedelta(minutes=5),
            closed_by=tiny_cache.start + timedelta(minutes=5),
            contract_id="CON.F.US.MNQ.U24",
        )

        fingerprint = first._topsignal_input_fingerprint
        assert fingerprint is not None
        assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        assert first._topsignal_slice_start == same_bounds._topsignal_slice_start == 0
        assert first._topsignal_slice_end == same_bounds._topsignal_slice_end == 5
        assert same_bounds._topsignal_input_fingerprint == fingerprint
        assert reopened._topsignal_input_fingerprint == fingerprint
        assert reopened._topsignal_series_fingerprint == first._topsignal_series_fingerprint
        assert other_contract._topsignal_input_fingerprint != fingerprint
    finally:
        first_store.clear()
        second_store.clear()


def test_relative_store_root_is_resolved_from_the_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    relative = Path("test-artifacts") / "databento-cache"
    store = DatabentoReplayStore(relative)
    repository_root = Path(__file__).resolve().parents[2]
    assert store.cache_root == (repository_root / relative).resolve()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1m", ("minute", 1)),
        ("60s", ("second", 60)),
        ("4h", ("hour", 4)),
        ("1d", ("day", 1)),
    ],
)
def test_parse_timeframe_accepts_supported_cache_keys(text: str, expected: tuple[str, int]):
    assert parse_timeframe(text) == expected


def test_parse_timeframe_rejects_subminute_data():
    with pytest.raises(DatabentoCacheError, match="cannot_resample_below_one_minute"):
        parse_timeframe("30s")


@pytest.mark.parametrize("root_symbol", sorted(ROOT_CONTRACTS))
def test_archive_metadata_accepts_each_supported_root(
    root_symbol: str,
    tmp_path: Path,
):
    archive = _write_dbn_archive(
        tmp_path / f"{root_symbol.lower()}-definition.zip",
        root_symbol=root_symbol,
        schema_name="definition",
        records=[_definition(root_symbol)],
    )
    descriptor = inspect_archive(archive)
    assert descriptor.root_symbol == root_symbol
    assert descriptor.schema == "definition"
    assert descriptor.dataset == DATASET
    assert descriptor.sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_sqlite_persistence_benchmark_checks_cold_and_warm_result_cache(
    tmp_path: Path,
):
    archives, start = _tiny_mnq_archives(
        tmp_path / "archives",
        bar_count=150,
    )
    cache_root = tmp_path / "cache"
    build_databento_cache(
        archives,
        cache_root=cache_root,
        timeframes=("5m",),
    )
    end = start + timedelta(minutes=150)
    args = benchmark_databento_cache._parser().parse_args(
        [
            "--cache-dir",
            str(cache_root),
            "--root",
            "MNQ",
            "--timeframe",
            "5m",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--cold-repeats",
            "1",
            "--warm-repeats",
            "1",
            "--max-rows",
            "1000",
            "--lookback-bars",
            "25",
            "--fast-period",
            "1",
            "--slow-period",
            "2",
            "--sqlite-persistence",
        ]
    )

    report = benchmark_databento_cache._run_benchmark(args)
    persistence = report["sqlite_persistence"]

    assert report["case"]["input_mode"] == "lazy"
    assert report["case"]["source_method"] == "open_candles"
    assert report["case"]["input_type"] == "MmapCandleSequence"
    assert report["case"]["lazy_mmap"] is True
    assert report["case"]["storage_bytes_per_row"] == 68
    assert "prepare_input" in report["cold"]["phase_seconds"]
    assert persistence is not None
    assert persistence["input_mode"] == "application_lazy_mmap"
    assert persistence["cold"]["samples"][0]["source_calls"] == {
        "open_candles": 1,
        "load_candles": 0,
    }
    assert persistence["warm"]["samples"][0]["source_calls"] == {
        "open_candles": 1,
        "load_candles": 0,
    }
    assert persistence["tables"] == [
        "bot_backtests",
        "bot_configs",
        "instrument_metadata",
    ]
    assert persistence["persisted_rows"] == 2
    assert persistence["semantic_match"] is True
    assert persistence["cold"]["samples"][0]["result_cache_hit"] is False
    assert persistence["warm"]["samples"][0]["result_cache_hit"] is True
    assert (
        persistence["cold"]["samples"][0]["input_fingerprint"]
        == persistence["warm"]["samples"][0]["input_fingerprint"]
    )

    args.input_mode = "eager"
    args.sqlite_persistence = False
    eager_report = benchmark_databento_cache._run_benchmark(args)
    assert eager_report["case"]["input_mode"] == "eager"
    assert eager_report["case"]["source_method"] == "load_candles"
    assert eager_report["case"]["input_type"] == "CachedCandleList"
    assert eager_report["case"]["lazy_mmap"] is False
    assert eager_report["semantic_sha256"] == report["semantic_sha256"]
