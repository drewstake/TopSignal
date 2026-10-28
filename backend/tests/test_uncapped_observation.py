"""Uncapped research stops must agree across fitting, inference and paper admission."""
from dataclasses import replace
import json
import math

import pytest

from app.services import probabilistic_observation as obs, forward_results as paper
from app.services.probabilistic_artifacts import validate_binding
from app.services.probabilistic_protocol import digest
from app.services.probabilistic_strategy import (
    PathModel, UNCAPPED_STOP_POLICY, REGISTERED_STOP_POLICY, features, make_samples,
)
from app.services.topbot_mathematical import evaluate, require_live_worker
from test_probabilistic_strategy import NOW, candles, as_rows, fitted
from test_unvalidated_observation import observation
from test_forward_results import forecast, NOW as PAPER_NOW, CONTRACT


def volatile(n=21):
    return [replace(r, open=r.open+i*30, high=r.high+i*30,
                    low=r.low+i*30, close=r.close+i*30) for i, r in enumerate(candles(n))]


def test_training_and_forecast_use_identical_uncapped_stops():
    rows = volatile(90)
    capped, _ = make_samples(rows, stride=3)
    uncapped, _ = make_samples(rows, stride=3, stop_policy=UNCAPPED_STOP_POLICY)
    assert not capped and len(uncapped) >= 2
    assert all(s.features.stop_points == 53 for s in uncapped)
    model = PathModel('bayesian_cells_v1', tuple(uncapped), stop_policy=UNCAPPED_STOP_POLICY)
    assert PathModel.from_dict(model.to_dict()).stop_policy == UNCAPPED_STOP_POLICY
    with pytest.raises(ValueError, match='training metadata'):
        replace(model, stop_policy=REGISTERED_STOP_POLICY)
    with pytest.raises(ValueError, match='risk cap'):
        features(volatile(), as_of=NOW)
    query = features(volatile(), as_of=NOW, stop_policy=UNCAPPED_STOP_POLICY)
    with pytest.raises(ValueError, match='training policy'):
        fitted().forecast(query)


def install_uncapped(observation):
    path, artifact = observation
    artifact['model']['stop_policy'] = UNCAPPED_STOP_POLICY
    artifact['observation_stop_policy'] = UNCAPPED_STOP_POLICY
    # Synthetic broad-stop samples test actual model loading, not a mocked forecast.
    for sample in artifact['model']['samples']:
        sample['stop'] = 53.
        sample['volatility'] = 30.5
    artifact['model_sha256'] = digest(artifact['model'])
    path.write_text(json.dumps(artifact))
    return artifact


def test_wide_stop_reaches_strategy_and_paper_without_live_routing(observation, monkeypatch, tmp_path):
    monkeypatch.setenv(obs.UNCAPPED_FLAG, 'true')
    install_uncapped(observation)
    result = evaluate(as_rows(volatile()), as_of=NOW, root=tmp_path)
    f = result.raw_payload['probabilistic_research']
    assert result.action == 'BUY' and f['stop_points'] == 53
    assert f['target_points'] == 79.5 and f['pool_baseline']['forecasts']
    assert not result.raw_payload['live_routing_allowed']
    record = dict(stage='qualified_signal', forecast=f, action='BUY', status='dry_run_attempt',
                  id='wide', contract=CONTRACT, at=NOW.isoformat(), close=NOW.isoformat())
    state = paper.initial()
    assert paper.admission(state, record, NOW) == 'paper_entry_queued'
    assert state['position']['stop'] == 53
    with pytest.raises(ValueError, match='cannot route'):
        require_live_worker()
    with pytest.raises(ValueError, match='validated routing'):
        validate_binding(observation[1], root=tmp_path, as_of=NOW)


@pytest.mark.parametrize('fault', ['old_model', 'flag_off', 'live_gate', 'observation_off'])
def test_uncapped_requires_matching_model_and_disabled_live_gates(observation, monkeypatch, tmp_path, fault):
    monkeypatch.setenv(obs.UNCAPPED_FLAG, 'true')
    if fault != 'old_model':
        install_uncapped(observation)
    if fault == 'flag_off': monkeypatch.setenv(obs.UNCAPPED_FLAG, 'false')
    if fault == 'live_gate': monkeypatch.setenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED', 'true')
    if fault == 'observation_off': monkeypatch.setenv(obs.FLAG, 'false')
    result = evaluate(as_rows(volatile()), as_of=NOW, root=tmp_path)
    assert result.action == 'HOLD'
    assert result.raw_payload['probabilistic_research']['forecasts'] is None
    if fault == 'observation_off':
        from app.main import _validate_runtime_security_configuration
        with pytest.raises(ValueError, match='explicit unvalidated'):
            _validate_runtime_security_configuration()


@pytest.mark.parametrize('stop,net,expected', [
    (60., 0., 'paper_entry_queued'), (125., 0., 'paper_daily_loss_limit'),
    (60., -150., 'paper_daily_loss_limit'),
    (float('nan'), 0., 'paper_invalid_bracket'), (float('inf'), 0., 'paper_invalid_bracket'),
    (-1., 0., 'paper_invalid_bracket'),
])
def test_uncapped_keeps_daily_dollar_budget_and_finite_brackets(monkeypatch, stop, net, expected):
    monkeypatch.setenv(obs.FLAG, 'true')
    monkeypatch.setenv(obs.UNCAPPED_FLAG, 'true')
    monkeypatch.setenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED', 'false')
    monkeypatch.setenv('TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION', 'false')
    f = forecast()
    f.update(stop_policy=UNCAPPED_STOP_POLICY, stop_points=stop,
             target_points=math.ceil(stop*1.5/.25)*.25 if math.isfinite(stop) else None)
    record = dict(stage='qualified_signal', forecast=f, action='BUY', status='dry_run_attempt',
                  id='wide', contract=CONTRACT, at=PAPER_NOW.isoformat(), close=PAPER_NOW.replace(second=0).isoformat())
    state = paper.initial()
    state['daily']['2026-10-22'] = dict(entries=0, net_usd=net)
    assert paper.admission(state, record, PAPER_NOW) == expected
    if expected != 'paper_entry_queued': assert state['position'] is None
