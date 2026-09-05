"""Behavior and integration coverage for the single TopBot setup."""

import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.models import BotConfig
from app.bot_schemas import BotBacktestIn
from app.services import bot_backtesting as replay
from app.services import bot_service
from app.services.topbot import TOPBOT_SETTINGS
from app.services.topbot_strategy import evaluate, HISTORY_BARS, REVISION, RULES


def setup_candles(short=False):
    end = datetime(2026, 7, 6, 14, 5, tzinfo=timezone.utc)
    rows = []
    for index in range(220):
        close = 100 - (219 - index) * 0.25
        rows.append(SimpleNamespace(
            user_id="test", contract_id="CON.F.US.MNQ.U26", symbol="MNQ", live=False,
            unit="minute", unit_number=5, is_partial=False, fetched_at=None, raw_payload=None,
            candle_timestamp=end - timedelta(minutes=5 * (219 - index)),
            open_price=close - 0.25, high_price=close + 1.5,
            low_price=close - 1.5, close_price=close, volume=100,
            source_instrument_id=1, source_raw_symbol="MNQU6",
        ))
    rows[-2].open_price, rows[-2].high_price, rows[-2].low_price, rows[-2].close_price = 99, 99.5, 97, 98
    rows[-1].open_price, rows[-1].high_price, rows[-1].low_price, rows[-1].close_price = 98, 100.25, 97.75, 100
    if short:
        for row in rows:
            row.open_price, row.close_price = 200 - row.open_price, 200 - row.close_price
            row.high_price, row.low_price = 200 - row.low_price, 200 - row.high_price
    return rows


def config():
    return BotConfig(id=1, user_id="test", account_id=1, name="TopBot", provider="projectx",
                     enabled=False, execution_mode="dry_run", contract_id="CON.F.US.MNQ.U26",
                     **deepcopy(TOPBOT_SETTINGS))


@pytest.mark.parametrize("short,action,stop,target", [(False, "BUY", 50, 150), (True, "SELL", 150, 50)])
def test_pullback_confirmation_has_fixed_fifty_point_bracket(short, action, stop, target, monkeypatch):
    signal = evaluate(setup_candles(short))
    assert signal.action == action
    assert signal.price == 100
    assert signal.raw_payload["stop_loss"] == stop
    assert signal.raw_payload["take_profit"] == target
    assert signal.raw_payload["planned_risk_points"] == 50
    assert signal.raw_payload["planned_reward_points"] == 50
    assert signal.raw_payload["reward_r_multiple"] == 1
    assert signal.raw_payload["strategy_revision"] == REVISION
    assert signal.raw_payload["signal_category"] == "entry"
    assert signal.raw_payload["target_position_qty"] == (-1 if short else 1)
    assert signal.raw_payload["exit_policy"] == "bracket_only"
    assert "ensemble" not in signal.raw_payload
    assert "topbot_management" not in signal.raw_payload
    if short:
        assert signal.raw_payload["short_entry_allowed"] is True
        assert signal.raw_payload["ema"] < signal.raw_payload["short_trend_ema"]
        assert signal.raw_payload["short_trend_ema_slope"] < 0
    monkeypatch.setattr(bot_service, "_instrument_tick_size", lambda *a, **k: .25)
    assert bot_service._strategy_bracket_payloads(
        None, contract_id="CON.F.US.MNQ.U26", symbol="MNQ", action=action,
        entry_price=signal.price, decision_payload=signal.raw_payload,
    ) == {
        "stopLossBracket": {"ticks": 200, "type": 4},
        "takeProfitBracket": {"ticks": 200, "type": 1},
    }


def patch_short_trend(monkeypatch, *, current=110.0, prior=109.0):
    original = bot_service._ema_series
    def ema(values, *, period):
        if period == 50:
            return [prior] * (len(values) - 1) + [current]
        return original(values, period=period)
    monkeypatch.setattr(bot_service, "_ema_series", ema)


@pytest.mark.parametrize("current,prior", [(110, 109), (110, 110), (89, 90)])
def test_short_needs_alignment_and_strictly_falling_trend(monkeypatch, current, prior):
    patch_short_trend(monkeypatch, current=current, prior=prior)
    signal = evaluate(setup_candles(short=True))
    assert signal.action == "HOLD"
    assert signal.raw_payload["short_entry_allowed"] is False
    assert "signal_category" not in signal.raw_payload
    assert "target_position_qty" not in signal.raw_payload
    assert "short entry blocked" in signal.reason
    assert "stop_loss" not in signal.raw_payload
    assert "take_profit" not in signal.raw_payload


def test_long_entry_is_not_gated_by_the_extra_short_ema(monkeypatch):
    original = bot_service._ema_series
    def ema(values, *, period):
        assert period == 20, "Longs must not consult the short-entry filter"
        return original(values, period=period)
    monkeypatch.setattr(bot_service, "_ema_series", ema)
    signal = evaluate(setup_candles())
    assert signal.action == "BUY"
    assert signal.raw_payload["stop_loss"] == 50
    assert signal.raw_payload["take_profit"] == 150


@pytest.mark.parametrize("initial_side", [None, "long", "short"])
def test_blocked_short_preserves_existing_position_in_replay(monkeypatch, initial_side):
    patch_short_trend(monkeypatch)
    rows = setup_candles(short=True)
    filtered_short = evaluate(rows)
    signal_time = rows[-1].candle_timestamp
    next_bar = deepcopy(rows[-1])
    next_bar.candle_timestamp += timedelta(minutes=5)
    next_bar.open_price = next_bar.close_price = 100
    next_bar.high_price, next_bar.low_price = 101, 99
    rows.append(next_bar)

    def evaluator(candles):
        latest = candles[-1]
        if latest.candle_timestamp == signal_time:
            return filtered_short
        action, payload = "HOLD", {}
        if initial_side and latest.candle_timestamp == rows[-3].candle_timestamp:
            direction = 1 if initial_side == "long" else -1
            action = "BUY" if direction == 1 else "SELL"
            payload = {"stop_loss": latest.close_price - direction * 50,
                       "take_profit": latest.close_price + direction * 50}
        return bot_service.SignalResult(action=action, reason="fixture", candle_timestamp=latest.candle_timestamp,
                                        price=latest.close_price, raw_payload=payload)

    output = replay.run_backtest(
        config=config(), candles=rows, start=rows[-3].candle_timestamp,
        end=rows[-1].candle_timestamp + timedelta(minutes=5), starting_balance=50000,
        commission_per_contract=1.2, slippage_ticks=1, tick_size=.25, tick_value=.5,
        signal_evaluator=evaluator, include_evaluation_split=False,
    )
    if initial_side is None:
        assert output["trades"] == []
    else:
        assert len(output["trades"]) == 1
        assert output["trades"][0]["side"] == initial_side
        assert output["trades"][0]["exit_reason"] == "forced_end_of_test"


@pytest.mark.parametrize("short", [False, True])
def test_opposite_entry_cannot_flatten_or_reverse_an_open_trade(short):
    rows = setup_candles(short=short)
    signal = evaluate(rows)
    signal_time = rows[-1].candle_timestamp
    next_bar = deepcopy(rows[-1])
    next_bar.candle_timestamp += timedelta(minutes=5)
    rows.append(next_bar)
    initial_direction = 1 if short else -1

    def evaluator(candles):
        latest = candles[-1]
        if latest.candle_timestamp == signal_time:
            return signal
        if latest.candle_timestamp == rows[-3].candle_timestamp:
            return bot_service.SignalResult(
                action="BUY" if initial_direction > 0 else "SELL", reason="initial position",
                candle_timestamp=latest.candle_timestamp, price=latest.close_price,
                raw_payload={"stop_loss": latest.close_price - initial_direction * 50,
                             "take_profit": latest.close_price + initial_direction * 50,
                             "target_position_qty": float(initial_direction)},
            )
        return bot_service.SignalResult(action="HOLD", reason="fixture", candle_timestamp=latest.candle_timestamp,
                                       price=latest.close_price, raw_payload={})

    result = replay.run_backtest(
        config=config(), candles=rows, start=rows[-3].candle_timestamp,
        end=rows[-1].candle_timestamp + timedelta(minutes=5), starting_balance=50000,
        commission_per_contract=1.2, slippage_ticks=1, tick_size=.25, tick_value=.5,
        signal_evaluator=evaluator, include_evaluation_split=False,
    )
    assert len(result["trades"]) == 1
    assert result["trades"][0]["side"] == ("long" if short else "short")
    assert result["trades"][0]["exit_reason"] == "forced_end_of_test"
    assert any("atomic reversal not supported" in note for note in result["notes"])


@pytest.mark.parametrize("case", ["warmup", "partial", "opening_missing", "middle_missing", "no_volume", "no_touch", "no_confirmation", "vwap_opposed", "wrong_instrument", "wrong_timeframe", "weekend", "session_ended"])
def test_setup_waits_when_a_required_condition_is_missing(case):
    rows = setup_candles()
    if case == "warmup":
        rows = rows[-(HISTORY_BARS - 1):]
    elif case == "partial":
        rows[-1].is_partial = True
    elif case in {"opening_missing", "middle_missing"}:
        del rows[-8 if case == "opening_missing" else -4]
    elif case == "no_volume":
        for row in rows: row.volume = 0
    elif case == "no_touch":
        rows[-2].low_price = 98
    elif case == "no_confirmation":
        rows[-1].close_price = 99.25
    elif case == "vwap_opposed":
        rows[-8].high_price = 1000
        rows[-8].volume = 10000
    elif case == "wrong_instrument":
        for row in rows: row.symbol, row.contract_id = "MES", "CON.F.US.MES.U26"
    elif case == "wrong_timeframe":
        for row in rows: row.unit_number = 1
    elif case == "weekend":
        for row in rows: row.candle_timestamp -= timedelta(days=1)
    elif case == "session_ended":
        for row in rows: row.candle_timestamp += timedelta(hours=5, minutes=40)
    assert evaluate(rows).action == "HOLD"


def test_bracket_distance_does_not_depend_on_pullback_size_or_atr(monkeypatch):
    rows = setup_candles()
    rows[-2].low_price = 40
    monkeypatch.setattr(bot_service, "_atr_series", lambda *a, **k: pytest.fail("ATR-based stop"))
    signal = evaluate(rows)
    assert signal.action == "BUY"
    assert signal.raw_payload["stop_loss"] == 50
    assert signal.raw_payload["take_profit"] == 150


def test_late_or_duplicate_session_candles_do_not_create_an_entry():
    rows = setup_candles()
    rows[-3].candle_timestamp = rows[-4].candle_timestamp
    assert evaluate(rows).action == "HOLD"


@pytest.mark.parametrize("exit_reason,high,low,exit_price", [
    ("take_profit", 151, 99, 150.25),
    ("stop_loss", 101, 50, 50.25),
])
def test_replay_matches_direct_signal_and_fills_only_on_the_next_bar(monkeypatch, exit_reason, high, low, exit_price):
    rows = setup_candles()
    signal_bar = rows[-1]
    next_bar = deepcopy(signal_bar)
    next_bar.candle_timestamp += timedelta(minutes=5)
    next_bar.open_price = next_bar.close_price = 100.25
    next_bar.high_price, next_bar.low_price = high, low
    rows.append(next_bar)
    seen = []
    original = bot_service.evaluate_topbot_adaptive
    def capture(candles, **kwargs):
        result = original(candles, **kwargs)
        seen.append(result)
        assert all(row.candle_timestamp <= candles[-1].candle_timestamp for row in candles)
        return result
    monkeypatch.setattr(bot_service, "evaluate_topbot_adaptive", capture)
    monkeypatch.setattr(bot_service, "dispatch_strategy_evaluator", lambda *a, **k: pytest.fail("ensemble dispatch"))
    output = replay.run_backtest(
        config=config(), candles=rows, start=signal_bar.candle_timestamp,
        end=next_bar.candle_timestamp + timedelta(minutes=5), starting_balance=50000,
        commission_per_contract=1.2, slippage_ticks=1, tick_size=.25, tick_value=.5,
        include_evaluation_split=False,
    )
    assert seen[0] == evaluate(rows[:-1])
    assert len(output["trades"]) == 1
    trade = output["trades"][0]
    assert trade["entry_timestamp"] == next_bar.candle_timestamp.isoformat()
    assert trade["entry_price"] == 100.5
    assert trade["exit_reason"] == exit_reason
    assert trade["exit_price"] == exit_price
    assert output["assumptions"]["synchronized_stream_count"] == 1
    assert output["config_snapshot"]["strategy_params"]["revision"] == REVISION


def test_replay_roll_clears_old_delivery_warmup():
    rows = setup_candles()
    rows[-2].source_instrument_id = rows[-1].source_instrument_id = 2
    rows[-2].source_raw_symbol = rows[-1].source_raw_symbol = "MNQZ6"
    output = replay.run_backtest(
        config=config(), candles=rows, start=rows[-3].candle_timestamp,
        end=rows[-1].candle_timestamp + timedelta(minutes=5), starting_balance=50000,
        commission_per_contract=1.2, slippage_ticks=1, tick_size=.25, tick_value=.5,
        include_evaluation_split=False,
    )
    assert output["trades"] == []
    assert any("delivery change" in note for note in output["notes"])


def test_new_backtest_uses_code_defaults_without_mutating_old_config():
    saved = config()
    saved.strategy_params = {"source_strategies": ["sma_cross"], "minimum_score": 0}
    saved.timeframe_unit_number, saved.order_size = 1, 4
    effective = replay._config_for_backtest_request(saved, BotBacktestIn(strategy_type="topbot_adaptive", instrument="MNQ"))
    assert effective.strategy_params == RULES
    assert effective.timeframe_unit_number == 5
    assert effective.order_size == 1
    assert saved.strategy_params == {"source_strategies": ["sma_cross"], "minimum_score": 0}
    assert saved.timeframe_unit_number == 1
    with pytest.raises(replay.BacktestConfigurationError, match="MNQ only"):
        replay._config_for_backtest_request(saved, BotBacktestIn(strategy_type="topbot_adaptive", instrument="MES"))


def test_topbot_does_not_load_auxiliary_or_benchmark_data():
    selected = config()
    rows = setup_candles()
    class NoStoreAccess:
        def __getattr__(self, name):
            pytest.fail(f"unexpected data-store call: {name}")
    streams = replay._load_databento_topbot_replay_streams(
        NoStoreAccess(), user_id="test", config=selected, root_symbol="MNQ", window=None,
        closed_by=rows[-1].candle_timestamp, primary_rows=rows, max_rows=1, replay_store=NoStoreAccess(),
    )
    assert list(streams) == ["asset:minute:5"]
    assert streams["asset:minute:5"] is rows
