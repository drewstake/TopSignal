"""Depth scheduling uses observation time after slow database/provider work."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.bot_worker as worker
from app.models import BotConfig, BotRun
from app.services import forward_results, market_archive, topbot_audit, topbot_depth, topbot_strategy
from test_bot_worker import session_factory, _runtime, _seed_run


@pytest.mark.parametrize('side,age,persistence,expected', [
    ('BUY', .1, 35.5, True),
    ('SELL', .1, 35.5, True),
    ('BUY', .1, 30, True),
    ('BUY', .1, 29.9, False),
    ('BUY', 3, 35.5, False),
    ('BUY', -.1, 35.5, False),
])
def test_worker_checks_depth_after_io_and_preserves_entry_gates(
    session_factory, monkeypatch, side, age, persistence, expected,
):
    poll_started = datetime(2026, 11, 2, 18, 2, tzinfo=timezone.utc)
    # A new tick arrives while the cycle is loading its run and credentials.
    checked_at = poll_started + timedelta(seconds=10)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return checked_at.astimezone(tz) if tz else checked_at.replace(tzinfo=None)

    monkeypatch.setattr(topbot_depth, 'datetime', Clock)
    _seed_run(session_factory, started_at=poll_started - timedelta(hours=1))
    contract = 'CON.F.US.MNQ.H26'
    with session_factory() as db:
        config = db.get(BotConfig, 1)
        config.strategy_type = 'topbot_adaptive'
        config.strategy_params = dict(topbot_strategy.DEFAULTS)
        db.commit()

    feed = topbot_depth.DepthFeed()
    book = topbot_depth.DepthState(contract, .1)
    book.connected = True
    book.bids = {20000: 800 if side == 'BUY' else 1200}
    book.asks = {20000.25: 1200 if side == 'BUY' else 800}
    book.last_at = checked_at - timedelta(seconds=age)
    book.episode_at = book.last_at - timedelta(seconds=persistence)
    feed.streams[('user-a', contract)] = {'state': book, 'touched': 0}
    monkeypatch.setattr(topbot_depth, 'feed', feed)
    monkeypatch.setattr(feed, 'ensure', lambda *a, **kw: None)
    # This is between candle boundaries: only the qualifying DOM can trigger it.
    monkeypatch.setattr(worker, '_evaluation_is_due', lambda *a, **kw: False)
    monkeypatch.setattr(market_archive, 'prepare_decision', lambda result: None)
    monkeypatch.setattr(forward_results, 'prepare_committed_record', lambda result: None)
    monkeypatch.setattr(topbot_audit, 'record', lambda *a, **kw: None)
    runtime = _runtime(session_factory)
    monkeypatch.setattr(runtime, '_client_for_user', lambda owner: object())
    evaluations = []

    def evaluate(db, **kwargs):
        observation = feed.snapshot('user-a', contract)
        signal = topbot_strategy.evaluate([], strategy_params=kwargs['config'].strategy_params,
                                         observation=observation)
        evaluations.append(signal.action)
        assert kwargs['worker_lease_token'] is not None
        assert kwargs['preserve_run_on_transient_pre_routing_error'] is True
        return SimpleNamespace(run=kwargs['run'], risk_events=[], status='dry_run_attempt')

    monkeypatch.setattr(worker, 'evaluate_bot_config', evaluate)
    arguments = dict(run_id=1, now=poll_started, startup_recovery=False,
                     blocked_accounts=set(), probe_provider=False, probed_users=set())
    result = runtime._process_run(**arguments)
    assert result['evaluated'] is expected
    assert evaluations == ([side] if expected else [])
    if expected:
        with session_factory() as db:
            assert db.get(BotRun, 1).raw_state['dom_episode_evaluated'] == book.episode_at.isoformat()
        # The same qualifying episode must not produce another evaluation.
        assert runtime._process_run(**arguments)['evaluated'] is False
        assert evaluations == [side]
