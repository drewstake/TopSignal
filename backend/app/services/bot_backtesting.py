from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from ..models import BotConfig, ProjectXMarketCandle
from .bot_service import (
    SignalResult,
    _as_utc,
    _normalize_strategy_params,
    _strategy_config_view,
    _validate_strategy_type,
    evaluate_atr_adjusted_relative_strength,
    evaluate_bollinger_mean_reversion,
    evaluate_bollinger_rsi_reversal,
    evaluate_delayed_orb_confirmation,
    evaluate_donchian_breakout,
    evaluate_ema_scalping,
    evaluate_ema_trend_pullback,
    evaluate_fisher_transform_mean_reversion,
    evaluate_fvg_sweep_mss,
    evaluate_liquidity_sweep_retest,
    evaluate_macd_support_resistance,
    evaluate_opening_rvol_breakout,
    evaluate_orb_fibonacci_pullback,
    evaluate_pullback_trap_reversal,
    evaluate_relative_strength_vs_spy,
    evaluate_sma_cross,
    evaluate_supertrend_pivot_points,
    evaluate_support_resistance_levels,
    evaluate_topbot_adaptive_strategy,
    evaluate_vwap_atr_mean_reversion,
    evaluate_vwap_gap_retrace,
    fetch_and_store_market_candles,
    list_market_candles,
    market_candle_cache_covers_request,
    market_candle_cache_needs_refresh,
)
from .instruments import build_point_value_lookup, load_instrument_specs, resolve_point_value
from .projectx_client import ProjectXClient, ProjectXClientError
from .projectx_metrics import TradeMetricSample, compute_daily_pnl_calendar, compute_trade_summary
from .topstep_fees import effective_topstep_trade_fee
from .trading_day import trading_day_key

_BACKTEST_UNIT_SECONDS_BY_NAME = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "month": 31 * 24 * 60 * 60,
}
_MIN_BACKTEST_WARMUP_BARS = 25
_MAX_SIGNAL_LOOKBACK_BARS = 500
_ENTRY_ACTIONS = {"BUY", "SELL"}


@dataclass
class BacktestPosition:
    side: str
    quantity: float
    entry_time: datetime
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    signal_reason: str
    raw_payload: Mapping[str, Any] | None


@dataclass
class BacktestTrade:
    id: int
    side: str
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    gross_pnl: float
    fees: float
    net_pnl: float
    points: float
    signal_reason: str


def run_bot_backtest(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 20000,
) -> dict[str, Any]:
    end_utc = _as_utc(end) if end is not None else datetime.now(timezone.utc)
    bounded_limit = max(100, min(int(limit), 20000))
    start_utc = _as_utc(start) if start is not None else _max_backtest_start(config=config, end=end_utc, limit=bounded_limit)
    if start_utc >= end_utc:
        raise ValueError("backtest start must be before end")

    candles = _load_backtest_candles(
        db,
        user_id=user_id,
        config=config,
        client=client,
        start=start_utc,
        end=end_utc,
        limit=bounded_limit,
    )
    candles = _closed_candles(candles)

    point_value_by_symbol = build_point_value_lookup(load_instrument_specs(db))
    point_value = resolve_point_value(
        symbol=config.symbol,
        contract_id=config.contract_id,
        point_value_by_symbol=point_value_by_symbol,
    )
    if point_value is None or point_value <= 0:
        point_value = 1.0

    trades, signals_evaluated = _replay_bot(
        config=config,
        candles=candles,
        point_value=point_value,
    )
    samples = [
        TradeMetricSample(
            timestamp=trade.exit_time,
            pnl=trade.gross_pnl,
            fees=0.0,
            order_id=f"backtest-{int(config.id)}-{trade.id}",
            symbol=config.symbol,
            contract_id=config.contract_id,
            side=trade.side,
            size=trade.quantity,
            price=trade.exit_price,
        )
        for trade in trades
    ]
    summary = compute_trade_summary(samples, point_value_by_symbol=point_value_by_symbol)
    daily_pnl = compute_daily_pnl_calendar(samples)
    response_start = _as_utc(candles[0].candle_timestamp) if candles else start_utc

    return {
        "bot_config_id": int(config.id),
        "bot_name": str(config.name),
        "strategy_type": str(config.strategy_type),
        "contract_id": str(config.contract_id),
        "symbol": config.symbol,
        "start": response_start,
        "end": end_utc,
        "generated_at": datetime.now(timezone.utc),
        "candles_processed": len(candles),
        "signals_evaluated": signals_evaluated,
        "point_value": _round(point_value, 4),
        "assumptions": {
            "history_window": "Blank start uses the farthest window supported by the selected timeframe and 20,000-bar backtest cap.",
            "entry_model": "Signals enter at the closed signal candle price.",
            "exit_model": "Stops and targets are filled if a later candle trades through them; same-bar stop/target conflicts use the stop first.",
            "fees": "Net PnL applies the existing Topstep commission model with zero broker fee input.",
            "positioning": "The replay holds at most one simulated position at a time.",
        },
        "summary": {
            **summary,
            "total_profit": summary.get("net_pnl", 0.0),
            "total_pnl": summary.get("net_pnl", 0.0),
            "gross_profit": _round(sum(trade.gross_pnl for trade in trades if trade.gross_pnl > 0)),
            "gross_loss": _round(sum(trade.gross_pnl for trade in trades if trade.gross_pnl < 0)),
        },
        "daily_pnl": daily_pnl,
        "trades": [_serialize_trade(trade) for trade in trades[-100:]],
    }


def _load_backtest_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[ProjectXMarketCandle]:
    cached = list_market_candles(
        db,
        user_id=user_id,
        contract_id=str(config.contract_id),
        live=False,
        start=start,
        end=end,
        unit=str(config.timeframe_unit),
        unit_number=int(config.timeframe_unit_number),
        limit=limit,
        include_partial_bar=False,
    )
    cache_covers = market_candle_cache_covers_request(
        cached,
        start=start,
        unit=str(config.timeframe_unit),
        unit_number=int(config.timeframe_unit_number),
        limit=limit,
    )
    end_is_recent = end >= datetime.now(timezone.utc) - timedelta(days=1)
    if cached and cache_covers and not (
        end_is_recent
        and market_candle_cache_needs_refresh(
            cached,
            end=end,
            unit=str(config.timeframe_unit),
            unit_number=int(config.timeframe_unit_number),
            include_partial_bar=False,
        )
    ):
        return cached

    try:
        fetched = fetch_and_store_market_candles(
            db,
            user_id=user_id,
            client=client,
            contract_id=str(config.contract_id),
            symbol=config.symbol,
            live=False,
            start=start,
            end=end,
            unit=str(config.timeframe_unit),
            unit_number=int(config.timeframe_unit_number),
            limit=limit,
            include_partial_bar=False,
        )
        db.flush()
    except ProjectXClientError:
        if cached:
            return cached
        raise

    response_contract_id = str(fetched[-1].contract_id) if fetched else str(config.contract_id)
    combined = list_market_candles(
        db,
        user_id=user_id,
        contract_id=response_contract_id,
        live=False,
        start=start,
        end=end,
        unit=str(config.timeframe_unit),
        unit_number=int(config.timeframe_unit_number),
        limit=limit,
        include_partial_bar=False,
    )
    return combined or fetched


def _max_backtest_start(*, config: BotConfig, end: datetime, limit: int) -> datetime:
    unit = str(config.timeframe_unit).strip().lower()
    unit_seconds = _BACKTEST_UNIT_SECONDS_BY_NAME.get(unit)
    if unit_seconds is None:
        raise ValueError("unsupported candle unit")
    unit_number = max(1, int(config.timeframe_unit_number))
    bar_count = max(1, int(limit) - 1)
    return _as_utc(end) - timedelta(seconds=unit_seconds * unit_number * bar_count)


def _replay_bot(
    *,
    config: BotConfig,
    candles: list[ProjectXMarketCandle],
    point_value: float,
) -> tuple[list[BacktestTrade], int]:
    if len(candles) < 2:
        return [], 0

    warmup_bars = _warmup_bars(config=config, candle_count=len(candles))
    position: BacktestPosition | None = None
    trades: list[BacktestTrade] = []
    signals_evaluated = 0
    daily_trade_counts: dict[str, int] = {}
    daily_net_pnl: dict[str, float] = {}
    last_exit_time: datetime | None = None

    for index in range(warmup_bars, len(candles)):
        candle = candles[index]
        timestamp = _as_utc(candle.candle_timestamp)
        day_key = trading_day_key(timestamp)

        if position is not None:
            exit_event = _stop_or_target_exit(position, candle)
            if exit_event is not None:
                trade = _close_position(
                    position,
                    exit_time=timestamp,
                    exit_price=exit_event["price"],
                    exit_reason=exit_event["reason"],
                    point_value=point_value,
                    trade_id=len(trades) + 1,
                    config=config,
                )
                trades.append(trade)
                daily_net_pnl[day_key] = daily_net_pnl.get(day_key, 0.0) + trade.net_pnl
                last_exit_time = timestamp
                position = None

        lookback_start = max(0, index + 1 - _signal_lookback_bars(config))
        signal_candles = candles[lookback_start : index + 1]
        signal = _evaluate_strategy_signal(config=config, candles=signal_candles)
        signals_evaluated += 1

        if position is not None:
            if _is_opposite_action(position.side, signal.action):
                exit_price = _signal_price(signal, candle)
                trade = _close_position(
                    position,
                    exit_time=timestamp,
                    exit_price=exit_price,
                    exit_reason="opposite_signal",
                    point_value=point_value,
                    trade_id=len(trades) + 1,
                    config=config,
                )
                trades.append(trade)
                daily_net_pnl[day_key] = daily_net_pnl.get(day_key, 0.0) + trade.net_pnl
                last_exit_time = timestamp
                position = None
            continue

        if signal.action not in _ENTRY_ACTIONS or _signal_category(signal) == "exit":
            continue
        if not _backtest_entry_allowed(
            config=config,
            timestamp=timestamp,
            daily_trade_count=daily_trade_counts.get(day_key, 0),
            daily_net_pnl=daily_net_pnl.get(day_key, 0.0),
            last_exit_time=last_exit_time,
        ):
            continue

        position = _open_position(config=config, signal=signal, candle=candle)
        daily_trade_counts[day_key] = daily_trade_counts.get(day_key, 0) + 1

    if position is not None and candles:
        final_candle = candles[-1]
        trade = _close_position(
            position,
            exit_time=_as_utc(final_candle.candle_timestamp),
            exit_price=float(final_candle.close_price),
            exit_reason="end_of_backtest",
            point_value=point_value,
            trade_id=len(trades) + 1,
            config=config,
        )
        trades.append(trade)

    return trades, signals_evaluated


def _evaluate_strategy_signal(*, config: BotConfig, candles: list[ProjectXMarketCandle]) -> SignalResult:
    strategy_type = _validate_strategy_type(str(config.strategy_type))
    if strategy_type == "topbot_adaptive":
        return _evaluate_topbot_signal(config=config, candles=candles)
    return _evaluate_non_topbot_signal(strategy_type=strategy_type, config=config, candles=candles)


def _evaluate_topbot_signal(*, config: BotConfig, candles: list[ProjectXMarketCandle]) -> SignalResult:
    params = _normalize_strategy_params("topbot_adaptive", config.strategy_params)
    source_params_by_strategy = params.get("source_strategy_params")
    if not isinstance(source_params_by_strategy, dict):
        source_params_by_strategy = {}

    source_results: list[tuple[str, SignalResult]] = []
    for source_strategy in params["source_strategies"]:
        source_params = source_params_by_strategy.get(source_strategy)
        if not isinstance(source_params, dict):
            source_params = {}
        source_config = _strategy_config_view(
            config,
            strategy_type=source_strategy,
            strategy_params=_normalize_strategy_params(source_strategy, source_params),
        )
        try:
            signal = _evaluate_non_topbot_signal(
                strategy_type=source_strategy,
                config=source_config,
                candles=candles,
            )
        except Exception as exc:
            latest = candles[-1] if candles else None
            signal = SignalResult(
                action="HOLD",
                reason=f"{source_strategy} source unavailable in backtest: {exc}",
                candle_timestamp=_as_utc(latest.candle_timestamp) if latest is not None else None,
                price=float(latest.close_price) if latest is not None else None,
                raw_payload={"strategy_type": source_strategy, "source_error": str(exc)},
            )
        source_results.append((source_strategy, signal))

    latest = candles[-1] if candles else None
    return evaluate_topbot_adaptive_strategy(
        candles,
        strategy_signals=source_results,
        strategy_params=params,
        config=config,
        risk_state={},
        now=_as_utc(latest.candle_timestamp) if latest is not None else None,
    )


def _evaluate_non_topbot_signal(
    *,
    strategy_type: str,
    config: Any,
    candles: list[ProjectXMarketCandle],
) -> SignalResult:
    params = _normalize_strategy_params(strategy_type, getattr(config, "strategy_params", None))
    if strategy_type == "sma_cross":
        return evaluate_sma_cross(candles, fast_period=int(config.fast_period), slow_period=int(config.slow_period))
    if strategy_type == "ema_scalping":
        return evaluate_ema_scalping(candles, fast_period=int(config.fast_period), slow_period=int(config.slow_period))
    if strategy_type == "ema_trend_pullback":
        return evaluate_ema_trend_pullback(
            candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=params,
        )
    if strategy_type == "donchian_breakout":
        return evaluate_donchian_breakout(candles, strategy_params=params, base_order_size=float(config.order_size))
    if strategy_type == "support_resistance":
        return evaluate_support_resistance_levels(
            higher_timeframe_candles=candles,
            lower_timeframe_candles=candles,
            strategy_params=params,
        )
    if strategy_type == "liquidity_sweep_retest":
        return evaluate_liquidity_sweep_retest(
            higher_timeframe_candles=candles,
            lower_timeframe_candles=candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=params,
        )
    if strategy_type == "macd_support_resistance":
        return evaluate_macd_support_resistance(
            higher_timeframe_candles=candles,
            lower_timeframe_candles=candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=params,
        )
    if strategy_type == "fvg_sweep_mss":
        return evaluate_fvg_sweep_mss(fvg_candles=candles, structure_candles=candles, strategy_params=params)
    if strategy_type == "supertrend_pivot":
        return evaluate_supertrend_pivot_points(
            signal_timeframe_candles=candles,
            daily_candles=[],
            strategy_params=params,
        )
    if strategy_type == "opening_rvol_breakout":
        return evaluate_opening_rvol_breakout(candles, strategy_params=params, session_start_time=str(config.trading_start_time))
    if strategy_type == "delayed_orb_confirmation":
        return evaluate_delayed_orb_confirmation(candles, strategy_params=params, session_start_time=str(config.trading_start_time))
    if strategy_type == "orb_fibonacci_pullback":
        return evaluate_orb_fibonacci_pullback(
            candles,
            timeframe_unit=str(config.timeframe_unit),
            timeframe_unit_number=int(config.timeframe_unit_number),
            strategy_params=params,
            session_start_time=str(config.trading_start_time),
            session_end_time=str(config.trading_end_time),
        )
    if strategy_type == "bollinger_mean_reversion":
        return evaluate_bollinger_mean_reversion(candles, strategy_params=params)
    if strategy_type == "bollinger_rsi_reversal":
        return evaluate_bollinger_rsi_reversal(candles, strategy_params=params)
    if strategy_type == "vwap_atr_mean_reversion":
        return evaluate_vwap_atr_mean_reversion(candles, strategy_params=params)
    if strategy_type == "fisher_transform_mean_reversion":
        return evaluate_fisher_transform_mean_reversion(
            candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=params,
        )
    if strategy_type == "pullback_trap_reversal":
        return evaluate_pullback_trap_reversal(
            candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=params,
        )
    if strategy_type == "vwap_gap_retrace":
        return evaluate_vwap_gap_retrace(candles, strategy_params=params)
    if strategy_type == "atr_adjusted_relative_strength":
        return evaluate_atr_adjusted_relative_strength(
            candles,
            benchmark_candles=[],
            strategy_params=params,
            session_start_time=str(config.trading_start_time),
        )
    if strategy_type == "relative_strength_spy":
        return evaluate_relative_strength_vs_spy(
            asset_candles=candles,
            benchmark_candles=[],
            strategy_params=params,
        )
    return evaluate_sma_cross(candles, fast_period=int(config.fast_period), slow_period=int(config.slow_period))


def _open_position(*, config: BotConfig, signal: SignalResult, candle: ProjectXMarketCandle) -> BacktestPosition:
    payload = signal.raw_payload if isinstance(signal.raw_payload, Mapping) else {}
    entry_price = _optional_float(payload.get("entry_price")) or _signal_price(signal, candle)
    quantity = (
        _optional_float(payload.get("effective_order_size"))
        or _optional_float(payload.get("order_size"))
        or _optional_float(payload.get("quantity"))
        or float(config.order_size)
    )
    return BacktestPosition(
        side=signal.action,
        quantity=max(0.0, float(quantity)),
        entry_time=_as_utc(signal.candle_timestamp or candle.candle_timestamp),
        entry_price=float(entry_price),
        stop_loss=_optional_float(payload.get("stop_loss")),
        take_profit=_optional_float(payload.get("take_profit"))
        or _optional_float(payload.get("final_take_profit"))
        or _optional_float(payload.get("partial_take_profit")),
        signal_reason=signal.reason,
        raw_payload=payload,
    )


def _close_position(
    position: BacktestPosition,
    *,
    exit_time: datetime,
    exit_price: float,
    exit_reason: str,
    point_value: float,
    trade_id: int,
    config: BotConfig,
) -> BacktestTrade:
    side_sign = 1 if position.side == "BUY" else -1
    points = (float(exit_price) - float(position.entry_price)) * side_sign
    gross_pnl = points * float(point_value) * float(position.quantity)
    fees = effective_topstep_trade_fee(
        trade_timestamp=exit_time,
        pnl=gross_pnl,
        fees=0.0,
        symbol=config.symbol,
        contract_id=config.contract_id,
        size=position.quantity,
        raw_fee_is_per_side=False,
    )
    return BacktestTrade(
        id=trade_id,
        side=position.side,
        quantity=_round(position.quantity, 4),
        entry_time=position.entry_time,
        entry_price=_round(position.entry_price, 6),
        exit_time=exit_time,
        exit_price=_round(exit_price, 6),
        exit_reason=exit_reason,
        gross_pnl=_round(gross_pnl),
        fees=_round(fees),
        net_pnl=_round(gross_pnl - fees),
        points=_round(points, 4),
        signal_reason=position.signal_reason,
    )


def _stop_or_target_exit(position: BacktestPosition, candle: ProjectXMarketCandle) -> dict[str, float | str] | None:
    high = float(candle.high_price)
    low = float(candle.low_price)
    if position.side == "BUY":
        stop_hit = position.stop_loss is not None and low <= position.stop_loss
        target_hit = position.take_profit is not None and high >= position.take_profit
        if stop_hit:
            return {"price": float(position.stop_loss), "reason": "stop_loss"}
        if target_hit:
            return {"price": float(position.take_profit), "reason": "take_profit"}
    else:
        stop_hit = position.stop_loss is not None and high >= position.stop_loss
        target_hit = position.take_profit is not None and low <= position.take_profit
        if stop_hit:
            return {"price": float(position.stop_loss), "reason": "stop_loss"}
        if target_hit:
            return {"price": float(position.take_profit), "reason": "take_profit"}
    return None


def _backtest_entry_allowed(
    *,
    config: BotConfig,
    timestamp: datetime,
    daily_trade_count: int,
    daily_net_pnl: float,
    last_exit_time: datetime | None,
) -> bool:
    if int(config.max_trades_per_day) > 0 and daily_trade_count >= int(config.max_trades_per_day):
        return False
    if float(config.max_daily_loss) > 0 and daily_net_pnl <= -abs(float(config.max_daily_loss)):
        return False
    if last_exit_time is not None and int(config.cooldown_seconds) > 0:
        if timestamp < last_exit_time + timedelta(seconds=int(config.cooldown_seconds)):
            return False
    return True


def _closed_candles(candles: list[ProjectXMarketCandle]) -> list[ProjectXMarketCandle]:
    rows = [candle for candle in candles if not bool(candle.is_partial)]
    rows.sort(key=lambda row: _as_utc(row.candle_timestamp))
    return rows


def _warmup_bars(*, config: BotConfig, candle_count: int) -> int:
    configured = max(_MIN_BACKTEST_WARMUP_BARS, min(int(config.lookback_bars), _MAX_SIGNAL_LOOKBACK_BARS))
    return max(1, min(configured, candle_count - 1))


def _signal_lookback_bars(config: BotConfig) -> int:
    return max(_MIN_BACKTEST_WARMUP_BARS, min(int(config.lookback_bars), _MAX_SIGNAL_LOOKBACK_BARS))


def _signal_price(signal: SignalResult, candle: ProjectXMarketCandle) -> float:
    price = _optional_float(signal.price)
    return price if price is not None else float(candle.close_price)


def _signal_category(signal: SignalResult) -> str | None:
    payload = signal.raw_payload if isinstance(signal.raw_payload, Mapping) else None
    value = payload.get("signal_category") if payload is not None else None
    return str(value).strip().lower() if value is not None else None


def _is_opposite_action(open_side: str, signal_action: str) -> bool:
    return (open_side == "BUY" and signal_action == "SELL") or (open_side == "SELL" and signal_action == "BUY")


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _serialize_trade(trade: BacktestTrade) -> dict[str, Any]:
    return {
        "id": trade.id,
        "side": trade.side,
        "quantity": trade.quantity,
        "entry_time": trade.entry_time,
        "entry_price": trade.entry_price,
        "exit_time": trade.exit_time,
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason,
        "gross_pnl": trade.gross_pnl,
        "net_pnl": trade.net_pnl,
        "fees": trade.fees,
        "points": trade.points,
        "signal_reason": trade.signal_reason,
    }


def _round(value: float, digits: int = 2) -> float:
    if not math.isfinite(float(value)):
        return 0.0
    return round(float(value), digits)
