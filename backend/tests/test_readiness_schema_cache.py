from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import os

import pytest
from sqlalchemy import create_engine

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main
from app.services.readiness_schema import ReadinessSchemaCache
from test_readiness import _Inspector, _Session


@pytest.fixture
def engine():
    engine = create_engine("postgresql+psycopg://unused:unused@localhost/unused")
    yield engine  # No database connection is opened.
    engine.dispose()


def test_cache_expires_and_failed_inspection_retries(engine):
    clock = [0]
    cache = ReadinessSchemaCache(clock=lambda: clock[0])
    calls = []
    def validate():
        calls.append(1)
        return {"accounts"}
    assert cache.get(engine, validate) == {"accounts"}
    clock[0] = 299
    assert cache.get(engine, validate) == {"accounts"}
    assert len(calls) == 1
    clock[0] = 300
    def broken():
        raise RuntimeError("schema changed")
    with pytest.raises(RuntimeError):
        cache.get(engine, broken)
    assert cache.get(engine, validate) == {"accounts"}
    assert len(calls) == 2


def test_concurrent_readiness_only_inspects_schema_once(engine):
    cache = ReadinessSchemaCache()
    started, release = Event(), Event()
    calls = []
    def validate():
        calls.append(1)
        started.set()
        assert release.wait(5)
        return {"accounts"}
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = [pool.submit(cache.get, engine, validate) for _ in range(6)]
        assert started.wait(5)
        release.set()
        assert all(task.result() == {"accounts"} for task in tasks)
    assert len(calls) == 1


def test_cache_isolated_by_database_engine(engine):
    second = create_engine("postgresql+psycopg://unused:unused@localhost/other")
    try:
        cache = ReadinessSchemaCache()
        assert cache.get(engine, lambda: {"one"}) == {"one"}
        assert cache.get(second, lambda: {"two"}) == {"two"}
    finally:
        second.dispose()


def test_warm_schema_does_not_cache_connectivity_migrations_or_runtime(engine, monkeypatch):
    cache = ReadinessSchemaCache()
    monkeypatch.setattr(main, "schema_cache", cache)
    db = _Session()
    monkeypatch.setattr(db, "get_bind", lambda: engine)
    inspections = []
    def inspect(_):
        inspections.append(1)
        return _Inspector()
    monkeypatch.setattr(main, "inspect", inspect)
    monkeypatch.setattr(main, "_offline_workspace_has_disabled_automation", lambda db: False)
    monkeypatch.setattr(main, "_bot_worker_runtime", object())
    runtime = SimpleNamespace(ready=True, failed_checks=[])
    monkeypatch.setattr(main, "inspect_bot_runtime", lambda *a, **kw: runtime)
    assert main._readiness(db) == {"status": "ready"}
    assert main._readiness(db) == {"status": "ready"}
    assert len(inspections) == 1
    runtime.ready = False
    runtime.failed_checks = ["account_classification_fresh"]
    assert b"account_classification_fresh" in main._readiness(db).body
    runtime.ready = True
    db.migration_applied = False
    assert b"schema_outdated" in main._readiness(db).body
    db.migration_applied = True
    db.fail = True
    assert b"database_unavailable" in main._readiness(db).body
    db.fail = False
    assert main._readiness(db) == {"status": "ready"}
    assert len(inspections) == 2
