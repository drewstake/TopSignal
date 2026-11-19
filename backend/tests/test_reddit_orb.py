"""Causal ORB signal and actual-fill absolute-stop tests, using synthetic prices."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tools.fixtures import reddit_orb as orb


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


def test_holiday_clock_does_not_reset_after_missing_deadline():
    entry = datetime(2026, 7, 3, 14, tzinfo=timezone.utc)
    deadline = datetime(2026, 7, 3, 16, 55, tzinfo=timezone.utc)
    assert not orb.should_flatten(entry, deadline - timedelta(minutes=1), orb.VARIANT)
    assert orb.should_flatten(entry, deadline, orb.VARIANT)
    assert orb.should_flatten(entry, deadline + timedelta(days=3), orb.VARIANT)
