from __future__ import annotations

from importlib import import_module
from typing import Any

from sqlalchemy.exc import SQLAlchemyError


_SHARED_CONFIGURED_CANDLE_STRATEGIES = {
    "sma_cross",
    "ema_scalping",
    "ema_trend_pullback",
    "bollinger_rsi_reversal",
    "bollinger_mean_reversion",
    "vwap_atr_mean_reversion",
    "fisher_transform_mean_reversion",
    "pullback_trap_reversal",
}


class _SourceConfigView:
    """Proxy a TopBot config while overriding one source strategy's settings."""

    def __init__(
        self,
        base: Any,
        *,
        strategy_type: str,
        strategy_params: dict[str, Any],
        fast_period: int,
        slow_period: int,
    ) -> None:
        self._base = base
        self.strategy_type = strategy_type
        self.strategy_params = strategy_params
        self.fast_period = fast_period
        self.slow_period = slow_period

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _service() -> Any:
    # Lazy import keeps this focused adapter independent from the legacy
    # evaluator implementations while the monolith is reduced incrementally.
    return import_module(".bot_service", package=__package__)


def acquire_and_evaluate_strategy(
    db: Any,
    *,
    user_id: str,
    config: Any,
    client: Any,
) -> tuple[list[Any], Any]:
    service = _service()
    strategy_type = service._validate_strategy_type(str(config.strategy_type))
    strategy_params = service._normalize_strategy_params(strategy_type, config.strategy_params)

    if strategy_type == "topbot_adaptive":
        return _acquire_and_evaluate_topbot(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )

    if strategy_type == "delayed_orb_confirmation":
        candle_sets = service.fetch_and_store_delayed_orb_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        candles = candle_sets.get("1m", [])
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            candles=candles,
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
        )
        return candles, signal

    if strategy_type == "orb_fibonacci_pullback":
        candles = service.fetch_and_store_orb_fibonacci_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            candles,
            timeframe_unit=str(config.timeframe_unit),
            timeframe_unit_number=int(config.timeframe_unit_number),
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
            session_end_time=str(config.trading_end_time),
        )
        return candles, signal

    if strategy_type == "opening_rvol_breakout":
        candles = service.fetch_and_store_opening_rvol_breakout_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            candles,
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
        )
        return candles, signal

    if strategy_type == "vwap_gap_retrace":
        candles = service.fetch_and_store_vwap_gap_retrace_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        return candles, service.dispatch_strategy_evaluator(
            strategy_type,
            candles,
            strategy_params=strategy_params,
        )

    if strategy_type in {"support_resistance", "liquidity_sweep_retest", "macd_support_resistance"}:
        candle_sets = service.fetch_and_store_support_resistance_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_type=strategy_type,
            strategy_params=strategy_params,
        )
        signal_candles = candle_sets.get("1H", [])
        evaluator_args: dict[str, Any] = {
            "higher_timeframe_candles": candle_sets.get("4H", []),
            "lower_timeframe_candles": signal_candles,
            "strategy_params": strategy_params,
        }
        if strategy_type != "support_resistance":
            evaluator_args.update(
                fast_period=int(config.fast_period),
                slow_period=int(config.slow_period),
            )
        signal = service.dispatch_strategy_evaluator(strategy_type, **evaluator_args)
        return signal_candles, signal

    if strategy_type == "supertrend_pivot":
        candle_sets = service.fetch_and_store_supertrend_pivot_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal_candles = candle_sets.get("signal", [])
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            signal_timeframe_candles=signal_candles,
            daily_candles=candle_sets.get("1D", []),
            strategy_params=strategy_params,
        )
        return signal_candles, signal

    if strategy_type == "fvg_sweep_mss":
        candle_sets = service.fetch_and_store_fvg_sweep_mss_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        structure_candles = candle_sets.get("structure", [])
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            fvg_candles=candle_sets.get("fvg", []),
            structure_candles=structure_candles,
            strategy_params=strategy_params,
        )
        return structure_candles, signal

    if strategy_type == "atr_adjusted_relative_strength":
        candles = service.fetch_and_store_candles(db, user_id=user_id, config=config, client=client)
        benchmark_candles = service.fetch_and_store_relative_strength_benchmark_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            candles,
            benchmark_candles=benchmark_candles,
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
        )
        return candles, signal

    if strategy_type == "relative_strength_spy":
        candle_sets = service.fetch_and_store_relative_strength_spy_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        asset_candles = candle_sets.get("5m", [])
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            asset_candles=asset_candles,
            benchmark_candles=candle_sets.get("benchmark", []),
            strategy_params=strategy_params,
        )
        return asset_candles, signal

    minimum_lookback_bars: int | None = None
    if strategy_type == "bollinger_mean_reversion":
        minimum_lookback_bars = max(
            int(strategy_params["bollinger_period"]) + 1,
            int(strategy_params["atr_period"]),
            25,
        )
    candles = service.fetch_and_store_candles(
        db,
        user_id=user_id,
        config=config,
        client=client,
        minimum_lookback_bars=minimum_lookback_bars,
    )

    if strategy_type == "donchian_breakout":
        latest_candle = candles[-1] if candles else None
        contract_id = service._execution_contract_id(config, latest_candle)
        symbol = service._execution_symbol(config, latest_candle)
        position_state = service.load_open_position_state(
            db,
            user_id=user_id,
            account_id=int(config.account_id),
            contract_id=contract_id,
            symbol=symbol,
        )
        signal = service.dispatch_strategy_evaluator(
            strategy_type,
            candles,
            strategy_params=config.strategy_params,
            position_state=position_state,
            latest_entry_plan=service.load_latest_bot_entry_plan(
                db,
                user_id=user_id,
                bot_config_id=int(config.id),
                position_state=position_state,
            ),
            base_order_size=float(config.order_size),
        )
        return candles, signal

    evaluator_args = _generic_evaluator_arguments(
        strategy_type,
        config=config,
        strategy_params=config.strategy_params,
    )
    return candles, service.dispatch_strategy_evaluator(strategy_type, candles, **evaluator_args)


def _acquire_and_evaluate_topbot(
    db: Any,
    *,
    user_id: str,
    config: Any,
    client: Any,
    strategy_params: dict[str, Any],
) -> tuple[list[Any], Any]:
    service = _service()
    main_candles = service.fetch_and_store_candles(
        db,
        user_id=user_id,
        config=config,
        client=client,
        minimum_lookback_bars=300,
    )
    source_overrides = strategy_params.get("source_strategy_params") or {}
    source_results: list[dict[str, Any]] = []

    for source_strategy in strategy_params["source_strategies"]:
        try:
            source_params = service._normalize_strategy_params(
                source_strategy,
                source_overrides.get(source_strategy, {}),
            )
            fast_period, slow_period = service._normalized_strategy_period_values(
                source_strategy,
                fast_period=int(config.fast_period),
                slow_period=int(config.slow_period),
            )
            source_config = _SourceConfigView(
                config,
                strategy_type=source_strategy,
                strategy_params=source_params,
                fast_period=fast_period,
                slow_period=slow_period,
            )
            service._validate_strategy_configuration(
                strategy_type=source_strategy,
                timeframe_unit=str(source_config.timeframe_unit),
                timeframe_unit_number=int(source_config.timeframe_unit_number),
                fast_period=fast_period,
                slow_period=slow_period,
            )

            if source_strategy in _SHARED_CONFIGURED_CANDLE_STRATEGIES:
                source_candles = main_candles
                signal = service.dispatch_strategy_evaluator(
                    source_strategy,
                    source_candles,
                    **_generic_evaluator_arguments(
                        source_strategy,
                        config=source_config,
                        strategy_params=source_params,
                    ),
                )
            else:
                source_candles, signal = acquire_and_evaluate_strategy(
                    db,
                    user_id=user_id,
                    config=source_config,
                    client=client,
                )

            source_results.append(
                _build_topbot_source_result(
                    service,
                    strategy_type=source_strategy,
                    config=source_config,
                    candles=source_candles,
                    signal=signal,
                )
            )
        except SQLAlchemyError:
            raise
        except Exception as exc:  # One optional source must not disable the whole ensemble.
            source_results.append(
                {
                    "strategy_type": source_strategy,
                    "action": "ERROR",
                    "reason": "Source evaluation failed.",
                    "error": service.sanitize_error(exc, max_length=300),
                    "score": None,
                    "reward_risk": None,
                    "eligible": False,
                }
            )

    signal = service.dispatch_strategy_evaluator(
        "topbot_adaptive",
        source_results,
        strategy_params=strategy_params,
    )
    return main_candles, signal


def _build_topbot_source_result(
    service: Any,
    *,
    strategy_type: str,
    config: Any,
    candles: list[Any],
    signal: Any,
) -> dict[str, Any]:
    payload = dict(signal.raw_payload) if isinstance(signal.raw_payload, dict) else {}
    entry = service._finite_optional_float(payload.get("entry_price"))
    if entry is None:
        entry = service._finite_optional_float(signal.price)
    stop = service._finite_optional_float(payload.get("stop_loss"))
    target = None
    for key in ("take_profit", "final_take_profit", "partial_take_profit"):
        target = service._finite_optional_float(payload.get(key))
        if target is not None:
            break

    reward_risk: float | None = None
    valid_geometry = False
    if entry is not None and stop is not None and target is not None:
        if signal.action == "BUY" and stop < entry < target:
            valid_geometry = True
            reward_risk = (target - entry) / (entry - stop)
        elif signal.action == "SELL" and target < entry < stop:
            valid_geometry = True
            reward_risk = (entry - target) / (stop - entry)

    trade_evaluation = None
    if signal.action in {"BUY", "SELL"} and valid_geometry:
        try:
            analysis = service.build_bot_market_analysis(candles=candles, config=config, signal=signal)
            trade_evaluation = service.build_signal_trade_evaluation(
                candles=candles,
                config=config,
                signal=signal,
                analysis=analysis,
            )
        except (TypeError, ValueError):
            trade_evaluation = None
    score = None
    if isinstance(trade_evaluation, dict):
        score = service._finite_optional_float(
            trade_evaluation.get("total_score", trade_evaluation.get("score"))
        )

    return {
        "strategy_type": strategy_type,
        "action": str(signal.action),
        "reason": str(signal.reason),
        "candle_timestamp": signal.candle_timestamp,
        "price": service._finite_optional_float(signal.price),
        "raw_payload": payload,
        "score": score,
        "reward_risk": round(reward_risk, 4) if reward_risk is not None else None,
        "eligible": bool(valid_geometry and score is not None),
        "error": None,
    }


def _generic_evaluator_arguments(
    strategy_type: str,
    *,
    config: Any,
    strategy_params: Any,
) -> dict[str, Any]:
    if strategy_type in {"bollinger_mean_reversion", "bollinger_rsi_reversal", "vwap_atr_mean_reversion"}:
        return {"strategy_params": strategy_params}
    if strategy_type in {
        "ema_trend_pullback",
        "fisher_transform_mean_reversion",
        "pullback_trap_reversal",
    }:
        return {
            "fast_period": int(config.fast_period),
            "slow_period": int(config.slow_period),
            "strategy_params": strategy_params,
        }
    return {
        "fast_period": int(config.fast_period),
        "slow_period": int(config.slow_period),
    }


__all__ = ["acquire_and_evaluate_strategy"]
