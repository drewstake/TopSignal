"""Causal proxy checks; synthetic bars are never used as performance evidence."""
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


from tools.fixtures import reddit_scalper as proxy

VARIANT = "reddit_scalper_1m_proxy"


def candles():
    latest = datetime(2026, 7, 6, 14, tzinfo=timezone.utc)
    rows = [SimpleNamespace(
        user_id="proxy-test", contract_id="CON.F.US.MNQ.U26", symbol="MNQ",
        unit="minute", unit_number=1, is_partial=False, live=False, fetched_at=None,
        candle_timestamp=latest - timedelta(minutes=31 - i),
        open_price=close, high_price=close + .25, low_price=close - .25, close_price=close,
        volume=100, source_instrument_id=1, source_raw_symbol="MNQU6",
    ) for i, close in enumerate([100.] * 30 + [90., 91.])]
    return rows


def test_entry_waits_for_reversal_and_is_exactly_one_mnq():
    rows = candles()
    assert proxy.evaluate(rows[:-1], VARIANT).action == "HOLD"
    signal = proxy.evaluate(rows, VARIANT)
    assert signal.action == "BUY"
    assert signal.raw_payload["target_position_qty"] == 1
    assert signal.raw_payload["planned_risk_points"] == 5
    rows[-1].close_price = 89
    rows[-1].low_price = 88.75
    assert proxy.evaluate(rows, VARIANT).action == "HOLD"


def test_short_is_symmetric_and_existing_positions_never_average_or_reverse():
    rows = candles()
    for row in rows:
        row.open_price = row.close_price = 200 - row.close_price
        row.high_price, row.low_price = row.close_price + .25, row.close_price - .25
    assert proxy.evaluate(rows, VARIANT).action == "SELL"
    for quantity in [-1, 1]:
        signal = proxy.evaluate(rows, VARIANT, position_qty=quantity)
        assert signal.action == "HOLD" or signal.raw_payload["target_position_qty"] == 0


def test_mean_cross_uses_exit_without_new_bracket():
    rows = candles()
    rows[-1].open_price = rows[-1].close_price = 103
    rows[-1].high_price, rows[-1].low_price = 103.25, 102.75
    signal = proxy.evaluate(rows, VARIANT, position_qty=1)
    assert signal.action == "SELL"
    assert signal.raw_payload["target_position_qty"] == 0
    assert signal.raw_payload["signal_category"] == "exit"
    assert "stop_loss" not in signal.raw_payload


def test_gaps_wrong_timeframe_and_delivery_changes_block_signal():
    rows = candles()
    assert proxy.evaluate(rows[:5] + rows[6:], VARIANT).action == "HOLD"
    rows[5].source_instrument_id = 2
    assert proxy.evaluate(rows, VARIANT).action == "HOLD"
    rows[5].source_instrument_id = 1
    rows[-1].unit_number = 5
    assert proxy.evaluate(rows, VARIANT).action == "HOLD"


def test_six_minute_deadline_does_not_reset_across_outages_and_holiday_close():
    entry = datetime(2026, 7, 6, 14, tzinfo=timezone.utc)
    assert not proxy.should_flatten(entry, entry + timedelta(minutes=5), VARIANT)
    assert proxy.should_flatten(entry, entry + timedelta(minutes=6), VARIANT)
    assert proxy.should_flatten(entry, entry + timedelta(days=3), VARIANT)
    holiday_entry = datetime(2026, 7, 3, 16, 53, tzinfo=timezone.utc)
    assert proxy.should_flatten(holiday_entry, holiday_entry + timedelta(minutes=2), VARIANT)
