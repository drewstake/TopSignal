"""Selected mathematical decisions and preserved execution boundaries."""
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from app.services import bot_service, topbot_mathematical as strategy
from app.services.probabilistic_shadow import model_path, scope_hash
from app.services.topbot import TOPBOT_SETTINGS, prepare_topbot
from test_probabilistic_strategy import NOW, CONTRACT, as_rows, candles, fitted
from test_bot_execution_safety import (
    USER_A, RecordingClient, _add_account_and_config, _patch_actionable_signal,
    db_session, open_exchange_session,
)


def install(root, *, side="BUY", model=None):
    model = model or fitted("bayesian_cells_v1")
    model = replace(model, samples=tuple(replace(s, path=tuple(tuple(v * 5 for v in bar) for bar in s.path)) for s in model.samples))
    if side == "SELL":
        model = replace(model, samples=tuple(replace(s, path=tuple(
            (-o, -lo, -hi, -c) for o, hi, lo, c in s.path)) for s in model.samples))
    from app.services.probabilistic_protocol import digest, implementation_sha, protocol
    from app.services.probabilistic_artifacts import experiment_path
    from app.services.probabilistic_artifacts import OFFLINE_CHECKS
    report = {"mode": "development", "status": "offline_passed", "protocol_sha256": digest(protocol()),
              "implementation_sha256": implementation_sha(),
              "pooled": {model.version: {"acceptance_checks": dict.fromkeys(OFFLINE_CHECKS, True), "frozen_pool_mix": model.pool_mix}}}
    experiment_id = digest(report)
    report["experiment_id"] = experiment_id
    record = experiment_path(root, experiment_id)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(report), encoding="utf-8")
    path = model_path("owner", CONTRACT, False, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"owner_hash": scope_hash("owner"), "root_symbol": "MNQ",
        "roll_policy": protocol()["roll_policy"], "data_live": False, "model": model.to_dict(),
        "experiment_id": experiment_id, "experiment_sha256": digest(report),
        "protocol_sha256": digest(protocol()), "implementation_sha256": implementation_sha(),
        "validation_status": "offline_passed", "review": {"reviewed_by": "synthetic test fixture", "model_version": model.version}}), encoding="utf-8")
    return path


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_real_mathematical_forecast_is_the_signal_not_a_shadow(tmp_path, side):
    install(tmp_path, side=side)
    result = strategy.evaluate(as_rows(candles(21)), as_of=NOW, root=tmp_path)
    assert result.action == side
    assert result.raw_payload["probabilistic_research"]["research_action"] == side
    assert result.raw_payload["strategy_revision"] == strategy.REVISION
    assert abs(result.raw_payload["stop_loss"] - result.price) == 4
    assert abs(result.raw_payload["take_profit"] - result.price) == 6
    assert result.raw_payload["target_position_qty"] == (1 if side == "BUY" else -1)
    assert result.raw_payload["live_routing_allowed"] is False
    assert result.raw_payload["validation_status"] == "offline_passed"
    assert "ema" not in result.raw_payload and "session_vwap" not in result.raw_payload


@pytest.mark.parametrize("hours", [-8, 6, 12])
def test_mathematical_model_holds_outside_regular_session(tmp_path, hours):
    install(tmp_path)
    offset = timedelta(hours=hours)
    rows = as_rows([replace(row, timestamp=row.timestamp + offset) for row in candles(21)])
    result = strategy.evaluate(rows, as_of=NOW + offset, root=tmp_path)
    assert result.action == "HOLD"
    assert result.raw_payload["hold_reason"] in {"outside_research_session", "entry_too_close_to_session_close"}


@pytest.mark.parametrize("fault", ["missing", "stale", "owner", "contract", "gap", "partial", "volume", "few_paths", "bad_payoff", "other_model"])
def test_unusable_model_or_candles_hold_without_legacy_fallback(tmp_path, fault):
    rows = as_rows(candles(21))
    model = fitted("bayesian_cells_v1")
    if fault == "few_paths": model = replace(model, samples=model.samples[-20:])
    if fault == "bad_payoff": model = fitted("bayesian_cells_v1", losing=70)
    if fault == "other_model": model = replace(model, version="kernel_paths_v1")
    if fault != "missing": install(tmp_path, model=model)
    if fault == "owner": rows[-1].user_id = "other"
    if fault == "contract": rows[-1].contract_id = "CON.F.US.MNQ.Z26"
    if fault == "gap": rows[10].candle_timestamp += timedelta(minutes=5)
    if fault == "partial": rows[10].is_partial = True
    if fault == "volume": rows[10].volume = None
    result = strategy.evaluate(rows, as_of=NOW+timedelta(minutes=3) if fault == "stale" else NOW, root=tmp_path)
    assert result.action == "HOLD"
    assert result.reason.startswith("NO TRADE:")
    assert "target_position_qty" not in result.raw_payload


def test_scope_is_bound_to_request_and_new_preset_cannot_weaken_model_limits(tmp_path):
    install(tmp_path)
    assert strategy.evaluate(as_rows(candles(21)), owner="other", contract_id=CONTRACT,
                             as_of=NOW, root=tmp_path).action == "HOLD"
    normalized = bot_service._normalize_strategy_params("topbot_adaptive", dict(
        strategy.RULES, minimum_training_paths=2, position_size=10, routing_policy="live"))
    assert normalized == strategy.RULES
    assert TOPBOT_SETTINGS["strategy_params"] == strategy.RULES
    assert TOPBOT_SETTINGS["max_daily_loss"] == 250
    assert TOPBOT_SETTINGS["order_size"] == 1
    removed = {"revision": "mnq_ema_vwap_pullback_v6_all_sessions", "ema_period": 20, "stop_points": 50.0}
    assert bot_service._normalize_strategy_params("topbot_adaptive", removed) == strategy.RULES


def test_dispatch_selects_math_and_holds_removed_ema_vwap_configs(monkeypatch):
    monkeypatch.setattr(strategy, "evaluate", lambda rows, **kw: "math")
    assert bot_service.evaluate_topbot_adaptive([], strategy_params=strategy.RULES) == "math"
    for stored in ({"revision": "mnq_ema_vwap_pullback_v6_all_sessions"}, {}, None):
        held = bot_service.evaluate_topbot_adaptive([], strategy_params=stored)
        assert held.action == "HOLD"
        assert "EMA/VWAP strategy was removed" in held.reason
        assert held.raw_payload["retired_strategy"] is True


@pytest.mark.parametrize("offset", [-1, 1])
def test_receipt_time_prevents_using_provisional_or_future_candle_values(tmp_path, offset):
    install(tmp_path)
    rows = as_rows(candles(21))
    rows[-1].fetched_at = NOW + timedelta(seconds=offset)
    result = strategy.evaluate(rows, as_of=NOW, root=tmp_path)
    assert result.action == "HOLD" and "received before its close or after" in result.reason
    assert result.raw_payload["probabilistic_research"]["forecasts"] is None
    assert result.raw_payload["probabilistic_research"]["reasons"][0] == result.reason


def test_ordinary_backtest_cannot_replay_a_current_mathematical_artifact():
    from app.services import bot_backtesting
    from app.bot_schemas import BotBacktestIn
    from app.models import BotConfig
    config = BotConfig(strategy_type="topbot_adaptive", strategy_params=dict(strategy.RULES))
    with pytest.raises(bot_backtesting.BacktestConfigurationError, match="chronological"):
        bot_backtesting._config_for_backtest_request(config, BotBacktestIn(strategy_type="topbot_adaptive", instrument="MNQ"))


@pytest.mark.parametrize("operation", ["prepare", "start", "evaluate"])
def test_live_entry_points_require_enabled_worker_before_provider_or_run_changes(db_session, monkeypatch, operation):
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ENABLED", "false")
    account, config = _add_account_and_config(db_session, execution_mode="live", enabled=False)
    config.strategy_type = "topbot_adaptive"
    config.strategy_params = dict(strategy.RULES)
    db_session.commit()
    client = RecordingClient()
    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", lambda *a, **k: pytest.fail("no provider fetch"))
    with pytest.raises(ValueError, match="requires the live worker"):
        if operation == "prepare":
            prepare_topbot(db_session, user_id=USER_A, account_id=config.account_id, dry_run=False, contract_id=CONTRACT)
        elif operation == "start":
            bot_service.start_bot_run(db_session, user_id=USER_A, bot_config_id=config.id,
                                     client=client, dry_run=False, confirm_live_order_routing=True)
        else:
            bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account,
                                            client=client, dry_run=False, confirm_live_order_routing=True)
    assert config.enabled is False
    assert client.place_order_calls == []


def test_selected_forecast_reaches_dry_run_router_and_api_without_second_model_read(db_session, monkeypatch, open_exchange_session, tmp_path):
    from app.bot_schemas import BotEvaluationOut
    account, config = _add_account_and_config(db_session)
    config.strategy_type = "topbot_adaptive"
    config.strategy_params = dict(strategy.RULES)
    config.trading_start_time, config.trading_end_time = "09:30", "15:45"
    monkeypatch.setattr(bot_service, "_is_inside_trading_session", lambda *a, **kw: False)
    monkeypatch.setattr("app.services.topbot_session.entry_boundary_reason", lambda *a, **kw: None)
    db_session.flush()
    install(tmp_path)
    mathematical = strategy.evaluate(as_rows(candles(21)), as_of=NOW, root=tmp_path)
    analysis_builder = bot_service.build_bot_market_analysis
    _patch_actionable_signal(monkeypatch)
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", analysis_builder)
    fetch = bot_service.fetch_candles_and_evaluate_strategy
    def selected_fetch(*args, **kwargs):
        rows, baseline = fetch(*args, **kwargs)
        return rows, replace(mathematical, candle_timestamp=baseline.candle_timestamp, price=baseline.price)
    monkeypatch.setattr(bot_service, "fetch_candles_and_evaluate_strategy", selected_fetch)
    monkeypatch.setattr("app.services.probabilistic_shadow.explain_shadow", lambda **k: pytest.fail("must reuse decision forecast"))
    client = RecordingClient()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=client, dry_run=True)
    assert result.status == "dry_run_attempt" and result.decision.action == "BUY"
    response = BotEvaluationOut.model_validate(bot_service.serialize_evaluation(result)).model_dump(mode="json")
    explanation = response["analysis"]["bot_decision"]
    assert explanation["strategy"]["name"].startswith("TopBot Mathematical")
    assert explanation["probabilistic_research"]["research_action"] == "BUY"
    assert client.place_order_calls == []
