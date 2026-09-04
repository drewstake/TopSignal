from dataclasses import replace

import pytest

from app.services.bot_risk import RiskEvaluationContext, evaluate_risk


def _context(**changes):
    context = RiskEvaluationContext(
        bot_enabled=True, account_state="ACTIVE", account_can_trade=True,
        live_funded_account=False, configured_execution_mode="live", dry_run=False,
        confirm_live_order_routing=True, running_under_tests=False, live_environment_enabled=True,
        contract_allowed=True, action="BUY", order_size=1.0, resulting_position_qty=1.0,
        max_contracts=1.0, max_open_position=1.0, trades_today=0, max_trades_per_day=3,
        daily_pnl=0.0, max_daily_loss=250.0, latest_candle_age_seconds=10.0,
        max_data_staleness_seconds=60, inside_trading_session=True,
    )
    return replace(context, **changes)


@pytest.mark.parametrize("age", [-1.0, float("nan"), float("inf"), -float("inf")])
def test_unknown_or_future_closed_candle_age_blocks_entry(age):
    assert "invalid_market_data_age" in {
        block.code for block in evaluate_risk(_context(latest_candle_age_seconds=age))
    }


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf")])
def test_invalid_staleness_limit_blocks_entry(limit):
    assert "invalid_market_data_age" in {
        block.code for block in evaluate_risk(_context(max_data_staleness_seconds=limit))
    }


@pytest.mark.parametrize("tradability", [None, False, 1, "true"])
def test_tradability_requires_explicit_boolean_true(tradability):
    assert "account_cannot_trade" in {
        block.code for block in evaluate_risk(_context(account_can_trade=tradability))
    }


@pytest.mark.parametrize("changes", [
    {"trades_today": -1}, {"trades_today": float("nan")},
    {"max_trades_per_day": -1}, {"max_trades_per_day": float("inf")},
])
def test_invalid_trade_count_cannot_disable_daily_limit(changes):
    assert "invalid_daily_trade_count" in {
        block.code for block in evaluate_risk(_context(**changes))
    }


def test_negative_daily_loss_limit_is_invalid_even_with_positive_pnl():
    assert "invalid_daily_pnl_risk_data" in {
        block.code for block in evaluate_risk(_context(max_daily_loss=-1, daily_pnl=100))
    }


def test_safe_entry_context_passes():
    assert evaluate_risk(_context()) == []
