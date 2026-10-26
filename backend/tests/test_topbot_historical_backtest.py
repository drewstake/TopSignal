from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.market_data_routes import market_data_user_id, router
from app.services import topbot_backtest as replay
from app.services.topbot_backtest_jobs import BacktestRequest, Job, Jobs
from app.services.probabilistic_strategy import BAR, Candle, Costs
from test_market_data_workspace import request

START = datetime(2026, 10, 19, 13, 35, tzinfo=timezone.utc)


def candles(start=START, count=30):
    return [Candle(start+(i-21)*BAR, 20000., 20001., 19999., 20000.+(i % 2)*.25,
                   100., "CON.F.US.MNQ.Z26", "alice") for i in range(count)]


class BuyingModel:
    def forecast(self, f, costs):
        return {"research_action": "BUY", "target_points": 6., "training_paths": 300,
                "forecasts": {"BUY": {"lower_utility_usd": 2., "expected_net_usd": 4., "uncertainty_penalty_usd": 2., "effective_days": 30.}}}


def run_rows(rows, **kwargs):
    return replay.replay(rows, BuyingModel(), START, START+45*timedelta(minutes=1), 50000, Costs(),
                         cancel=kwargs.get("cancel", Event()), progress=lambda *a: None)


def test_replay_trades_reconcile_prices_fees_equity_and_drawdown():
    result = run_rows(candles())
    assert result["trades"]
    for trade in result["trades"]:
        assert trade["net_pnl"] == pytest.approx((trade["exit_price"]-trade["entry_price"])*2-trade["commission"])
        assert trade["entry_timestamp"] >= START.isoformat()
        assert trade["bars_held"] == 3
    net = sum(t["net_pnl"] for t in result["trades"])
    assert result["metrics"]["net_pnl"] == pytest.approx(net)
    assert result["equity_curve"][-1]["equity"] == pytest.approx(50000+net)
    assert result["metrics"]["max_drawdown_dollars"] == pytest.approx(-net)
    assert all(b["entry_timestamp"] >= a["exit_timestamp"] for a, b in zip(result["trades"], result["trades"][1:]))
    assert sum(result["skipped"].values())+len(result["trades"]) == result["bar_count"]
    assert len(result["entry_diagnostics"]["closest_forecasts"]) <= 5


def test_missing_exposure_fails_instead_of_silently_filtering_losing_trade():
    rows = candles()
    del rows[22]
    with pytest.raises(ValueError, match="Unknown trade outcome"):
        run_rows(rows)


def test_contract_roll_during_exposure_fails():
    from dataclasses import replace
    rows = candles()
    rows[22] = replace(rows[22], contract_id="CON.F.US.MNQ.H27")
    with pytest.raises(ValueError, match="contract roll"):
        run_rows(rows)


def test_both_brackets_hit_uses_stop_first():
    from dataclasses import replace
    rows = candles()
    rows[21] = replace(rows[21], low=19990., high=20020.)
    result = run_rows(rows)
    assert result["trades"][0]["exit_reason"] == "stop"
    assert result["trades"][0]["net_pnl"] < 0


def test_cancel_returns_no_partial_results():
    cancel = Event(); cancel.set()
    with pytest.raises(replay.Cancelled):
        run_rows(candles(), cancel=cancel)


def test_daily_stop_risk_blocks_further_entries():
    from dataclasses import replace
    rows = candles(count=100)
    rows[21:] = [replace(row, low=19990.) for row in rows[21:]]
    result = replay.replay(rows, BuyingModel(), START, START+timedelta(hours=6), 50000, Costs(), cancel=Event(), progress=lambda *a: None)
    assert 0 < len(result["trades"]) < 30
    assert result["skipped"]["daily_risk_limit"] > 0
    assert result["metrics"]["net_pnl"] > -250


def test_training_never_uses_selected_test_period(monkeypatch):
    from app.services.probabilistic_protocol import protocol
    monkeypatch.setattr(replay, "protocol", lambda: {**protocol(), "minimum_training_examples": 2})
    rows = []
    for offset in (4, 3):  # Prior Thursday and Friday, full 09:30–15:45 ET sessions.
        day = (START-timedelta(days=offset)).replace(hour=12, minute=0)
        rows.extend(candles(start=day+21*BAR, count=96))
    model, days = replay.fit_prior_model(rows, START, training_sessions=2)
    altered, same_days = replay.fit_prior_model(rows+candles(count=200), START, training_sessions=2)
    assert model.to_dict() == altered.to_dict()
    assert days == same_days
    assert model.trained_through < START-timedelta(minutes=15)


def test_insufficient_training_is_explicit():
    with pytest.raises(ValueError, match="240 complete prior"):
        replay.fit_prior_model(candles(), START)


@pytest.mark.parametrize("change", [{"starting_balance": float("nan")}, {"slippage_ticks": 1.5},
    {"end": START}, {"end": START+timedelta(days=367)}, {"start": START.replace(tzinfo=None)}, {"unexpected": True}])
def test_request_rejects_invalid_or_unbounded_inputs(change):
    with pytest.raises(ValidationError):
        BacktestRequest.model_validate({"start": START, "end": START+timedelta(days=1), **change})


def test_job_routes_are_owner_scoped_and_cancel_cooperatively(monkeypatch):
    from app import market_data_routes
    manager = Jobs()
    payload = BacktestRequest(start=START, end=START+timedelta(days=1))
    job = Job("alice", payload)
    manager.items[job.id] = job
    monkeypatch.setattr(market_data_routes, "backtest_jobs", manager)
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[market_data_user_id] = lambda: "bob"
    path = f"/api/market-data/databento/backtests/{job.id}"
    assert request(app, "GET", path).status_code == 404
    assert request(app, "POST", path+"/cancel").status_code == 404
    assert not job.cancel.is_set()
    app.dependency_overrides[market_data_user_id] = lambda: "alice"
    assert request(app, "GET", path).json()["status"] == "running"
    assert request(app, "POST", path+"/cancel").json()["cancel_requested"] is True
    with pytest.raises(RuntimeError, match="already running"):
        manager.start("alice", payload)


def test_failed_worker_does_not_expose_internal_paths(monkeypatch):
    manager = Jobs()
    job = Job("alice", BacktestRequest(start=START, end=START+timedelta(days=1)))
    manager.items[job.id] = job
    def fail(*a, **kw):
        raise OSError("private-cache-path")
    monkeypatch.setattr(replay, "run", fail)
    manager._run(job)
    output = manager.get("alice", job.id)
    assert output["status"] == "failed"
    assert "private-cache-path" not in output["error"]
    assert output["result"] is None


def test_completed_job_retains_only_result_not_candle_inputs(monkeypatch):
    manager = Jobs()
    job = Job("alice", BacktestRequest(start=START, end=START+timedelta(days=1)))
    manager.items[job.id] = job
    monkeypatch.setattr(replay, "run", lambda *a, **kw: {"trades": [], "metrics": {"net_pnl": 0}})
    manager._run(job)
    assert manager.get("alice", job.id)["result"] == {"trades": [], "metrics": {"net_pnl": 0}}
    assert not hasattr(job, "candles")


def test_runner_reads_exact_local_candles_and_returns_bounded_result(monkeypatch):
    rows = candles()
    source = [SimpleNamespace(candle_timestamp=r.timestamp, open_price=r.open, high_price=r.high,
        low_price=r.low, close_price=r.close, volume=r.volume, source_raw_symbol="MNQZ6", source_file_sha256="source") for r in rows]
    calls = []
    def open_candles(**kw):
        calls.append(kw)
        return source
    monkeypatch.setattr(replay, "get_default_databento_cache", lambda: SimpleNamespace(
        series_bounds=lambda *a: (START-timedelta(days=1000), START+timedelta(hours=1)), open_candles=open_candles))
    model = BuyingModel()
    model.to_dict = lambda: {"test": "model"}
    model.samples = [None]*300
    model.pool_mix = 0.
    model.trained_through = START-timedelta(days=1)
    monkeypatch.setattr(replay, "fit_prior_model", lambda *a: (model, ["2025-01-01", "2026-10-16"]))
    output = replay.run(BacktestRequest(start=START, end=START+timedelta(minutes=45), model_source="historical_refit"), owner="alice", cancel=Event(), progress=lambda *a: None)
    assert calls[0]["user_id"] == "alice"
    assert (calls[0]["unit"], calls[0]["unit_number"]) == ("minute", 5)
    assert output["trades"] and output["assumptions"]["live_order_routing"] == "disabled"
    assert "samples" not in output["config_snapshot"]
    assert "candles" not in output and "model" not in output
    # Public result shape remains compatible with the existing ledger/analytics.
    from app.bot_schemas import BotBacktestOut
    assert BotBacktestOut.model_validate(output).metrics.trade_count == len(output["trades"])


def test_start_route_binds_owner_and_validates_before_dispatch(monkeypatch):
    from app import market_data_routes
    calls = []
    def start(owner, payload):
        calls.append((owner, payload))
        return {"id": "test-job", "status": "running"}
    monkeypatch.setattr(market_data_routes, "backtest_jobs", SimpleNamespace(start=start))
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[market_data_user_id] = lambda: "alice"
    data = {"start": START.isoformat(), "end": (START+timedelta(days=1)).isoformat()}
    assert request(app, "POST", "/api/market-data/databento/backtests", body=data).status_code == 202
    assert calls[0][0] == "alice"
    assert request(app, "POST", "/api/market-data/databento/backtests", body={**data, "slippage_ticks": -1}).status_code == 422
    assert len(calls) == 1


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_real_model_entries_match_current_topbot_adapter(tmp_path, side):
    """Use the actual Bayesian model and current adapter, not BuyingModel."""
    import json
    from app.services import topbot_mathematical
    from app.services.probabilistic_strategy import PathModel
    from test_topbot_mathematical import install
    from test_probabilistic_strategy import NOW, as_rows, candles as real_candles
    path = install(tmp_path, side=side)  # Synthetic evidence under pytest tmp only.
    model = PathModel.from_dict(json.loads(path.read_text())["model"])
    rows = real_candles(25)
    signal = topbot_mathematical.evaluate(as_rows(rows[:21]), as_of=NOW, root=tmp_path)
    result = replay.replay(rows, model, NOW, NOW+4*BAR, 50000, Costs(), cancel=Event(), progress=lambda *a: None)
    assert signal.action == side
    trade = result["trades"][0]
    assert trade["side"] == ("long" if side == "BUY" else "short")
    assert trade["lower_utility_usd"] == pytest.approx(signal.raw_payload["probabilistic_research"]["forecasts"][side]["lower_utility_usd"])
    assert abs(trade["stop_price"]-trade["entry_price"]) == abs(signal.raw_payload["stop_loss"]-signal.price)
    assert abs(trade["target_price"]-trade["entry_price"]) == abs(signal.raw_payload["take_profit"]-signal.price)


@pytest.mark.parametrize("fault,reason", [("edge", "no_model_edge"), ("support", "insufficient_training_support"), ("volatility", "volatility_above_risk_cap")])
def test_current_adapter_and_replay_hold_for_same_mathematical_gates(tmp_path, fault, reason):
    import json
    from dataclasses import replace
    from app.services import topbot_mathematical
    from app.services.probabilistic_strategy import PathModel
    from test_topbot_mathematical import install
    from test_probabilistic_strategy import NOW, as_rows, candles as real_candles, fitted
    model = fitted("bayesian_cells_v1", n=20 if fault == "support" else 360, losing=70 if fault == "edge" else 0)
    path = install(tmp_path, model=model)
    model = PathModel.from_dict(json.loads(path.read_text())["model"])
    rows = real_candles(25)
    if fault == "volatility":
        rows = [replace(r, open=r.open+i*20, high=r.high+i*20, low=r.low+i*20, close=r.close+i*20) for i, r in enumerate(rows)]
    signal = topbot_mathematical.evaluate(as_rows(rows[:21]), as_of=NOW, root=tmp_path)
    result = replay.replay(rows, model, NOW, NOW+4*BAR, 50000, Costs(), cancel=Event(), progress=lambda *a: None)
    assert signal.action == "HOLD" and not result["trades"]
    assert result["skipped"][reason] > 0
    assert sum(result["skipped"].values()) == result["bar_count"]
