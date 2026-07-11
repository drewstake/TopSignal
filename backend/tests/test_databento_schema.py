from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import (
    DatabentoImportBatch,
    DatabentoImportFile,
    DatabentoInstrument,
    DatabentoOhlcv1m,
    DatabentoRollSchedule,
)


_HASH_A = "a" * 64
_HASH_B = "b" * 64
_DATASET = "GLBX.MDP3"
_TABLES = [
    DatabentoImportBatch.__table__,
    DatabentoImportFile.__table__,
    DatabentoInstrument.__table__,
    DatabentoOhlcv1m.__table__,
    DatabentoRollSchedule.__table__,
]


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("pragma foreign_keys = on")

    Base.metadata.create_all(bind=engine, tables=_TABLES)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(_TABLES)))
        engine.dispose()


def _instrument(instrument_id: int, raw_symbol: str) -> DatabentoInstrument:
    return DatabentoInstrument(
        dataset=_DATASET,
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        root_symbol="MNQ",
        instrument_class="F",
        security_type="FUT",
        activation=datetime(2024, 1, 1, tzinfo=timezone.utc),
        expiration=datetime(2024, 7, 1, tzinfo=timezone.utc),
        min_price_increment_nano=250_000_000,
        unit_of_measure_qty_nano=1_000_000_000,
        definition_ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
        source_file_sha256=_HASH_A,
    )


def test_import_identity_constraints_make_retries_idempotent(db_session):
    batch = DatabentoImportBatch(
        job_id="job-a",
        archive_name="job-a.zip",
        archive_sha256=_HASH_A,
        dataset=_DATASET,
        schema_name="ohlcv-1m",
        root_symbol="MNQ",
    )
    db_session.add(batch)
    db_session.commit()

    db_session.add(
        DatabentoImportBatch(
            job_id="job-b",
            archive_name="same-content.zip",
            archive_sha256=_HASH_A,
            dataset=_DATASET,
            schema_name="ohlcv-1m",
            root_symbol="MNQ",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(
        DatabentoImportFile(
            batch_id=batch.id,
            filename="part-000.dbn.zst",
            file_sha256=_HASH_B,
            schema_name="ohlcv-1m",
        )
    )
    db_session.commit()
    db_session.add(
        DatabentoImportFile(
            batch_id=batch.id,
            filename="part-000.dbn.zst",
            file_sha256=_HASH_B,
            schema_name="ohlcv-1m",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_ohlcv_natural_key_and_price_envelope_are_enforced(db_session):
    db_session.add(_instrument(101, "MNQM4"))
    db_session.commit()
    timestamp = datetime(2024, 3, 1, 14, 30, tzinfo=timezone.utc)
    values = {
        "dataset": _DATASET,
        "instrument_id": 101,
        "ts_event": timestamp,
        "trading_date": date(2024, 3, 1),
        "open_nano": 18_000_000_000_000,
        "high_nano": 18_010_000_000_000,
        "low_nano": 17_990_000_000_000,
        "close_nano": 18_005_000_000_000,
        "volume": 42,
        "source_file_sha256": _HASH_B,
    }
    db_session.add(DatabentoOhlcv1m(**values))
    db_session.commit()

    db_session.add(DatabentoOhlcv1m(**values))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    invalid = dict(values)
    invalid["ts_event"] = datetime(2024, 3, 1, 14, 31, tzinfo=timezone.utc)
    invalid["open_nano"] = invalid["high_nano"] + 1
    db_session.add(DatabentoOhlcv1m(**invalid))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_instrument_lifecycle_accepts_zero_duration_spreads_but_not_inverted_ranges(
    db_session,
):
    activation = datetime(2024, 3, 15, 13, 30, tzinfo=timezone.utc)
    spread = _instrument(201, "MNQH4-MNQM4")
    spread.instrument_class = "S"
    spread.activation = activation
    spread.expiration = activation
    spread.unit_of_measure_qty_nano = None
    db_session.add(spread)
    db_session.commit()

    inverted = _instrument(202, "MNQU4-MNQZ4")
    inverted.instrument_class = "S"
    inverted.activation = activation
    inverted.expiration = datetime(2024, 3, 14, 13, 30, tzinfo=timezone.utc)
    db_session.add(inverted)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_roll_schedule_rejects_same_session_volume_lookahead(db_session):
    db_session.add_all([_instrument(101, "MNQM4"), _instrument(102, "MNQU4")])
    db_session.commit()
    db_session.add(
        DatabentoRollSchedule(
            root_symbol="MNQ",
            trading_date=date(2024, 3, 4),
            dataset=_DATASET,
            instrument_id=102,
            raw_symbol="MNQU4",
            decision_session_date=date(2024, 3, 4),
            from_instrument_id=101,
            current_volume=1_000,
            candidate_volume=1_001,
            reason="candidate_volume_exceeded_current",
            policy_version="prior-session-volume-v1",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
