from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import bot_service, topbot_strategy
from app.services.bot_risk import RiskBlock, evaluate_risk
from app.services.topbot import prepare_topbot
from test_bot_risk_hardening import _context
from test_topbot import db, USER
from test_topbot_dom import db_session, USER_A, CONTRACT_ID, observation, feed, _add_account_and_config


@pytest.mark.parametrize('enabled,count,blocked', [(True, 4, True), (False, 4, False), (False, 1000000, False)])
def test_explicit_uncapped_preference_only_removes_entry_count_gate(enabled, count, blocked):
    context = _context(trades_today=count, max_trades_per_day=4, daily_entry_limit_enabled=enabled)
    codes = {b.code for b in evaluate_risk(context)}
    assert ('max_trades_per_day' in codes) is blocked
    if not blocked:
        assert not codes


@pytest.mark.parametrize('changes,expected', [
    ({'daily_pnl': -250}, 'max_daily_loss'),
    ({'order_size': 2, 'resulting_position_qty': 2}, 'max_contracts'),
    ({'resulting_position_qty': 2}, 'max_open_position'),
    ({'cooldown_block': RiskBlock('cooldown_after_loss', 'Wait')}, 'cooldown_after_loss'),
    ({'trades_today': -1}, 'invalid_daily_trade_count'),
    ({'max_trades_per_day': -1}, 'invalid_daily_trade_count'),
])
def test_uncapped_keeps_other_risk_gates(changes, expected):
    codes = {b.code for b in evaluate_risk(_context(daily_entry_limit_enabled=False, **changes))}
    assert expected in codes


def test_zero_retains_existing_block_all_entries_meaning():
    assert 'max_trades_per_day' in {b.code for b in evaluate_risk(_context(max_trades_per_day=0))}


@pytest.mark.parametrize('pnl,blocked', [(-499.99, False), (-500, True), (-501, True)])
def test_uncapped_500_dollar_daily_loss_budget(pnl, blocked):
    codes = {b.code for b in evaluate_risk(_context(daily_entry_limit_enabled=False,
        max_daily_loss=500, daily_pnl=pnl, trades_today=100))}
    assert ('max_daily_loss' in codes) is blocked
    assert 'max_trades_per_day' not in codes


@pytest.mark.parametrize('value', [None, 0, 1, 'false', 'true'])
def test_preference_requires_boolean(value):
    with pytest.raises(ValueError, match='daily_entry_limit_enabled'):
        topbot_strategy.normalize_params({**topbot_strategy.DEFAULTS, 'daily_entry_limit_enabled': value})


@pytest.mark.parametrize('strategy,revision,expected', [
    ('topbot_adaptive', topbot_strategy.REVISION, False),
    ('topbot_adaptive', 'custom_unconfigured', True),
    ('sma_cross', topbot_strategy.REVISION, True),
])
def test_preference_is_scoped_to_dom(strategy, revision, expected):
    config = SimpleNamespace(strategy_type=strategy,
        strategy_params={'revision': revision, 'daily_entry_limit_enabled': False})
    assert topbot_strategy.daily_entry_limit_enabled(config) is expected


def test_uncapped_survives_new_run_and_contract_roll_without_changing_other_account(db):
    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id='CON.F.US.MNQ.U26')
    config.strategy_params = {**config.strategy_params, 'daily_entry_limit_enabled': False}
    db.commit()
    config = prepare_topbot(db, user_id=USER, account_id=101, dry_run=True, contract_id='CON.F.US.MNQ.Z26')
    assert not topbot_strategy.daily_entry_limit_enabled(config)
    assert config.max_daily_loss == 500 and config.max_contracts == config.max_open_position == 1
    assert config.cooldown_seconds == 60
    other = prepare_topbot(db, user_id=USER, account_id=102, dry_run=True, contract_id='CON.F.US.MNQ.Z26')
    assert topbot_strategy.daily_entry_limit_enabled(other)


@pytest.mark.parametrize('enabled', [True, False])
def test_service_passes_saved_dom_preference_to_risk_policy(db_session, monkeypatch, enabled):
    account, config = _add_account_and_config(db_session)
    config.strategy_type = 'topbot_adaptive'
    config.strategy_params = {**topbot_strategy.DEFAULTS, 'daily_entry_limit_enabled': enabled}
    config.max_trades_per_day = 4
    now = datetime.now(timezone.utc)
    depth = observation(now)
    monkeypatch.setattr(feed, 'snapshot', lambda *a, **kw: depth)
    monkeypatch.setattr(bot_service, '_todays_account_entry_trade_count', lambda *a, **kw: 4)
    blocks = bot_service.evaluate_risk_gates(db_session, user_id=USER_A, config=config, account=account,
        latest_candle=None, contract_id=CONTRACT_ID, symbol='MNQ', action='SELL',
        dry_run=True, confirm_live_order_routing=False, decision=SimpleNamespace(raw_payload={'depth_entry': depth}))
    assert ('max_trades_per_day' in {b.code for b in blocks}) is enabled
