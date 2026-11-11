from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import bot_service, topbot_runner as runner, topbot_strategy as strategy
from app.services.topbot_depth import feed
from test_bot_execution_safety import USER_A, CONTRACT_ID, db_session, open_exchange_session
from test_topbot_dom import dom_entry, observation


@pytest.mark.parametrize("value", [True, 20261104, "20261104", "2026-02-30", "2026-11-04T00:00:00", ""])
def test_session_override_rejects_invalid_dates(value):
    with pytest.raises((ValueError, TypeError)):
        strategy.normalize_params({**strategy.DEFAULTS, "fixed_bracket_session": value})


@pytest.mark.parametrize("at,expected", [
    ("2026-11-03T22:59:59+00:00", "trailing"),
    ("2026-11-03T23:00:00+00:00", "fixed"),
    ("2026-11-04T22:59:59+00:00", "fixed"),
    ("2026-11-04T23:00:00+00:00", "trailing"),
])
def test_override_only_applies_to_selected_new_york_trading_session(at, expected):
    params = strategy.normalize_params({**strategy.DEFAULTS, "fixed_bracket_session": "2026-11-04"})
    assert strategy.exit_mode(params, datetime.fromisoformat(at)) == expected


@pytest.mark.parametrize("value", [None, True, 1, "FIXED", "unknown", ""])
def test_persistent_exit_policy_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="Invalid exit_mode"):
        strategy.normalize_params({**strategy.DEFAULTS, "exit_mode": value})


@pytest.mark.parametrize("at", ["2026-11-04T22:59:59+00:00", "2026-11-04T23:00:00+00:00", "2026-12-01T15:00:00+00:00"])
def test_persistent_fixed_policy_survives_session_rollover(at):
    params = strategy.normalize_params({**strategy.DEFAULTS, "exit_mode": "fixed", "fixed_bracket_session": "2026-11-04"})
    assert strategy.exit_mode(params, datetime.fromisoformat(at)) == "fixed"


@pytest.mark.parametrize("policy", [{"fixed_bracket_session": "2026-11-04"}, {"exit_mode": "fixed"}])
@pytest.mark.parametrize("imbalance,action", [(.2, "SELL"), (-.2, "BUY")])
def test_fixed_signal_has_equal_25_point_stop_and_target(imbalance, action, policy):
    now = datetime(2026, 11, 4, 15, tzinfo=timezone.utc)
    signal = strategy.evaluate([], strategy_params={**strategy.DEFAULTS, **policy},
                               observation=observation(now, imbalance), now=now)
    assert signal.action == action
    p = signal.raw_payload
    direction = 1 if action == "BUY" else -1
    assert signal.price - p["stop_loss"] == direction*25
    assert p["take_profit"] - signal.price == direction*25
    assert p["protective_stop_ticks"] == p["take_profit_ticks"] == 100
    assert p["exit_mode"] == "fixed"


@pytest.mark.parametrize("dom_entry", ["fixed", "persistent_fixed"], indirect=True)
def test_fixed_entry_routes_native_brackets_and_saves_exit_mode(dom_entry):
    result, client, _ = dom_entry
    call = client.place_order_calls[0]
    assert call["stop_loss_bracket"] == {"ticks": 100, "type": 4}
    assert call["take_profit_bracket"] == {"ticks": 100, "type": 1}
    plan = result.order_attempt.raw_request["timeExit"]
    assert plan["runner"]["exit_mode"] == "fixed"
    assert result.order_attempt.raw_request["strategyOrderPlan"]["exit_mode"] == "fixed"
    assert not bot_service._order_attempt_protection_block(config=result.config, order_attempt=result.order_attempt)
    for bracket in [None, {"ticks": 101, "type": 1}, {"ticks": 100, "type": 4}]:
        result.order_attempt.raw_request = {**result.order_attempt.raw_request, "takeProfitBracket": bracket}
        assert bot_service._order_attempt_protection_block(config=result.config, order_attempt=result.order_attempt).code == "fixed_brackets_invalid"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("fresh", [True, False])
def test_saved_fixed_trade_never_trails_even_after_25_points(db_session, dom_entry, monkeypatch, side, fresh):
    result, client, token = dom_entry
    entry = result.order_attempt
    entry.side = side
    now = datetime.now(timezone.utc)
    plan = runner.runner_plan(now, exit_mode="fixed")
    plan["runner"].update(entry_at=now.isoformat(), entry_price=20000, side=side, mfe_points=40, mae_points=-2)
    direction = 1 if side == "BUY" else -1
    stop = dict(order_id="stop", account_id=9001, contract_id=CONTRACT_ID, raw_payload={"stopPrice": 20000-direction*25})
    target = dict(order_id="target", account_id=9001, contract_id=CONTRACT_ID,
                  parent_order_id=entry.provider_order_id, order_type=1, status=1,
                  side=1 if side == "BUY" else 0, size=1, raw_payload={"limitPrice": 20000+direction*25})
    monkeypatch.setattr(feed, "snapshot", lambda *a, **kw: {**observation(now), "ready": fresh,
                       "bid": 20000+direction*40, "ask": 20000+direction*40+.25})
    client.modify_stop_order = lambda **kw: pytest.fail("Fixed stop must never trail")
    # Current config is trailing: management must still follow the saved fixed plan.
    assert result.config.strategy_params["fixed_bracket_session"] is None
    assert not runner.manage(db_session, entry=entry, plan=plan, client=client, stops=[stop], orders=[stop, target],
                             worker_lease_token=token, now=now)
    assert not plan["runner"]["trailing_active"]
    assert plan["runner"]["confirmed_stop"] == 20000-direction*25
    assert plan["runner"]["confirmed_target"] == 20000+direction*25


@pytest.mark.parametrize("fault", ["missing", "wrong_price", "wrong_parent", "wrong_size", "wrong_contract", "wrong_account"])
def test_fixed_target_must_be_verified_before_holding_position(db_session, dom_entry, monkeypatch, fault):
    result, client, token = dom_entry
    entry = result.order_attempt
    now = datetime.now(timezone.utc)
    plan = runner.runner_plan(now, exit_mode="fixed")
    plan["runner"].update(entry_at=now.isoformat(), entry_price=20000, side="BUY", mfe_points=0, mae_points=0)
    stop = dict(order_id="stop", raw_payload={"stopPrice": 19975})
    target = dict(order_id="target", account_id=9001, contract_id=CONTRACT_ID,
                  parent_order_id=entry.provider_order_id, order_type=1, status=1, side=1, size=1,
                  raw_payload={"limitPrice": 20025})
    if fault == "wrong_price": target["raw_payload"]["limitPrice"] = 20026
    if fault == "wrong_parent": target["parent_order_id"] = "other"
    if fault == "wrong_size": target["size"] = 2
    if fault == "wrong_contract": target["contract_id"] = "OTHER"
    if fault == "wrong_account": target["account_id"] = 9002
    monkeypatch.setattr(feed, "snapshot", lambda *a, **kw: observation(now))
    assert runner.manage(db_session, entry=entry, plan=plan, client=client, stops=[stop],
                         orders=[] if fault == "missing" else [target], worker_lease_token=token, now=now)
    assert plan["runner_exit_reason"] == "fixed_take_profit_not_verified"


def test_expired_session_signal_is_blocked_before_routing(db_session, dom_entry, monkeypatch):
    result, client, _ = dom_entry
    now = datetime.now(timezone.utc)
    depth = observation(now, -.2)
    monkeypatch.setattr(feed, "snapshot", lambda *a, **kw: depth)
    decision = SimpleNamespace(raw_payload={"depth_entry": depth, "exit_mode": "fixed"}, quantity=1)
    blocks = bot_service.evaluate_risk_gates(db_session, user_id=USER_A, config=result.config,
        account=bot_service._require_owned_account(db_session, user_id=USER_A, account_id=9001),
        latest_candle=None, contract_id=CONTRACT_ID, symbol="MNQ", action="BUY", decision=decision,
        dry_run=True, confirm_live_order_routing=False, now=now)
    assert "dom_exit_policy_changed" in {block.code for block in blocks}
