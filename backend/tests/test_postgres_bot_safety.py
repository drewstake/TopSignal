"""Real-PostgreSQL acceptance checks; use a migrated disposable audit database."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app import bot_worker
from app.bot_worker import BotWorkerRuntime, BotWorkerSettings
from app.db import SessionLocal, _build_engine_options


TEST_URL = os.getenv("TOPSIGNAL_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="Disposable PostgreSQL required for worker fencing and timeout acceptance")


@pytest.fixture
def postgres_sessions():
    url = make_url(TEST_URL)
    if not (url.database or "").startswith("topsignal_audit_"):
        pytest.fail("Use a disposable migrated database named topsignal_audit_*; never a trading database")
    engine = create_engine(TEST_URL, **_build_engine_options(TEST_URL))
    try:
        yield lambda: SessionLocal(bind=engine)
    finally:
        engine.dispose()


def test_postgres_application_transactions_bound_waits_and_recover(postgres_sessions):
    with postgres_sessions() as db:
        for setting, expected in [("statement_timeout", "30s"), ("lock_timeout", "5s"), ("idle_in_transaction_session_timeout", "1min")]:
            assert db.execute(text("select current_setting(:setting)"), {"setting": setting}).scalar_one() == expected
        db.execute(text("SET LOCAL statement_timeout = '50ms'"))
        with pytest.raises(DBAPIError):
            db.execute(text("select pg_sleep(0.2)"))
        db.rollback()
        assert db.execute(text("select 1")).scalar_one() == 1


def test_postgres_two_workers_exclude_each_other_and_fence_expired_owner(postgres_sessions, monkeypatch):
    lease_name = "offline-audit-" + uuid4().hex
    monkeypatch.setattr(bot_worker, "_LEASE_NAME", lease_name)
    settings = BotWorkerSettings(enabled=True)
    workers = [BotWorkerRuntime(session_factory=postgres_sessions, client_factory=lambda **_: pytest.fail("No provider calls"), settings=settings) for _ in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            won = list(pool.map(lambda worker: worker._acquire_or_renew_lease(), workers))
        assert sum(won) == 1
        owner = workers[won.index(True)]
        standby = workers[won.index(False)]
        with postgres_sessions() as db:
            db.execute(text("update bot_runtime_leases set expires_at = current_timestamp - interval '1 second' where lease_name = :name"), {"name": lease_name})
            db.commit()
        assert standby._acquire_or_renew_lease() is True
        assert owner._acquire_or_renew_lease() is False
    finally:
        with postgres_sessions() as db:
            db.execute(text("delete from bot_runtime_leases where lease_name = :name"), {"name": lease_name})
            db.commit()
