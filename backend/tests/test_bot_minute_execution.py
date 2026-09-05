"""Signal-bar completeness must not determine whether resting orders execute."""

from datetime import timedelta

import pytest

from app.services import bot_backtesting as replay
from app.services import bot_service
from test_bot_backtesting import BASE_TIME, _candle, _config, _hold, _scripted_evaluator


def _minutes(count=20, *, omitted=()):
    return [
        _candle(BASE_TIME + timedelta(minutes=i), unit_number=1,
                open_price=100, high_price=101, low_price=99, close_price=100)
        for i in range(count) if i not in omitted
    ]


def _run(signals, minutes, evaluator, **kwargs):
    return replay.run_backtest(
        config=kwargs.pop("config", _config()), candles=signals,
        execution_candles=minutes, signal_evaluator=evaluator,
        start=BASE_TIME, end=BASE_TIME + timedelta(minutes=20),
        starting_balance=kwargs.pop("starting_balance", 50_000), commission_per_contract=1.2,
        slippage_ticks=1, tick_size=0.25, tick_value=0.5,
        include_evaluation_split=False, **kwargs,
    )


def _buy_script(*offsets, stop=95, target=120):
    return _scripted_evaluator({
        BASE_TIME + timedelta(minutes=i): {
            "action": "BUY", "price": 100,
            "payload": {"stop_loss": stop, "take_profit": target},
        } for i in offsets
    })


def test_resting_stop_executes_inside_an_omitted_signal_aggregate():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    minutes = _minutes()
    minutes[7].low_price = 90
    result = _run(signals, minutes, _buy_script(0))
    trade = result["trades"][0]
    assert trade["entry_timestamp"] == (BASE_TIME + timedelta(minutes=5)).isoformat()
    assert trade["exit_timestamp"] == (BASE_TIME + timedelta(minutes=8)).isoformat()
    assert trade["exit_reason"] == "stop_loss"
    assert trade["entry_price"] == 100.25
    assert trade["exit_price"] == 95.0  # anchored 95.25 stop less one adverse tick
    assert trade["commission"] == 2.4
    assert result["range"]["bar_count"] == 20
    assert result["assumptions"]["execution_stream"] == "observed_1m"


def test_only_exact_new_signal_bar_closes_are_evaluated():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    observed = []

    def evaluator(rows):
        observed.append(rows[-1].candle_timestamp)
        return _hold(rows)

    _run(signals, _minutes(), evaluator)
    assert observed == [row.candle_timestamp for row in signals]


def test_missing_next_minute_drops_pending_entry_instead_of_filling_late():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    result = _run(signals, _minutes(omitted=(5,)), _buy_script(0))
    assert result["trades"] == []
    assert any("missing next execution minute" in note for note in result["notes"])


def test_missing_minute_at_signal_close_does_not_evaluate_stale_signal():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    observed = []

    def evaluator(rows):
        observed.append(rows[-1].candle_timestamp)
        return _buy_script(0)(rows)

    result = _run(signals, _minutes(omitted=(4,)), evaluator)
    assert observed == [signals[-1].candle_timestamp]
    assert result["trades"] == []


def test_loss_cooldown_starts_when_intrabar_loss_is_certain():
    signals = [_candle(BASE_TIME + timedelta(minutes=i)) for i in (0, 5, 10)]
    minutes = _minutes()
    minutes[5].low_price = 90
    result = _run(signals, minutes, _buy_script(0, 5), config=_config(cooldown_seconds=300))
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_timestamp"] == (BASE_TIME + timedelta(minutes=6)).isoformat()
    assert any("cooldown after loss" in note for note in result["notes"])


def _rolled_streams():
    signals = [_candle(BASE_TIME + timedelta(minutes=i)) for i in (0, 5, 10, 15)]
    minutes = _minutes()
    for row in [*signals, *minutes]:
        old = row.candle_timestamp < BASE_TIME + timedelta(minutes=10)
        row.source_raw_symbol = "MNQM6" if old else "MNQU6"
        row.source_instrument_id = 101 if old else 202
    return signals, minutes


def test_carried_roll_fails_without_observed_old_delivery_open():
    signals, minutes = _rolled_streams()
    with pytest.raises(replay.InsufficientBacktestDataError, match="causal_roll_exit_missing"):
        _run(signals, minutes, _buy_script(0, stop=50))


def test_roll_liquidates_at_causal_old_delivery_open_and_charges_costs():
    signals, minutes = _rolled_streams()
    calls = []

    def resolver(previous, timestamp):
        calls.append((previous.source_raw_symbol, timestamp))
        row = _candle(timestamp, unit_number=1, open_price=90)
        row.source_raw_symbol = "MNQM6"
        row.source_instrument_id = 101
        return row

    result = _run(signals, minutes, _buy_script(0, stop=50), roll_exit_candle_resolver=resolver)
    trade = result["trades"][0]
    assert calls == [("MNQM6", BASE_TIME + timedelta(minutes=10))]
    assert trade["exit_reason"] == "contract_roll"
    assert trade["exit_timestamp"] == (BASE_TIME + timedelta(minutes=10)).isoformat()
    assert trade["exit_price"] == 89.75
    assert trade["net_pnl"] == -23.4
    assert trade["source_raw_symbol"] == "MNQM6"
    assert trade["source_instrument_id"] == 101
    assert trade["stop_loss"] == 50.25
    assert trade["take_profit"] == 120.25


def test_roll_resolver_cannot_backdate_a_future_old_delivery_minute():
    signals, minutes = _rolled_streams()

    def resolver(previous, timestamp):
        row = _candle(timestamp + timedelta(minutes=1), unit_number=1)
        row.source_raw_symbol = previous.source_raw_symbol
        row.source_instrument_id = previous.source_instrument_id
        return row

    with pytest.raises(replay.MalformedBacktestDataError, match="invalid_causal_roll_exit_candle"):
        _run(signals, minutes, _buy_script(0, stop=50), roll_exit_candle_resolver=resolver)


def test_real_topbot_uses_200_signal_bars_for_warmup_and_never_minute_bars(monkeypatch):
    from app.services.topbot_strategy import HISTORY_BARS

    first = BASE_TIME - timedelta(minutes=5 * (HISTORY_BARS - 1))
    signals = [_candle(first + timedelta(minutes=5 * i)) for i in range(HISTORY_BARS + 3)]
    minutes = [
        _candle(first + timedelta(minutes=i), unit_number=1)
        for i in range(5 * (HISTORY_BARS + 3))
    ]
    original = bot_service.evaluate_topbot_adaptive
    observations = []

    def observe(rows, *, strategy_params):
        observations.append((len(rows), rows[-1].candle_timestamp))
        assert all(row.unit == "minute" and row.unit_number == 5 for row in rows)
        return original(rows, strategy_params=strategy_params)

    monkeypatch.setattr(bot_service, "evaluate_topbot_adaptive", observe)
    config = _config(strategy_type="topbot_adaptive", lookback_bars=HISTORY_BARS)
    result = replay.run_backtest(
        config=config, candles=signals, execution_candles=minutes,
        start=first, end=BASE_TIME + timedelta(minutes=20),
        starting_balance=50_000, commission_per_contract=1.2,
        slippage_ticks=1, tick_size=0.25, tick_value=0.5,
        include_evaluation_split=False,
    )
    assert observations == [(HISTORY_BARS, BASE_TIME + timedelta(minutes=5 * i)) for i in range(4)]
    assert result["range"]["start"] == (BASE_TIME + timedelta(minutes=4)).isoformat()
    assert result["data_quality"]["warmup_available"] == HISTORY_BARS
    assert result["assumptions"]["roll_gap_rule"] == "old_delivery_observed_open_at_roll_time_or_fail_if_position_carried"
    assert not any("legacy" in str(value) for value in result["assumptions"].values())


def test_remaining_daily_budget_blocks_third_stop_after_two_real_losses():
    signals = [_candle(BASE_TIME + timedelta(minutes=i)) for i in (0, 5, 10)]
    minutes = _minutes()
    minutes[5].low_price = minutes[10].low_price = 40
    result = _run(signals, minutes, _buy_script(0, 5, 10, stop=50), config=_config(max_daily_loss=250))
    assert len(result["trades"]) == 2
    assert result["metrics"]["net_pnl"] == -205.8
    assert any("proposed stop risk exceeds daily loss budget" in note for note in result["notes"])


@pytest.mark.parametrize("daily_limit,allowed", [(0.99, False), (1.0, False), (1.01, True)])
def test_rounded_stop_budget_boundary_matches_pure_live_risk_gate(daily_limit, allowed):
    from app.services.bot_risk import evaluate_risk
    from test_bot_risk_hardening import _context

    # 1.5 ticks rounds to 2 whole ticks in live bracket construction: $1 risk.
    live_blocks = evaluate_risk(_context(
        max_daily_loss=daily_limit, proposed_stop_risk=1.0, require_proposed_stop_risk=True,
    ))
    assert (not live_blocks) is allowed
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=5))]
    result = _run(signals, _minutes(), _buy_script(0, stop=99.625), config=_config(max_daily_loss=daily_limit))
    assert (len(result["trades"]) == 1) is allowed
    if allowed:
        # Like live preflight, proposed risk excludes fees and fill slippage;
        # those costs still appear in the actual net trade result.
        assert result["trades"][0]["net_pnl"] < -daily_limit


def test_missing_stop_plan_cannot_enter_corrected_replay():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=5))]
    result = _run(signals, _minutes(), _scripted_evaluator({BASE_TIME: {"action": "BUY"}}))
    assert result["trades"] == []
    assert any("proposed stop risk unavailable" in note for note in result["notes"])


def test_bankrupt_cash_cannot_fund_later_entry_after_an_adverse_gap():
    signals = [_candle(BASE_TIME + timedelta(minutes=i)) for i in (0, 5, 10)]
    minutes = _minutes()
    minutes[6].open_price = minutes[6].high_price = minutes[6].low_price = minutes[6].close_price = 1
    result = _run(signals, minutes, _buy_script(0, 5, stop=50), starting_balance=50)
    assert len(result["trades"]) == 1
    assert result["metrics"]["net_pnl"] < -50
    assert any("nonpositive cash" in note for note in result["notes"])


def test_corrected_cooldown_includes_exact_live_threshold_boundary():
    signals = [_candle(BASE_TIME + timedelta(minutes=i)) for i in (0, 5, 10)]
    minutes = _minutes()
    minutes[9].low_price = 90
    result = _run(signals, minutes, _buy_script(0, 10), config=_config(cooldown_seconds=300))
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_timestamp"] == (BASE_TIME + timedelta(minutes=10)).isoformat()
    assert any("cooldown after loss" in note for note in result["notes"])


def test_overnight_risk_pnl_books_full_trade_net_to_live_exit_day():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(days=1))]
    minutes = _minutes()
    next_minutes = _minutes()
    for row in next_minutes:
        row.candle_timestamp += timedelta(days=1)
    next_minutes[0].open_price = next_minutes[0].close_price = 90
    next_minutes[0].high_price = 91
    next_minutes[0].low_price = 89
    engine = replay.BacktestEngine(
        config=_config(), candles=signals, execution_candles=[*minutes, *next_minutes],
        signal_evaluator=_buy_script(0), settings=replay.BacktestSettings(
            start=BASE_TIME, end=BASE_TIME + timedelta(days=1, minutes=20),
            starting_balance=50_000, commission_per_contract=1.2,
            slippage_ticks=1, tick_size=0.25, tick_value=0.5,
        ),
    )
    result = engine.run()
    assert len(result["trades"]) == 1
    assert engine.daily_net_activity[BASE_TIME.date()] == 0
    assert engine.daily_net_activity[(BASE_TIME + timedelta(days=1)).date()] == pytest.approx(result["trades"][0]["net_pnl"])
    assert engine.cash == pytest.approx(50_000 + result["trades"][0]["net_pnl"])


def test_entry_delay_preserves_signal_information_and_anchors_to_delayed_open():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    minutes = _minutes()
    minutes[5].high_price, minutes[5].low_price = 200, 1
    minutes[6].open_price = minutes[6].close_price = 103
    minutes[6].low_price, minutes[6].high_price = 102, 104
    observed = []

    def evaluate(rows):
        observed.append((rows[-1].candle_timestamp, rows[-1].close_price))
        return _buy_script(0)(rows)

    result = _run(signals, minutes, evaluate, entry_delay_minutes=1)
    trade = result["trades"][0]
    assert observed[0] == (BASE_TIME, 100)
    assert trade["signal_timestamp"] == BASE_TIME.isoformat()
    assert trade["entry_timestamp"] == (BASE_TIME + timedelta(minutes=6)).isoformat()
    assert trade["entry_price"] == 103.25
    assert trade["stop_loss"] == 98.25
    assert trade["take_profit"] == 123.25
    assert trade["mfe"] < 10  # the pre-entry 200 high cannot affect this trade
    assert result["assumptions"]["entry_delay_minutes"] == 1


def test_missing_delayed_minute_discards_instead_of_using_later_open():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    result = _run(signals, _minutes(omitted=(6,)), _buy_script(0), entry_delay_minutes=1)
    assert result["trades"] == []
    assert any("missing next execution minute" in note for note in result["notes"])


def test_delayed_entry_cannot_cross_configured_session_end():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    result = _run(signals, _minutes(), _buy_script(0), entry_delay_minutes=1,
                  config=_config(trading_start_time="09:30", trading_end_time="10:05"))
    assert result["trades"] == []
    assert any("stale session signal" in note for note in result["notes"])


def test_waiting_entry_is_not_overwritten_by_a_new_signal():
    signals = _minutes(4)
    evaluator = _scripted_evaluator({
        BASE_TIME: {"action": "BUY", "payload": {"stop_loss": 95, "take_profit": 120}},
        BASE_TIME + timedelta(minutes=1): {"action": "SELL", "payload": {"stop_loss": 105, "take_profit": 80}},
    })
    result = _run(signals, _minutes(4), evaluator, entry_delay_minutes=1,
                  config=_config(timeframe_unit_number=1))
    assert len(result["trades"]) == 1
    assert result["trades"][0]["side"] == "long"
    assert result["trades"][0]["signal_timestamp"] == BASE_TIME.isoformat()
    assert result["trades"][0]["entry_timestamp"] == (BASE_TIME + timedelta(minutes=2)).isoformat()
    assert any("pending entry wait" in note for note in result["notes"])


def test_entry_delay_does_not_delay_explicit_exit_signals():
    signals = [_candle(BASE_TIME + timedelta(minutes=i)) for i in (0, 5, 10)]
    evaluator = _scripted_evaluator({
        BASE_TIME: {"action": "BUY", "payload": {"stop_loss": 95, "take_profit": 120}},
        BASE_TIME + timedelta(minutes=5): {"action": "SELL", "payload": {"signal_category": "exit", "target_position_qty": 0}},
    })
    result = _run(signals, _minutes(), evaluator, entry_delay_minutes=1)
    assert result["trades"][0]["entry_timestamp"] == (BASE_TIME + timedelta(minutes=6)).isoformat()
    assert result["trades"][0]["exit_timestamp"] == (BASE_TIME + timedelta(minutes=10)).isoformat()


def test_delayed_entry_is_discarded_at_delivery_change():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    minutes = _minutes()
    for row in [*signals, *minutes]:
        old = row.candle_timestamp < BASE_TIME + timedelta(minutes=6)
        row.source_raw_symbol = "MNQM6" if old else "MNQU6"
        row.source_instrument_id = 101 if old else 202
    result = _run(signals, minutes, _buy_script(0), entry_delay_minutes=1)
    assert result["trades"] == []
    assert any("1 pending signal(s) were discarded" in note for note in result["notes"])


def test_explicit_zero_delay_matches_default_trades_and_original_signal_time():
    signals = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=10))]
    default = _run(signals, _minutes(), _buy_script(0))
    explicit = _run(signals, _minutes(), _buy_script(0), entry_delay_minutes=0)
    assert default["trades"] == explicit["trades"]
    assert default["metrics"] == explicit["metrics"]
    assert default["trades"][0]["signal_timestamp"] == BASE_TIME.isoformat()
    assert default["trades"][0]["entry_timestamp"] == (BASE_TIME + timedelta(minutes=5)).isoformat()


def test_delay_is_rejected_without_observed_minute_execution():
    with pytest.raises(replay.BacktestConfigurationError, match="requires observed minute execution"):
        replay.BacktestEngine(
            config=_config(), candles=[_candle(BASE_TIME)], entry_delay_minutes=1,
            settings=replay.BacktestSettings(
                start=BASE_TIME, end=BASE_TIME + timedelta(minutes=20),
                starting_balance=50_000, commission_per_contract=1.2,
                slippage_ticks=1, tick_size=.25, tick_value=.5,
            ),
        )
