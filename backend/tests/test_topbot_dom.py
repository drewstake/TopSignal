from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest

from app.services import topbot_strategy as strategy, bot_service
from app.services.topbot_depth import DepthState, feed
from app.services.topbot_runner import desired_stop, runner_plan, finalize, manage
from test_bot_execution_safety import USER_A, CONTRACT_ID, RecordingClient, _add_account_and_config, db_session, open_exchange_session
from test_topbot_time_exit import lease


def observation(now, imbalance=.2):
    return {"ready": True, "imbalance": imbalance, "bid_total": 1500, "ask_total": 1000,
            "bid": 20000., "ask": 20000.25, "observed_at": now.isoformat(),
            "episode_at": (now-timedelta(seconds=30)).isoformat(), "persistence_seconds": 30}


@pytest.mark.parametrize("imbalance,action,stop", [(.2,"SELL",20025),(-.2,"BUY",19975.25)])
def test_contrarian_directions_and_fixed_stop(imbalance, action, stop):
    result = strategy.evaluate([], strategy_params=strategy.DEFAULTS, observation=observation(datetime.now(timezone.utc), imbalance))
    assert result.action == action
    assert result.raw_payload["stop_loss"] == stop
    assert result.raw_payload["protective_stop_ticks"] == 100
    assert "take_profit" not in result.raw_payload


@pytest.mark.parametrize("patch", [{"ready": False}, {"imbalance": .09}, {"persistence_seconds": 29.9}, {"imbalance": 0}])
def test_nonqualifying_depth_holds(patch):
    assert strategy.evaluate([], strategy_params=strategy.DEFAULTS,
        observation={**observation(datetime.now(timezone.utc)), **patch}).action == "HOLD"


def snapshot_event(at, sequence=1, bids=100, asks=100):
    return {"event": "snapshot", "data": {"contract_id": CONTRACT_ID, "sequence": sequence,
        "timestamp": at.isoformat(), "bids": [{"price":20000-i*.25,"size":15} for i in range(bids)],
        "asks": [{"price":20000.25+i*.25,"size":10} for i in range(asks)]}}


def test_depth_persistence_freshness_gaps_and_top100():
    now = datetime.now(timezone.utc)
    state = DepthState(CONTRACT_ID)
    for i in range(31): state.apply(snapshot_event(now+timedelta(seconds=i), i+1, 110, 110), now+timedelta(seconds=i))
    result = state.snapshot(now+timedelta(seconds=30))
    assert result["bid_total"] == 1500 and result["ask_total"] == 1000
    assert result["persistence_seconds"] == 30
    assert not state.snapshot(now+timedelta(seconds=33))["ready"]
    state.apply(snapshot_event(now+timedelta(seconds=34), 40), now+timedelta(seconds=34))
    assert state.snapshot(now+timedelta(seconds=34))["persistence_seconds"] == 0
    state.apply({"event":"state", "data":{"state":"reconnecting"}}, now)
    assert not state.snapshot(now)["ready"]


def test_insufficient_depth_never_qualifies():
    now = datetime.now(timezone.utc)
    state = DepthState(CONTRACT_ID)
    state.apply(snapshot_event(now, bids=99), now)
    assert not state.snapshot(now)["ready"]


def test_brief_level_removal_resets_qualification_and_keeps_reason_after_recovery():
    now = datetime.now(timezone.utc)
    state = DepthState(CONTRACT_ID)
    for i in range(11):
        at = now + timedelta(seconds=i)
        state.apply(snapshot_event(at, i+1), at)
    assert state.snapshot(at)["persistence_seconds"] == 10
    removed_at = at + timedelta(milliseconds=10)
    state.apply({"event": "update", "data": {"contract_id": CONTRACT_ID, "sequence": 12,
        "timestamp": removed_at.isoformat(), "side": "ask", "price": 20025, "size": 0}}, removed_at)
    partial = state.snapshot(removed_at)
    assert partial["imbalance"] > .1 and partial["persistence_seconds"] == 0
    assert partial["qualification_reset"]["reason"] == "insufficient_levels"
    assert partial["qualification_reset"]["ask_levels"] == 99
    recovered_at = removed_at + timedelta(milliseconds=10)
    state.apply(snapshot_event(recovered_at, 13), recovered_at)
    later = recovered_at + timedelta(seconds=1)
    state.apply(snapshot_event(later, 14), later)
    recovered = state.snapshot(later)
    assert recovered["ready"] and recovered["persistence_seconds"] == 1
    assert recovered["qualification_reset"] == partial["qualification_reset"]


@pytest.mark.parametrize("cause", ["below_threshold", "side_changed", "sequence_gap", "depth_gap", "provider_reset", "disconnected"])
def test_qualification_reset_reason_and_entry_duration(cause):
    now = datetime.now(timezone.utc)
    state = DepthState(CONTRACT_ID)
    state.apply(snapshot_event(now), now)
    at = now + timedelta(seconds=3 if cause == "depth_gap" else 1)
    event = snapshot_event(at, 3 if cause == "sequence_gap" else 2)
    if cause in {"below_threshold", "side_changed"}:
        for row in event["data"]["bids"]: row["size"] = 10 if cause == "below_threshold" else 5
    elif cause == "provider_reset": event["data"]["reset"] = True
    elif cause == "disconnected": event = {"event": "state", "data": {"state": "reconnecting"}}
    state.apply(event, at)
    result = state.snapshot(at)
    assert result["qualification_reset"]["reason"] == cause
    assert result.get("persistence_seconds", 0) == 0


@pytest.mark.parametrize("side,entry,mfe,current,expected", [
    ("BUY",20000,24.75,19975,19975), ("BUY",20000,25,19975,20000),
    ("BUY",20000,40,20000,20015), ("BUY",20000,30,20020,20020),
    ("SELL",20000,25,20025,20000), ("SELL",20000,40,20000,19985),
    ("SELL",20000,30,19980,19980)])
def test_trail_activation_and_never_widen(side,entry,mfe,current,expected):
    assert desired_stop(side,entry,mfe,current) == expected


@pytest.fixture
def dom_entry(db_session, monkeypatch, open_exchange_session):
    for key in ("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "TOPSIGNAL_BOT_WORKER_ENABLED", "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION"):
        monkeypatch.setenv(key,"true")
    monkeypatch.setattr(bot_service,"running_under_tests",lambda:False)
    monkeypatch.setattr(feed,"ensure",lambda *a,**kw:None)
    now = datetime.now(timezone.utc)
    # These runner/order tests hold the session open independently of wall time.
    # The real cutoff and overnight behavior are covered by the risk-gate tests below.
    monkeypatch.setattr("app.services.topbot_session.flat_deadline", lambda *a, **kw: now + timedelta(hours=1))
    observed = observation(now, -.2)
    monkeypatch.setattr(feed,"snapshot",lambda *a,**kw:observed)
    account, config = _add_account_and_config(db_session, enabled=False, execution_mode="live")
    config.strategy_type = "topbot_adaptive"
    config.strategy_params = dict(strategy.DEFAULTS)
    db_session.commit()
    client = RecordingClient()
    result = bot_service.start_bot_run(db_session,user_id=USER_A,bot_config_id=config.id,client=client,
                                     dry_run=False,confirm_live_order_routing=True,continuous=True)
    assert result.order_attempt is None  # Only the fenced worker may enter.
    token = lease(db_session)
    result = bot_service.evaluate_bot_config(db_session,user_id=USER_A,config=config,account=account,client=client,
        run=result.run,dry_run=False,confirm_live_order_routing=True,worker_lease_token=token)
    assert result.order_attempt is not None, [(r.code,r.message) for r in result.risk_events]
    assert result.order_attempt.status == "submitted", [(r.code,r.message) for r in result.risk_events]
    db_session.commit()
    return result, client, token


@pytest.mark.parametrize("value,expected", [
    ("2026-11-01T18:00", None),  # Sunday reopen after DST changes.
    ("2026-11-02T02:00", None),
    ("2026-11-02T09:34", None),
    ("2026-11-02T15:31", None),
    ("2026-11-02T16:07:59", None),
    ("2026-11-02T16:08", "entry_too_close_to_session_close"),
    ("2026-11-02T16:45", "entry_too_close_to_session_close"),
    ("2026-11-02T17:30", "exchange_session_closed"),
    ("2026-11-02T18:00", None),
    ("2026-11-26T12:57:59", None),
    ("2026-11-26T12:58", "entry_too_close_to_session_close"),
    ("2026-11-07T10:00", "exchange_session_closed"),
])
def test_dom_entry_session_risk_gate(db_session, monkeypatch, value, expected):
    from zoneinfo import ZoneInfo
    from app.services.trading_day import futures_session_is_open
    now = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("America/New_York"))
    account, config = _add_account_and_config(db_session)
    config.strategy_type = "topbot_adaptive"
    config.strategy_params = dict(strategy.DEFAULTS)
    # Previously saved daytime hours must not restrict the DOM model either.
    config.trading_start_time, config.trading_end_time = "09:35", "15:30"
    observed = observation(now, -.2)
    monkeypatch.setattr(feed, "snapshot", lambda *a, **kw: observed)
    monkeypatch.setattr(bot_service, "futures_session_is_open", futures_session_is_open)
    decision = SimpleNamespace(raw_payload={"depth_entry": observed})
    blocks = bot_service.evaluate_risk_gates(
        db_session, user_id=USER_A, config=config, account=account,
        latest_candle=None, contract_id=CONTRACT_ID, symbol="MNQ", action="BUY",
        dry_run=True, confirm_live_order_routing=False, decision=decision, now=now,
    )
    codes = {block.code for block in blocks}
    assert "outside_trading_session" not in codes
    assert "outside_research_session" not in codes
    assert "dom_signal_expired" not in codes
    assert "stale_market_data" not in codes
    if expected:
        assert expected in codes
    else:
        assert not codes, codes


def test_api_entry_has_only_25_point_stop_and_durable_runner(dom_entry):
    result, client, _ = dom_entry
    assert client.place_order_calls[0]["stop_loss_bracket"] == {"ticks":100,"type":4}
    assert client.place_order_calls[0].get("take_profit_bracket") is None
    assert result.order_attempt.raw_request["timeExit"]["runner"]["activation_points"] == 25


def test_same_episode_never_places_twice(db_session,dom_entry):
    result,client,token = dom_entry
    bot_service.evaluate_bot_config(db_session,user_id=USER_A,config=result.config,account=None,client=client,
        run=result.run,dry_run=False,confirm_live_order_routing=True,worker_lease_token=token)
    assert len(client.place_order_calls) == 1


def test_fill_stats_use_actual_fills_and_fees(monkeypatch):
    now = datetime.now(timezone.utc)
    entry = SimpleNamespace(id=1,user_id=USER_A,account_id=9001,contract_id=CONTRACT_ID,
                            provider_order_id="entry",side="BUY",size=1,created_at=now)
    client = RecordingClient(trades=[
        dict(account_id=9001,contract_id=CONTRACT_ID,order_id="entry",side="BUY",size=1,price=20000.25,
             timestamp=now,fees=.61,voided=False),
        dict(account_id=9001,contract_id=CONTRACT_ID,order_id="stop",side="SELL",size=1,price=20030.25,
             timestamp=now+timedelta(minutes=7),fees=.61,voided=False)])
    plan = runner_plan(now)
    assert finalize(entry,plan,client,now+timedelta(minutes=8))
    r = plan["runner"]
    assert r["hold_seconds"] == 420 and r["net_pnl_usd"] == pytest.approx(58.78)
    assert r["gross_points"] == 30 and r["fees_usd"] == 1.22


@pytest.mark.parametrize("status,complete,stats_status", [
    ("cancelled", True, "not_filled"), ("submitted", False, "awaiting_fill"),
])
def test_missing_fill_only_completes_for_verified_cancel(status, complete, stats_status):
    now = datetime.now(timezone.utc)
    entry = SimpleNamespace(id=1, user_id=USER_A, account_id=9001, contract_id=CONTRACT_ID,
                            provider_order_id="entry", side="BUY", size=1, created_at=now, status=status)
    plan = runner_plan(now)
    assert finalize(entry, plan, RecordingClient(trades=[]), now) is complete
    assert plan["runner"]["stats_status"] == stats_status
    assert "net_pnl_usd" not in plan["runner"]


def test_trail_modifies_owned_stop_in_place(db_session,dom_entry,monkeypatch):
    result,client,token=dom_entry
    entry=result.order_attempt
    now=datetime.now(timezone.utc)
    plan=entry.raw_request["timeExit"]
    plan["runner"].update(entry_at=now.isoformat(),entry_price=20000,side="BUY",mfe_points=40,mae_points=-2)
    stop={"order_id":"123","account_id":9001,"contract_id":CONTRACT_ID,"raw_payload":{"stopPrice":19975}}
    client.open_orders=[stop]
    calls=[]
    def modify(**kw):
        calls.append(kw); stop["raw_payload"]["stopPrice"]=kw["stop_price"]
    client.modify_stop_order=modify
    monkeypatch.setattr(feed,"snapshot",lambda *a,**kw:{**observation(now),"bid":20035,"ask":20035.25})
    assert not manage(db_session,entry=entry,plan=plan,client=client,stops=[stop],worker_lease_token=token,now=now)
    assert calls == [{"account_id":9001,"order_id":"123","stop_price":20015}]
    assert not client.cancel_order_calls
    assert plan["runner"]["confirmed_stop"] == 20015
    from app.services.topbot_audit import page
    events = page(USER_A, 9001)["items"]
    verified = next(e for e in events if e["kind"] == "stop_change_verified")
    requested = next(e for e in events if e["kind"] == "broker_request" and e["details"]["action"] == "modify_stop_order")
    assert verified["operation"] == requested["operation"]
    assert verified["details"]["old_stop"] == 19975
    assert verified["details"]["new_stop"] == 20015


@pytest.mark.parametrize("fault", ["funded", "lost_lease", "unknown_classification"])
def test_runner_never_mutates_without_practice_and_lease(db_session,dom_entry,fault):
    from app.services.topbot_time_exit import process_time_exit
    result,client,token=dom_entry
    entry=result.order_attempt
    client.positions=[{"account_id":9001,"contract_id":CONTRACT_ID,"signed_size":1}]
    if fault == "funded": client.account_simulated=False
    if fault == "unknown_classification":
        client.account_simulated=None
        from app.models import Account
        account=db_session.query(Account).filter_by(external_id="9001").one()
        account.provider_simulated=None
        account.provider_classification_observed_at=None
        db_session.commit()
    if fault == "lost_lease":
        token=bot_service.BotWorkerLeaseToken(lease_name="test-time-exits",owner_id="wrong",lease_ttl_seconds=45)
    assert not process_time_exit(db_session,entry_id=entry.id,client=client,worker_lease_token=token)
    assert client.close_position_calls == [] and client.cancel_order_calls == []


def test_stats_scoped_to_owner_and_account(db_session,dom_entry):
    from app.services.topbot_runner import statistics
    result,_,_=dom_entry
    assert len(statistics(db_session,user_id=USER_A,account_id=9001)["items"]) == 1
    assert statistics(db_session,user_id=USER_A,account_id=9002)["items"] == []
    assert statistics(db_session,user_id="00000000-0000-0000-0000-000000000099",account_id=9001)["items"] == []


def test_restart_keeps_tighter_broker_stop(db_session,dom_entry,monkeypatch):
    result,client,token=dom_entry
    entry=result.order_attempt
    now=datetime.now(timezone.utc)
    plan=entry.raw_request["timeExit"]
    plan["runner"].update(entry_at=now.isoformat(),entry_price=20000,side="BUY",mfe_points=50,mae_points=-2)
    stop={"order_id":"123","account_id":9001,"contract_id":CONTRACT_ID,"raw_payload":{"stopPrice":20030}}
    client.modify_stop_order=lambda **kw: pytest.fail("Must not widen the existing broker stop")
    monkeypatch.setattr(feed,"snapshot",lambda *a,**kw:{**observation(now),"bid":20040,"ask":20040.25})
    assert not manage(db_session,entry=entry,plan=plan,client=client,stops=[stop],worker_lease_token=token,now=now)
    assert plan["runner"]["confirmed_stop"] == 20030
