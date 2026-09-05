"""Synthetic observations only; no historical replay or reserved-data reads."""
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

from app.services.trading_day import TRADING_TZ
from tools.fixtures import topbot_research as original
from tools.fixtures import topbot_research_opening_drive_neighbors as neighbors


def et(value):
    return datetime.fromisoformat(value).replace(tzinfo=TRADING_TZ).astimezone(timezone.utc)


def opening_rows(*, fraction=.70, width=100, direction=1, day="2026-07-06"):
    decision = et(day + "T10:00")
    rows = [SimpleNamespace(
        user_id="research-test", contract_id="CON.F.US.MNQ.U26", symbol="MNQ",
        unit="minute", unit_number=5, is_partial=False, live=False,
        fetched_at=None, candle_timestamp=decision - timedelta(minutes=5 * (220 - index)),
        open_price=20_000.0, high_price=20_001.0, low_price=19_999.0,
        close_price=20_000.0, volume=100, source="synthetic-test",
        source_instrument_id=1, source_raw_symbol="MNQU6",
    ) for index in range(220)]
    for row in rows[-6:]:
        row.high_price = 20_000 + width * (.75 if direction > 0 else .25)
        row.low_price = row.high_price - width
    rows[-1].close_price = 20_000 + direction * fraction * width
    return rows


def economic_signal(signal):
    payload = deepcopy(signal.raw_payload)
    for key in ("strategy_revision", "research_variant", "source_strategy_revision", "source_normalized_sha256"):
        payload.pop(key, None)
    return signal.action, signal.reason, signal.candle_timestamp, signal.price, payload


def test_exactly_six_one_parameter_neighbors_and_unchanged_center():
    expected = {
        "opening_drive_center": {},
        "opening_drive_displacement_060": {"displacement_fraction": .60},
        "opening_drive_displacement_070": {"displacement_fraction": .70},
        "opening_drive_stop_040": {"range_stop_multiple": .40},
        "opening_drive_stop_060": {"range_stop_multiple": .60},
        "opening_drive_reward_175": {"reward_multiple": 1.75},
        "opening_drive_reward_225": {"reward_multiple": 2.25},
    }
    center = original.CANDIDATES["opening_drive"]["parameters"]
    assert set(neighbors.CANDIDATES) == set(expected)
    for variant, changes in expected.items():
        definition = neighbors.CANDIDATES[variant]
        assert definition["parameters"] == {**center, **changes}
        assert definition["description"] and definition["hypothesis"]
        assert neighbors.required_warmup_bars(variant) == 200
        settings = neighbors.get_settings(variant)
        original_settings = original.get_settings("opening_drive")
        assert {key: value for key, value in settings.items() if key != "strategy_params"} == {
            key: value for key, value in original_settings.items() if key != "strategy_params"
        }
        assert settings["strategy_params"] == {"research_revision": neighbors.REVISION, **center, **changes}


@pytest.mark.parametrize("fraction", [.59, .60, .625, .65, .675, .70, .725])
@pytest.mark.parametrize("direction", [-1, 1])
def test_center_exact_economic_equivalence_and_threshold_neighbors(fraction, direction):
    rows = opening_rows(fraction=fraction, direction=direction)
    center = neighbors.evaluate(rows, "opening_drive_center")
    assert economic_signal(center) == economic_signal(original.evaluate(rows, "opening_drive"))
    assert center.raw_payload["research_variant"] == "opening_drive_center"
    for variant, threshold in [("opening_drive_displacement_060", .60), ("opening_drive_displacement_070", .70)]:
        result = neighbors.evaluate(rows, variant)
        assert result.action == (("BUY" if direction > 0 else "SELL") if fraction >= threshold else "HOLD")
        if result.action != "HOLD":
            assert result.raw_payload["planned_risk_points"] == 50
            assert result.raw_payload["planned_reward_points"] == 100


@pytest.mark.parametrize("variant,risk,reward", [
    ("opening_drive_stop_040", 40, 80),
    ("opening_drive_stop_060", 60, 120),
    ("opening_drive_reward_175", 50, 87.5),
    ("opening_drive_reward_225", 50, 112.5),
])
@pytest.mark.parametrize("direction", [-1, 1])
def test_bracket_neighbors_change_only_the_registered_distance(variant, risk, reward, direction):
    signal = neighbors.evaluate(opening_rows(direction=direction), variant)
    assert signal.action == ("BUY" if direction > 0 else "SELL")
    assert signal.raw_payload["target_position_qty"] == direction
    assert signal.raw_payload["planned_risk_points"] == risk
    assert signal.raw_payload["planned_reward_points"] == reward
    assert signal.raw_payload["stop_loss"] == signal.price - direction * risk
    assert signal.raw_payload["take_profit"] == signal.price + direction * reward


@pytest.mark.parametrize("width", [1, 40.25, 1000])
def test_shared_stop_floor_cap_and_tick_rounding_remain_in_force(width):
    import math
    for variant, definition in neighbors.CANDIDATES.items():
        params = definition["parameters"]
        result = neighbors.evaluate(opening_rows(width=width, fraction=.725), variant)
        risk = math.ceil(max(10, min(100, width * params["range_stop_multiple"])) * 4) / 4
        reward = math.ceil(risk * params["reward_multiple"] * 4) / 4
        assert result.action == "BUY"
        assert result.raw_payload["planned_risk_points"] == risk
        assert result.raw_payload["planned_reward_points"] == reward
        assert 10 <= risk <= 100


def test_missing_partial_early_and_late_opening_bars_cannot_create_an_entry():
    rows = opening_rows()
    late = deepcopy(rows[-1])
    late.candle_timestamp += timedelta(minutes=5)
    partial = deepcopy(rows)
    partial[-1].is_partial = True
    inputs = [rows[:-1], [*rows, late], rows[:-5] + rows[-4:], partial, rows[-199:]]
    for candles in inputs:
        assert max(row.candle_timestamp for row in candles) <= et("2026-07-06T10:00")
        expected = original.evaluate(candles, "opening_drive")
        assert economic_signal(neighbors.evaluate(candles, "opening_drive_center")) == economic_signal(expected)
        for variant in neighbors.CANDIDATES:
            assert neighbors.evaluate(candles, variant).action == "HOLD"


@pytest.mark.parametrize("position", [-1, 1])
def test_positions_keep_original_no_scaling_and_scheduled_exit_behavior(position):
    rows = opening_rows()
    for variant in neighbors.CANDIDATES:
        assert neighbors.evaluate(rows, variant, position_qty=position).action == "HOLD"
    rows[-1].candle_timestamp = et("2026-07-06T15:50")
    for variant in neighbors.CANDIDATES:
        result = neighbors.evaluate(rows, variant, position_qty=position)
        assert result.action == ("SELL" if position > 0 else "BUY")
        assert result.raw_payload["target_position_qty"] == 0
        assert result.raw_payload["signal_category"] == "exit"
        assert "stop_loss" not in result.raw_payload
        assert economic_signal(neighbors.evaluate(rows, "opening_drive_center", position_qty=position)) == economic_signal(
            original.evaluate(rows, "opening_drive", position_qty=position)
        )


@pytest.mark.parametrize("entry,deadline", [
    ("2026-03-05T10:00", "2026-03-05T15:55"),
    ("2026-03-09T10:00", "2026-03-09T15:55"),
    ("2024-07-03T10:00", "2024-07-03T13:10"),
])
def test_independent_clock_exactly_matches_original_calendar_and_outage_behavior(entry, deadline):
    for variant in neighbors.CANDIDATES:
        for event in (et(deadline) - timedelta(seconds=1), et(deadline), et(deadline) + timedelta(days=3)):
            assert neighbors.should_flatten(et(entry), event, variant) == original.should_flatten(et(entry), event, "opening_drive")
        assert not neighbors.should_flatten(et(entry), et(deadline) - timedelta(seconds=1), variant)
        assert neighbors.should_flatten(et(entry), et(deadline), variant)


def test_interleaved_variants_do_not_mutate_shared_rules_or_input_candles():
    definitions = deepcopy(original.CANDIDATES)
    rows = opening_rows()
    initial_rows = deepcopy(rows)
    original_signal = original.evaluate(rows, "opening_drive")
    before = {variant: neighbors.evaluate(rows, variant) for variant in neighbors.CANDIDATES}
    for variant in reversed(neighbors.CANDIDATES):
        assert neighbors.evaluate(rows, variant) == before[variant]
    assert rows == initial_rows
    assert original.CANDIDATES == definitions
    assert original.evaluate(rows, "opening_drive") == original_signal


def test_runner_style_loading_without_file_global_supports_private_original_dependency():
    # research_topbot compiles the fixture into a module without defining __file__.
    # This must keep working and may read source code only, never historical data.
    from pathlib import Path
    from types import ModuleType
    fixture = ModuleType("app.services._synthetic_neighbor_fixture")
    fixture.__package__ = "app.services"
    source = Path(neighbors.__file__).read_text(encoding="utf-8")
    exec(compile(source, neighbors.__file__, "exec"), fixture.__dict__)
    assert fixture.CANDIDATES == neighbors.CANDIDATES
    assert fixture.evaluate(opening_rows(), "opening_drive_center") == neighbors.evaluate(opening_rows(), "opening_drive_center")


def test_center_and_original_produce_identical_synthetic_execution_ledgers():
    from app.services import bot_backtesting as replay
    from app.services.topbot import TOPBOT_SETTINGS
    from test_bot_backtesting import _config
    from tools.research_topbot import make_engine_class

    rows = opening_rows()
    minutes = []
    for timestamp, price in [
        ("2026-07-06T09:59", 20_070), ("2026-07-06T10:00", 20_070),
        ("2026-07-06T10:01", 20_070), ("2026-07-06T15:54", 20_080),
        ("2026-07-06T15:55", 20_080), ("2026-07-06T15:56", 20_080),
    ]:
        candle = deepcopy(rows[-1])
        candle.candle_timestamp, candle.unit_number = et(timestamp), 1
        candle.open_price = candle.close_price = price
        candle.high_price, candle.low_price = price + 1, price - 1
        minutes.append(candle)
    results = []
    for fixture, variant in [(original, "opening_drive"), (neighbors, "opening_drive_center")]:
        settings = deepcopy(TOPBOT_SETTINGS)
        settings.update(fixture.get_settings(variant))
        config = _config(user_id="research-test", contract_id="CON.F.US.MNQ.U26", **settings)
        assert config.max_daily_loss == 250
        assert config.order_size == config.max_contracts == config.max_open_position == 1
        engine = make_engine_class(replay, fixture, variant)(
            config=config, candles=deepcopy(rows), execution_candles=deepcopy(minutes),
            settings=replay.BacktestSettings(
                start=minutes[0].candle_timestamp, end=et("2026-07-06T15:57"),
                starting_balance=50_000, commission_per_contract=.61,
                slippage_ticks=1, tick_size=.25, tick_value=.5,
            ),
        )
        results.append(engine.run())
    assert len(results[0]["trades"]) == 1
    assert results[0]["trades"][0]["entry_timestamp"] == et("2026-07-06T10:00").isoformat()
    assert results[0]["trades"][0]["exit_timestamp"] == et("2026-07-06T15:55").isoformat()
    assert results[0]["trades"][0]["exit_reason"] == "scheduled_session_flatten"
    assert results[0]["trades"] == results[1]["trades"]
    assert results[0]["equity_curve"] == results[1]["equity_curve"]
    assert results[0]["metrics"] == results[1]["metrics"]
