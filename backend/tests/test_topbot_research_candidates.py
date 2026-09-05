"""Behavioral checks for causal research rules; synthetic candles are tests only."""
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

from tools.fixtures import topbot_research as research


def opening_breakout():
    end = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    rows = [SimpleNamespace(
        user_id="research-test", contract_id="CON.F.US.MNQ.U26", symbol="MNQ",
        unit="minute", unit_number=5, is_partial=False, live=False,
        candle_timestamp=end - timedelta(minutes=5 * (219 - i)),
        open_price=100, high_price=110, low_price=90, close_price=100,
        volume=100, source_instrument_id=1, source_raw_symbol="MNQU6",
    ) for i in range(220)]
    rows[-1].open_price = 108
    rows[-1].high_price = 112
    rows[-1].low_price = 107
    rows[-1].close_price = 111
    return rows


def append_bar(rows, *, close=113):
    row = deepcopy(rows[-1])
    row.candle_timestamp += timedelta(minutes=5)
    row.close_price = close
    row.high_price = max(row.high_price, close)
    row.low_price = min(row.low_price, close)
    return [*rows, row]


def test_opening_range_cannot_trade_before_range_is_closed():
    rows = opening_breakout()
    assert research.evaluate(rows[:-1], "orb30_both").action == "HOLD"
    signal = research.evaluate(rows, "orb30_both")
    assert signal.action == "BUY"
    assert signal.raw_payload["planned_risk_points"] == 10
    assert signal.raw_payload["planned_reward_points"] == 20
    assert signal.raw_payload["target_position_qty"] == 1
    assert research.evaluate(append_bar(rows), "orb30_both").action == "HOLD"


def test_prior_downside_break_precludes_cherry_picking_later_upside_break():
    rows = opening_breakout()
    rows[-1].close_price = 89
    rows[-1].low_price = 88
    signal = research.evaluate(rows, "orb30_both")
    assert signal.action == "SELL"
    assert research.evaluate(rows, "orb30_long").action == "HOLD"
    later = append_bar(rows, close=111)
    assert research.evaluate(later, "orb30_long").action == "HOLD"


def test_missing_opening_minute_aggregate_blocks_entry():
    rows = opening_breakout()
    assert research.evaluate(rows[:-5] + rows[-4:], "orb30_both").action == "HOLD"


@pytest.mark.parametrize("position,action", [(1, "SELL"), (-1, "BUY")])
def test_scheduled_exit_uses_observed_position_and_does_not_need_full_session(position, action):
    rows = opening_breakout()
    rows[-1].candle_timestamp = datetime(2026, 7, 6, 19, 50, tzinfo=timezone.utc)
    signal = research.evaluate(rows, "orb30_both", position_qty=position)
    assert signal.action == action
    assert signal.raw_payload["target_position_qty"] == 0
    assert signal.raw_payload["signal_category"] == "exit"
    assert "stop_loss" not in signal.raw_payload
    assert research.evaluate(rows, "orb30_both", position_qty=0).action == "HOLD"


def test_existing_position_cannot_scale_or_reverse():
    rows = opening_breakout()
    for quantity in (-1, 1):
        assert research.evaluate(rows, "orb30_both", position_qty=quantity).action == "HOLD"


def test_risk_is_tick_aligned_and_capped_without_increasing_quantity():
    rows = opening_breakout()
    for row in rows[:-1]:
        row.high_price, row.low_price = 500, 1
    rows[-1].close_price = 501
    rows[-1].high_price = 502
    signal = research.evaluate(rows, "orb30_both")
    assert signal.action == "BUY"
    assert signal.raw_payload["planned_risk_points"] == 100
    assert signal.raw_payload["planned_reward_points"] == 200
    assert signal.raw_payload["target_position_qty"] == 1


def test_invalid_instrument_and_timeframe_do_not_generate_signals():
    rows = opening_breakout()
    rows[-1].symbol = "NQ"
    assert research.evaluate(rows, "orb30_both").action == "HOLD"
    rows[-1].symbol = "MNQ"
    rows[-1].unit_number = 1
    assert research.evaluate(rows, "orb30_both").action == "HOLD"


def test_independent_clock_honors_early_close_and_outage_without_resetting_deadline():
    entry = datetime(2026, 7, 3, 14, tzinfo=timezone.utc)
    before = datetime(2026, 7, 3, 16, 54, tzinfo=timezone.utc)
    due = datetime(2026, 7, 3, 16, 55, tzinfo=timezone.utc)
    assert not research.should_flatten(entry, before, "orb30_both")
    assert research.should_flatten(entry, due, "orb30_both")
    assert research.should_flatten(entry, due + timedelta(days=3), "orb30_both")
    assert not research.should_flatten(entry, due, "baseline_v5")
    assert not research.should_flatten(entry, due, "v5_long")
