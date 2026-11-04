from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from app.services.topbot_study import PROTOCOL, EXPERIMENT_ID, simulate, classify, exit_classification, metrics, entry_snapshot
from app.services import topbot_study_store as store
from app.services.topbot_runner import runner_plan, finalize
from app.services.topbot_depth import DepthFeed, DepthState, utc
from app.services.topbot_audit import record, page

NOW = datetime(2026, 11, 4, 15, tzinfo=timezone.utc)
CONTRACT = "CON.F.US.MNQ.Z26"


def entry(side="BUY", price=20000):
    return {"entry_id": 1, "entry_at": NOW.isoformat(), "entry_price": price, "side": side,
        "strategy_version_id": "version", "strategy_snapshot": {"initial_risk_usd": 50, "quantity": 1,
        "initial_risk_points": 25, "contract_id": CONTRACT, "session": "2026-11-04",
        "research_cutoff": (NOW+timedelta(seconds=2)).isoformat()}}


def quote(seconds, points, side="BUY", price=20000):
    bid = price+points if side == "BUY" else price-points-.25
    return dict(at=(NOW+timedelta(seconds=seconds)).isoformat(), bid=bid, ask=bid+.25, bid_size=2, ask_size=2)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_limit_touch_is_not_fill_and_latency_costs_use_actual_anchor(side):
    path = [quote(.1, 0, side), quote(.3, 25, side), quote(.5, 25.25, side)]
    result = simulate(entry(side), path)
    first = result["variants"][0]
    assert first["exit_at"] == path[-1]["at"]
    assert first["net_pnl_usd"] == 50-1.72
    assert first["realized_r"] == (50-1.72)/50
    assert result["variants"][1]["status"] == "censored"
    assert "net_pnl_usd" not in result["variants"][1]
    assert not result["paired_valid"]


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_gap_stop_latches_before_target_and_prices_after_latency(side):
    result = simulate(entry(side), [quote(.1, 0, side), quote(.4, -26, side), quote(.7, -30, side), quote(.9, 80, side)])
    assert result["paired_valid"]
    assert all(v["exit_reason"] == "initial_stop" and v["net_pnl_usd"] == -60.5-1.72 for v in result["variants"])


def test_missing_ambiguous_censored_are_not_zero_returns():
    for path in ([quote(.1, 0), quote(4, 80)], [quote(.1, 0), {"at": quote(.5, 0)["at"], "gap": "disconnected"}], []):
        result = simulate(entry(), path)
        assert not result["paired_valid"]
        assert all(v["status"] in {"missing", "censored"} and "net_pnl_usd" not in v for v in result["variants"])
    result = simulate(entry(), [{"at": quote(.5, 0)["at"], "gap": "capture_index_gap"}])
    assert all(v["reason"] == "capture_index_gap" for v in result["variants"])


def test_target_rounding_short_long_and_quantity_unknown():
    for side in ("BUY", "SELL"):
        e = entry(side, 20000.1)
        result = simulate(e, [quote(.1, 0, side, 20000.1), quote(.5, 26, side, 20000.1)])
        assert result["variants"][0]["exit_price"] == (20025.25 if side == "BUY" else 19975.)
    e["strategy_snapshot"]["quantity"] = 2
    assert simulate(e, [quote(.1, 0)])["variants"][0]["reason"] == "unsupported_contract_quantity_or_risk"


def test_trailing_resolves_after_actual_exit_without_using_mfe_summary():
    e = entry()
    e["strategy_snapshot"]["research_cutoff"] = (NOW+timedelta(seconds=7)).isoformat()
    e.update(exit_at=quote(.5, 25.25)["at"], mfe_points=99999, mae_points=-99999)
    path = [quote(.1, 0), quote(.5, 25.25), quote(.8, 80), *[quote(s, 80) for s in (2, 3, 4, 5, 5.3)], quote(5.7, 54), quote(6, 53)]
    result = simulate(e, path)
    assert result["paired_valid"]
    assert result["variants"][-1]["exit_reason"] == "trailing_stop"
    assert result["variants"][-1]["exit_at"] > e["exit_at"]
    assert result["variants"][-1]["exit_price"] == 20052.75


def test_cutoff_never_uses_future_quote_or_invents_exit():
    e = entry()
    e["strategy_snapshot"]["research_cutoff"] = quote(.4, 0)["at"]
    result = simulate(e, [quote(.1, 0), quote(.5, 90)])
    assert all(v["status"] == "censored" for v in result["variants"])


def test_classification_only_saved_plan_unknown_and_snapshot_immutable():
    assert classify({})["strategy_class"] == "unknown"
    old = {"timeExit": {"runner": {"initial_stop_points": 25, "activation_points": 25, "trail_points": 25}}}
    assert classify(old)["classification_basis"].startswith("inferred")
    config = SimpleNamespace(strategy_params={"revision": "dom_contrarian_v1", "fixed_bracket_session": "2026-11-04"}, execution_mode="live")
    snap = entry_snapshot(config, account=1, contract=CONTRACT, side="BUY", quantity=1, plan=runner_plan(NOW, exit_mode="fixed"), now=NOW)
    config.strategy_params["fixed_bracket_session"] = None
    assert snap["rules"]["entry"]["fixed_bracket_session"] == "2026-11-04"
    assert snap["experiment_id"] == EXPERIMENT_ID


def test_exit_reason_requires_order_evidence_including_session_and_daily():
    assert exit_classification({"target_order_id": "target"}, {}, "target")[0] == "take_profit"
    assert exit_classification({}, {"runner_exit_reason": "session_cutoff"}, "x")[0] == "unknown"
    for reason, expected in (("session_cutoff", "session_close"), ("daily_profit_target_reached", "daily_risk_profit_rule")):
        assert exit_classification({}, {"runner_exit_reason": reason, "audit": {"close_response": {"orderId": "x"}}}, "x")[0] == expected
    assert exit_classification({}, {"runner_exit_reason": "protective_stop_too_wide", "audit": {"close_response": {"orderId": "x"}}}, "x")[0] == "unknown"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("order_type,reason", [(1, "take_profit"), (4, "initial_stop")])
def test_fast_native_exit_verified_from_broker_history_and_duplicate_fills(side, order_type, reason, monkeypatch):
    from app.services import topbot_runner as runner
    monkeypatch.setattr(runner, "record", lambda **kw: None)
    monkeypatch.setattr(runner.feed, "track", lambda *a, **kw: {})
    monkeypatch.setattr(runner.feed, "untrack", lambda *a, **kw: None)
    trade = SimpleNamespace(id=77, user_id="owner", account_id=1, contract_id=CONTRACT, provider_order_id="entry",
                            created_at=NOW, side=side, size=1, status="submitted", raw_request={})
    sign = 1 if side == "BUY" else -1
    price = 20000+sign*(25 if order_type == 1 else -25)
    fills = [dict(source_trade_id="a", order_id="entry", account_id=1, contract_id=CONTRACT, voided=False,
                  side=side, size=1, price=20000, fees=.36, timestamp=NOW),
             dict(source_trade_id="b", order_id="child", account_id=1, contract_id=CONTRACT, voided=False,
                  side="SELL" if side == "BUY" else "BUY", size=1, price=price, fees=.36, timestamp=NOW+timedelta(seconds=1))]
    from app.services.projectx_client import ProjectXClient
    order = dict(id="child", parentOrderId="entry", accountId=1, contractId=CONTRACT,
                 status=2, size=1, side=1 if side == "BUY" else 0, type=order_type,
                 **{"limitPrice" if order_type == 1 else "stopPrice": price})
    client = ProjectXClient(base_url="https://example.test", username="fixture", api_key="fixture")
    def request(method, path, **kwargs):
        assert path == "/api/Order/search"  # Only the read-only broker-history path.
        return {"orders": [order]}
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "fetch_trade_history", lambda **kw: fills+deepcopy(fills))
    plan = runner_plan(NOW, exit_mode="fixed")
    assert finalize(trade, plan, client, NOW+timedelta(seconds=2))
    assert plan["runner"]["exit_reason"] == reason
    assert plan["runner"]["exit_fill_id"] == "b"
    before = deepcopy(plan)
    client.fetch_trade_history = lambda **kw: pytest.fail("Completed fills must not be finalized again")
    assert finalize(trade, plan, client, NOW+timedelta(seconds=3)) and plan == before


def test_conflicting_duplicate_and_partial_fills_remain_ambiguous(monkeypatch):
    from app.services import topbot_runner as runner
    trade = SimpleNamespace(id=1, account_id=1, contract_id=CONTRACT, provider_order_id="entry", created_at=NOW, side="BUY", size=1)
    fill = dict(source_trade_id="a", order_id="entry", account_id=1, contract_id=CONTRACT, voided=False,
                side="BUY", size=1, price=20000, fees=.36, timestamp=NOW)
    client = SimpleNamespace(fetch_trade_history=lambda **kw: [fill, {**fill, "price": 20001}])
    plan = runner_plan(NOW)
    with pytest.raises(ValueError, match="exact single-contract"): runner.initialize(trade, plan, client, NOW)
    assert plan["runner"]["stats_status"] == "ambiguous_entry_fills"


def test_restart_dedup_and_append_only_result_corrections_account_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    result = simulate(entry(), [quote(.1, 0), quote(.4, -26), quote(.7, -30)])
    result.update(store.enroll("a", 1, 1))
    store.save("a", 1, result)
    store.save("a", 1, deepcopy(result))
    assert len(store.read("a", 1)) == 1
    assert store.read("a", 2) == store.read("b", 1) == []
    corrected = {**result, "correction_reason": "fixture_verified_source_revision"}
    store.save("a", 1, corrected)
    assert store.read("a", 1)[0]["previous_hash"]
    with store.connection("a") as db:
        assert db.execute("select count(*) from results").fetchone()[0] == 2
    with store.connection("a", write=True) as db:
        with pytest.raises(sqlite3.IntegrityError): db.execute("delete from results")
    a = record("a", 1, "entry_fill_verified", operation="same", fill_id="1")
    assert record("a", 1, "entry_fill_verified", operation="same", fill_id="1") == a
    assert len(page("a", 1)["items"]) == 1


def test_cache_backup_preserves_registration_selection_and_result_history(tmp_path, monkeypatch):
    from tools.backup_market_cache import backup, verify
    cache = tmp_path/"cache"
    monkeypatch.setattr(store, "default_cache_root", lambda: cache)
    result = simulate(entry(), [quote(.1, 0), quote(.4, -26), quote(.7, -30)])
    result.update(store.enroll("owner", 1, 1))
    store.selection("owner", 1, ratio=2)
    store.save("owner", 1, result)
    with pytest.raises(ValueError, match="experiment_mismatch"):
        store.save("owner", 1, {**result, "experiment_id": "different-model"})
    store.save("owner", 1, {**result, "correction_reason": "test_verified_revision"})
    output = tmp_path/"backup"
    manifest = backup(cache, output)
    assert verify(output) == manifest
    restored = output/store.location("owner").relative_to(cache)
    with sqlite3.connect(restored) as db:
        assert db.execute("select count(*) from results").fetchone()[0] == 2
        assert db.execute("select ordinal,phase from enrollments").fetchone() == (1, "exploratory")
        assert db.execute("select ratio from selections").fetchone()[0] == 2
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_retention_does_not_change_live_parameters_or_create_orders():
    feed = DepthFeed()
    state = DepthState(CONTRACT, .2)
    feed.streams[("owner", CONTRACT)] = {"state": state, "touched": 0}
    def forbidden(): raise AssertionError("Existing stream must not make a client")
    feed.retain("owner", CONTRACT, forbidden, NOW, {"imbalance_threshold": .1})
    assert state.threshold == .2
    assert feed.streams[("owner", CONTRACT)]["retain_until"] == NOW


def test_paired_cohort_same_count_not_nine_independent_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    for index in (1, 2):
        e = entry(); e["entry_id"] = index
        result = simulate(e, [quote(.1, 0), quote(.4, -26), quote(.7, -30)] if index == 1 else [])
        result.update(store.enroll("a", 1, index))
        store.save("a", 1, result)
    result = store.comparison("a", 1, [])
    assert result["valid_entries"] == 1 and result["excluded_entries"] == 1
    assert all(s["dollars"]["count"] == 1 for s in result["series"])
    assert result["coverage_rate"] == .5
    assert result["progress_to_100"] == 2  # Missing observations still consumed a registration.
    assert metrics([{"net_pnl_usd": 1, "session": "today"}])["interval_95"] is None


def test_unfilled_attempts_are_not_actual_trades_or_pending_research(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    rows = [{"entry_id": 1, "strategy_class": "fixed", "attempt_status": "blocked",
             "stats_status": "awaiting_fill", "experiment_id": EXPERIMENT_ID},
            {"entry_id": 2, "strategy_class": "fixed", "attempt_status": "submission_unknown",
             "stats_status": "awaiting_fill", "experiment_id": EXPERIMENT_ID}]
    result = store.comparison("a", 1, rows)
    assert result["pending_entries"] == 0 and result["nonfilled_attempts"] == 1
    assert next(r for r in result["actual"] if r["strategy_class"] == "fixed")["incomplete"] == 1
    rows[0]["entry_at"] = None
    assert store.comparison("a", 1, rows, start="2026-01-01")["nonfilled_attempts"] == 0


def test_preentry_full_snapshot_recovers_book_but_postentry_gap_is_excluded(monkeypatch):
    from hashlib import sha256
    from test_topbot_evidence import book
    rows = [{"metadata": {"owner_hash": sha256(b"owner").hexdigest(), "contract_id": CONTRACT}}]
    for index, second in [(1, -2), (3, -.5), (4, .1), (6, .5), (7, .8)]:
        at = NOW+timedelta(seconds=second)
        rows.append({"event_index": index, "received_at": at.isoformat(), "event": book(at, index)})
    monkeypatch.setattr(store, "recorded_batches", lambda path: iter(rows))
    path = list(store.quotes("fixture", "owner", CONTRACT, NOW, NOW+timedelta(seconds=2)))
    assert path[0]["bid"] == 20000 and "gap" not in path[0]
    assert path[1]["gap"] == path[2]["gap"] == "capture_index_gap"


def test_candidate_cannot_be_reselected_after_seeing_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    assert store.selection("a", 1) is None
    selected = store.selection("a", 1, ratio=2)
    assert store.selection("a", 1, ratio=2) == selected
    with pytest.raises(ValueError, match="prespecified"): store.selection("a", 1, ratio=3)
    assert store.selection("a", 2) is None
    for index in range(1, 102):
        assignment = store.enroll("a", 1, index)
    assert assignment["phase"] == "holdout"
    assert store.enroll("a", 1, 1)["phase"] == "exploratory"
    result = simulate(entry(), [quote(.1, 0), quote(.4, -26), quote(.7, -30)])
    result.update(assignment)
    result["entry_id"] = 101
    result["entry_at"] = (utc(selected["registered_at"])-timedelta(seconds=1)).isoformat()
    store.save("a", 1, result)
    assert store.comparison("a", 1, [], phase="holdout")["valid_entries"] == 0
