from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import (
    BotConfig,
    BotDecision,
    BotOrderAttempt,
    BotRiskEvent,
    BotRun,
    ProjectXMarketCandle,
)


def serialize_bot_config(row: BotConfig) -> dict[str, Any]:
    from .bot_service import _normalize_allowed_contracts, _normalize_strategy_params

    return {
        "id": int(row.id),
        "name": row.name,
        "account_id": int(row.account_id),
        "provider": row.provider,
        "enabled": bool(row.enabled),
        "execution_mode": row.execution_mode,
        "strategy_type": row.strategy_type,
        "strategy_params": _normalize_strategy_params(row.strategy_type, row.strategy_params),
        "contract_id": row.contract_id,
        "symbol": row.symbol,
        "timeframe_unit": row.timeframe_unit,
        "timeframe_unit_number": int(row.timeframe_unit_number),
        "lookback_bars": int(row.lookback_bars),
        "fast_period": int(row.fast_period),
        "slow_period": int(row.slow_period),
        "order_size": float(row.order_size),
        "max_contracts": float(row.max_contracts),
        "max_daily_loss": float(row.max_daily_loss),
        "max_trades_per_day": int(row.max_trades_per_day),
        "max_open_position": float(row.max_open_position),
        "allowed_contracts": _normalize_allowed_contracts(row.allowed_contracts),
        "trading_start_time": row.trading_start_time,
        "trading_end_time": row.trading_end_time,
        "cooldown_seconds": int(row.cooldown_seconds),
        "max_data_staleness_seconds": int(row.max_data_staleness_seconds),
        "allow_market_depth": bool(row.allow_market_depth),
        "created_at": _as_utc(row.created_at),
        "updated_at": _as_utc(row.updated_at),
    }


def serialize_supported_bot_configs(rows: list[BotConfig]) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep one incompatible legacy strategy from breaking the bot list."""

    from .bot_strategy_registry import get_strategy_definition

    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        try:
            get_strategy_definition(row.strategy_type)
            items.append(serialize_bot_config(row))
        except ValueError:
            strategy_type = str(getattr(row, "strategy_type", "") or "unknown")
            name = str(getattr(row, "name", "") or f"Bot #{getattr(row, 'id', 'unknown')}")
            warnings.append(
                f'{name} was not loaded because strategy "{strategy_type}" is not supported by this build. '
                "The saved configuration was not modified."
            )
    return items, warnings


def serialize_bot_run(row: BotRun) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "account_id": int(row.account_id),
        "status": row.status,
        "dry_run": bool(row.dry_run),
        "started_at": _as_utc(row.started_at),
        "stopped_at": _as_utc(row.stopped_at) if row.stopped_at is not None else None,
        "stop_reason": row.stop_reason,
        "last_heartbeat_at": _as_utc(row.last_heartbeat_at) if row.last_heartbeat_at is not None else None,
        "last_error": row.last_error,
        "last_evaluated_at": _as_utc(row.last_evaluated_at) if row.last_evaluated_at is not None else None,
    }


def serialize_bot_decision(row: BotDecision) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "bot_run_id": int(row.bot_run_id) if row.bot_run_id is not None else None,
        "account_id": int(row.account_id),
        "contract_id": row.contract_id,
        "symbol": row.symbol,
        "decision_type": row.decision_type,
        "action": row.action,
        "reason": row.reason,
        "candle_timestamp": _as_utc(row.candle_timestamp) if row.candle_timestamp is not None else None,
        "price": float(row.price) if row.price is not None else None,
        "quantity": float(row.quantity) if row.quantity is not None else None,
        "correlation_id": row.correlation_id,
        "idempotency_key": row.idempotency_key,
        "created_at": _as_utc(row.created_at),
    }


def serialize_bot_order_attempt(row: BotOrderAttempt) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "bot_run_id": int(row.bot_run_id) if row.bot_run_id is not None else None,
        "bot_decision_id": int(row.bot_decision_id) if row.bot_decision_id is not None else None,
        "account_id": int(row.account_id),
        "contract_id": row.contract_id,
        "side": row.side,
        "order_type": row.order_type,
        "size": float(row.size),
        "status": row.status,
        "execution_mode": row.execution_mode,
        "correlation_id": row.correlation_id,
        "idempotency_key": row.idempotency_key,
        "provider_order_id": row.provider_order_id,
        "rejection_reason": row.rejection_reason,
        "created_at": _as_utc(row.created_at),
        "updated_at": _as_utc(row.updated_at),
    }


def serialize_bot_risk_event(row: BotRiskEvent) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "bot_run_id": int(row.bot_run_id) if row.bot_run_id is not None else None,
        "account_id": int(row.account_id),
        "severity": row.severity,
        "code": row.code,
        "message": row.message,
        "created_at": _as_utc(row.created_at),
    }


def serialize_market_candle(row: ProjectXMarketCandle) -> dict[str, Any]:
    return {
        "id": int(row.id) if row.id is not None else None,
        "contract_id": row.contract_id,
        "symbol": row.symbol,
        "live": bool(row.live),
        "unit": row.unit,
        "unit_number": int(row.unit_number),
        "timestamp": _as_utc(row.candle_timestamp),
        "open": float(row.open_price),
        "high": float(row.high_price),
        "low": float(row.low_price),
        "close": float(row.close_price),
        "volume": float(row.volume),
        "is_partial": bool(row.is_partial),
        "fetched_at": _as_utc(row.fetched_at) if row.fetched_at is not None else None,
    }


def serialize_evaluation(result: Any) -> dict[str, Any]:
    from .bot_service import serialize_bot_trade_levels

    return {
        "status": result.status,
        "correlation_id": result.correlation_id,
        "idempotency_key": result.idempotency_key,
        "duplicate_of_order_attempt_id": result.duplicate_of_order_attempt_id,
        "config": serialize_bot_config(result.config),
        "run": serialize_bot_run(result.run) if result.run is not None else None,
        "decision": serialize_bot_decision(result.decision),
        "order_attempt": serialize_bot_order_attempt(result.order_attempt) if result.order_attempt is not None else None,
        "risk_events": [serialize_bot_risk_event(row) for row in result.risk_events],
        "trade_levels": serialize_bot_trade_levels(result.decision),
        "analysis": result.analysis,
        "candles": [serialize_market_candle(row) for row in result.candles[-50:]],
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "serialize_bot_config",
    "serialize_bot_decision",
    "serialize_bot_order_attempt",
    "serialize_bot_risk_event",
    "serialize_bot_run",
    "serialize_evaluation",
    "serialize_market_candle",
]
