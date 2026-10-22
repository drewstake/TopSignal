"""Synthetic safety checks, not validation evidence for the trading model."""
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from app.services import probabilistic_observation as obs
from app.services.probabilistic_protocol import digest, implementation_sha, protocol
from app.services.probabilistic_shadow import scope_hash, explain_shadow
from app.services import topbot_mathematical as strategy
from app.services.bot_execution_safety import live_execution_environment_enabled
from test_probabilistic_strategy import NOW, CONTRACT, as_rows, candles, fitted
from test_bot_execution_safety import db_session, open_exchange_session


@pytest.fixture
def observation(monkeypatch, tmp_path):
    monkeypatch.setenv(obs.FLAG, "true")
    monkeypatch.setenv("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "false")
    model = fitted("bayesian_cells_v1")
    # Deliberately stale model with strong synthetic outcomes tests the routing barrier.
    samples = []
    for s in model.samples:
        t = s.features.decision_at - timedelta(days=100)
        samples.append(replace(s, features=replace(s.features, decision_at=t, day=t.date().isoformat()),
            label_end=s.label_end-timedelta(days=100), path=tuple(tuple(v*5 for v in bar) for bar in s.path)))
    model = replace(model, samples=tuple(samples))
    artifact = {"purpose": "dry_run_observation_only", "validation_status": "unvalidated", "routing_allowed": False,
        "owner_hash": scope_hash("owner"), "root_symbol": "MNQ", "roll_policy": protocol()["roll_policy"],
        "data_live": False, "protocol_sha256": digest(protocol()), "implementation_sha256": implementation_sha(),
        "experiment_id": "synthetic-observation", "model": model.to_dict()}
    artifact["model_sha256"] = digest(artifact["model"])
    path = obs.observation_path("owner", CONTRACT, False, root=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact))
    return path, artifact


def test_stale_failed_unvalidated_model_can_only_make_explicit_dry_run_forecasts(observation, tmp_path):
    result = strategy.evaluate(as_rows(candles(21)), as_of=NOW, root=tmp_path)
    f = result.raw_payload["probabilistic_research"]
    assert result.action == "BUY"
    assert f["validation_status"] == "unvalidated" and f["observation_only"] is True
    assert f["training_stale"] and f["model_age_days"] > 100
    assert result.raw_payload["live_routing_allowed"] is False
    assert not live_execution_environment_enabled()
    assert f['pool_baseline']['forecasts']
    assert f['pool_baseline']['forecasts']['BUY']['effective_days'] >= 20
    with pytest.raises(ValueError, match="cannot route"):
        strategy.require_live_worker()


@pytest.mark.parametrize("fault", ["flag_off", "wrong_owner", "changed_model", "changed_code", "claimed_validated", "wrong_subscription", "future_labels", "stale_market", "live_gate", "worker_live_gate"])
def test_observation_never_weakens_scope_integrity_fresh_market_or_live_guards(observation, tmp_path, monkeypatch, fault):
    path, a = observation
    rows = as_rows(candles(21)); now = NOW
    if fault == "flag_off": monkeypatch.setenv(obs.FLAG, "false")
    elif fault == "wrong_owner": a["owner_hash"] = "other"
    elif fault == "changed_model": a["model"]["pool_mix"] = 1
    elif fault == "changed_code": a["implementation_sha256"] = "0"*64
    elif fault == "claimed_validated": a["validation_status"] = "passed"
    elif fault == "wrong_subscription": a["data_live"] = True
    elif fault == "future_labels":
        s = a["model"]["samples"][-1]; s["decision_at"] = NOW.isoformat(); s["day"] = NOW.date().isoformat(); s["label_end"]=(NOW+timedelta(minutes=15)).isoformat()
        a["model_sha256"] = digest(a["model"])
    elif fault == "stale_market": now += timedelta(minutes=3)
    elif fault == "live_gate": monkeypatch.setenv("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "true")
    else: monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "true")
    path.write_text(json.dumps(a))
    result = strategy.evaluate(rows, as_of=now, root=tmp_path)
    assert result.action == "HOLD" and not result.raw_payload["live_routing_allowed"]
    if fault != "flag_off": assert not live_execution_environment_enabled()


def test_forecasts_and_hypothetical_outcomes_persist_once_across_repeated_evaluations(observation, tmp_path):
    from app.services.observation_results import collect
    forecast = explain_shadow(candles=as_rows(candles(21)), owner="owner", contract_id=CONTRACT, as_of=NOW, root=tmp_path)
    for _ in range(2):
        collect(as_rows(candles(21)), owner="owner", contract_id=CONTRACT, as_of=NOW, forecast=forecast, root=tmp_path)
    for _ in range(2):
        collect(as_rows(candles(24)), owner="owner", contract_id=CONTRACT, as_of=NOW+timedelta(minutes=15), root=tmp_path)
    records=[json.loads(line) for p in (tmp_path/'probabilistic-v3/observations').rglob('*.jsonl') for line in p.read_text().splitlines()]
    assert [r['event'] for r in records] == ['observation_forecast','observation_outcome']
    assert records[-1]['outcome_status']=='observed'
    assert set(records[-1]['outcomes']) == {'BUY','SELL'}
    assert all(r['validation_status']=='unvalidated' and r['broker_orders'] is False for r in records)


def test_unvalidated_buy_uses_dry_run_router_without_provider_order(observation, tmp_path, db_session, monkeypatch, open_exchange_session):
    from app.services import bot_service
    from test_bot_execution_safety import USER_A, RecordingClient, _add_account_and_config, _patch_actionable_signal
    account, config = _add_account_and_config(db_session)
    config.strategy_type = 'topbot_adaptive'; config.strategy_params = dict(strategy.RULES)
    config.trading_start_time, config.trading_end_time = '09:30', '15:45'
    monkeypatch.setattr('app.services.topbot_session.entry_boundary_reason', lambda *a, **kw: None)
    monkeypatch.setattr(bot_service, '_is_inside_trading_session', lambda *a, **kw: False)
    db_session.flush()
    signal = strategy.evaluate(as_rows(candles(21)), as_of=NOW, root=tmp_path)
    assert signal.action == 'BUY' and signal.raw_payload['validation_status'] == 'unvalidated'
    _patch_actionable_signal(monkeypatch)
    original = bot_service.fetch_candles_and_evaluate_strategy
    def fetch(*a, **kw):
        rows, baseline = original(*a, **kw)
        return rows, replace(signal, candle_timestamp=baseline.candle_timestamp, price=baseline.price)
    monkeypatch.setattr(bot_service, 'fetch_candles_and_evaluate_strategy', fetch)
    client = RecordingClient()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account, client=client, dry_run=True)
    assert result.status == 'dry_run_attempt' and client.place_order_calls == []


def test_incompatible_live_settings_fail_startup(observation, monkeypatch):
    from app.main import _validate_runtime_security_configuration
    monkeypatch.setenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED', 'true')
    with pytest.raises(ValueError, match='explicitly disabled'):
        _validate_runtime_security_configuration()
