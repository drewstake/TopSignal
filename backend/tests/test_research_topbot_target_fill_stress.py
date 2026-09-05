"""Synthetic execution/report tests; no history, provider or reserved data."""
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import bot_backtesting as replay
from tools import research_topbot as original_runner
from tools import research_topbot_target_fill_stress as stress
from test_bot_backtesting import BASE_TIME, _candle, _config, _scripted_evaluator


def build_engine(direction=1, *, stress_enabled=True, slippage=1, repeat_entry=False, flatten_at=None, mutate=None):
    signals = [_candle(BASE_TIME + timedelta(minutes=index)) for index in (0, 5, 10, 15)]
    minutes = [_candle(BASE_TIME + timedelta(minutes=index), unit_number=1) for index in range(20)]
    entry = 100 + direction * slippage * .25
    stop, target = entry - direction * 5, entry + direction * 10
    if mutate:
        mutate(minutes, stop, target)
    instruction = {"action": "BUY" if direction > 0 else "SELL", "price": 100,
                   "payload": {"stop_loss": 100 - direction * 5, "take_profit": 100 + direction * 10,
                               "target_position_qty": direction}}
    script = {BASE_TIME: instruction}
    if repeat_entry:
        script[BASE_TIME + timedelta(minutes=5)] = instruction
    fixture = SimpleNamespace()
    if flatten_at is not None:
        fixture.should_flatten = lambda _entry, event, _variant: event >= flatten_at
    engine_type = (stress.make_stress_engine_class if stress_enabled else original_runner.make_engine_class)(replay, fixture, "synthetic_test")
    return engine_type(
        config=_config(strategy_type="topbot_adaptive", max_daily_loss=250, max_contracts=1, max_open_position=1),
        candles=signals, execution_candles=minutes, signal_evaluator=_scripted_evaluator(script),
        settings=replay.BacktestSettings(
            start=BASE_TIME, end=BASE_TIME + timedelta(minutes=20), starting_balance=50_000,
            commission_per_contract=.61, slippage_ticks=slippage, tick_size=.25, tick_value=.5,
            force_close_at_end=False,
        ),
    )


def target_bar(row, *, target, direction, through=0, gap=False):
    row.open_price = target + direction * through if gap else 100
    row.close_price = row.open_price
    row.high_price = target + through if direction > 0 else 101
    row.low_price = 99 if direction > 0 else target - through


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("gap", [False, True])
@pytest.mark.parametrize("through", [0, .125])
def test_touch_or_less_than_one_tick_keeps_position_and_original_brackets(direction, gap, through):
    def modify(minutes, _stop, target):
        target_bar(minutes[6], target=target, direction=direction, through=through, gap=gap)
    engine = build_engine(direction, mutate=modify)
    result = engine.run()
    assert result["trades"] == []
    assert engine.position is not None
    assert engine.position.entry_timestamp == BASE_TIME + timedelta(minutes=5)
    assert engine.position.take_profit == engine.position.entry_price + direction * 10
    assert engine.position.stop_loss == engine.position.entry_price - direction * 5
    assert engine.daily_entry_counts[replay.trading_day_date(BASE_TIME)] == 1
    assert engine.cash == pytest.approx(50_000 - .61)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("gap", [False, True])
@pytest.mark.parametrize("slippage", [1, 2, 4])
def test_exact_tick_through_fills_original_target_with_original_fees_and_slippage(direction, gap, slippage):
    expected_target = 100 + direction * (10 + slippage * .25)
    def modify(minutes, _stop, target):
        target_bar(minutes[6], target=target, direction=direction, through=.25, gap=gap)
    engine = build_engine(direction, slippage=slippage, mutate=modify)
    result = engine.run()
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "take_profit"
    assert trade["exit_timestamp"] == (BASE_TIME + timedelta(minutes=6 if gap else 7)).isoformat()
    assert trade["take_profit"] == expected_target
    assert trade["exit_price"] == expected_target - direction * slippage * .25
    assert trade["commission"] == 1.22
    assert trade["net_pnl"] == pytest.approx(20 - slippage * .5 - 1.22)
    assert engine.position is None


@pytest.mark.parametrize("direction", [-1, 1])
def test_large_favorable_gap_does_not_improve_target_fill_price(direction):
    def modify(minutes, _stop, target):
        target_bar(minutes[6], target=target, direction=direction, through=20, gap=True)
    result = build_engine(direction, mutate=modify).run()
    trade = result["trades"][0]
    assert trade["exit_price"] == trade["take_profit"] - direction * .25


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("through", [0, .25])
def test_same_bar_stop_priority_and_ambiguity_label_are_unchanged(direction, through):
    def modify(minutes, stop, target):
        row = minutes[6]
        row.high_price = target + through if direction > 0 else stop
        row.low_price = stop if direction > 0 else target - through
    stressed = build_engine(direction, mutate=modify).run()
    original = build_engine(direction, stress_enabled=False, mutate=modify).run()
    assert stressed["trades"] == original["trades"]
    assert stressed["trades"][0]["exit_reason"] == "stop_loss_same_bar_conservative"


@pytest.mark.parametrize("direction", [-1, 1])
def test_adverse_stop_gap_precedes_later_target_cross_and_keeps_actual_gap_fill(direction):
    def modify(minutes, stop, target):
        row = minutes[6]
        row.open_price = row.close_price = stop - direction * 2
        row.high_price = target + .25 if direction > 0 else row.open_price + 1
        row.low_price = row.open_price - 1 if direction > 0 else target - .25
    stressed = build_engine(direction, mutate=modify).run()
    original = build_engine(direction, stress_enabled=False, mutate=modify).run()
    assert stressed["trades"] == original["trades"]
    trade = stressed["trades"][0]
    assert trade["exit_reason"] == "stop_loss_gap"
    assert trade["exit_price"] == trade["stop_loss"] - direction * 2.25


@pytest.mark.parametrize("direction", [-1, 1])
def test_touch_then_later_confirmation_waits_for_observed_cross(direction):
    def modify(minutes, _stop, target):
        target_bar(minutes[6], target=target, direction=direction)
        target_bar(minutes[8], target=target, direction=direction, through=.25)
    result = build_engine(direction, mutate=modify).run()
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_timestamp"] == (BASE_TIME + timedelta(minutes=9)).isoformat()


@pytest.mark.parametrize("direction", [-1, 1])
def test_retained_position_changes_later_entry_opportunities_without_pyramiding(direction):
    def modify(minutes, _stop, target):
        target_bar(minutes[6], target=target, direction=direction)
    stressed = build_engine(direction, repeat_entry=True, mutate=modify)
    original = build_engine(direction, stress_enabled=False, repeat_entry=True, mutate=modify)
    stressed.run()
    original.run()
    day = replay.trading_day_date(BASE_TIME)
    assert stressed.daily_entry_counts[day] == 1
    assert original.daily_entry_counts[day] == 2
    assert stressed.position.quantity == original.position.quantity == 1
    assert stressed.position.entry_timestamp == BASE_TIME + timedelta(minutes=5)
    assert original.position.entry_timestamp == BASE_TIME + timedelta(minutes=10)


@pytest.mark.parametrize("direction", [-1, 1])
def test_unconfirmed_gap_target_still_reaches_independent_calendar_flatten(direction):
    def modify(minutes, _stop, target):
        target_bar(minutes[6], target=target, direction=direction, gap=True)
    engine = build_engine(direction, mutate=modify, flatten_at=BASE_TIME + timedelta(minutes=6))
    result = engine.run()
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_reason"] == "scheduled_session_flatten"
    assert result["trades"][0]["exit_timestamp"] == (BASE_TIME + timedelta(minutes=6)).isoformat()
    assert engine.position is None


@pytest.mark.parametrize("direction", [-1, 1])
def test_confirmed_gap_target_keeps_resting_order_priority_over_calendar_exit(direction):
    def modify(minutes, _stop, target):
        target_bar(minutes[6], target=target, direction=direction, through=.25, gap=True)
    engine = build_engine(direction, mutate=modify, flatten_at=BASE_TIME + timedelta(minutes=6))
    result = engine.run()
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_reason"] == "take_profit"


def test_base_module_and_runner_remain_unmodified_and_results_have_distinct_assumptions():
    original_factory = original_runner.make_engine_class
    original_writer = original_runner.write_new_json
    result = build_engine().run()
    assumptions = result["assumptions"]
    assert replay.BACKTEST_ENGINE_VERSION == stress.BASE_ENGINE_VERSION
    assert original_runner.make_engine_class is original_factory
    assert original_runner.write_new_json is original_writer
    assert result["engine_version"] == assumptions["engine_version"] == stress.STRESS_ENGINE_VERSION
    assert result["execution_model"] == assumptions["execution_model"] == stress.EXECUTION_MODEL
    assert assumptions["base_engine_version"] == stress.BASE_ENGINE_VERSION
    assert assumptions["target_confirmation_ticks"] == 1
    assert assumptions["commission_per_contract"] == .61
    assert "target_confirmation_ticks" not in build_engine(stress_enabled=False).run()["assumptions"]


def test_manifest_is_stamped_before_fingerprinting_and_all_case_reports_are_labeled(tmp_path):
    runner = stress.load_private_runner()
    manifest = {"engine_version": replay.BACKTEST_ENGINE_VERSION, "execution_minutes": 1,
                "entry_delay_minutes": 0, "costs": {"commission_per_side": .61}}
    runner.write_new_json(tmp_path / "manifest.json", manifest)
    saved = json.loads((tmp_path / "manifest.json").read_text())
    fingerprint = runner.fingerprint(manifest)
    assert fingerprint == runner.fingerprint(saved)
    assert saved["engine_version"] == stress.STRESS_ENGINE_VERSION
    assert saved["execution_sensitivity"]["target_confirmation_ticks"] == 1
    assert saved["costs"]["commission_per_side"] == .61
    started = {"hypothesis_manifest_sha256": fingerprint}
    runner.write_new_json(tmp_path / "case.started.json", started)
    assert json.loads((tmp_path / "case.started.json").read_text())["hypothesis_manifest_sha256"] == fingerprint
    for name in ("case.replay.json", "case.summary.json", "case.failure.json", "results.json"):
        report = {"manifest_sha256": fingerprint}
        if name == "results.json":
            report["results"] = {"case": {"status": "failed"}}
        runner.write_new_json(tmp_path / name, report)
        assert json.loads((tmp_path / name).read_text())["engine_version"] == stress.STRESS_ENGINE_VERSION
    assert report["results"]["case"]["execution_model"] == stress.EXECUTION_MODEL
    with pytest.raises(FileExistsError):
        runner.write_new_json(tmp_path / "manifest.json", manifest)


def test_private_runner_freezes_wrapper_source_alongside_original_dependencies(tmp_path):
    runner = stress.load_private_runner()
    bundle = runner.capture_sources(tmp_path, stress.BACKEND / "tools/fixtures/topbot_research.py")
    wrapper_path = "backend/tools/research_topbot_target_fill_stress.py"
    assert wrapper_path in bundle["files"]
    assert (tmp_path / "sources" / wrapper_path).read_bytes() == Path(stress.__file__).read_bytes()
    for dependency in stress.SOURCE_HASHES:
        assert "backend/" + dependency in bundle["files"]


@pytest.mark.parametrize("arguments", [
    ["--variants", "opening_drive"],
    ["--variants", "opening_drive", "--commission-per-side", "1.20"],
    ["--variants", "baseline_v5", "--commission-per-side", "0.61"],
    ["--variants", "opening_drive", "--commission-per-side", "0.61", "--execution-minutes", "5"],
    ["--variants", "opening_drive", "--commission-per-side", "0.61", "--entry-delay-minutes", "1"],
    ["--variants", "opening_drive", "--commission-per-side", "0.61", "--fixture", "other.py"],
])
def test_cli_requires_exact_preregistered_model_and_explicit_base_fee(arguments):
    with pytest.raises(SystemExit) as error:
        stress.validate_stress_arguments(arguments)
    assert error.value.code == 2


def test_cli_accepts_registered_original_candidate_without_consuming_runner_options():
    stress.validate_stress_arguments([
        "--variants", "opening_drive", "--commission-per-side", "0.61",
        "--label", "synthetic-validation-only", "--periods", "full", "development", "diagnostic",
        "--slippage-ticks", "1", "2", "4", "--protocol", "existing.md",
    ])


def test_help_shows_required_fee_and_restricted_stress_choices(monkeypatch, capsys):
    monkeypatch.setattr(stress.sys, "argv", ["target-stress", "--help"])
    with pytest.raises(SystemExit) as error:
        stress.main()
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--commission-per-side {0.61}" in help_text
    assert "--execution-minutes {1}" in help_text
    assert "--entry-delay-minutes {0}" in help_text
    assert "{1,5}" not in help_text
    assert "{0,1}" not in help_text


def test_source_pin_detects_changed_dependencies_without_modifying_source(monkeypatch):
    monkeypatch.setattr(stress, "SOURCE_HASHES", {"tools/research_topbot.py": "incorrect"})
    with pytest.raises(RuntimeError, match="Frozen target-stress dependency changed"):
        stress.verify_sources()
