"""Synthetic timing and absolute-stop checks for the offline Reddit adaptation."""
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.services.trading_day import TRADING_TZ
from tools.fixtures import reddit_hourly_range as fixture


def bars():
    decision = datetime(2026, 7, 6, 10, tzinfo=TRADING_TZ).astimezone(timezone.utc)
    rows = []
    for i in range(200):
        price = 1010 if i < 189 else 900
        rows.append(SimpleNamespace(
            candle_timestamp=decision - timedelta(hours=200-i),
            open_price=price, close_price=price, high_price=price+5,
            low_price=price-5, volume=100, unit="hour", unit_number=1,
            symbol="MNQ", contract_id="CON.F.US.MNQ.U26", user_id="synthetic",
            is_partial=False, live=False, fetched_at=None,
            source_instrument_id=1, source_raw_symbol="MNQU6",
        ))
    rows[-1].close_price = 906
    rows[-1].high_price = 908
    return rows


def test_long_and_short_use_prior_range_without_current_bar_and_opposite_trend():
    rows = bars()
    long = fixture.evaluate(rows, fixture.VARIANT)
    assert long.action == "BUY"
    assert long.raw_payload["absolute_range_stop"] == 895
    assert long.raw_payload["range_width"] == 10
    assert long.raw_payload["take_profit"] == 921
    reflected = deepcopy(rows)
    for row in reflected:
        row.open_price, row.close_price = 2000-row.open_price, 2000-row.close_price
        row.low_price, row.high_price = 2000-row.high_price, 2000-row.low_price
    short = fixture.evaluate(reflected, fixture.VARIANT)
    assert short.action == "SELL"
    assert short.raw_payload["absolute_range_stop"] == 1105
    assert short.raw_payload["take_profit"] == 1079


def test_no_lookahead_from_partial_breakout_and_no_averaging():
    rows = bars()
    assert fixture.evaluate(rows, fixture.VARIANT, position_qty=1).action == "HOLD"
    rows[-1].is_partial = True
    assert fixture.evaluate(rows, fixture.VARIANT).action == "HOLD"


def test_signal_after_cutoff_and_wrong_trend_are_rejected():
    rows = bars()
    for row in rows:
        row.candle_timestamp += timedelta(hours=4)
    assert fixture.evaluate(rows, fixture.VARIANT).action == "HOLD"
    rows = bars()
    for row in rows[:189]:
        row.open_price = row.close_price = 800
        row.high_price, row.low_price = 805, 795
    assert fixture.evaluate(rows, fixture.VARIANT).action == "HOLD"


def test_exit_clock_does_not_reset_after_missing_minutes_or_midnight():
    entered = datetime(2026, 7, 6, 10, tzinfo=TRADING_TZ)
    due = datetime(2026, 7, 6, 15, 55, tzinfo=TRADING_TZ)
    assert not fixture.should_flatten(entered, due-timedelta(minutes=1), fixture.VARIANT)
    assert fixture.should_flatten(entered, due, fixture.VARIANT)
    assert fixture.should_flatten(entered, due+timedelta(days=2), fixture.VARIANT)
