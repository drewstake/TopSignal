"""Causal proxy checks; synthetic bars are never used as performance evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

from tools.fixtures import reddit_scalper as proxy
from tools.research_reddit_scalper import make_one_minute_engine, one_minute_configuration_validator, one_minute_stream_specs

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


def test_native_stream_spec_does_not_request_five_minute_data():
    config = SimpleNamespace(timeframe_unit="minute", timeframe_unit_number=1,
                             contract_id="CON.F.US.MNQ.U26", symbol="MNQ")
    spec = next(iter(one_minute_stream_specs(config).values()))
    assert spec.unit_number == 1
    assert spec.warmup_bars == 200
    config.timeframe_unit_number = 5
    with pytest.raises(ValueError, match="native one-minute"):
        one_minute_stream_specs(config)


@pytest.mark.parametrize("missing_entry_minute", [False, True])
def test_real_engine_fills_after_signal_and_runs_clock_on_each_observed_minute(monkeypatch, missing_entry_minute):
    from app.models import BotConfig
    from app.services import bot_backtesting as replay
    from app.services.topbot import TOPBOT_SETTINGS

    monkeypatch.setattr(replay, "_topbot_stream_specs", one_minute_stream_specs)
    monkeypatch.setattr(replay, "_validate_replay_configuration",
                        one_minute_configuration_validator(replay._validate_replay_configuration))
    # This minute closes at exactly 10:00 ET, the declared session boundary.
    signal_at = datetime(2026, 7, 6, 13, 59, tzinfo=timezone.utc)
    template = candles()[0]
    rows = []
    for i in range(212):
        row = deepcopy(template)
        row.candle_timestamp = signal_at - timedelta(minutes=201 - i)
        if missing_entry_minute and row.candle_timestamp == signal_at + timedelta(minutes=1):
            continue
        rows.append(row)

    def evaluate(candles, variant, position_qty=0):
        latest = candles[-1]
        buy = latest.candle_timestamp == signal_at and not position_qty
        payload = ({"signal_category": "entry", "target_position_qty": 1.,
                    "entry_price": 100., "stop_loss": 95., "take_profit": 105.} if buy else {})
        return proxy.indicators.SignalResult(action="BUY" if buy else "HOLD", reason="clock test",
            candle_timestamp=latest.candle_timestamp, price=100., raw_payload=payload)

    fixture = SimpleNamespace(evaluate=evaluate, should_flatten=proxy.should_flatten, REVISION="test")
    settings = deepcopy(TOPBOT_SETTINGS)
    settings.update(proxy.get_settings(VARIANT))
    config = BotConfig(id=1, user_id="proxy-test", account_id=1, name="proxy-test",
        provider="projectx", enabled=False, execution_mode="dry_run",
        contract_id="CON.F.US.MNQ.U26", **settings)
    engine = make_one_minute_engine(replay, fixture, VARIANT)(config=config, candles=rows,
        execution_candles=rows, settings=replay.BacktestSettings(start=rows[0].candle_timestamp,
        end=rows[-1].candle_timestamp + timedelta(minutes=1), starting_balance=50000,
        commission_per_contract=.61, slippage_ticks=1, tick_size=.25, tick_value=.5))
    result = engine.run()
    if missing_entry_minute:
        assert result["trades"] == []
        assert any("missing next execution minute" in note for note in result["notes"])
        return
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert datetime.fromisoformat(trade["entry_timestamp"]) == signal_at + timedelta(minutes=1)
    assert datetime.fromisoformat(trade["exit_timestamp"]) == signal_at + timedelta(minutes=7)
    assert trade["net_pnl"] == pytest.approx(-2.22)
