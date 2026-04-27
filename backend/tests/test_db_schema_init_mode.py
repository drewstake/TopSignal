import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.db as db


def _patch_schema_init_steps(monkeypatch, calls):
    monkeypatch.setattr(db.Base.metadata, "create_all", lambda **_: calls.append("create_all"))
    monkeypatch.setattr(db, "_ensure_accounts_schema_compatibility", lambda: calls.append("accounts"))
    monkeypatch.setattr(db, "_ensure_journal_schema_compatibility", lambda: calls.append("journal"))
    monkeypatch.setattr(db, "_ensure_multi_tenant_schema_compatibility", lambda: calls.append("multi_tenant"))
    monkeypatch.setattr(db, "_ensure_bot_schema_compatibility", lambda: calls.append("bot"))
    monkeypatch.setattr(db, "_ensure_default_instrument_metadata", lambda: calls.append("instruments"))


def test_init_db_skips_schema_init_when_disabled(monkeypatch):
    calls = []
    _patch_schema_init_steps(monkeypatch, calls)
    monkeypatch.setenv("TOPSIGNAL_DB_SCHEMA_INIT", "skip")

    db.init_db()

    assert calls == []


def test_init_db_force_runs_schema_init_when_disabled(monkeypatch):
    calls = []
    _patch_schema_init_steps(monkeypatch, calls)
    monkeypatch.setenv("TOPSIGNAL_DB_SCHEMA_INIT", "skip")

    db.init_db(force=True)

    assert calls == ["create_all", "accounts", "journal", "multi_tenant", "bot", "instruments"]
