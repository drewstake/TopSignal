import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine, inspect, text

import app.db as db


def test_local_candle_upgrade_preserves_rows_and_is_idempotent(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.sqlite3'}")
    monkeypatch.setattr(db, "engine", engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE projectx_market_candles (id INTEGER PRIMARY KEY, close_price NUMERIC)"))
            conn.execute(text("INSERT INTO projectx_market_candles VALUES (1, 123.25)"))

        db._ensure_local_market_candle_schema_compatibility()
        with engine.begin() as conn:
            assert conn.execute(text(
                "SELECT id, close_price, first_fetched_at, revision_hash FROM projectx_market_candles"
            )).one() == (1, 123.25, None, None)
            conn.execute(text("UPDATE projectx_market_candles SET revision_hash = 'existing-hash' WHERE id = 1"))

        db._ensure_local_market_candle_schema_compatibility()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT revision_hash FROM projectx_market_candles")).scalar_one() == "existing-hash"
            columns = {column["name"]: column for column in inspect(conn).get_columns("projectx_market_candles")}
            assert columns["first_fetched_at"]["nullable"]
            assert columns["revision_hash"]["nullable"]
    finally:
        engine.dispose()


def test_local_candle_upgrade_does_not_create_historical_tables(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(db, "engine", engine)
    try:
        db._ensure_local_market_candle_schema_compatibility()
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_local_candle_upgrade_never_opens_cloud_database(monkeypatch):
    # No begin method: attempting any database work would fail this test.
    monkeypatch.setattr(db, "engine", SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    db._ensure_local_market_candle_schema_compatibility()
