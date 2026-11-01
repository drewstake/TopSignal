from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os

import pytest
from sqlalchemy import event

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app import bot_worker as worker
from app.models import Account, BotRun
from test_bot_worker import session_factory, _seed_run, _runtime, _settings


@pytest.mark.parametrize("rows,expected", [
    ([{"id":101,"simulated":True}], True),
    ([{"id":101,"simulated":False}], False),
    ([{"id":101,"simulated":"true"}], None),
    ([{"id":101}], None), ([], None),
    ([{"id":101,"simulated":True},{"id":101,"simulated":False}], None),
])
def test_probe_refresh_is_strict_scoped_and_never_rearms(session_factory, rows, expected):
    _seed_run(session_factory, dry_run=False)
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    with session_factory() as db:
        account = db.query(Account).one()
        account.provider_simulated = True
        account.provider_classification_observed_at = old
        db.add(Account(user_id="other",provider="projectx",external_id="101",name="Other",
                       provider_simulated=False, provider_classification_observed_at=old))
        db.add(Account(user_id="user-a",provider="projectx",external_id="999",name="Unarmed",
                       provider_simulated=False, provider_classification_observed_at=old))
        db.commit()
        state = dict(db.get(BotRun,1).raw_state)
    now = datetime.now(timezone.utc)
    _runtime(session_factory)._refresh_account_classifications("user-a", rows, now)
    with session_factory() as db:
        account = db.query(Account).filter_by(user_id="user-a",external_id="101").one()
        assert worker._fresh_account_classification(account, now=now) is expected
        if expected is None:
            assert account.provider_classification_observed_at is None
        assert db.query(Account).filter_by(user_id="other").one().provider_simulated is False
        assert db.query(Account).filter_by(external_id="999").one().provider_simulated is False
        assert db.get(BotRun,1).raw_state == state
    from app.services.topbot_audit import page
    assert page("user-a",101)["items"][0]["kind"] == "account_classification_committed"


def test_idle_worker_probe_refreshes_without_a_signal_or_extra_provider_call(session_factory, monkeypatch):
    _seed_run(session_factory, dry_run=False)
    monkeypatch.setattr(worker, "_evaluation_is_due", lambda *a, **kw: False)
    calls = []
    def list_accounts(**kw):
        calls.append(kw)
        return [{"id":101,"simulated":True}]
    runtime = worker.BotWorkerRuntime(session_factory=session_factory,
        client_factory=lambda *a, **kw: SimpleNamespace(list_accounts=list_accounts), settings=_settings())
    now = datetime.now(timezone.utc)
    probed = set()
    result = runtime._process_run(run_id=1, now=now, startup_recovery=False,
        blocked_accounts=set(), probe_provider=True, probed_users=probed)
    assert result == {"evaluated": False, "provider_ok": True}
    assert len(calls) == 1 and probed == {"user-a"}
    with session_factory() as db:
        assert worker._fresh_account_classification(db.query(Account).one(), now=now) is True


def test_failed_probe_does_not_refresh_old_classification(session_factory, monkeypatch):
    _seed_run(session_factory, dry_run=False)
    monkeypatch.setattr(worker, "_evaluation_is_due", lambda *a, **kw: False)
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    with session_factory() as db:
        account = db.query(Account).one()
        account.provider_simulated = True
        account.provider_classification_observed_at = old
        db.commit()
    def fail(**kw):
        raise RuntimeError("provider unavailable")
    runtime = worker.BotWorkerRuntime(session_factory=session_factory,
        client_factory=lambda *a, **kw: SimpleNamespace(list_accounts=fail), settings=_settings())
    with pytest.raises(RuntimeError):
        runtime._process_run(run_id=1, now=datetime.now(timezone.utc), startup_recovery=False,
            blocked_accounts=set(), probe_provider=True, probed_users=set())
    with session_factory() as db:
        assert worker._fresh_account_classification(db.query(Account).one(), now=datetime.now(timezone.utc)) is None


def test_status_queries_omit_unneeded_configuration_without_lazy_reads(session_factory):
    _seed_run(session_factory)
    runtime = _runtime(session_factory)
    statements = []
    with session_factory() as db:
        bind = db.get_bind()
        def capture(conn, cursor, statement, *args):
            statements.append(statement)
        event.listen(bind, "before_cursor_execute", capture)
        try:
            assert worker._active_armed_run_count(db) == 1
            worker.inspect_bot_runtime(db, runtime=runtime)
        finally:
            event.remove(bind, "before_cursor_execute", capture)
    assert not any("strategy_params" in s or "max_daily_loss" in s for s in statements)
    assert sum("count(bot_order_attempts.id)" in s for s in statements) == 1
