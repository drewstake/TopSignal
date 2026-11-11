from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.services import topbot_profit_guard as guard, topbot_audit, bot_service
from test_bot_execution_safety import USER_A, USER_B, CONTRACT_ID, RecordingClient, db_session, open_exchange_session
from test_topbot_dom import dom_entry


NOW = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("values,armed,trigger", [
    ([199.99, 0, -249], False, None),
    ([200, 100, .01], False, None),
    ([200, 0], False, None),
    ([220, -10], False, None),
    ([400, 499.99], False, None),
    ([-499.99], False, None),
    ([-500, 0, 100], False, "daily_loss_limit_reached"),
    ([500], False, "daily_profit_target_reached"),
    ([520, 150, 0], False, "daily_profit_target_reached"),
    ([200, 0, 500], False, "daily_profit_target_reached"),
])
def test_thresholds_and_sticky_daily_stop(values, armed, trigger):
    for index, value in enumerate(values):
        state = guard.observe(USER_A, 9001, value, now=NOW + timedelta(seconds=index))
    assert state["armed"] is armed
    assert state["trigger"] == trigger
    assert state["peak_pnl"] == max(values)
    assert guard.read(USER_A, 9001, now=NOW + timedelta(minutes=1))["trigger"] == trigger


def test_persistent_account_state_survives_sql_rollback_and_is_scoped(db_session):
    guard.observe(USER_A, 9001, 200, now=NOW)
    db_session.rollback()
    assert not guard.read(USER_A, 9001, now=NOW)["armed"]
    assert not guard.read(USER_A, 9002, now=NOW)["armed"]
    assert not guard.read(USER_B, 9001, now=NOW)["armed"]
    guard.observe(USER_A, 9001, 0, now=NOW + timedelta(seconds=1))
    assert guard.read(USER_A, 9001, now=NOW + timedelta(seconds=2))["trigger"] is None
    assert guard.read(USER_A, 9001, now=NOW)["peak_pnl"] == 200


@pytest.mark.parametrize("pnl", [500, -500])
def test_session_rollover_resets_at_6pm_new_york(pnl):
    before = datetime(2026, 11, 3, 22, 59, 59, tzinfo=timezone.utc)
    guard.observe(USER_A, 9001, pnl, now=before)
    after = before + timedelta(seconds=1)
    assert guard.read(USER_A, 9001, now=after)["trigger"] is None
    assert not guard.observe(USER_A, 9001, -1, now=after)["armed"]
    with pytest.raises(ValueError, match="Out-of-order"):
        guard.observe(USER_A, 9001, pnl, now=before)


@pytest.mark.parametrize("trigger", [None, "daily_profit_breakeven_stop", "daily_profit_target_reached"])
def test_saved_break_even_state_is_retired_but_target_stop_survives(trigger, monkeypatch):
    state = guard.observe(USER_A, 9001, 220, now=NOW)
    state.update(armed=True, arm_at=200, floor=0, trigger=trigger,
                 triggered_at=NOW.isoformat() if trigger else None)
    with topbot_audit.connection(USER_A) as db:
        db.execute("UPDATE daily_profit_guard SET state=? WHERE account=?", (json.dumps(state), 9001))
    expected = "daily_profit_target_reached" if trigger == "daily_profit_target_reached" else None
    status = guard.public_status(USER_A, 9001, now=NOW)
    assert status["available"] and not status["armed"]
    assert status["arm_at"] is None and status["floor"] is None
    assert status["trigger"] == expected
    assert bool(guard.risk_block(status)) == bool(expected)
    monkeypatch.setattr(bot_service, "_fetch_account_daily_pnl", lambda *a, **kw: (0, 1, None))
    monkeypatch.setattr("app.services.topbot_depth.feed.snapshot", lambda *a, **kw: {})
    assert guard.poll(USER_A, 9001, CONTRACT_ID, RecordingClient(), now=NOW)["trigger"] == expected
    guard.mark_unavailable(USER_A, 9001, now=NOW)
    assert guard.read(USER_A, 9001, now=NOW)["trigger"] == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, True])
def test_invalid_observations_cannot_disarm_or_change_latch(value):
    guard.observe(USER_A, 9001, 500, now=NOW)
    with pytest.raises(ValueError):
        guard.observe(USER_A, 9001, value, now=NOW)
    assert guard.read(USER_A, 9001, now=NOW)["trigger"] == "daily_profit_target_reached"


def test_only_transitions_add_audit_events_and_state_is_bounded():
    for index, value in enumerate([0, 100, 200, 210, 100, 0, -10, 500, 0]):
        guard.observe(USER_A, 9001, value, now=NOW + timedelta(seconds=index))
    events = topbot_audit.page(USER_A, 9001)["items"]
    assert [e["kind"] for e in events] == ["daily_profit_stop"]
    with topbot_audit.connection(USER_A) as db:
        assert db.execute("SELECT count(*) FROM daily_profit_guard").fetchone()[0] == 1


@pytest.mark.parametrize("size,bid,ask,expected", [(1, 20050, 20051, 200), (-1, 19949, 19950, 200)])
def test_poll_combines_net_realized_with_liquidation_side_unrealized(monkeypatch, size, bid, ask, expected):
    monkeypatch.setattr(bot_service, "_fetch_account_daily_pnl", lambda *a, **kw: (100, 1, None))
    monkeypatch.setattr("app.services.topbot_depth.feed.snapshot", lambda *a, **kw:
        dict(ready=True, bid=bid, ask=ask, observed_at=NOW.isoformat()))
    client = RecordingClient(positions=[dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=size, average_price=20000)])
    state = guard.poll(USER_A, 9001, CONTRACT_ID, client, now=NOW)
    assert state["pnl"] == expected and not state["armed"]
    assert not client.place_order_calls and not client.close_position_calls


@pytest.mark.parametrize("fault", ["stale_quote", "other_position", "wrong_account", "bad_pnl"])
def test_incomplete_pnl_cannot_trip_or_clear_protection(monkeypatch, fault):
    monkeypatch.setattr(bot_service, "_fetch_account_daily_pnl", lambda *a, **kw: (0, 1, None))
    monkeypatch.setattr("app.services.topbot_depth.feed.snapshot", lambda *a, **kw: dict(ready=False))
    row = dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=1, average_price=20000)
    if fault == "other_position": row["contract_id"] = "OTHER"
    if fault == "wrong_account": row["account_id"] = 9002
    if fault == "bad_pnl": row["unrealized_pnl"] = float("nan")
    guard.observe(USER_A, 9001, 200, now=NOW)
    with pytest.raises(ValueError):
        guard.poll(USER_A, 9001, CONTRACT_ID, RecordingClient(positions=[row]), now=NOW)
    assert not guard.read(USER_A, 9001, now=NOW)["armed"]
    assert guard.read(USER_A, 9001, now=NOW)["trigger"] is None


def test_trigger_does_not_require_more_provider_reads(monkeypatch):
    guard.observe(USER_A, 9001, 500, now=NOW)
    assert guard.poll(USER_A, 9001, CONTRACT_ID, object(), now=NOW)["trigger"]
    guard.mark_unavailable(USER_A, 9001, now=NOW)
    assert guard.read(USER_A, 9001, now=NOW)["trigger"]


def test_position_closing_during_pnl_read_does_not_falsely_trigger_zero(monkeypatch):
    guard.observe(USER_A, 9001, 200, now=NOW)
    client = RecordingClient(positions=[dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=1, unrealized_pnl=250)])
    def history(*args, **kwargs):
        client.positions = []  # Closed after the history query's cutoff.
        return 0, 1, None
    monkeypatch.setattr(bot_service, "_fetch_account_daily_pnl", history)
    with pytest.raises(ValueError, match="positions changed"):
        guard.poll(USER_A, 9001, CONTRACT_ID, client, now=NOW)
    assert guard.read(USER_A, 9001, now=NOW)["trigger"] is None


@pytest.mark.parametrize("arm,pnl,reason", [
    (False, 500, "daily_profit_target_reached"), (True, 500, "daily_profit_target_reached"),
    (False, -500, "daily_loss_limit_reached"), (True, -500, "daily_loss_limit_reached"),
])
def test_profit_stop_closes_owned_position_before_deadline_even_when_stopped(db_session, dom_entry, monkeypatch, arm, pnl, reason):
    from app.services.topbot_time_exit import process_time_exit
    result, client, token = dom_entry
    entry = result.order_attempt
    result.config.enabled = False
    if arm:
        guard.observe(USER_A, 9001, 200)
    client.positions = [dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=1, unrealized_pnl=pnl)]
    client.open_orders = [dict(account_id=9001, contract_id=CONTRACT_ID, order_id="stop", order_type=4,
        parent_order_id=entry.provider_order_id, side=1, status=1, size=1, raw_payload={"stopPrice":19975})]
    # Fill statistics are independent of proving the close and residual-order cleanup.
    monkeypatch.setattr("app.services.topbot_runner.finalize", lambda *a, **kw: True)
    db_session.commit()
    assert process_time_exit(db_session, entry_id=entry.id, client=client, worker_lease_token=token)
    assert client.close_position_calls == [{"account_id":9001, "contract_id":CONTRACT_ID}]
    assert entry.raw_request["timeExit"]["runner_exit_reason"] == reason
    assert not client.positions and not client.open_orders
    assert guard.read(USER_A, 9001)["trigger"] == reason


@pytest.mark.parametrize("legacy_exit", [False, True])
def test_return_to_zero_keeps_position_management_running(db_session, dom_entry, monkeypatch, legacy_exit):
    from app.services.topbot_time_exit import process_time_exit
    result, client, token = dom_entry
    entry = result.order_attempt
    guard.observe(USER_A, 9001, 220)
    if legacy_exit:
        request = dict(entry.raw_request)
        request["timeExit"] = {**request["timeExit"], "profit_stop_reason": "daily_profit_breakeven_stop",
                               "runner_exit_reason": "daily_profit_breakeven_stop"}
        entry.raw_request = request
    client.positions = [dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=1, unrealized_pnl=0)]
    client.open_orders = [dict(account_id=9001, contract_id=CONTRACT_ID, order_id="stop", order_type=4,
        parent_order_id=entry.provider_order_id, side=1, status=1, size=1, raw_payload={"stopPrice":19975})]
    managed = []
    monkeypatch.setattr("app.services.topbot_runner.manage", lambda *a, **kw: managed.append(kw["entry"].id) or False)
    db_session.commit()
    assert not process_time_exit(db_session, entry_id=entry.id, client=client, worker_lease_token=token)
    assert managed == [entry.id]
    assert not client.close_position_calls
    assert not entry.raw_request["timeExit"].get("profit_stop_reason")
    assert entry.raw_request["timeExit"].get("runner_exit_reason") != "daily_profit_breakeven_stop"
    assert guard.read(USER_A, 9001)["trigger"] is None


@pytest.mark.parametrize("pnl,expected", [(500, "daily_profit_target_reached"), (-500, "daily_loss_limit_reached"), (0, None)])
def test_daily_target_blocks_reentry_but_return_to_zero_does_not(db_session, dom_entry, pnl, expected):
    result, client, token = dom_entry
    guard.observe(USER_A, 9001, 220)
    guard.observe(USER_A, 9001, pnl)
    # Simulate a new run/config evaluation with recovered P&L. The guard is keyed
    # by account and trading session, not either run ID or configuration ID.
    result.config.enabled = True
    decision = SimpleNamespace(raw_payload={"depth_entry": result.decision.raw_payload["depth_entry"]}, quantity=1)
    blocks = bot_service.evaluate_risk_gates(db_session, user_id=USER_A, config=result.config,
        account=bot_service._require_owned_account(db_session, user_id=USER_A, account_id=9001),
        latest_candle=None, contract_id=CONTRACT_ID, symbol="MNQ", action="BUY", decision=decision,
        dry_run=False, confirm_live_order_routing=True, client=client)
    assert ({b.code for b in blocks} & {"daily_profit_target_reached", "daily_loss_limit_reached"}) == ({expected} if expected else set())
    assert "daily_profit_breakeven_stop" not in {b.code for b in blocks}
    assert len(client.place_order_calls) == 1


def test_unavailable_guard_blocks_new_entries(db_session, dom_entry, monkeypatch):
    result, client, _ = dom_entry
    monkeypatch.setattr(guard, "read", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk unavailable")))
    decision = SimpleNamespace(raw_payload={"depth_entry": result.decision.raw_payload["depth_entry"]}, quantity=1)
    blocks = bot_service.evaluate_risk_gates(db_session, user_id=USER_A, config=result.config,
        account=bot_service._require_owned_account(db_session, user_id=USER_A, account_id=9001),
        latest_candle=None, contract_id=CONTRACT_ID, symbol="MNQ", action="BUY", decision=decision,
        dry_run=False, confirm_live_order_routing=True, client=client)
    assert "daily_profit_guard_unavailable" in {b.code for b in blocks}


def test_failed_close_retries_after_rollover_without_clearing_exit_intent(db_session, dom_entry, monkeypatch):
    from app.services.topbot_time_exit import process_time_exit
    result, client, token = dom_entry
    entry = result.order_attempt
    guard.observe(USER_A, 9001, 500)
    client.positions = [dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=1)]
    close = client.close_position
    client.close_position = lambda **kw: (_ for _ in ()).throw(TimeoutError("Unconfirmed"))
    monkeypatch.setattr("app.services.topbot_runner.finalize", lambda *a, **kw: True)
    assert not process_time_exit(db_session, entry_id=entry.id, client=client, worker_lease_token=token)
    db_session.commit()
    assert entry.raw_request["timeExit"]["profit_stop_reason"] == "daily_profit_target_reached"
    assert guard.read(USER_A, 9001)["trigger"] == "daily_profit_target_reached"
    # A new session's empty daily latch must not erase this unfinished close.
    monkeypatch.setattr(guard, "poll", lambda *a, **kw: {"trigger": None})
    client.close_position = close
    retry = datetime.fromisoformat(entry.raw_request["timeExit"]["retry_at"]) + timedelta(seconds=1)
    assert process_time_exit(db_session, entry_id=entry.id, client=client, worker_lease_token=token, now=retry)
    assert len(client.close_position_calls) == 1
    assert entry.raw_request["timeExit"]["runner_exit_reason"] == "daily_profit_target_reached"


@pytest.mark.parametrize("fault", ["funded", "lost_lease", "different_position"])
def test_profit_trigger_does_not_bypass_broker_mutation_safety(db_session, dom_entry, fault):
    from app.services.topbot_time_exit import process_time_exit
    result, client, token = dom_entry
    guard.observe(USER_A, 9001, 500)
    client.positions = [dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=1)]
    if fault == "funded":
        client.account_simulated = False
    if fault == "different_position":
        client.positions[0]["signed_size"] = -1
    if fault == "lost_lease":
        token = bot_service.BotWorkerLeaseToken(lease_name="test-time-exits", owner_id="wrong", lease_ttl_seconds=45)
    assert not process_time_exit(db_session, entry_id=result.order_attempt.id, client=client, worker_lease_token=token)
    assert not client.close_position_calls
    assert guard.read(USER_A, 9001)["trigger"] == "daily_profit_target_reached"


def test_monitor_failure_preserves_existing_stop_management(db_session, dom_entry, monkeypatch):
    from app.services.topbot_time_exit import process_time_exit
    result, client, token = dom_entry
    entry = result.order_attempt
    client.positions = [dict(account_id=9001, contract_id=CONTRACT_ID, signed_size=1)]
    client.open_orders = [dict(account_id=9001, contract_id=CONTRACT_ID, order_id="stop", order_type=4,
        parent_order_id=entry.provider_order_id, side=1, status=1, size=1, raw_payload={"stopPrice":19975})]
    monkeypatch.setattr(guard, "poll", lambda *a, **kw: (_ for _ in ()).throw(ValueError("Missing P&L")))
    managed = []
    monkeypatch.setattr("app.services.topbot_runner.manage", lambda *a, **kw: managed.append(kw["entry"].id) or False)
    assert not process_time_exit(db_session, entry_id=entry.id, client=client, worker_lease_token=token)
    assert managed == [entry.id]
    assert not client.close_position_calls
    assert guard.read(USER_A, 9001)["error"]
