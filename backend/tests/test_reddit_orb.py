"""Causal ORB signal and actual-fill absolute-stop tests, using synthetic prices."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import bot_backtesting as replay
from tools.fixtures import reddit_orb as orb
from tools.research_reddit_orb import make_engine_class
from test_bot_backtesting import BASE_TIME, _candle, _config, _scripted_evaluator


def setup_rows():
    end = datetime(2026, 7, 6, 13, 55, tzinfo=timezone.utc)
    rows = [SimpleNamespace(
        user_id="research-test", contract_id="CON.F.US.MNQ.U26", symbol="MNQ",
        unit="minute", unit_number=5, is_partial=False, live=False,
        candle_timestamp=end - timedelta(minutes=5 * (219 - i)),
        open_price=100, high_price=110, low_price=90, close_price=100,
        volume=100, source_instrument_id=1, source_raw_symbol="MNQU6",
    ) for i in range(220)]
    rows[-1].close_price, rows[-1].high_price = 111, 112
    return rows


def test_only_completed_fifteen_minute_cross_can_trigger():
    rows = setup_rows()
    assert orb.evaluate(rows[:-1], orb.VARIANT).action == "HOLD"
    signal = orb.evaluate(rows, orb.VARIANT)
    assert signal.action == "BUY"
    assert signal.raw_payload["stop_loss"] == 90
    rows[-3].open_price = 110.25
    rows[-3].high_price = 111
    assert orb.evaluate(rows, orb.VARIANT).action == "HOLD"


def test_missing_opening_or_later_five_minute_bar_blocks_signal():
    rows = setup_rows()
    for position in (-6, -4, -2):
        missing = list(rows)
        missing.pop(position)
        assert orb.evaluate(missing, orb.VARIANT).action == "HOLD"


def test_multiple_separate_crossings_are_allowed_when_flat():
    rows = setup_rows()
    assert orb.evaluate(rows, orb.VARIANT).action == "BUY"
    for index in range(3):
        row = deepcopy(rows[-1])
        row.candle_timestamp += timedelta(minutes=5)
        row.open_price = 100
        rows.append(row)
    assert orb.evaluate(rows, orb.VARIANT).action == "BUY"
    assert orb.evaluate(rows, orb.VARIANT, position_qty=1).action == "HOLD"


def test_no_entry_at_noon_and_no_short():
    rows = setup_rows()
    rows[-1].candle_timestamp = datetime(2026, 7, 6, 15, 55, tzinfo=timezone.utc)
    assert orb.evaluate(rows, orb.VARIANT).action == "HOLD"
    rows = setup_rows()
    rows[-1].close_price, rows[-1].low_price = 89, 88
    assert orb.evaluate(rows, orb.VARIANT).action == "HOLD"


def test_actual_fill_plan_keeps_absolute_low_and_rejects_wide_risk():
    plan = orb.fill_plan(111.25, 90)
    assert plan["stop_loss"] == 90
    assert plan["planned_risk_points"] == 21.25
    assert plan["take_profit"] == 143.25
    assert orb.fill_plan(190.25, 90) is None
    assert orb.fill_plan(90, 90) is None
    assert orb.fill_plan(float("nan"), 90) is None


@pytest.mark.parametrize("slippage", [1, 2])
def test_observed_gap_entry_preserves_stop_and_charges_both_fees(slippage):
    signals = [_candle(BASE_TIME + timedelta(minutes=index)) for index in (0, 5, 10)]
    minutes = [_candle(BASE_TIME + timedelta(minutes=index), unit_number=1,
                       open_price=105, high_price=106, low_price=104, close_price=105)
               for index in range(15)]
    minutes[6].low_price = 89
    instruction = {"action": "BUY", "price": 100,
                   "payload": {"signal_category": "entry", "stop_loss": 90,
                               "take_profit": 115, "range_low": 90,
                               "target_position_qty": 1}}
    engine = make_engine_class(replay, orb, orb.VARIANT)(
        config=_config(strategy_type="topbot_adaptive", max_daily_loss=250,
                       max_contracts=1, max_open_position=1),
        candles=signals, execution_candles=minutes,
        signal_evaluator=_scripted_evaluator({BASE_TIME: instruction}),
        settings=replay.BacktestSettings(start=BASE_TIME, end=BASE_TIME + timedelta(minutes=15),
                                        starting_balance=50_000, commission_per_contract=.61,
                                        slippage_ticks=slippage, tick_size=.25, tick_value=.5),
    )
    result = engine.run()
    trade = result["trades"][0]
    assert trade["entry_price"] == 105 + .25 * slippage
    assert trade["stop_loss"] == 90
    assert trade["exit_price"] == 90 - .25 * slippage
    assert trade["commission"] == 1.22
    assert trade["net_pnl"] == pytest.approx(-30 - slippage - 1.22)
    assert sum(row["net_pnl"] for row in engine.session_ledger()) == pytest.approx(trade["net_pnl"])


def test_holiday_clock_does_not_reset_after_missing_deadline():
    entry = datetime(2026, 7, 3, 14, tzinfo=timezone.utc)
    deadline = datetime(2026, 7, 3, 16, 55, tzinfo=timezone.utc)
    assert not orb.should_flatten(entry, deadline - timedelta(minutes=1), orb.VARIANT)
    assert orb.should_flatten(entry, deadline, orb.VARIANT)
    assert orb.should_flatten(entry, deadline + timedelta(days=3), orb.VARIANT)
