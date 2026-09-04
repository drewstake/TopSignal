import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.db as db


def _patch_schema_init_steps(monkeypatch, calls):
    monkeypatch.setattr(db.Base.metadata, "create_all", lambda **_: calls.append("create_all"))
    monkeypatch.setattr(db, "_ensure_accounts_schema_compatibility", lambda: calls.append("accounts"))
    monkeypatch.setattr(db, "_ensure_journal_schema_compatibility", lambda: calls.append("journal"))
    monkeypatch.setattr(db, "_ensure_multi_tenant_schema_compatibility", lambda: calls.append("multi_tenant"))
    monkeypatch.setattr(db, "_ensure_bot_schema_compatibility", lambda: calls.append("bot"))
    monkeypatch.setattr(db, "_ensure_query_performance_indexes", lambda: calls.append("performance"))
    monkeypatch.setattr(db, "_ensure_default_instrument_metadata", lambda: calls.append("instruments"))


def test_init_db_skips_schema_init_when_disabled(monkeypatch):
    calls = []
    _patch_schema_init_steps(monkeypatch, calls)
    monkeypatch.setenv("TOPSIGNAL_DB_SCHEMA_INIT", "skip")

    db.init_db()

    assert calls == []


def test_init_db_defaults_to_skip(monkeypatch):
    calls = []
    _patch_schema_init_steps(monkeypatch, calls)
    monkeypatch.delenv("TOPSIGNAL_DB_SCHEMA_INIT", raising=False)

    db.init_db()

    assert calls == []


def test_init_db_rejects_unknown_mode(monkeypatch):
    calls = []
    _patch_schema_init_steps(monkeypatch, calls)
    monkeypatch.setenv("TOPSIGNAL_DB_SCHEMA_INIT", "maybe")

    import pytest

    with pytest.raises(RuntimeError, match="expected full or skip"):
        db.init_db()

    assert calls == []


def test_init_db_force_runs_schema_init_when_disabled(monkeypatch):
    calls = []
    _patch_schema_init_steps(monkeypatch, calls)
    monkeypatch.setenv("TOPSIGNAL_DB_SCHEMA_INIT", "skip")

    db.init_db(force=True)

    assert calls == ["create_all", "accounts", "journal", "multi_tenant", "bot", "performance", "instruments"]


def test_init_db_never_creates_legacy_databento_market_tables(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        db.Base.metadata,
        "create_all",
        lambda **kwargs: captured.update(kwargs),
    )
    for name in (
        "_ensure_accounts_schema_compatibility",
        "_ensure_journal_schema_compatibility",
        "_ensure_multi_tenant_schema_compatibility",
        "_ensure_bot_schema_compatibility",
        "_ensure_query_performance_indexes",
        "_ensure_default_instrument_metadata",
    ):
        monkeypatch.setattr(db, name, lambda: None)

    db.init_db(force=True)

    created = {table.name for table in captured["tables"]}
    assert created.isdisjoint(db.LEGACY_DATABENTO_TABLE_NAMES)
    assert "bot_backtests" in created
    assert "expense_suppressions" in created
