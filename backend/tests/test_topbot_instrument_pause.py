from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import pytest

from app.models import BotOrderAttempt
from app.services import bot_service, topbot_instrument_pause as pause, topbot_strategy
from app.services.projectx_client import (
    PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE, PROJECTX_ERROR_PROVIDER_RESPONSE,
    ProjectXClient, ProjectXClientError,
)
from app.services.topbot_depth import feed
from test_bot_execution_safety import (
    USER_A, CONTRACT_ID, RecordingClient, _add_account_and_config, db_session, open_exchange_session,
)
from test_topbot_dom import observation
from test_topbot_time_exit import lease

MESSAGE = "Trading is currently unavailable. The instrument is not in an active trading status."


@pytest.mark.parametrize("path,code,message,expected", [
    ("/api/Order/place", 2, MESSAGE, True),
    ("/api/Order/place", 5, None, True),
    ("/api/Order/place", 2, "Brackets cannot be used with Position Brackets.", False),
    ("/api/Order/place", 6, MESSAGE, False),
    ("/api/Order/place", 7, MESSAGE, False),
    ("/api/Order/place", 9, MESSAGE, False),
    ("/api/Order/place", "5", MESSAGE, False),
    ("/api/Order/place", None, MESSAGE, False),
    ("/api/Order/cancel", 5, MESSAGE, False),
    ("/api/Position/closeContract", 2, MESSAGE, False),
])
def test_only_explicit_temporary_entry_rejections_are_recoverable(monkeypatch, path, code, message, expected):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_):
            return json.dumps(dict(success=False, errorCode=code, errorMessage=message, orderId=123)).encode()
    monkeypatch.setattr("app.services.projectx_client._open_projectx_request", lambda *a, **k: Response())
    client = ProjectXClient(base_url="https://example.test", username="fixture", api_key="fixture")
    with pytest.raises(ProjectXClientError) as caught:
        client._request_once("POST", path, payload={}, with_auth=False)
    assert (caught.value.reason_code == PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE) is expected
    assert not caught.value.submission_outcome_unknown


@pytest.fixture
def active_bot(db_session, monkeypatch):
    for key in ("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "TOPSIGNAL_BOT_WORKER_ENABLED", "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr("app.services.market_context_bundle.build_collected_context", lambda **kw: {})
    clock = SimpleNamespace(now=datetime.now(timezone.utc))
    class ClockMeta(type):
        def __instancecheck__(cls, value): return isinstance(value, datetime)
    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None): return clock.now
    monkeypatch.setattr(bot_service, "datetime", Clock)
    monkeypatch.setattr("app.services.projectx_accounts.datetime", Clock)
    monkeypatch.setattr(feed, "ensure", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.topbot_session.futures_session_is_open", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.topbot_session.flat_deadline", lambda *a, **kw: clock.now + timedelta(hours=1))
    depth = observation(clock.now, -.2)
    monkeypatch.setattr(feed, "snapshot", lambda *a, **kw: depth)
    account, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    account.provider_classification_observed_at = clock.now
    config.strategy_type = "topbot_adaptive"
    config.strategy_params = dict(topbot_strategy.DEFAULTS)
    client = RecordingClient(order_error=ProjectXClientError(
        MESSAGE, reason_code=PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE))
    db_session.commit()
    started = bot_service.start_bot_run(db_session, user_id=USER_A, bot_config_id=config.id, client=client,
        dry_run=False, confirm_live_order_routing=True, continuous=True)
    def evaluate():
        token = lease(db_session)
        # Keep fixture lease valid with the injected submission clock.
        from app.models import BotRuntimeLease
        db_session.query(BotRuntimeLease).one().expires_at = clock.now + timedelta(minutes=10)
        return bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account,
            client=client, run=started.run, dry_run=False, confirm_live_order_routing=True, worker_lease_token=token)
    def settle(attempt):
        from app.services.topbot_time_exit import process_time_exit
        assert process_time_exit(db_session, entry_id=attempt.id, client=client,
                                 worker_lease_token=lease(db_session), now=clock.now)
        assert attempt.raw_request["timeExit"]["outcome"] == "entry_not_submitted"
        assert not client.cancel_order_calls and not client.close_position_calls
        db_session.commit()
    return SimpleNamespace(db=db_session, config=config, account=account, run=started.run,
                           client=client, evaluate=evaluate, settle=settle, clock=clock, depth=depth)


def test_rejection_pauses_then_fresh_signal_can_submit_without_rearming(active_bot):
    b = active_bot
    first = b.evaluate()
    assert first.order_attempt.status == "error", first.order_attempt.rejection_reason
    assert b.config.enabled and b.run.status == "running"
    b.db.commit()
    b.db.expire_all()  # Recovery depends on committed state, not process memory.
    assert b.run.raw_state["phase"] == "instrument_wait"
    assert pause.snapshot(b.run)["order_attempt_id"] == first.order_attempt.id
    b.settle(first.order_attempt)  # The worker settles exit plans before new entries.
    b.client.order_error = None
    b.clock.now += timedelta(seconds=59)
    b.depth.update(observation(b.clock.now, -.2))
    held = b.evaluate()
    assert held.order_attempt is None and len(b.client.place_order_calls) == 1
    b.clock.now += timedelta(seconds=2)
    # A still-running old episode must never resubmit the rejected order.
    b.depth.update(observation(b.clock.now, -.2), episode_at=first.decision.candle_timestamp.isoformat())
    assert b.evaluate().order_attempt is None
    assert len(b.client.place_order_calls) == 1
    b.depth.update(observation(b.clock.now, -.2))
    submitted = b.evaluate()
    assert submitted.order_attempt is not None, (submitted.status, submitted.decision.reason,
        submitted.decision.raw_payload.get("hold_reason"), [(e.code, e.message) for e in submitted.risk_events])
    assert submitted.order_attempt.status == "submitted", submitted.order_attempt.rejection_reason
    assert submitted.order_attempt.id != first.order_attempt.id
    assert submitted.order_attempt.idempotency_key != first.order_attempt.idempotency_key
    assert pause.snapshot(b.run) is None
    assert b.run.status == "running" and b.config.enabled
    assert b.db.query(BotOrderAttempt).count() == 2


@pytest.mark.parametrize("kind", ["ambiguous", "unrelated", "manual_stop", "account_locked", "session_closed"])
def test_recovery_does_not_override_safety_controls(active_bot, monkeypatch, kind):
    b = active_bot
    if kind in {"ambiguous", "unrelated"}:
        b.client.order_error = ProjectXClientError(MESSAGE,
            submission_outcome_unknown=kind == "ambiguous",
            reason_code=PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE if kind == "ambiguous" else PROJECTX_ERROR_PROVIDER_RESPONSE)
        result = b.evaluate()
        assert not b.config.enabled and b.run.status == "error"
        assert pause.snapshot(b.run) is None
        assert result.order_attempt.status == ("submission_unknown" if kind == "ambiguous" else "error")
        return
    rejected = b.evaluate()
    b.settle(rejected.order_attempt)
    b.db.commit()
    b.clock.now += timedelta(seconds=90)
    b.depth.update(observation(b.clock.now, -.2))
    b.client.order_error = None
    if kind == "manual_stop":
        bot_service.stop_latest_bot_run(b.db, user_id=USER_A, bot_config_id=b.config.id)
        with pytest.raises(ValueError): b.evaluate()
        assert not b.config.enabled and b.run.status == "stopped"
    elif kind == "account_locked":
        b.client.account_can_trade = False
        blocked = b.evaluate()
        assert blocked.status == "risk_blocked"
        assert any("account" in e.code for e in blocked.risk_events)
    else:
        monkeypatch.setattr("app.services.topbot_session.futures_session_is_open", lambda *a, **kw: False)
        assert b.evaluate().order_attempt is None
    assert len(b.client.place_order_calls) == 1


def test_backoff_is_durable_and_capped():
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(raw_state={})
    attempt = SimpleNamespace(id=1, contract_id=CONTRACT_ID, status="error",
        raw_response={"reason_code": PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE})
    for seconds in (60, 120, 240, 300, 300):
        assert pause.pause_after_rejection(run, attempt, now=now)
        value = json.loads(json.dumps(run.raw_state))["instrument_pause"]
        assert datetime.fromisoformat(value["retry_at"]) - now == timedelta(seconds=seconds)
        assert pause.entry_pause_reason(value, contract_id=CONTRACT_ID, now=now)
        assert pause.entry_pause_reason(value, contract_id="different", now=now + timedelta(hours=1))
        now += timedelta(seconds=seconds)
