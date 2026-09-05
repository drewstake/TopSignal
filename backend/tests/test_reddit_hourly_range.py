"""Synthetic timing and absolute-stop checks for the offline Reddit adaptation."""
import os
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.services.trading_day import TRADING_TZ
from app.services.bot_backtesting import _PendingSignal
from tools.fixtures import reddit_hourly_range as fixture
from tools.research_reddit_hourly import make_hourly_engine, validate_hourly_config
import pytest


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


class CaptureBase:
    position = None
    settings = SimpleNamespace(tick_size=.25)

    def __init__(self):
        self.block_counts = defaultdict(int)
        self.captured = None

    def _slipped_price(self, value, *, action):
        return value + (.25 if action == "BUY" else -.25)

    def _fill_pending_signal(self, pending, *, candle):
        self.captured = pending


def test_fill_preserves_absolute_stop_and_checks_actual_gap_risk():
    engine_type = make_hourly_engine(None, fixture, fixture.VARIANT,
                                    base_factory=lambda *_: CaptureBase)
    signal = fixture.evaluate(bars(), fixture.VARIANT)
    pending = _PendingSignal("BUY", signal.candle_timestamp,
        signal.candle_timestamp+timedelta(hours=1), signal.price, signal.reason, signal.raw_payload)
    engine = engine_type()
    engine._fill_pending_signal(pending, candle=SimpleNamespace(open_price=910))
    assert engine.captured.payload["stop_loss"] == 895
    assert engine.captured.signal_price == 910.25
    assert engine.captured.payload["planned_risk_points"] == 15.25
    assert engine.captured.payload["take_profit"] == 925.25
    assert pending.signal_price == 906  # Original immutable signal is unchanged.
    for bad_open in (894, 1000):
        engine = engine_type()
        engine._fill_pending_signal(pending, candle=SimpleNamespace(open_price=bad_open))
        assert engine.captured is None
        assert engine.block_counts["hourly_invalid_fill_stop_risk"] == 1


def test_exit_clock_does_not_reset_after_missing_minutes_or_midnight():
    entered = datetime(2026, 7, 6, 10, tzinfo=TRADING_TZ)
    due = datetime(2026, 7, 6, 15, 55, tzinfo=TRADING_TZ)
    assert not fixture.should_flatten(entered, due-timedelta(minutes=1), fixture.VARIANT)
    assert fixture.should_flatten(entered, due, fixture.VARIANT)
    assert fixture.should_flatten(entered, due+timedelta(days=2), fixture.VARIANT)


def test_hourly_validator_preserves_period_validation_and_rejects_other_timeframes():
    from app.services.bot_backtesting import _validate_replay_configuration
    config = SimpleNamespace(strategy_type="topbot_adaptive", timeframe_unit="hour",
                             timeframe_unit_number=1, fast_period=9, slow_period=21, strategy_params={})
    validate_hourly_config(config, _validate_replay_configuration)
    config.fast_period = 0
    with pytest.raises(ValueError, match="fast_period must be positive"):
        validate_hourly_config(config, _validate_replay_configuration)
    config.timeframe_unit = "day"
    with pytest.raises(ValueError, match="native-hourly"):
        validate_hourly_config(config, _validate_replay_configuration)
