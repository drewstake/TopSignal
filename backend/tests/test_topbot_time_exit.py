from datetime import datetime, timedelta, timezone

import pytest

from app.models import BotRuntimeLease
from app.services import bot_service, topbot_mathematical
from app.services.topbot_time_exit import pending_exits, process_time_exit
from test_bot_execution_safety import (
    USER_A, CONTRACT_ID, RecordingClient, _add_account_and_config,
    _patch_actionable_signal, db_session, open_exchange_session,
)


@pytest.fixture
def live_entry(db_session, monkeypatch, open_exchange_session):
    for key in ("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "TOPSIGNAL_BOT_WORKER_ENABLED",
                "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr("app.services.topbot_session.entry_boundary_reason", lambda *a, **kw: None)
    account, config = _add_account_and_config(db_session, execution_mode="live", enabled=False)
    config.strategy_type = "topbot_adaptive"
    config.strategy_params = dict(topbot_mathematical.RULES)
    config.cooldown_seconds = 300
    db_session.commit()
    _patch_actionable_signal(monkeypatch, raw_payload={
        "signal_category": "entry", "target_position_qty": 1,
        "stop_loss": 97, "take_profit": 107, "entry_price": 101,
        "live_routing_allowed": True,
    })
    client = RecordingClient()
    result = bot_service.start_bot_run(
        db_session, user_id=USER_A, bot_config_id=config.id, client=client,
        dry_run=False, confirm_live_order_routing=True, continuous=True,
    )
    assert result.order_attempt is None
    assert result.decision.reason == "worker_lease_required"
    result = bot_service.evaluate_bot_config(
        db_session, user_id=USER_A, config=config, account=account, client=client,
        run=result.run, dry_run=False, confirm_live_order_routing=True,
        worker_lease_token=lease(db_session),
    )
    assert result.order_attempt is not None, [(r.code, r.message) for r in result.risk_events]
    assert result.order_attempt.status == "submitted", [r.code for r in result.risk_events]
    assert len(client.place_order_calls) == 1
    assert client.place_order_calls[0]["stop_loss_bracket"] == {"ticks": 16, "type": 4}
    assert client.place_order_calls[0]["take_profit_bracket"] == {"ticks": 24, "type": 1}
    db_session.commit()
    return result, client


def lease(db):
    now = datetime.now(timezone.utc)
    if db.query(BotRuntimeLease).filter_by(lease_name="test-time-exits").first() is None:
        db.add(BotRuntimeLease(lease_name="test-time-exits", owner_id="test-worker",
                              acquired_at=now, heartbeat_at=now, expires_at=now + timedelta(minutes=2)))
    db.commit()
    return bot_service.BotWorkerLeaseToken(
        lease_name="test-time-exits", owner_id="test-worker", lease_ttl_seconds=45,
        mutation_allowed=lambda: True,
    )


def due(entry):
    return datetime.fromisoformat(entry.raw_request["timeExit"]["deadline"])


def test_durable_exit_cancels_brackets_and_closes_even_after_run_stopped(db_session, live_entry):
    result, client = live_entry
    entry = result.order_attempt
    result.run.status = "stopped"
    result.config.enabled = False
    db_session.commit()
    token = lease(db_session)
    client.positions = [{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1}]
    client.open_orders = [protective_stop()]
    assert not process_time_exit(db_session, entry_id=entry.id, client=client,
                                 worker_lease_token=token, now=due(entry)-timedelta(seconds=1))
    assert client.close_position_calls == [] and client.cancel_order_calls == []
    assert process_time_exit(db_session, entry_id=entry.id, client=client,
                             worker_lease_token=token, now=due(entry))
    db_session.commit()
    assert len(client.close_position_calls) == len(client.cancel_order_calls) == 1
    assert pending_exits(db_session).count() == 0
    assert entry.raw_request["timeExit"]["outcome"] == "verified_flat"
    # Entry outcome remains an entry audit; exit audit is separate and durable.
    assert entry.provider_order_id == "provider-order-1"
    assert process_time_exit(db_session, entry_id=entry.id, client=client,
                             worker_lease_token=token, now=due(entry))
    assert len(client.close_position_calls) == 1


@pytest.mark.parametrize("fault", ["unknown_submission", "funded", "lost_lease", "unconfirmed_cancel"])
def test_exit_fails_closed_and_keeps_obligation(db_session, live_entry, fault):
    result, client = live_entry
    entry = result.order_attempt
    token = lease(db_session)
    client.positions = [{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1}]
    if fault == "unknown_submission": entry.status = "submission_unknown"
    if fault == "funded": client.account_simulated = False
    if fault == "lost_lease":
        token = bot_service.BotWorkerLeaseToken(lease_name="test-time-exits", owner_id="other", lease_ttl_seconds=45)
    if fault == "unconfirmed_cancel":
        client.open_orders = [{"account_id": 9001, "contract_id": CONTRACT_ID, "order_id": "bracket"}]
        client.cancel_order = lambda **kw: {"success": False}
    db_session.commit()
    assert not process_time_exit(db_session, entry_id=entry.id, client=client,
                                 worker_lease_token=token, now=due(entry))
    assert len(client.close_position_calls) == (1 if fault == "unconfirmed_cancel" else 0)
    assert pending_exits(db_session).count() == 1


def test_flat_position_is_noop_and_unrelated_contract_is_untouched(db_session, live_entry):
    result, client = live_entry
    entry = result.order_attempt
    client.positions = [{"account_id": 9001, "contract_id": "OTHER", "signed_size": 1}]
    assert process_time_exit(db_session, entry_id=entry.id, client=client,
                             worker_lease_token=lease(db_session), now=due(entry))
    assert client.close_position_calls == []
    assert len(client.positions) == 1


def test_pending_obligation_blocks_next_entry(db_session, live_entry, monkeypatch):
    result, client = live_entry
    _patch_actionable_signal(monkeypatch, candle_timestamp=datetime.now(timezone.utc), raw_payload={
        "signal_category": "entry", "target_position_qty": 1,
        "stop_loss": 97, "take_profit": 107, "entry_price": 101,
    })
    following = bot_service.evaluate_bot_config(
        db_session, user_id=USER_A, config=result.config, account=None, client=client,
        run=result.run, dry_run=False, confirm_live_order_routing=True,
        worker_lease_token=lease(db_session),
    )
    assert following.decision.action == "HOLD"
    assert following.decision.reason == "mathematical_exit_pending"
    assert following.run.status == "running" and following.config.enabled
    assert len(client.place_order_calls) == 1


def test_stopped_run_with_pending_exit_still_blocks_manual_tests(db_session, live_entry):
    from app.services.manual_order_tests import _require_idle
    result, _ = live_entry
    result.config.enabled = False
    result.run.status = "stopped"
    db_session.flush()
    with pytest.raises(ValueError, match="timed bot exit is pending"):
        _require_idle(db_session, USER_A, result.config.account_id)


def test_roll_stops_run_explicitly_and_keeps_original_exit_obligation(db_session, live_entry, monkeypatch):
    from app.models import BotRiskEvent
    result, client = live_entry
    original = bot_service.fetch_candles_and_evaluate_strategy
    def rolled(*args, **kwargs):
        candles, signal = original(*args, **kwargs)
        candles[-1].contract_id = "CON.F.US.MNQ.Z26"
        return candles, signal
    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", rolled)
    # The fixture uses M26. A newly resolved delivery must stop explicitly.
    following = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=result.config,
        account=None, client=client, run=result.run, dry_run=False, confirm_live_order_routing=True,
        worker_lease_token=lease(db_session))
    assert following.decision.action == "HOLD" and following.decision.reason == "contract_rolled"
    assert following.run.status == "blocked" and not following.config.enabled
    assert db_session.query(BotRiskEvent).filter_by(code="contract_rolled").count() == 1
    assert pending_exits(db_session).count() == 1 and len(client.place_order_calls) == 1


def test_worker_recovers_due_exit_without_rearming_or_fetching_candles(db_session, live_entry, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.bot_worker import BotWorkerRuntime, BotWorkerSettings

    result, client = live_entry
    entry = result.order_attempt
    request = dict(entry.raw_request)
    request["timeExit"] = {**request["timeExit"],
                           "deadline": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}
    entry.raw_request = request
    db_session.commit()
    client.positions = [{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1}]
    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", lambda *a, **kw: pytest.fail("no candles needed"))
    runtime = BotWorkerRuntime(
        session_factory=sessionmaker(bind=db_session.bind, expire_on_commit=False),
        client_factory=lambda *a, **kw: client, settings=BotWorkerSettings(enabled=True),
    )
    assert runtime._acquire_or_renew_lease()
    result_cycle = runtime._run_cycle(startup_recovery=True)
    db_session.expire_all()
    assert result_cycle["errors"] == 0
    assert result.run.status != "running"
    assert result.config.enabled is False
    assert len(client.close_position_calls) == 1
    assert len(client.place_order_calls) == 1
    assert pending_exits(db_session).count() == 0


def protective_stop():
    return {"account_id": 9001, "contract_id": CONTRACT_ID, "order_id": "bracket",
            "order_type": 4, "side": 1, "size": 1, "status": 1,
            "parent_order_id": "provider-order-1", "raw_payload": {"stopPrice": 97}}


def test_unknown_entry_escalates_once_without_resubmission(db_session, live_entry):
    from app.models import BotRiskEvent
    result, client = live_entry
    result.order_attempt.status = "submission_unknown"
    token = lease(db_session)
    for _ in range(4):
        assert not process_time_exit(db_session, entry_id=result.order_attempt.id, client=client,
                                     worker_lease_token=token, now=due(result.order_attempt))
    assert db_session.query(BotRiskEvent).filter_by(code="mathematical_entry_unresolved").count() == 1
    assert len(client.place_order_calls) == 1
    assert client.close_position_calls == []


def test_current_durable_attempt_does_not_block_itself_under_cooldown(db_session, live_entry):
    result, _ = live_entry
    entry = result.order_attempt
    now = bot_service._as_utc(entry.created_at) + timedelta(seconds=1)
    assert bot_service._cooldown_block(db_session, user_id=USER_A, config=result.config, now=now).code == "cooldown_after_entry"
    assert bot_service._cooldown_block(db_session, user_id=USER_A, config=result.config, now=now, ignore_order_attempt_id=entry.id) is None


def test_cancelled_remainder_does_not_discard_an_observed_fill(db_session, live_entry):
    result, client = live_entry
    entry = result.order_attempt
    entry.status = "cancelled"
    entry.raw_response = {"provider_order": {"fillVolume": 1}}
    client.positions = [{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1}]
    client.open_orders = [protective_stop()]
    assert not process_time_exit(db_session, entry_id=entry.id, client=client,
                                 worker_lease_token=lease(db_session), now=due(entry)-timedelta(seconds=1))
    assert pending_exits(db_session).count() == 1
    assert process_time_exit(db_session, entry_id=entry.id, client=client,
                             worker_lease_token=lease(db_session), now=due(entry))
    assert len(client.close_position_calls) == 1


def test_exit_drift_includes_time_spent_verifying_the_close(db_session, live_entry, monkeypatch):
    result, client = live_entry
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr("app.services.topbot_time_exit.perf_counter", lambda: next(ticks))
    client.positions = [{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1}]
    assert process_time_exit(db_session, entry_id=result.order_attempt.id, client=client,
                             worker_lease_token=lease(db_session), now=due(result.order_attempt)+timedelta(seconds=1))
    assert result.order_attempt.raw_request["timeExit"]["exit_drift_seconds"] == 3


def test_failed_close_preserves_protective_stop(db_session, live_entry):
    result, client = live_entry
    client.positions = [{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1}]
    client.open_orders = [protective_stop()]
    def failed_close(**kwargs):
        raise RuntimeError("broker unavailable")
    client.close_position = failed_close
    assert not process_time_exit(db_session, entry_id=result.order_attempt.id, client=client,
                                 worker_lease_token=lease(db_session), now=due(result.order_attempt))
    assert client.open_orders == [protective_stop()]
    assert not client.cancel_order_calls


def test_missing_bracket_flattens_before_deadline(db_session, live_entry):
    result, client = live_entry
    client.positions = [{"account_id": 9001, "contract_id": CONTRACT_ID, "signed_size": 1}]
    assert process_time_exit(db_session, entry_id=result.order_attempt.id, client=client,
                             worker_lease_token=lease(db_session), now=due(result.order_attempt)-timedelta(minutes=10))
    assert len(client.close_position_calls) == 1
    assert result.order_attempt.raw_request["timeExit"]["missing_stop_alerted_at"]


def test_pending_exit_prevents_deleting_its_config(db_session, live_entry):
    result, _ = live_entry
    with pytest.raises(ValueError, match="timed Practice exit is pending"):
        bot_service.delete_bot_config(db_session, user_id=USER_A, bot_config_id=result.config.id)
    assert pending_exits(db_session).count() == 1


def test_failed_exit_records_warning_and_retries_only_after_backoff(db_session, live_entry):
    from app.models import BotRiskEvent

    result, client = live_entry
    entry = result.order_attempt
    token = lease(db_session)
    client.account_simulated = False
    assert not process_time_exit(db_session, entry_id=entry.id, client=client,
                                 worker_lease_token=token, now=due(entry))
    db_session.commit()
    retry_at = datetime.fromisoformat(entry.raw_request["timeExit"]["retry_at"])
    assert db_session.query(BotRiskEvent).filter_by(code="mathematical_time_exit_pending").count() == 1
    client.account_simulated = True
    assert not process_time_exit(db_session, entry_id=entry.id, client=client,
                                 worker_lease_token=token, now=retry_at-timedelta(seconds=1))
    assert process_time_exit(db_session, entry_id=entry.id, client=client,
                             worker_lease_token=token, now=retry_at)
    assert pending_exits(db_session).count() == 0
