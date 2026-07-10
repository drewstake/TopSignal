import os
from types import SimpleNamespace
from typing import get_args

import pytest


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services import bot_service
from app.bot_schemas import BotStrategyType
from app.services.bot_backtesting import SUPPORTED_BACKTEST_STRATEGIES
from app.services.bot_serialization import serialize_supported_bot_configs
from app.services.bot_strategy_registry import (
    BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS,
    STRATEGY_REGISTRY,
    SUPPORTED_STRATEGY_IDENTIFIERS,
    dispatch_strategy,
    dispatch_strategy_evaluator,
    get_strategy_definition,
)


EXPECTED_EVALUATORS = {
    "topbot_adaptive": "evaluate_topbot_adaptive",
    "sma_cross": "evaluate_sma_cross",
    "support_resistance": "evaluate_support_resistance_levels",
    "liquidity_sweep_retest": "evaluate_liquidity_sweep_retest",
    "donchian_breakout": "evaluate_donchian_breakout",
    "opening_rvol_breakout": "evaluate_opening_rvol_breakout",
    "bollinger_rsi_reversal": "evaluate_bollinger_rsi_reversal",
    "bollinger_mean_reversion": "evaluate_bollinger_mean_reversion",
    "macd_support_resistance": "evaluate_macd_support_resistance",
    "delayed_orb_confirmation": "evaluate_delayed_orb_confirmation",
    "orb_fibonacci_pullback": "evaluate_orb_fibonacci_pullback",
    "supertrend_pivot": "evaluate_supertrend_pivot_points",
    "ema_trend_pullback": "evaluate_ema_trend_pullback",
    "ema_scalping": "evaluate_ema_scalping",
    "vwap_atr_mean_reversion": "evaluate_vwap_atr_mean_reversion",
    "vwap_gap_retrace": "evaluate_vwap_gap_retrace",
    "fisher_transform_mean_reversion": "evaluate_fisher_transform_mean_reversion",
    "atr_adjusted_relative_strength": "evaluate_atr_adjusted_relative_strength",
    "relative_strength_spy": "evaluate_relative_strength_vs_spy",
    "pullback_trap_reversal": "evaluate_pullback_trap_reversal",
    "fvg_sweep_mss": "evaluate_fvg_sweep_mss",
}


def test_registry_contains_every_supported_bot_strategy():
    assert set(STRATEGY_REGISTRY) == EXPECTED_EVALUATORS.keys()
    assert SUPPORTED_STRATEGY_IDENTIFIERS == frozenset(bot_service._SUPPORTED_STRATEGY_TYPES)
    assert SUPPORTED_STRATEGY_IDENTIFIERS == frozenset(get_args(BotStrategyType))
    assert BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS == SUPPORTED_BACKTEST_STRATEGIES


@pytest.mark.parametrize(("identifier", "evaluator_name"), EXPECTED_EVALUATORS.items())
def test_registry_entries_expose_required_metadata(identifier, evaluator_name):
    definition = STRATEGY_REGISTRY[identifier]

    assert definition.identifier == identifier
    assert callable(definition.parameter_normalizer)
    assert callable(definition.configuration_validator)
    assert definition.required_timeframes
    assert all(requirement.role for requirement in definition.required_timeframes)
    assert callable(definition.minimum_history)
    assert callable(definition.evaluator)
    assert definition.evaluator.callable_name == evaluator_name
    assert isinstance(definition.auxiliary_data_requirements, tuple)
    assert definition.backtesting_supported is (identifier in BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS)

    assert isinstance(definition.parameter_normalizer(None), dict)
    history = definition.minimum_history()
    assert history
    assert {item.role for item in history} == {item.role for item in definition.required_timeframes}
    assert all(item.minimum_bars is None or item.minimum_bars >= 1 for item in history)
    assert all(
        item.hard_minimum_bars is None
        or item.minimum_bars is None
        or item.minimum_bars >= item.hard_minimum_bars
        for item in history
    )


def test_registry_lookup_preserves_default_and_invalid_identifier_behavior():
    assert get_strategy_definition(None).identifier == "sma_cross"

    with pytest.raises(ValueError, match="unsupported bot strategy type"):
        get_strategy_definition("not-a-strategy")


def test_bot_config_list_skips_incompatible_legacy_strategy_without_mutating_it():
    legacy = SimpleNamespace(id=32, name="Retired Bot", strategy_type="retired_strategy")

    items, warnings = serialize_supported_bot_configs([legacy])

    assert items == []
    assert warnings == [
        'Retired Bot was not loaded because strategy "retired_strategy" is not supported by this build. '
        "The saved configuration was not modified."
    ]
    assert legacy.strategy_type == "retired_strategy"


def test_topbot_normalizer_preserves_legacy_gates_and_removes_recursive_sources():
    params = get_strategy_definition("topbot_adaptive").parameter_normalizer(
        {
            "source_strategies": ["sma_cross", "topbot_adaptive", "sma_cross", "retired_strategy"],
            "source_strategy_params": {"sma_cross": {"ignored": True}},
            "minimum_score": 70,
            "minimum_confidence": 55,
            "minimum_directional_votes": 2,
            "max_opposing_votes": 1,
            "minimum_reward_risk": 1.5,
            "enable_trailing_stop": "true",
        }
    )

    assert params["source_strategies"] == ["sma_cross"]
    assert params["source_strategy_params"] == {"sma_cross": {"ignored": True}}
    assert params["minimum_score"] == 70
    assert params["minimum_confidence"] == 55
    assert params["minimum_directional_votes"] == 2
    assert params["max_opposing_votes"] == 1
    assert params["minimum_reward_risk"] == 1.5
    assert params["enable_trailing_stop"] is True


def test_topbot_selects_highest_scoring_consensus_bracket():
    timestamp = bot_service.datetime(2026, 7, 9, tzinfo=bot_service.timezone.utc)
    source_results = [
        {
            "strategy_type": "ema_trend_pullback",
            "action": "BUY",
            "reason": "EMA pullback",
            "candle_timestamp": timestamp,
            "price": 100.0,
            "raw_payload": {"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0},
            "score": 78.0,
            "reward_risk": 2.0,
            "eligible": True,
            "error": None,
        },
        {
            "strategy_type": "bollinger_rsi_reversal",
            "action": "BUY",
            "reason": "RSI reversal",
            "candle_timestamp": timestamp,
            "price": 100.0,
            "raw_payload": {"entry_price": 100.0, "stop_loss": 98.0, "take_profit": 104.0},
            "score": 86.0,
            "reward_risk": 2.0,
            "eligible": True,
            "error": None,
        },
        {
            "strategy_type": "sma_cross",
            "action": "HOLD",
            "reason": "No crossover",
            "score": None,
            "reward_risk": None,
            "eligible": False,
            "error": None,
        },
    ]

    signal = bot_service.evaluate_topbot_adaptive(source_results)

    assert signal.action == "BUY"
    assert signal.raw_payload["source_strategy"] == "bollinger_rsi_reversal"
    assert signal.raw_payload["topbot_score"] == 86
    assert signal.raw_payload["ensemble"]["buy_votes"] == 2
    assert signal.raw_payload["ensemble"]["agreement_confidence"] == 100


def test_topbot_holds_on_tie_or_when_quality_gate_has_no_candidate():
    tied = bot_service.evaluate_topbot_adaptive(
        [
            {"strategy_type": "a", "action": "BUY", "eligible": True, "score": 90, "reward_risk": 2},
            {"strategy_type": "b", "action": "SELL", "eligible": True, "score": 90, "reward_risk": 2},
        ],
        strategy_params={"minimum_directional_votes": 1},
    )
    below_score = bot_service.evaluate_topbot_adaptive(
        [
            {"strategy_type": "a", "action": "BUY", "eligible": True, "score": 60, "reward_risk": 2},
            {"strategy_type": "b", "action": "BUY", "eligible": True, "score": 65, "reward_risk": 2},
        ]
    )

    assert tied.action == "HOLD"
    assert "tied" in tied.reason
    assert below_score.action == "HOLD"
    assert "score and reward/risk gates" in below_score.reason


def test_topbot_acquisition_reuses_primary_candles_and_dispatches_ensemble(monkeypatch):
    timestamp = bot_service.datetime(2026, 7, 9, tzinfo=bot_service.timezone.utc)
    candles = [object()]
    config = SimpleNamespace(
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": ["sma_cross"],
            "minimum_directional_votes": 1,
            "minimum_score": 70,
        },
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=200,
        fast_period=2,
        slow_period=3,
        max_data_staleness_seconds=600,
        order_size=1,
        max_daily_loss=250,
        symbol="MNQ",
        contract_id="CON.F.US.MNQ.U26",
    )
    fetch_calls = []

    def fake_fetch(db, *, user_id, config, client, minimum_lookback_bars=None):
        fetch_calls.append((db, user_id, config, client, minimum_lookback_bars))
        return candles

    monkeypatch.setattr(bot_service, "fetch_and_store_candles", fake_fetch)
    monkeypatch.setattr(
        bot_service,
        "evaluate_sma_cross",
        lambda *_args, **_kwargs: bot_service.SignalResult(
            action="BUY",
            reason="SMA source",
            candle_timestamp=timestamp,
            price=100.0,
            raw_payload={"entry_price": 100.0, "stop_loss": 99.0, "take_profit": 102.0},
        ),
    )
    monkeypatch.setattr(bot_service, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(bot_service, "build_signal_trade_evaluation", lambda **_kwargs: {"total_score": 82})

    result_candles, signal = bot_service.fetch_candles_and_evaluate_strategy(
        "db",
        user_id="user-1",
        config=config,
        client="client",
    )

    assert result_candles is candles
    assert signal.action == "BUY"
    assert signal.raw_payload["source_strategy"] == "sma_cross"
    assert signal.raw_payload["topbot_score"] == 82
    assert fetch_calls == [("db", "user-1", config, "client", 300)]


def test_registry_preserves_ema_scalping_configuration_validation():
    definition = get_strategy_definition("ema_scalping")

    definition.configuration_validator(
        timeframe_unit="minute",
        timeframe_unit_number=5,
        fast_period=100,
        slow_period=101,
    )
    with pytest.raises(ValueError, match="3-minute or 5-minute"):
        definition.configuration_validator(
            timeframe_unit="minute",
            timeframe_unit_number=1,
            fast_period=9,
            slow_period=15,
        )


def test_registry_history_describes_fixed_and_derived_timeframes():
    support = get_strategy_definition("support_resistance")
    assert [(item.unit, item.unit_number) for item in support.required_timeframes] == [("hour", 4), ("hour", 1)]

    fvg = get_strategy_definition("fvg_sweep_mss")
    assert fvg.required_timeframes[1].source == "derived"
    assert fvg.required_timeframes[1].derivation == "_derive_lower_timeframe"

    relative_strength = get_strategy_definition("relative_strength_spy")
    assert relative_strength.required_timeframes[1].aligned_to == "signal"
    assert relative_strength.minimum_history()[0].minimum_bars == 50


def test_dispatch_entrypoint_resolves_evaluator_lazily(monkeypatch):
    calls = []

    def fake_evaluator(*args, **kwargs):
        calls.append((args, kwargs))
        return "sentinel"

    monkeypatch.setattr(bot_service, "evaluate_sma_cross", fake_evaluator)

    result = dispatch_strategy_evaluator("sma_cross", ["candles"], fast_period=2, slow_period=3)
    alias_result = dispatch_strategy("sma_cross", ["candles"], fast_period=5, slow_period=8)

    assert result == "sentinel"
    assert alias_result == "sentinel"
    assert calls == [
        ((["candles"],), {"fast_period": 2, "slow_period": 3}),
        ((["candles"],), {"fast_period": 5, "slow_period": 8}),
    ]


def test_fetch_and_evaluate_routes_sma_through_registry_without_changing_arguments(monkeypatch):
    candles = [object(), object()]
    expected_signal = bot_service.SignalResult(
        action="HOLD",
        reason="registry sentinel",
        candle_timestamp=None,
        price=None,
        raw_payload={"source": "registry"},
    )
    evaluator_calls = []
    dispatch_calls = []

    def fake_fetch(db, *, user_id, config, client, minimum_lookback_bars=None):
        assert db is db_sentinel
        assert user_id == "user-1"
        assert config is config_row
        assert client is client_sentinel
        assert minimum_lookback_bars is None
        return candles

    def fake_sma_evaluator(received_candles, *, fast_period, slow_period):
        evaluator_calls.append((received_candles, fast_period, slow_period))
        return expected_signal

    original_dispatch = bot_service.dispatch_strategy_evaluator

    def dispatch_spy(identifier, /, *args, **kwargs):
        dispatch_calls.append((identifier, args, kwargs))
        return original_dispatch(identifier, *args, **kwargs)

    config_row = bot_service.BotConfig(
        strategy_type="sma_cross",
        strategy_params={},
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=2,
        slow_period=3,
    )
    db_sentinel = object()
    client_sentinel = object()
    monkeypatch.setattr(bot_service, "fetch_and_store_candles", fake_fetch)
    monkeypatch.setattr(bot_service, "evaluate_sma_cross", fake_sma_evaluator)
    monkeypatch.setattr(bot_service, "dispatch_strategy_evaluator", dispatch_spy)

    result_candles, result_signal = bot_service.fetch_candles_and_evaluate_strategy(
        db_sentinel,
        user_id="user-1",
        config=config_row,
        client=client_sentinel,
    )

    assert result_candles is candles
    assert result_signal is expected_signal
    assert evaluator_calls == [(candles, 2, 3)]
    assert dispatch_calls == [
        ("sma_cross", (candles,), {"fast_period": 2, "slow_period": 3}),
    ]


def test_fetch_and_evaluate_routes_multitimeframe_strategy_through_registry_without_changing_arguments(monkeypatch):
    higher_candles = [object()]
    signal_candles = [object(), object()]
    candle_sets = {"4H": higher_candles, "1H": signal_candles}
    expected_params = {
        "bars_per_timeframe": 100,
        "swing_window": 3,
        "level_tolerance_percent": 0.25,
        "stop_beyond_level_percent": 1.0,
        "take_profit_r_multiple": 2.0,
    }
    expected_signal = bot_service.SignalResult(
        action="BUY",
        reason="registry multitimeframe sentinel",
        candle_timestamp=None,
        price=100.0,
        raw_payload={"source": "registry"},
    )
    acquisition_calls = []
    evaluator_calls = []
    dispatch_calls = []

    def fake_fetch(
        db,
        *,
        user_id,
        config,
        client,
        strategy_type,
        strategy_params,
    ):
        acquisition_calls.append(
            (db, user_id, config, client, strategy_type, strategy_params)
        )
        return candle_sets

    def fake_support_resistance_evaluator(
        *,
        higher_timeframe_candles,
        lower_timeframe_candles,
        strategy_params,
    ):
        evaluator_calls.append(
            (higher_timeframe_candles, lower_timeframe_candles, strategy_params)
        )
        return expected_signal

    original_dispatch = bot_service.dispatch_strategy_evaluator

    def dispatch_spy(identifier, /, *args, **kwargs):
        dispatch_calls.append((identifier, args, kwargs))
        return original_dispatch(identifier, *args, **kwargs)

    config_row = bot_service.BotConfig(
        strategy_type="support_resistance",
        strategy_params={"swing_window": 3},
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=9,
        slow_period=21,
    )
    db_sentinel = object()
    client_sentinel = object()
    monkeypatch.setattr(
        bot_service,
        "fetch_and_store_support_resistance_candles",
        fake_fetch,
    )
    monkeypatch.setattr(
        bot_service,
        "evaluate_support_resistance_levels",
        fake_support_resistance_evaluator,
    )
    monkeypatch.setattr(bot_service, "dispatch_strategy_evaluator", dispatch_spy)

    result_candles, result_signal = bot_service.fetch_candles_and_evaluate_strategy(
        db_sentinel,
        user_id="user-1",
        config=config_row,
        client=client_sentinel,
    )

    assert result_candles is signal_candles
    assert result_signal is expected_signal
    assert acquisition_calls == [
        (
            db_sentinel,
            "user-1",
            config_row,
            client_sentinel,
            "support_resistance",
            expected_params,
        )
    ]
    assert evaluator_calls == [(higher_candles, signal_candles, expected_params)]
    assert dispatch_calls == [
        (
            "support_resistance",
            (),
            {
                "higher_timeframe_candles": higher_candles,
                "lower_timeframe_candles": signal_candles,
                "strategy_params": expected_params,
            },
        )
    ]
