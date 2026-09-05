"""Synthetic checks only: no historical data, experiments, or broker access."""
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

from app.services.trading_day import TRADING_TZ, trading_day_date
from tools.fixtures import topbot_research_overnight as research
from tools.research_topbot import make_engine_class


def et(value):
    return datetime.fromisoformat(value).replace(tzinfo=TRADING_TZ).astimezone(timezone.utc)


def signal_rows(decision="2026-07-06T16:00", *, count=200):
    known_at = et(decision)
    return [SimpleNamespace(
        user_id="research-test", contract_id="CON.F.US.MNQ.U26", symbol="MNQ",
        unit="minute", unit_number=5, is_partial=False, live=False, fetched_at=None,
        candle_timestamp=known_at - timedelta(minutes=5 * (count - index)),
        open_price=20_000.0, high_price=20_001.0, low_price=19_999.0,
        close_price=20_000.0, volume=100, source="synthetic-test",
        source_instrument_id=1, source_raw_symbol="MNQU6",
    ) for index in range(count)]


def test_exactly_four_predeclared_variants_with_fixed_size_and_risk():
    expected = {
        "overnight_long_75": ("BUY", 1, 75, 150),
        "overnight_long_50": ("BUY", 1, 50, 100),
        "overnight_long_100": ("BUY", 1, 100, 200),
        "overnight_short_control_75": ("SELL", -1, 75, 150),
    }
    assert set(research.CANDIDATES) == set(expected)
    for variant, (action, direction, stop, target) in expected.items():
        specification = research.CANDIDATES[variant]
        assert specification["description"] and specification["hypothesis"]
        params = specification["parameters"]
        settings = research.get_settings(variant)
        assert research.required_warmup_bars(variant) == settings["lookback_bars"] == 200
        assert settings["trading_start_time"] == "15:30"
        assert settings["trading_end_time"] == "16:30"
        assert settings["max_daily_loss"] == 250
        assert settings["max_trades_per_day"] == 1
        assert all(settings[name] == 1 for name in ("order_size", "max_contracts", "max_open_position"))
        assert params["stop_cap_points"] == 100
        assert stop <= params["stop_cap_points"]
        result = research.evaluate(signal_rows(), variant)
        plan = result.raw_payload
        assert result.action == action
        assert plan["target_position_qty"] == direction
        assert plan["stop_loss"] == result.price - direction * stop
        assert plan["take_profit"] == result.price + direction * target
        assert plan["planned_risk_points"] == stop
        assert plan["planned_reward_points"] == 2 * stop == target
        assert plan["exit_deadline"] == et("2026-07-07T09:25").isoformat()


@pytest.mark.parametrize("day,action", [
    ("2026-07-06", "BUY"), ("2026-07-07", "BUY"),
    ("2026-07-08", "BUY"), ("2026-07-09", "BUY"),
    ("2026-07-10", "HOLD"), ("2026-07-11", "HOLD"), ("2026-07-12", "HOLD"),
])
def test_only_monday_through_thursday_entries(day, action):
    assert research.evaluate(signal_rows(day + "T16:00"), "overnight_long_75").action == action


def test_closed_bar_produces_only_one_possible_entry_clock_per_day():
    rows = signal_rows()
    latest = rows[-1].candle_timestamp
    for minutes in range(-30, 35, 5):
        shifted = deepcopy(rows)
        for row in shifted:
            row.candle_timestamp += timedelta(minutes=minutes)
        result = research.evaluate(shifted, "overnight_long_75")
        assert result.action == ("BUY" if minutes == 0 else "HOLD")
        assert result.candle_timestamp == latest + timedelta(minutes=minutes)
    rows[-1].is_partial = True
    assert research.evaluate(rows, "overnight_long_75").action == "HOLD"
    assert research.evaluate(signal_rows(count=199), "overnight_long_75").action == "HOLD"


@pytest.mark.parametrize("position", [-2, -1, 1, 2])
def test_any_existing_position_prevents_scaling_or_reversal(position):
    for variant in research.CANDIDATES:
        result = research.evaluate(signal_rows(), variant, position_qty=position)
        assert result.action == "HOLD"
        assert "target_position_qty" not in result.raw_payload


@pytest.mark.parametrize("decision", [
    "2019-07-03T16:00",  # Known Independence Eve early close.
    "2025-12-25T16:00",  # Current full closure.
    "2024-12-31T16:00",  # Next date fully closed for New Year.
    "2024-03-28T16:00",  # Next Good Friday fully closed.
    "2026-04-02T16:00",  # Next Good Friday closes at 09:15, before the exit.
])
def test_known_current_and_next_session_closures_block_entry(decision):
    result = research.evaluate(signal_rows(decision), "overnight_long_75")
    assert result.action == "HOLD"
    assert "calendar" in result.reason or "Next known session" in result.reason


def test_next_0930_early_close_allows_0925_exit_without_future_prices():
    result = research.evaluate(signal_rows("2025-01-08T16:00"), "overnight_long_75")
    assert result.action == "BUY"
    assert result.raw_payload["exit_deadline"] == et("2025-01-09T09:25").isoformat()


@pytest.mark.parametrize("entry,due_utc", [
    ("2026-03-07T16:00", "2026-03-08T13:25+00:00"),
    ("2026-10-31T16:00", "2026-11-01T14:25+00:00"),
    ("2026-07-06T16:00", "2026-07-07T13:25+00:00"),
])
def test_clock_uses_original_next_local_date_across_dst_and_multiday_outage(entry, due_utc):
    # Weekend entries are prohibited, but the independent exit must handle any
    # carried exposure and a changed UTC offset without resetting its deadline.
    entered = et(entry)
    due = datetime.fromisoformat(due_utc)
    for variant in research.CANDIDATES:
        assert not research.should_flatten(entered, due - timedelta(seconds=1), variant)
        assert research.should_flatten(entered, due, variant)
        assert research.should_flatten(entered, due + timedelta(days=4), variant)
        assert not research.should_flatten(entered, entered + timedelta(hours=9), variant)


@pytest.mark.parametrize("day,utc_hour", [("2026-03-05", 21), ("2026-03-09", 20)])
def test_eligible_entry_remains_1600_et_after_dst_change(day, utc_hour):
    result = research.evaluate(signal_rows(day + "T16:00"), "overnight_long_75")
    assert result.action == "BUY"
    assert (result.candle_timestamp + timedelta(minutes=5)).hour == utc_hour


def test_past_price_path_cannot_change_unconditional_timing_or_fixed_bracket():
    original = signal_rows()
    changed = deepcopy(original)
    for index, row in enumerate(changed[:-1]):
        row.open_price = row.close_price = 10_000 + 25 * index
        row.high_price, row.low_price, row.volume = 30_000, 5_000, index * 10_000
    before = research.evaluate(original, "overnight_long_75")
    after = research.evaluate(changed, "overnight_long_75")
    assert after == before
    changed[-1].close_price += 17.25
    changed[-1].high_price = changed[-1].close_price + 1
    shifted = research.evaluate(changed, "overnight_long_75")
    assert shifted.action == before.action
    for field in ("entry_price", "stop_loss", "take_profit"):
        assert shifted.raw_payload[field] == before.raw_payload[field] + 17.25


@pytest.mark.parametrize("field,value", [
    ("symbol", "NQ"), ("unit_number", 1), ("unit", "hour"),
])
def test_invalid_inputs_do_not_create_entries(field, value):
    rows = signal_rows()
    setattr(rows[-1], field, value)
    assert research.evaluate(rows, "overnight_long_75").action == "HOLD"


@pytest.mark.parametrize("price", [float("nan"), 0])
def test_malformed_prices_fail_explicitly_before_producing_a_signal(price):
    rows = signal_rows()
    rows[-1].close_price = price
    with pytest.raises(ValueError, match="invalid_market_candle"):
        research.evaluate(rows, "overnight_long_75")


@pytest.mark.parametrize("variant,expected_entries", [
    ("overnight_long_50", 2), ("overnight_long_100", 1),
])
def test_real_engine_overnight_clock_and_next_day_proposed_stop_budget(variant, expected_entries):
    from app.services import bot_backtesting as replay
    from test_bot_backtesting import _config

    rows = signal_rows()
    next_signal = deepcopy(rows[-1])
    next_signal.candle_timestamp += timedelta(days=1)
    signals = [*rows, next_signal]
    # Synthetic sparse observations deliberately omit the target exit minute.
    # The actual first available 09:27 open must determine the clock exit.
    observations = [
        ("2026-07-06T15:59", 20_000), ("2026-07-06T16:00", 20_000),
        ("2026-07-06T16:01", 20_000), ("2026-07-06T16:30", 20_000),
        ("2026-07-06T18:00", 20_000), ("2026-07-07T00:00", 20_000),
        ("2026-07-07T09:24", 19_960), ("2026-07-07T09:27", 19_960),
        ("2026-07-07T15:59", 20_000), ("2026-07-07T16:00", 20_000),
        ("2026-07-07T16:01", 20_000),
    ]
    minutes = []
    for timestamp, price in observations:
        candle = deepcopy(rows[-1])
        candle.candle_timestamp = et(timestamp)
        candle.unit_number = 1
        candle.open_price = candle.close_price = price
        candle.high_price, candle.low_price = price + 1, price - 1
        minutes.append(candle)
    config = _config(strategy_type="topbot_adaptive", user_id="research-test",
                     contract_id="CON.F.US.MNQ.U26", allowed_contracts=["CON.F.US.MNQ.U26"],
                     **research.get_settings(variant))
    engine = make_engine_class(replay, research, variant)(
        config=config, candles=signals, execution_candles=minutes,
        settings=replay.BacktestSettings(
            start=minutes[0].candle_timestamp, end=et("2026-07-07T16:02"),
            starting_balance=50_000, commission_per_contract=.61,
            slippage_ticks=1, tick_size=.25, tick_value=.5,
        ),
    )
    result = engine.run()
    assert len(result["trades"]) == expected_entries
    first = result["trades"][0]
    assert first["signal_timestamp"] == rows[-1].candle_timestamp.isoformat()
    assert first["entry_timestamp"] == et("2026-07-06T16:00").isoformat()
    assert first["exit_timestamp"] == et("2026-07-07T09:27").isoformat()
    assert first["exit_reason"] == "scheduled_session_flatten"
    risk = research.CANDIDATES[variant]["parameters"]["stop_points"]
    assert first["stop_loss"] == first["entry_price"] - risk
    assert first["take_profit"] == first["entry_price"] + 2 * risk
    assert first["exit_price"] == 19_959.75
    assert first["commission"] == 1.22
    assert first["net_pnl"] == pytest.approx(-82.22)
    assert engine.daily_entry_counts[trading_day_date(et("2026-07-06T16:00"))] == 1
    if expected_entries == 1:
        assert engine.block_counts["proposed_stop_risk_exceeds_daily_loss_budget"] == 1
    else:
        assert engine.daily_entry_counts[trading_day_date(et("2026-07-07T16:00"))] == 1
    assert sum(row["net_pnl"] for row in engine.session_ledger()) == pytest.approx(result["metrics"]["net_pnl"])
