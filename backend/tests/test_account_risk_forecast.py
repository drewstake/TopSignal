import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base, get_db
from app.models import Account, BotBacktest, ProjectXTradeEvent
from app.risk_forecast_routes import router, risk_forecast_user_id
from app.risk_forecast_schemas import RiskForecastRequest
from app.services import account_risk_forecast as risk
from app.services.topbot_backtest_jobs import BacktestRequest, Job, Jobs
from test_market_data_workspace import request as http_request

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 10, 26, 18, tzinfo=timezone.utc)


def payload(**values):
    return RiskForecastRequest(**dict(starting_balance=1000, loss_buffer=500, **values))


def baseline(days, **values):
    return risk.simulate(days, payload(**values), seed=42, paths=100)[1]


def test_floor_equality_stops_paths_before_later_recovery():
    config = RiskForecastRequest(starting_balance=1000, loss_buffer=100, horizon_days=20)
    result = risk.simulate([[-100, 500]]*3, config, 42, paths=100)[1]
    assert result["account_breach"]["probability"] == 1
    assert result["ending_balance"] == {"p10": 900, "p50": 900, "p90": 900}
    assert result["max_drawdown"]["p90"] == 100
    assert all(point["p50"] == 900 for point in result["fan"][1:])


def test_daily_stop_skips_later_trades_and_resets_each_day():
    result = baseline([[-60, -60, 500]]*3, daily_loss_limit=100, horizon_days=3)
    assert result["daily_limit_hit"]["probability"] == 1
    assert result["account_breach"]["probability"] == 0
    assert result["ending_balance"]["p50"] == 640
    assert result["max_drawdown"]["p95"] == 360


def test_account_failure_precedes_and_prevents_later_daily_hit():
    config = RiskForecastRequest(starting_balance=1000, loss_buffer=50, daily_loss_limit=100, horizon_days=2)
    result = risk.simulate([[-60, -60, 500]]*3, config, 42, paths=100)[1]
    assert result["account_breach"]["probability"] == 1
    assert result["daily_limit_hit"]["probability"] == 0
    assert result["ending_balance"]["p50"] == 940


def test_trade_close_trailing_differs_from_end_of_day_and_respects_cap():
    common = dict(starting_balance=1000, loss_buffer=100, trailing_distance=100, horizon_days=1)
    def run(mode, **extra):
        return risk.simulate([[100, -100]]*3, RiskForecastRequest(**common, trailing_mode=mode, **extra), 42, paths=100)[1]
    assert run("trade_close")["account_breach"]["probability"] == 1
    assert run("end_of_day")["account_breach"]["probability"] == 0
    assert run("trade_close", floor_cap=900)["account_breach"]["probability"] == 0


def test_end_of_day_floor_ratchets_and_never_falls():
    result = baseline([[100], [-150], [100]], trailing_mode="end_of_day", trailing_distance=500, horizon_days=3)
    assert result["ending_balance"]["p50"] == 1050
    assert result["max_drawdown"]["p90"] == 150
    config = RiskForecastRequest(starting_balance=1000, loss_buffer=100, trailing_mode="end_of_day", trailing_distance=100, horizon_days=3)
    result = risk.simulate([[100], [-150], [100]], config, 0, paths=50)[1]
    assert result["ending_balance"]["p50"] == 950
    assert result["account_breach"]["probability"] == 1


def test_costs_and_exposure_scaling_reconcile():
    result = risk.simulate([[20, 30]]*3, payload(horizon_days=2, extra_cost_per_trade=5), 42, paths=100)
    assert [row["ending_balance"]["p50"] for row in result] == [1040, 1080, 1120]
    assert result == risk.simulate([[20, 30]]*3, payload(horizon_days=2, extra_cost_per_trade=5), 42, paths=100)
    assert result[0]["account_breach"]["simulation_interval"][1] > 0  # zero hits is not zero uncertainty


@pytest.mark.parametrize("mode", ["fixed", "end_of_day", "trade_close"])
def test_vectorized_simulator_matches_independent_sequential_account_ledger(mode):
    days = [[150, -100, -120, 20], [-40, 5], [30], [-250, 400], [70, -60, 50]]
    config = payload(horizon_days=7, daily_loss_limit=150, trailing_mode=mode,
                     trailing_distance=None if mode == "fixed" else 500,
                     floor_cap=None if mode == "fixed" else 800)
    paths, seed = 40, 12
    starts = np.random.default_rng(seed).integers(0, len(days)-2, size=(paths, 3))
    selections = (starts[:, :, None]+np.arange(3)).reshape(paths, -1)[:, :7]
    for forecast in risk.simulate(days, config, seed, paths=paths):
        endings, drawdowns, failures, daily_hits = [], [], [], []
        for selection in selections:
            equity, floor, peak, drawdown, failed, daily_hit = 1000., 500., 1000., 0., False, False
            for selected in selection:
                pnl = 0.
                for net in days[selected]:
                    net *= forecast["multiplier"]
                    equity += net
                    pnl += net
                    peak = max(peak, equity)
                    drawdown = max(drawdown, peak-equity)
                    if mode == "trade_close":
                        floor = max(floor, min(800., equity-500.))
                    if pnl <= -150:
                        daily_hit = True
                    if equity <= floor:
                        failed = True
                        break
                    if pnl <= -150:
                        break
                if mode == "end_of_day":
                    floor = max(floor, min(800., equity-500.))
                if failed:
                    break
            endings.append(equity); drawdowns.append(drawdown); failures.append(failed); daily_hits.append(daily_hit)
        assert forecast["account_breach"]["probability"] == np.mean(failures)
        assert forecast["daily_limit_hit"]["probability"] == np.mean(daily_hits)
        assert forecast["ending_balance"]["p50"] == round(float(np.median(endings)), 2)
        assert forecast["max_drawdown"]["p95"] == round(float(np.quantile(drawdowns, .95)), 2)


def test_insufficient_sample_has_no_probabilities_and_ready_sample_is_bounded():
    outcomes = [risk.Outcome(NOW-timedelta(days=i+1), (NOW-timedelta(days=i+1)).date().isoformat(), float(i-10))
                for i in range(20)]
    result = risk.forecast(outcomes, payload(), "Sample", [], NOW)
    assert result["status"] == "insufficient_data" and result["scenarios"] == []
    outcomes = sorted(outcomes*2, key=lambda t: t.timestamp)
    result = risk.forecast(outcomes, payload(), "Sample", [], NOW)
    assert result["status"] == "ready" and len(result["scenarios"]) == 3
    assert len(result["scenarios"][0]["fan"]) == 21
    assert "trades" not in result and "candles" not in result


@pytest.mark.parametrize("fields", [{"horizon_days": 61}, {"horizon_days": True}, {"loss_buffer": 0},
    {"starting_balance": math.inf}, {"daily_loss_limit": -1}, {"trailing_mode": "end_of_day"},
    {"trailing_distance": 5}, {"source_id": "../../data"}, {"source_id": "saved:9999999999999999999"},
    {"trailing_mode": "trade_close", "trailing_distance": 100}, {"extra_cost_per_trade": -1}])
def test_rejects_invalid_inputs(fields):
    with pytest.raises(ValidationError):
        RiskForecastRequest(**({"starting_balance": 1000, "loss_buffer": 500} | fields))


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Account.__table__, BotBacktest.__table__, ProjectXTradeEvent.__table__])
    session = sessionmaker(bind=engine)()
    session.add_all([Account(user_id=OWNER, provider="projectx", external_id="123", balance=1000),
                     Account(user_id=OTHER, provider="projectx", external_id="456", balance=9999)])
    session.commit()
    monkeypatch.setattr(risk, "jobs", Jobs())
    yield session
    session.close(); engine.dispose()


def event(db, **changes):
    data = dict(user_id=OWNER, account_id=123, contract_id="CON.F.US.MNQ.Z26", symbol="MNQ", side="SELL", size=1,
                price=20000, trade_timestamp=NOW-timedelta(days=2), pnl=20, fees=2, commissions=1,
                fee_scope="per_side", order_id=str(db.query(ProjectXTradeEvent).count()+1))
    db.add(ProjectXTradeEvent(**(data | changes))); db.commit()


def saved(db, **changes):
    result = {"trades": [{"exit_timestamp": (NOW-timedelta(days=2)).isoformat(), "net_pnl": -25}],
              "metrics": {"trade_count": 1, "net_pnl": -25}}
    data = dict(user_id=OWNER, account_id=123, engine_version="test", strategy_type="topbot", contract_id="MNQ",
                timeframe_unit="minute", timeframe_unit_number=5, requested_start=NOW-timedelta(days=3), requested_end=NOW,
                actual_start=NOW-timedelta(days=3), actual_end=NOW, starting_balance=1000,
                tick_size=.25, tick_value=.5, bar_count=100, input_fingerprint="abc", config_snapshot={},
                assumptions_snapshot={}, result_snapshot=result)
    row = BotBacktest(**(data | changes)); db.add(row); db.commit()
    return row


def test_recorded_source_scopes_owner_fees_voids_dates_and_open_legs(db):
    event(db)  # 20 - 2*2 - 1 = 15
    event(db, fee_scope="round_turn", pnl=-30)  # -30 - 2 - 1 = -33
    event(db, pnl=None)
    event(db, raw_payload={"voided": True}, pnl=-999)
    event(db, user_id=OTHER, pnl=999)
    event(db, account_id=456, pnl=999)
    event(db, trade_timestamp=NOW, pnl=999)
    before = db.query(ProjectXTradeEvent).count()
    outcomes, _, warnings = risk.load_outcomes(db, OWNER, 123, payload(), NOW)
    assert [item.net for item in outcomes] == [15, -33]
    assert any("Excluded 1" in item for item in warnings)
    assert db.query(ProjectXTradeEvent).count() == before
    with pytest.raises(LookupError):
        risk.load_outcomes(db, OWNER, 456, payload(), NOW)


def test_saved_source_scopes_account_and_rejects_truncated_detail(db):
    row = saved(db)
    outcomes, _, _ = risk.load_outcomes(db, OWNER, 123, payload(source_id=f"saved:{row.id}"), NOW)
    assert [t.net for t in outcomes] == [-25]
    foreign = saved(db, user_id=OTHER)
    wrong_account = saved(db, account_id=456)
    for identifier in (foreign.id, wrong_account.id):
        with pytest.raises(LookupError):
            risk.load_outcomes(db, OWNER, 123, payload(source_id=f"saved:{identifier}"), NOW)
    row.result_snapshot = {"trades": [], "metrics": {"trade_count": 1, "net_pnl": -25}}
    db.commit()
    with pytest.raises(ValueError, match="incomplete"):
        risk.load_outcomes(db, OWNER, 123, payload(source_id=f"saved:{row.id}"), NOW)
    assert [source["id"] for source in risk.sources(db, OWNER, 123)["sources"]] == ["trades", f"saved:{row.id}"]


def test_temporary_runs_are_owner_scoped_completed_and_expire(db):
    backtest = saved(db)
    job = Job(OWNER, BacktestRequest(start=NOW-timedelta(days=3), end=NOW), status="complete", result=backtest.result_snapshot)
    foreign = Job(OTHER, job.request, status="complete", result=job.result)
    risk.jobs.items = {job.id: job, foreign.id: foreign}
    assert len(risk.jobs.completed(OWNER)) == 1
    result, _, warnings = risk.load_outcomes(db, OWNER, 123, payload(source_id=f"job:{job.id}"), NOW)
    assert result[0].net == -25 and any("not this account" in item for item in warnings)
    with pytest.raises(LookupError):
        risk.load_outcomes(db, OWNER, 123, payload(source_id=f"job:{foreign.id}"), NOW)
    job.touched -= 3601
    assert not risk.jobs.completed(OWNER)
    with pytest.raises(LookupError):
        risk.load_outcomes(db, OWNER, 123, payload(source_id=f"job:{job.id}"), NOW)


def test_http_routes_validation_tenant_isolation_and_read_only_result(db):
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[risk_forecast_user_id] = lambda: OWNER
    assert http_request(app, "GET", "/api/accounts/123/risk-forecast/sources").status_code == 200
    assert http_request(app, "GET", "/api/accounts/456/risk-forecast/sources").status_code == 404
    assert http_request(app, "POST", "/api/accounts/123/risk-forecast", body={}).status_code == 422
    response = http_request(app, "POST", "/api/accounts/123/risk-forecast", body=payload().model_dump())
    assert response.status_code == 200 and response.json()["status"] == "insufficient_data"


def test_router_fails_closed_without_auth_binding(monkeypatch):
    import app.risk_forecast_routes as routes
    monkeypatch.setattr(routes, "auth_required", lambda: True)
    monkeypatch.setattr(routes, "get_authenticated_user", lambda: None)
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: None
    assert http_request(app, "GET", "/api/accounts/123/risk-forecast/sources").status_code == 401
