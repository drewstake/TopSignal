from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

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
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.services.bot_backtesting as backtesting_module
from app.bot_schemas import BotBacktestIn
from app.db import Base
from app.models import (
    BotBacktest,
    BotConfig,
    DatabentoImportBatch,
    DatabentoImportFile,
    DatabentoInstrument,
    DatabentoOhlcv1m,
    DatabentoRollSchedule,
    InstrumentMetadata,
)
from app.services.databento_ingestion import (
    MNQ_HISTORY_START_UTC,
    DatabentoIngestionError,
    import_databento_archives,
)
from app.services.databento_cache import (
    DatabentoReplayStore,
    build_databento_cache,
)
from app.services.databento_market_data import (
    ROLL_POLICY_VERSION,
    DatabentoMarketDataError,
    RolloverContract,
    _RawContinuousBar,
    build_volume_roll_schedule,
    load_databento_replay_candles,
    resample_databento_bars,
)
from app.services.trading_day import trading_day_date


DATASET = "GLBX.MDP3"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
CONTRACT_ID = "CON.F.US.MNQ.M24"
HASH_A = "a" * 64
HASH_B = "b" * 64
TABLES = [
    InstrumentMetadata.__table__,
    DatabentoImportBatch.__table__,
    DatabentoImportFile.__table__,
    DatabentoInstrument.__table__,
    DatabentoOhlcv1m.__table__,
    DatabentoRollSchedule.__table__,
    BotConfig.__table__,
    BotBacktest.__table__,
]


@pytest.fixture(autouse=True)
def enable_relational_databento_sqlite_fixtures(monkeypatch):
    monkeypatch.setattr(
        backtesting_module,
        "ALLOW_LEGACY_DATABENTO_SQLITE_FIXTURES",
        True,
    )


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("pragma foreign_keys = on")

    Base.metadata.create_all(bind=engine, tables=TABLES)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(TABLES)))
        engine.dispose()


def _unix_nanos(value: datetime) -> int:
    value = value.astimezone(timezone.utc)
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1_000


def _definition(
    *,
    instrument_id: int,
    raw_symbol: str,
    instrument_class: InstrumentClass = InstrumentClass.FUTURE,
) -> InstrumentDefMsg:
    return InstrumentDefMsg(
        publisher_id=1,
        instrument_id=instrument_id,
        ts_event=_unix_nanos(datetime(2024, 1, 2, tzinfo=timezone.utc)),
        ts_recv=_unix_nanos(datetime(2024, 1, 2, tzinfo=timezone.utc)),
        min_price_increment=250_000_000,
        display_factor=1_000_000_000,
        raw_symbol=raw_symbol,
        asset="MNQ",
        security_type="FUT",
        instrument_class=instrument_class,
        security_update_action=SecurityUpdateAction.ADD,
        expiration=_unix_nanos(datetime(2024, 6, 21, 13, 30, tzinfo=timezone.utc)),
        activation=_unix_nanos(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        unit_of_measure_qty=2_000_000_000,
    )


def _ohlcv(
    timestamp: datetime,
    *,
    instrument_id: int = 101,
    price_nano: int = 18_000_000_000_000,
    volume: int = 10,
) -> OHLCVMsg:
    return OHLCVMsg(
        rtype=RType.OHLCV_1M,
        publisher_id=1,
        instrument_id=instrument_id,
        ts_event=_unix_nanos(timestamp),
        open=price_nano,
        high=price_nano + 500_000_000,
        low=price_nano - 500_000_000,
        close=price_nano + 250_000_000,
        volume=volume,
    )


def _write_dbn_archive(
    path: Path,
    *,
    job_id: str,
    schema_name: str,
    records: Iterable[InstrumentDefMsg | OHLCVMsg],
) -> Path:
    records = list(records)
    schema = Schema.DEFINITION if schema_name == "definition" else Schema.OHLCV_1M
    record_times = [
        int(record.ts_recv if schema_name == "definition" else record.ts_event)
        for record in records
    ]
    start = min(record_times)
    end = max(record_times) + 60_000_000_000
    dbn_metadata = Metadata(
        dataset=DATASET,
        start=start,
        end=end,
        stype_in=SType.PARENT,
        stype_out=SType.INSTRUMENT_ID,
        schema=schema,
        symbols=["MNQ.FUT"],
    )
    uncompressed = bytes(dbn_metadata) + b"".join(bytes(record) for record in records)
    payload = zstandard.ZstdCompressor().compress(uncompressed)
    payload_name = f"tiny.{schema_name}.dbn.zst"
    payload_hash = hashlib.sha256(payload).hexdigest()
    metadata_json = {
        "version": 1,
        "job_id": job_id,
        "query": {
            "dataset": DATASET,
            "schema": schema_name,
            "symbols": ["MNQ.FUT"],
            "stype_in": "parent",
            "stype_out": "instrument_id",
            "encoding": "dbn",
            "compression": "zstd",
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


def _instrument(
    instrument_id: int = 101,
    *,
    raw_symbol: str = "MNQM4",
    expiration: datetime | None = None,
) -> DatabentoInstrument:
    return DatabentoInstrument(
        dataset=DATASET,
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        root_symbol="MNQ",
        instrument_class="F",
        security_type="FUT",
        activation=datetime(2024, 1, 1, tzinfo=timezone.utc),
        expiration=expiration
        or datetime(2024, 6, 21, 13, 30, tzinfo=timezone.utc),
        min_price_increment_nano=250_000_000,
        unit_of_measure_qty_nano=2_000_000_000,
        definition_ts=datetime(2024, 1, 2, tzinfo=timezone.utc),
        source_file_sha256=HASH_A,
    )


def _bar(timestamp: datetime, *, index: int = 0) -> DatabentoOhlcv1m:
    price = 18_000_000_000_000 + index * 250_000_000
    return DatabentoOhlcv1m(
        dataset=DATASET,
        instrument_id=101,
        ts_event=timestamp,
        trading_date=trading_day_date(timestamp),
        open_nano=price,
        high_nano=price + 500_000_000,
        low_nano=price - 500_000_000,
        close_nano=price + 250_000_000,
        volume=10 + index,
        source_file_sha256=HASH_B,
    )


def _schedule(session_date: date) -> DatabentoRollSchedule:
    return DatabentoRollSchedule(
        root_symbol="MNQ",
        trading_date=session_date,
        dataset=DATASET,
        instrument_id=101,
        raw_symbol="MNQM4",
        decision_session_date=None,
        from_instrument_id=None,
        current_volume=None,
        candidate_volume=None,
        reason="initial_front_contract",
        policy_version=ROLL_POLICY_VERSION,
    )


def test_generated_dbn_zstd_archives_import_in_dependency_order_and_are_idempotent(
    db_session,
    tmp_path,
):
    definition_archive = _write_dbn_archive(
        tmp_path / "definition.zip",
        job_id="tiny-definition",
        schema_name="definition",
        records=[
            _definition(instrument_id=101, raw_symbol="MNQM4"),
            _definition(
                instrument_id=201,
                raw_symbol="MNQM4-MNQU4",
                instrument_class=InstrumentClass.FUTURE_SPREAD,
            ),
        ],
    )
    first_bar = datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    ohlcv_archive = _write_dbn_archive(
        tmp_path / "ohlcv.zip",
        job_id="tiny-ohlcv",
        schema_name="ohlcv-1m",
        records=[
            _ohlcv(first_bar),
            _ohlcv(first_bar + timedelta(minutes=1), price_nano=18_001_000_000_000),
            _ohlcv(first_bar, instrument_id=201),
        ],
    )

    first = import_databento_archives(
        db_session,
        [ohlcv_archive, definition_archive],
        commit_batches=True,
    )

    assert [result.schema_name for result in first] == ["definition", "ohlcv-1m"]
    assert [(result.records_read, result.records_inserted) for result in first] == [
        (2, 2),
        (3, 2),
    ]
    assert all(result.files_completed == 1 for result in first)
    assert db_session.query(DatabentoInstrument).count() == 2
    assert db_session.query(DatabentoOhlcv1m).count() == 2
    assert {
        row.instrument_id for row in db_session.query(DatabentoOhlcv1m).all()
    } == {101}
    assert db_session.get(DatabentoInstrument, (DATASET, 101)).raw_symbol == "MNQM4"

    second = import_databento_archives(
        db_session,
        [definition_archive, ohlcv_archive],
        commit_batches=True,
    )

    assert all(result.skipped for result in second)
    assert [(result.records_read, result.records_inserted) for result in second] == [
        (2, 2),
        (3, 2),
    ]
    assert db_session.query(DatabentoImportBatch).count() == 2
    assert db_session.query(DatabentoImportFile).count() == 2
    assert db_session.query(DatabentoOhlcv1m).count() == 2


def test_local_cache_matches_relational_replay_for_the_same_dbn_sample(
    db_session,
    tmp_path,
):
    source_start = datetime(2024, 3, 4, 13, 30, tzinfo=timezone.utc)
    definition_archive = _write_dbn_archive(
        tmp_path / "parity-definition.zip",
        job_id="parity-definition",
        schema_name="definition",
        records=[_definition(instrument_id=101, raw_symbol="MNQM4")],
    )
    ohlcv_archive = _write_dbn_archive(
        tmp_path / "parity-ohlcv.zip",
        job_id="parity-ohlcv",
        schema_name="ohlcv-1m",
        records=[
            _ohlcv(
                source_start + timedelta(minutes=index),
                price_nano=18_000_000_000_000 + index * 250_000_000,
                volume=10 + index,
            )
            for index in range(150)
        ],
    )

    # Feed the exact same DBN payloads through the retained SQLite relational
    # fixture loader and the canonical local builder.
    import_databento_archives(
        db_session,
        [ohlcv_archive, definition_archive],
        commit_batches=True,
    )
    db_session.add(_schedule(trading_day_date(source_start)))
    db_session.commit()
    cache_root = tmp_path / "parity-cache"
    build_databento_cache(
        [definition_archive, ohlcv_archive],
        cache_root=cache_root,
        timeframes=["5m"],
    )
    store = DatabentoReplayStore(cache_root, build_missing_timeframes=False)

    load_options = {
        "max_rows": 100,
        "user_id": OWNER_ID,
        "contract_id": CONTRACT_ID,
        "root_symbol": "MNQ",
        "unit": "minute",
        "unit_number": 5,
        "start": source_start,
        "end": source_start + timedelta(minutes=150),
        "closed_by": source_start + timedelta(minutes=150),
    }
    relational = load_databento_replay_candles(db_session, **load_options)
    local = store.open_candles(
        **{key: value for key, value in load_options.items() if key != "max_rows"}
    )

    def candle_values(row):
        return (
            row.candle_timestamp,
            row.nominal_close_time,
            row.open_price,
            row.high_price,
            row.low_price,
            row.close_price,
            row.volume,
            row.source_instrument_id,
            row.source_raw_symbol,
            row.roll_policy_version,
        )

    assert [candle_values(row) for row in local] == [
        candle_values(row) for row in relational
    ]

    config = BotConfig(
        id=99,
        user_id=OWNER_ID,
        account_id=9001,
        name="DBN path parity",
        provider="projectx",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="sma_cross",
        strategy_params={},
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=1,
        slow_period=2,
        order_size=1,
        max_contracts=10,
        max_daily_loss=100_000,
        max_trades_per_day=100,
        max_open_position=10,
        allowed_contracts=[CONTRACT_ID],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3_600,
        allow_market_depth=False,
    )
    replay_options = {
        "config": config,
        "start": source_start,
        "end": source_start + timedelta(minutes=150),
        "starting_balance": 25_000.0,
        "commission_per_contract": 1.25,
        "slippage_ticks": 1.0,
        "tick_size": 0.25,
        "tick_value": 0.50,
        "force_close_at_end": True,
    }
    try:
        relational_result = backtesting_module.run_backtest(
            candles=relational,
            **replay_options,
        )
        local_result = backtesting_module.run_backtest(candles=local, **replay_options)
        assert local_result == relational_result

        # Exercise the TopBot history adapter too: its rolling evaluator
        # inputs must remain zero-copy mmap slices and still produce the exact
        # eager/list result.
        config.strategy_type = "topbot_adaptive"
        config.lookback_bars = 3
        config.strategy_params = {
            "source_strategies": ["sma_cross"],
            "minimum_directional_votes": 1,
            "max_opposing_votes": 0,
            "minimum_confidence": 0,
            "minimum_score": 0,
            "minimum_reward_risk": 1,
        }
        primary_key = backtesting_module._topbot_asset_stream_key("minute", 5)
        eager_topbot = backtesting_module.run_backtest(
            candles=relational,
            replay_streams={primary_key: relational},
            **replay_options,
        )
        lazy_topbot = backtesting_module.run_backtest(
            candles=local,
            replay_streams={primary_key: local},
            **replay_options,
        )
        assert lazy_topbot == eager_topbot
    finally:
        store.clear()


def test_generated_dbn_rejects_mnq_bars_before_the_exchange_launch(db_session, tmp_path):
    definition_archive = _write_dbn_archive(
        tmp_path / "definition.zip",
        job_id="prelaunch-definition",
        schema_name="definition",
        records=[_definition(instrument_id=101, raw_symbol="MNQM4")],
    )
    ohlcv_archive = _write_dbn_archive(
        tmp_path / "prelaunch.zip",
        job_id="prelaunch-ohlcv",
        schema_name="ohlcv-1m",
        records=[_ohlcv(MNQ_HISTORY_START_UTC - timedelta(minutes=1))],
    )

    with pytest.raises(DatabentoIngestionError, match="mnq_prelaunch_record_rejected"):
        import_databento_archives(db_session, [ohlcv_archive, definition_archive])

    assert db_session.query(DatabentoOhlcv1m).count() == 0


def test_volume_roll_schedule_is_prefix_invariant_uses_prior_session_and_keeps_ties():
    contracts = [
        RolloverContract(
            instrument_id=101,
            raw_symbol="MNQM4",
            activation=datetime(2024, 1, 1, tzinfo=timezone.utc),
            expiration=datetime(2024, 6, 21, 13, 30, tzinfo=timezone.utc),
        ),
        RolloverContract(
            instrument_id=102,
            raw_symbol="MNQU4",
            activation=datetime(2024, 1, 1, tzinfo=timezone.utc),
            expiration=datetime(2024, 9, 20, 13, 30, tzinfo=timezone.utc),
        ),
    ]
    monday = date(2024, 3, 4)
    volumes = {
        monday: {101: 100, 102: 90},
        monday + timedelta(days=1): {101: 100, 102: 100},
        monday + timedelta(days=2): {101: 90, 102: 120},
        monday + timedelta(days=3): {101: 1_000, 102: 1},
    }
    options = {
        "root_symbol": "MNQ",
        "dataset": DATASET,
        "contracts": contracts,
    }

    prefix = build_volume_roll_schedule(**options, daily_volumes=volumes)
    extended = build_volume_roll_schedule(
        **options,
        daily_volumes={
            **volumes,
            monday + timedelta(days=4): {101: 999_999, 102: 0},
        },
    )

    assert extended[: len(prefix)] == prefix
    assert [decision.instrument_id for decision in prefix] == [101, 101, 101, 102]
    assert prefix[2].decision_session_date == monday + timedelta(days=1)
    assert (prefix[2].current_volume, prefix[2].candidate_volume) == (100, 100)
    assert prefix[2].reason == "kept_current_contract"
    assert prefix[3].decision_session_date == monday + timedelta(days=2)
    assert (prefix[3].current_volume, prefix[3].candidate_volume) == (90, 120)


def test_resampling_uses_session_anchored_buckets_and_preserves_provenance():
    session_open = datetime(2024, 1, 7, 23, 0, tzinfo=timezone.utc)
    rows = [
        _RawContinuousBar(
            timestamp=session_open,
            instrument_id=101,
            raw_symbol="MNQM4",
            open_nano=100_000_000_000,
            high_nano=102_000_000_000,
            low_nano=99_000_000_000,
            close_nano=101_000_000_000,
            volume=4,
            source_file_sha256=HASH_A,
        ),
        _RawContinuousBar(
            timestamp=session_open + timedelta(minutes=89),
            instrument_id=101,
            raw_symbol="MNQM4",
            open_nano=101_000_000_000,
            high_nano=104_000_000_000,
            low_nano=100_000_000_000,
            close_nano=103_000_000_000,
            volume=6,
            source_file_sha256=HASH_B,
        ),
        _RawContinuousBar(
            timestamp=session_open + timedelta(minutes=90),
            instrument_id=101,
            raw_symbol="MNQM4",
            open_nano=103_000_000_000,
            high_nano=105_000_000_000,
            low_nano=102_000_000_000,
            close_nano=104_000_000_000,
            volume=8,
            source_file_sha256=HASH_B,
        ),
    ]

    candles = list(
        resample_databento_bars(
            rows,
            user_id=OWNER_ID,
            contract_id=CONTRACT_ID,
            root_symbol="MNQ",
            unit="minute",
            unit_number=90,
            closed_by=session_open + timedelta(minutes=105),
        )
    )

    assert len(candles) == 1
    candle = candles[0]
    assert candle.candle_timestamp == session_open
    assert candle.nominal_close_time == session_open + timedelta(minutes=90)
    assert (candle.open_price, candle.high_price, candle.low_price, candle.close_price) == (
        100.0,
        104.0,
        99.0,
        103.0,
    )
    assert candle.volume == 10
    assert candle.source == "databento"
    assert candle.source_instrument_id == 101
    assert candle.source_raw_symbol == "MNQM4"
    assert candle.source_file_sha256 == "multiple"
    assert candle.roll_policy_version == ROLL_POLICY_VERSION


def test_replay_loader_enforces_its_in_memory_row_ceiling(db_session):
    start = datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    db_session.add(_instrument())
    db_session.add_all(_bar(start + timedelta(minutes=index), index=index) for index in range(3))
    db_session.add(_schedule(trading_day_date(start)))
    db_session.commit()

    with pytest.raises(
        DatabentoMarketDataError,
        match="databento_replay_memory_budget_exceeded",
    ):
        load_databento_replay_candles(
            db_session,
            max_rows=1,
            user_id=OWNER_ID,
            contract_id=CONTRACT_ID,
            root_symbol="MNQ",
            unit="minute",
            unit_number=1,
            start=start,
            end=start + timedelta(minutes=3),
            closed_by=start + timedelta(minutes=3),
        )


def test_retained_sqlite_market_tables_do_not_override_the_canonical_cache(
    db_session,
    monkeypatch,
):
    start = datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    db_session.add(_instrument())
    db_session.add(_bar(start))
    db_session.add(_schedule(trading_day_date(start)))
    db_session.commit()
    monkeypatch.setattr(
        backtesting_module,
        "ALLOW_LEGACY_DATABENTO_SQLITE_FIXTURES",
        False,
    )

    assert (
        backtesting_module._database_databento_fixture_bounds(
            db_session,
            root_symbol="MNQ",
        )
        is None
    )


def test_create_bot_backtest_is_deterministic_and_never_reads_projectx(
    db_session,
    monkeypatch,
):
    source_start = datetime(2024, 3, 4, 13, 30, tzinfo=timezone.utc)
    db_session.add(_instrument())
    db_session.add_all(
        _bar(source_start + timedelta(minutes=index), index=index)
        for index in range(60)
    )
    db_session.add(_schedule(trading_day_date(source_start)))
    db_session.add(InstrumentMetadata(symbol="MNQ", tick_size=0.25, tick_value=0.50))
    config = BotConfig(
        user_id=OWNER_ID,
        account_id=9001,
        name="Databento deterministic replay",
        provider="projectx",
        enabled=False,
        execution_mode="live",
        strategy_type="sma_cross",
        strategy_params={},
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=1,
        slow_period=2,
        order_size=1,
        max_contracts=10,
        max_daily_loss=100_000,
        max_trades_per_day=100,
        max_open_position=10,
        allowed_contracts=[CONTRACT_ID],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3_600,
        allow_market_depth=False,
    )
    db_session.add(config)
    db_session.commit()

    def forbidden_projectx_path(*_args, **_kwargs):
        raise AssertionError("Databento backtest attempted to use ProjectX history")

    monkeypatch.setattr(
        backtesting_module,
        "_create_legacy_projectx_bot_backtest",
        forbidden_projectx_path,
    )
    monkeypatch.setattr(
        backtesting_module,
        "_projected_candle_query",
        forbidden_projectx_path,
    )
    payload = BotBacktestIn(
        start=datetime(2024, 3, 4, 14, 0, tzinfo=timezone.utc),
        end=datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc),
        starting_balance=25_000,
        commission_per_contract=1.25,
        slippage_ticks=1,
        force_close_at_end=True,
    )

    first = backtesting_module.create_bot_backtest(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=int(config.id),
        payload=payload,
        client=object(),
        now=datetime(2024, 3, 4, 15, 0, tzinfo=timezone.utc),
    )
    second = backtesting_module.create_bot_backtest(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=int(config.id),
        payload=payload,
        client=object(),
        now=datetime(2024, 3, 4, 15, 0, tzinfo=timezone.utc),
    )

    assert first.engine_version == backtesting_module.BACKTEST_ENGINE_VERSION
    assert first.input_fingerprint == second.input_fingerprint
    assert first.result_snapshot == second.result_snapshot
    assert first.bar_count == second.bar_count == 6
    assert first.assumptions_snapshot["historical_source"] == "databento"
    assert first.assumptions_snapshot["roll_policy_version"] == ROLL_POLICY_VERSION
    assert any(
        "ProjectX market history was not read" in warning
        for warning in first.result_snapshot["warnings"]
    )
    assert db_session.query(BotBacktest).count() == 2


def test_create_bot_backtest_uses_only_local_cache_and_persists_without_market_tables(
    tmp_path,
    monkeypatch,
):
    source_start = datetime(2024, 3, 4, 13, 30, tzinfo=timezone.utc)
    definition_archive = _write_dbn_archive(
        tmp_path / "definition.zip",
        job_id="local-cache-definition",
        schema_name="definition",
        records=[_definition(instrument_id=101, raw_symbol="MNQM4")],
    )
    ohlcv_archive = _write_dbn_archive(
        tmp_path / "ohlcv.zip",
        job_id="local-cache-ohlcv",
        schema_name="ohlcv-1m",
        records=[
            _ohlcv(
                source_start + timedelta(minutes=index),
                price_nano=18_000_000_000_000 + index * 250_000_000,
                volume=10 + index,
            )
            for index in range(60)
        ],
    )
    cache_root = tmp_path / "cache"
    build_databento_cache(
        [definition_archive, ohlcv_archive],
        cache_root=cache_root,
        timeframes=["5m"],
    )
    replay_store = DatabentoReplayStore(
        cache_root,
        max_entries=2,
        max_bytes=8 * 1024 * 1024,
        build_missing_timeframes=False,
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    persistence_tables = [
        InstrumentMetadata.__table__,
        BotConfig.__table__,
        BotBacktest.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=persistence_tables)
    assert set(inspect(engine).get_table_names()) == {
        "instrument_metadata",
        "bot_configs",
        "bot_backtests",
    }

    session = sessionmaker(bind=engine)()
    market_selects: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record_market_selects(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        normalized = " ".join(str(statement).upper().split())
        if normalized.startswith(("SELECT", "WITH")) and (
            "DATABENTO_" in normalized
            or "PROJECTX_MARKET_CANDLES" in normalized
        ):
            market_selects.append(str(statement))

    def forbidden_market_path(*_args, **_kwargs):
        raise AssertionError("backtest attempted to use a SQL or ProjectX market loader")

    class ForbiddenProjectXClient:
        def __getattr__(self, _name):
            raise AssertionError("backtest attempted to call ProjectX")

    monkeypatch.setattr(
        backtesting_module,
        "get_default_databento_cache",
        lambda: replay_store,
    )
    monkeypatch.setattr(
        replay_store,
        "load_candles",
        forbidden_market_path,
    )
    monkeypatch.setattr(
        backtesting_module,
        "ALLOW_LEGACY_DATABENTO_SQLITE_FIXTURES",
        False,
    )
    for name in (
        "databento_history_bounds",
        "load_databento_replay_candles",
        "_projected_candle_query",
        "_create_legacy_projectx_bot_backtest",
        "prepare_bot_backtest_data",
    ):
        monkeypatch.setattr(backtesting_module, name, forbidden_market_path)
    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "fetch_and_store_market_candles",
        forbidden_market_path,
    )

    try:
        session.add(
            InstrumentMetadata(symbol="MNQ", tick_size=0.25, tick_value=0.50)
        )
        config = BotConfig(
            user_id=OWNER_ID,
            account_id=9001,
            name="Local cache only replay",
            provider="projectx",
            enabled=False,
            execution_mode="live",
            strategy_type="sma_cross",
            strategy_params={},
            contract_id=CONTRACT_ID,
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=1,
            slow_period=2,
            order_size=1,
            max_contracts=10,
            max_daily_loss=100_000,
            max_trades_per_day=100,
            max_open_position=10,
            allowed_contracts=[CONTRACT_ID],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3_600,
            allow_market_depth=False,
        )
        session.add(config)
        session.commit()
        payload = BotBacktestIn(
            starting_balance=25_000,
            commission_per_contract=1.25,
            slippage_ticks=1,
            force_close_at_end=True,
        )
        captured_now = source_start + timedelta(hours=2)

        first = backtesting_module.create_bot_backtest(
            session,
            user_id=OWNER_ID,
            bot_config_id=int(config.id),
            payload=payload,
            client=ForbiddenProjectXClient(),
            now=captured_now,
        )
        first_fingerprint = first.input_fingerprint
        first_result = dict(first.result_snapshot)
        session.commit()
        monkeypatch.setattr(
            backtesting_module,
            "run_backtest",
            forbidden_market_path,
        )

        second = backtesting_module.create_bot_backtest(
            session,
            user_id=OWNER_ID,
            bot_config_id=int(config.id),
            payload=payload,
            client=ForbiddenProjectXClient(),
            now=captured_now,
        )
        session.commit()

        assert market_selects == []
        assert session.query(BotBacktest).count() == 2
        assert first_fingerprint == second.input_fingerprint
        assert first_result == second.result_snapshot
        # The first two five-minute bars are intentionally deferred so the
        # 1/2 SMA evaluator has its required three closed bars.
        assert first_result["range"]["bar_count"] == 10
        assert first_result["assumptions"]["historical_source"] == (
            "databento_local_cache"
        )
        # Lazy views reuse the one mapped series; exact reruns are served by
        # the backtest-result LRU rather than the eager candle-slice LRU.
        assert replay_store.stats()["mapped_series"] == 1
    finally:
        replay_store.clear()
        session.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(persistence_tables)))
        engine.dispose()
