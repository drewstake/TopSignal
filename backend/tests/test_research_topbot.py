from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.research_topbot import (
    concentration, create_run_directory, make_engine_class, resolve_periods,
    historical_screen, session_bootstrap, write_new_json,
)


def test_manifests_and_run_directories_cannot_overwrite_previous_evidence(tmp_path):
    first = create_run_directory(tmp_path, "same-hypothesis")
    second = create_run_directory(tmp_path, "same-hypothesis")
    assert first != second
    manifest = first / "manifest.json"
    write_new_json(manifest, {"hypothesis": "before the test"})
    with pytest.raises(FileExistsError):
        write_new_json(manifest, {"hypothesis": "after seeing the outcome"})
    assert "before the test" in manifest.read_text()
    with pytest.raises(ValueError):
        create_run_directory(tmp_path, "../overwrite")


def test_chronological_periods_keep_all_existing_history_and_fixed_boundary():
    start = datetime(2019, 5, 5, tzinfo=timezone.utc)
    end = datetime(2026, 7, 10, tzinfo=timezone.utc)
    periods = resolve_periods((start, end), ["full", "development", "diagnostic"])
    assert periods["full"] == (start, end)
    assert periods["development"] == (start, datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert periods["diagnostic"] == (periods["development"][1], end)
    with pytest.raises(ValueError, match="does not overlap"):
        resolve_periods((start, datetime(2023, 1, 1, tzinfo=timezone.utc)), ["diagnostic"])


def test_session_block_bootstrap_retains_zero_sessions_and_signed_losses():
    sessions = [{"net_pnl": -10.0} for _ in range(21)] + [{"net_pnl": 0.0} for _ in range(21)]
    result = session_bootstrap(sessions, repetitions=200)
    assert result == session_bootstrap(sessions, repetitions=200)
    assert result["session_count"] == 42
    assert result["mean_session_pnl"] == -5
    assert [row["block_sessions"] for row in result["estimates"]] == [5, 20]
    for estimate in result["estimates"]:
        assert estimate["resampled_fraction_positive"] == 0
        low, high = estimate["mean_session_pnl_95_percent_interval"]
        assert -10 <= low <= -5 <= high <= 0
    assert session_bootstrap([], repetitions=200)["estimates"] == []


def test_profit_concentration_exposes_one_trade_and_one_year_dependence():
    trades = [{"net_pnl": value} for value in [1000, 10, -300, -300]]
    sessions = [
        {"session": "2020-01-01", "net_pnl": 1000},
        {"session": "2021-01-01", "net_pnl": -590},
    ]
    result = concentration(trades, sessions)
    assert result["net_excluding_best_trade"] == -590
    assert result["net_excluding_best_calendar_year"] == -590
    assert result["top_1_percent_positive_profit_share"] == pytest.approx(1000 / 1010)


def test_observational_session_marks_include_commissions_and_fresh_state():
    class FakeEngine:
        def __init__(self, **_kwargs):
            self.settings = SimpleNamespace(starting_balance=50_000, tick_size=.25, tick_value=.5)
            self.cash = 50_000
            self.position = None

        def _record_equity(self, **_kwargs):
            pass

        def _replace_last_equity(self, **_kwargs):
            pass

    fixture = SimpleNamespace(evaluate=lambda candles, variant, position_qty: (candles, variant, position_qty))
    replay = SimpleNamespace(
        BacktestEngine=FakeEngine, trading_day_date=lambda timestamp: timestamp.date(),
        _price_pnl=lambda **kwargs: (kwargs["exit"] - kwargs["entry"]) * kwargs["quantity"] * 2 * (1 if kwargs["side"] == "long" else -1),
    )
    engine_type = make_engine_class(replay, fixture, "test")
    engine = engine_type()
    fresh = engine_type()
    timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    engine.cash = 49_998.8
    engine.position = SimpleNamespace(side="short", quantity=1, entry_price=10)
    assert engine._evaluate_topbot_adaptive(["closed candle"])[2] == -1
    engine._record_equity(event_time=timestamp, mark_price=9)
    assert engine.session_ledger()[0]["net_pnl"] == pytest.approx(.8)
    engine.position = None
    engine.cash = 49_999.6
    engine._replace_last_equity(event_time=timestamp)
    assert engine.session_ledger()[0]["net_pnl"] == pytest.approx(-.4)
    assert fresh.session_ledger() == []
    assert fresh.cash == 50_000


def test_historical_screen_does_not_promote_missing_or_reused_evidence():
    assert historical_screen({})["status"] == "incomplete"
    passing = {
        "metrics": {"trade_count": 600, "net_pnl": 2000, "profit_factor": 1.2},
        "years": {str(year): {"mark_to_market_net_pnl": 300} for year in range(2020, 2026)},
        "uncertainty": {"estimates": [
            {"block_sessions": block, "mean_session_pnl_95_percent_interval": [1, 10]}
            for block in (5, 20)
        ]},
        "concentration": {"net_excluding_best_5_trades": 1000, "net_excluding_best_calendar_year": 1700},
    }
    summaries = {(period, slip): passing for period in ("full", "development", "diagnostic") for slip in (1.0, 2.0)}
    screen = historical_screen(summaries)
    assert screen["status"] == "passes_measured_gates_only"
    assert not screen["confirmed_profitability"]
    assert len(screen["pending_requirements"]) == 2
    summaries[("diagnostic", 2.0)] = {"metrics": {"net_pnl": -1}}
    screen = historical_screen(summaries)
    assert screen["status"] == "fails_registered_screen"
    assert [row["gate"] for row in screen["gates"] if row["status"] == "fail"] == ["diagnostic_net_at_2_ticks"]


@pytest.mark.parametrize("resting_bracket_filled", [False, True])
def test_calendar_risk_exit_uses_observed_open_and_preserves_resting_priority(resting_bracket_filled):
    entry = datetime(2024, 7, 3, 14, tzinfo=timezone.utc)
    observed = datetime(2024, 7, 3, 17, 2, tzinfo=timezone.utc)
    calls = []

    class FakeEngine:
        def __init__(self, **_kwargs):
            self.position = SimpleNamespace(entry_timestamp=entry)

        def _process_open_gap(self, candle):
            calls.append(("resting", candle.candle_timestamp))
            if resting_bracket_filled:
                self.position = None

        def _update_excursion(self, low, high):
            calls.append(("excursion", low, high))

        def _close_position(self, **kwargs):
            calls.append(("market_exit", kwargs))
            self.position = None

    def should_flatten(entry_time, event_time, variant):
        calls.append(("risk_decision", entry_time, event_time, variant))
        return True

    fixture = SimpleNamespace(should_flatten=should_flatten)
    engine_type = make_engine_class(SimpleNamespace(BacktestEngine=FakeEngine), fixture, "calendar")
    engine = engine_type()
    engine._process_open_gap(SimpleNamespace(candle_timestamp=observed, open_price=101.25))
    assert calls[0] == ("resting", observed)
    if resting_bracket_filled:
        assert len(calls) == 1
    else:
        assert calls[1] == ("risk_decision", entry, observed, "calendar")
        assert calls[2] == ("excursion", 101.25, 101.25)
        assert calls[3] == ("market_exit", {"raw_exit_price": 101.25, "exit_timestamp": observed, "exit_reason": "scheduled_session_flatten"})
    assert engine.position is None


def test_calendar_risk_hook_is_disabled_for_unmodified_control_fixture():
    class FakeEngine:
        def __init__(self, **_kwargs):
            self.position = SimpleNamespace(entry_timestamp=None)

        def _process_open_gap(self, _candle):
            pass

    engine_type = make_engine_class(SimpleNamespace(BacktestEngine=FakeEngine), SimpleNamespace(), "control")
    engine = engine_type()
    position = engine.position
    engine._process_open_gap(SimpleNamespace())
    assert engine.position is position


def test_two_fresh_chronological_replays_reconcile_exact_marks_and_clock_exits():
    from app.services import bot_backtesting as replay
    from test_bot_backtesting import BASE_TIME, _candle, _config, _scripted_evaluator

    fixture = SimpleNamespace(should_flatten=lambda entry, event, _variant: event >= entry + timedelta(minutes=2))
    engine_type = make_engine_class(replay, fixture, "fixed_clock_exit")
    ledgers = []
    for day in range(2):
        start = BASE_TIME + timedelta(days=day)
        # The five-minute aggregate beginning at +5 is absent; the independent
        # risk clock still exits at +7 using that observed minute's opening.
        signals = [_candle(start), _candle(start + timedelta(minutes=10))]
        minutes = [_candle(start + timedelta(minutes=i), unit_number=1) for i in range(20)]
        evaluator = _scripted_evaluator({start: {
            "action": "BUY", "price": 100,
            "payload": {"stop_loss": 90, "take_profit": 120, "target_position_qty": 1},
        }})
        engine = engine_type(
            config=_config(strategy_type="topbot_adaptive", max_contracts=1, max_open_position=1),
            candles=signals, execution_candles=minutes, signal_evaluator=evaluator,
            settings=replay.BacktestSettings(
                start=start, end=start + timedelta(minutes=20), starting_balance=50_000,
                commission_per_contract=1.2, slippage_ticks=1, tick_size=.25, tick_value=.5,
            ),
        )
        result = engine.run()
        ledger = engine.session_ledger()
        ledgers.append(ledger)
        assert result["metrics"]["trade_count"] == 1
        assert result["trades"][0]["exit_timestamp"] == (start + timedelta(minutes=7)).isoformat()
        assert result["trades"][0]["exit_reason"] == "scheduled_session_flatten"
        assert result["metrics"]["net_pnl"] == pytest.approx(-3.4)
        assert sum(row["net_pnl"] for row in ledger) == pytest.approx(-3.4)
        assert ledger[-1]["ending_equity"] == pytest.approx(49_996.6)
        assert engine.research_bar_count == 20
        assert engine.research_exposure["long"] == 2
    assert ledgers[0][0]["session"] != ledgers[1][0]["session"]
