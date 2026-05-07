from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from ..models import (
    Account,
    BotConfig,
    BotDecision,
    BotOrderAttempt,
    BotRiskEvent,
    BotRun,
    ProjectXMarketCandle,
    ProjectXTradeEvent,
)
from .projectx_accounts import ACCOUNT_STATE_ACTIVE, get_projectx_account_row
from .projectx_client import ProjectXClient
from .trading_day import TRADING_TZ, trading_day_bounds_utc, trading_day_date

_PROJECTX_UNIT_BY_NAME = {
    "second": 1,
    "minute": 2,
    "hour": 3,
    "day": 4,
    "week": 5,
    "month": 6,
}
_UNIT_SECONDS_BY_NAME = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "month": 31 * 24 * 60 * 60,
}
_MARKET_CANDLE_TAIL_REVALIDATION_BARS = 3
_MARKET_CANDLE_TAIL_REVALIDATION_TTL = timedelta(seconds=15)
_ORDER_TYPE_MARKET = 2
_SIDE_BY_ACTION = {"BUY": 0, "SELL": 1}
_LIVE_ACCOUNT_PATTERN = re.compile(r"\b(LIVE|LFA|BROKERAGE|FUNDED\s+LIVE)\b", re.IGNORECASE)
_STRATEGY_SMA_CROSS = "sma_cross"
_STRATEGY_SUPPORT_RESISTANCE = "support_resistance"
_SUPPORTED_STRATEGY_TYPES = {_STRATEGY_SMA_CROSS, _STRATEGY_SUPPORT_RESISTANCE}
_SUPPORT_RESISTANCE_DEFAULTS = {
    "bars_per_timeframe": 100,
    "swing_window": 5,
    "level_tolerance_percent": 0.25,
    "stop_beyond_level_percent": 1.0,
    "take_profit_r_multiple": 2.0,
}


@dataclass(frozen=True)
class SignalResult:
    action: str
    reason: str
    candle_timestamp: datetime | None
    price: float | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class RiskBlock:
    code: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class SupportResistanceLevel:
    side: str
    price: float
    timestamp: datetime
    timeframe: str
    source_index: int
    score: float


@dataclass(frozen=True)
class EvaluationResult:
    config: BotConfig
    run: BotRun | None
    decision: BotDecision
    order_attempt: BotOrderAttempt | None
    risk_events: list[BotRiskEvent]
    candles: list[ProjectXMarketCandle]


def list_bot_configs(
    db: Session,
    *,
    user_id: str,
    account_id: int | None = None,
) -> list[BotConfig]:
    query = db.query(BotConfig).filter(BotConfig.user_id == user_id)
    if account_id is not None:
        query = query.filter(BotConfig.account_id == account_id)
    return query.order_by(BotConfig.created_at.desc(), BotConfig.id.desc()).all()


def get_bot_config(db: Session, *, user_id: str, bot_config_id: int) -> BotConfig | None:
    return (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id)
        .filter(BotConfig.id == bot_config_id)
        .one_or_none()
    )


def create_bot_config(db: Session, *, user_id: str, payload: Any) -> BotConfig:
    _require_owned_account(db, user_id=user_id, account_id=payload.account_id)
    name = _validate_unique_bot_name(db, user_id=user_id, name=payload.name)
    _validate_strategy_periods(payload.fast_period, payload.slow_period)
    strategy_type = _validate_strategy_type(payload.strategy_type)
    strategy_params = _normalize_strategy_params(strategy_type, payload.strategy_params)
    _validate_session_time(payload.trading_start_time)
    _validate_session_time(payload.trading_end_time)

    row = BotConfig(
        user_id=user_id,
        account_id=payload.account_id,
        name=name,
        enabled=bool(payload.enabled),
        execution_mode=payload.execution_mode,
        strategy_type=strategy_type,
        strategy_params=strategy_params,
        contract_id=payload.contract_id.strip(),
        symbol=_normalized_optional_text(payload.symbol),
        timeframe_unit=payload.timeframe_unit,
        timeframe_unit_number=payload.timeframe_unit_number,
        lookback_bars=payload.lookback_bars,
        fast_period=payload.fast_period,
        slow_period=payload.slow_period,
        order_size=payload.order_size,
        max_contracts=payload.max_contracts,
        max_daily_loss=payload.max_daily_loss,
        max_trades_per_day=payload.max_trades_per_day,
        max_open_position=payload.max_open_position,
        allowed_contracts=_normalize_allowed_contracts(payload.allowed_contracts),
        trading_start_time=payload.trading_start_time,
        trading_end_time=payload.trading_end_time,
        cooldown_seconds=payload.cooldown_seconds,
        max_data_staleness_seconds=payload.max_data_staleness_seconds,
        allow_market_depth=payload.allow_market_depth,
    )
    db.add(row)
    db.flush()
    return row


def update_bot_config(db: Session, *, user_id: str, bot_config_id: int, payload: Any) -> BotConfig:
    row = get_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    if row is None:
        raise LookupError("bot_config_not_found")

    update_data = payload.model_dump(exclude_unset=True)
    if "account_id" in update_data:
        _require_owned_account(db, user_id=user_id, account_id=int(update_data["account_id"]))
    if "name" in update_data and update_data["name"] is not None:
        update_data["name"] = _validate_unique_bot_name(
            db,
            user_id=user_id,
            name=update_data["name"],
            exclude_bot_config_id=bot_config_id,
        )
    if "contract_id" in update_data and update_data["contract_id"] is not None:
        update_data["contract_id"] = str(update_data["contract_id"]).strip()
    if "symbol" in update_data:
        update_data["symbol"] = _normalized_optional_text(update_data["symbol"])
    if "strategy_type" in update_data and update_data["strategy_type"] is not None:
        update_data["strategy_type"] = _validate_strategy_type(update_data["strategy_type"])
    if "allowed_contracts" in update_data and update_data["allowed_contracts"] is not None:
        update_data["allowed_contracts"] = _normalize_allowed_contracts(update_data["allowed_contracts"])
    if "trading_start_time" in update_data and update_data["trading_start_time"] is not None:
        _validate_session_time(update_data["trading_start_time"])
    if "trading_end_time" in update_data and update_data["trading_end_time"] is not None:
        _validate_session_time(update_data["trading_end_time"])

    for key, value in update_data.items():
        setattr(row, key, value)

    _validate_strategy_periods(int(row.fast_period), int(row.slow_period))
    if "strategy_type" in update_data or "strategy_params" in update_data:
        row.strategy_params = _normalize_strategy_params(str(row.strategy_type), row.strategy_params)
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def delete_bot_config(db: Session, *, user_id: str, bot_config_id: int) -> None:
    row = get_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    if row is None:
        raise LookupError("bot_config_not_found")

    filters = {"user_id": user_id, "bot_config_id": bot_config_id}
    db.query(BotOrderAttempt).filter_by(**filters).delete(synchronize_session=False)
    db.query(BotRiskEvent).filter_by(**filters).delete(synchronize_session=False)
    db.query(BotDecision).filter_by(**filters).delete(synchronize_session=False)
    db.query(BotRun).filter_by(**filters).delete(synchronize_session=False)
    db.delete(row)
    db.flush()


def start_bot_run(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    client: ProjectXClient,
    dry_run: bool | None = None,
    confirm_live_order_routing: bool = False,
) -> EvaluationResult:
    config = _require_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    account = _require_owned_account(db, user_id=user_id, account_id=int(config.account_id))
    config.enabled = True
    config.updated_at = datetime.now(timezone.utc)
    effective_dry_run = bool(dry_run) if dry_run is not None else config.execution_mode != "live"
    run = BotRun(
        user_id=user_id,
        bot_config_id=int(config.id),
        account_id=int(config.account_id),
        status="running",
        dry_run=effective_dry_run,
        started_at=datetime.now(timezone.utc),
        last_heartbeat_at=datetime.now(timezone.utc),
        raw_state={"source": "manual_start"},
    )
    db.add(run)
    db.flush()
    return evaluate_bot_config(
        db,
        user_id=user_id,
        config=config,
        account=account,
        client=client,
        run=run,
        dry_run=effective_dry_run,
        confirm_live_order_routing=confirm_live_order_routing,
    )


def evaluate_bot_config(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    account: Account | None,
    client: ProjectXClient,
    run: BotRun | None = None,
    dry_run: bool | None = None,
    confirm_live_order_routing: bool = False,
) -> EvaluationResult:
    resolved_account = account or _require_owned_account(db, user_id=user_id, account_id=int(config.account_id))
    effective_dry_run = bool(dry_run) if dry_run is not None else config.execution_mode != "live"
    candles, signal = fetch_candles_and_evaluate_strategy(db, user_id=user_id, config=config, client=client)
    decision = BotDecision(
        user_id=user_id,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id) if run is not None and run.id is not None else None,
        account_id=int(config.account_id),
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        decision_type="signal",
        action=signal.action,
        reason=signal.reason,
        candle_timestamp=signal.candle_timestamp,
        price=signal.price,
        quantity=float(config.order_size) if signal.action in {"BUY", "SELL"} else None,
        raw_payload=signal.raw_payload,
    )
    db.add(decision)
    db.flush()

    risk_events: list[BotRiskEvent] = []
    order_attempt: BotOrderAttempt | None = None
    if signal.action in {"BUY", "SELL"}:
        blocks = evaluate_risk_gates(
            db,
            user_id=user_id,
            config=config,
            account=resolved_account,
            latest_candle=candles[-1] if candles else None,
            action=signal.action,
            dry_run=effective_dry_run,
            confirm_live_order_routing=confirm_live_order_routing,
        )
        if blocks:
            risk_events = [
                _create_risk_event(db, user_id=user_id, config=config, run=run, block=block)
                for block in blocks
            ]
            db.add(
                BotDecision(
                    user_id=user_id,
                    bot_config_id=int(config.id),
                    bot_run_id=int(run.id) if run is not None and run.id is not None else None,
                    account_id=int(config.account_id),
                    contract_id=str(config.contract_id),
                    symbol=config.symbol,
                    decision_type="risk_reject",
                    action=signal.action,
                    reason="; ".join(block.message for block in blocks),
                    candle_timestamp=signal.candle_timestamp,
                    price=signal.price,
                    quantity=float(config.order_size),
                    raw_payload={"risk_blocks": [block.__dict__ for block in blocks]},
                )
            )
            if run is not None:
                run.status = "blocked"
                run.stopped_at = datetime.now(timezone.utc)
                run.stop_reason = "risk_gate_blocked_order"
        else:
            order_attempt = _create_order_attempt(
                db,
                user_id=user_id,
                config=config,
                run=run,
                decision=decision,
                action=signal.action,
            )
            db.flush()
            # Persist the audit row before any possible external order submission.
            db.commit()
            if effective_dry_run:
                order_attempt.status = "dry_run"
                order_attempt.raw_response = {"dry_run": True, "message": "Order not sent to ProjectX."}
            else:
                _submit_order_attempt(client=client, order_attempt=order_attempt)
            db.flush()

    if run is not None and run.status == "running":
        run.last_heartbeat_at = datetime.now(timezone.utc)
    db.flush()
    return EvaluationResult(
        config=config,
        run=run,
        decision=decision,
        order_attempt=order_attempt,
        risk_events=risk_events,
        candles=candles,
    )


def stop_latest_bot_run(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    reason: str = "manual_stop",
) -> BotRun:
    config = _require_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    config.enabled = False
    config.updated_at = datetime.now(timezone.utc)
    run = (
        db.query(BotRun)
        .filter(BotRun.user_id == user_id)
        .filter(BotRun.bot_config_id == bot_config_id)
        .filter(BotRun.status == "running")
        .order_by(BotRun.started_at.desc(), BotRun.id.desc())
        .first()
    )
    if run is None:
        run = BotRun(
            user_id=user_id,
            bot_config_id=bot_config_id,
            account_id=int(config.account_id),
            status="stopped",
            dry_run=True,
            started_at=datetime.now(timezone.utc),
            stopped_at=datetime.now(timezone.utc),
            stop_reason=reason,
        )
        db.add(run)
    else:
        run.status = "stopped"
        run.stopped_at = datetime.now(timezone.utc)
        run.stop_reason = reason

    db.add(
        BotDecision(
            user_id=user_id,
            bot_config_id=bot_config_id,
            bot_run_id=int(run.id) if run.id is not None else None,
            account_id=int(config.account_id),
            contract_id=str(config.contract_id),
            symbol=config.symbol,
            decision_type="lifecycle",
            action="STOP",
            reason=reason,
        )
    )
    db.flush()
    return run


def get_bot_activity(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    limit: int = 50,
) -> dict[str, Any]:
    config = _require_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    bounded_limit = max(1, min(int(limit), 200))
    runs = (
        db.query(BotRun)
        .filter(BotRun.user_id == user_id)
        .filter(BotRun.bot_config_id == bot_config_id)
        .order_by(BotRun.started_at.desc(), BotRun.id.desc())
        .limit(10)
        .all()
    )
    decisions = (
        db.query(BotDecision)
        .filter(BotDecision.user_id == user_id)
        .filter(BotDecision.bot_config_id == bot_config_id)
        .order_by(BotDecision.created_at.desc(), BotDecision.id.desc())
        .limit(bounded_limit)
        .all()
    )
    attempts = (
        db.query(BotOrderAttempt)
        .filter(BotOrderAttempt.user_id == user_id)
        .filter(BotOrderAttempt.bot_config_id == bot_config_id)
        .order_by(BotOrderAttempt.created_at.desc(), BotOrderAttempt.id.desc())
        .limit(bounded_limit)
        .all()
    )
    risk_events = (
        db.query(BotRiskEvent)
        .filter(BotRiskEvent.user_id == user_id)
        .filter(BotRiskEvent.bot_config_id == bot_config_id)
        .order_by(BotRiskEvent.created_at.desc(), BotRiskEvent.id.desc())
        .limit(bounded_limit)
        .all()
    )
    return {
        "config": config,
        "runs": runs,
        "decisions": decisions,
        "order_attempts": attempts,
        "risk_events": risk_events,
    }


def fetch_candles_and_evaluate_strategy(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
) -> tuple[list[ProjectXMarketCandle], SignalResult]:
    strategy_type = _validate_strategy_type(str(config.strategy_type))
    if strategy_type == _STRATEGY_SUPPORT_RESISTANCE:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candle_sets = fetch_and_store_support_resistance_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        candles_1h = candle_sets.get("1H", [])
        signal = evaluate_support_resistance_levels(
            higher_timeframe_candles=candle_sets.get("4H", []),
            lower_timeframe_candles=candles_1h,
            strategy_params=strategy_params,
        )
        return candles_1h, signal

    candles = fetch_and_store_candles(db, user_id=user_id, config=config, client=client)
    signal = evaluate_sma_cross(candles, fast_period=int(config.fast_period), slow_period=int(config.slow_period))
    return candles, signal


def fetch_and_store_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
) -> list[ProjectXMarketCandle]:
    now = datetime.now(timezone.utc)
    unit_seconds = _UNIT_SECONDS_BY_NAME[str(config.timeframe_unit)]
    lookback_seconds = unit_seconds * int(config.timeframe_unit_number) * int(config.lookback_bars) * 3
    start = now - timedelta(seconds=max(lookback_seconds, unit_seconds * int(config.timeframe_unit_number) * 25))
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )
    bars = client.retrieve_bars(
        contract_id=contract_id,
        live=False,
        start=start,
        end=now,
        unit=_PROJECTX_UNIT_BY_NAME[str(config.timeframe_unit)],
        unit_number=int(config.timeframe_unit_number),
        limit=int(config.lookback_bars),
        include_partial_bar=False,
    )
    return store_market_candles(
        db,
        user_id=user_id,
        contract_id=contract_id,
        symbol=symbol,
        live=False,
        unit=str(config.timeframe_unit),
        unit_number=int(config.timeframe_unit_number),
        bars=bars,
    )


def fetch_and_store_support_resistance_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, list[ProjectXMarketCandle]]:
    params = _normalize_strategy_params(_STRATEGY_SUPPORT_RESISTANCE, strategy_params)
    bars_per_timeframe = int(params["bars_per_timeframe"])
    now = datetime.now(timezone.utc)
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )

    candle_sets: dict[str, list[ProjectXMarketCandle]] = {}
    for label, unit, unit_number in (("4H", "hour", 4), ("1H", "hour", 1)):
        unit_seconds = _UNIT_SECONDS_BY_NAME[unit]
        lookback_seconds = unit_seconds * unit_number * bars_per_timeframe * 3
        start = now - timedelta(seconds=lookback_seconds)
        bars = client.retrieve_bars(
            contract_id=contract_id,
            live=False,
            start=start,
            end=now,
            unit=_PROJECTX_UNIT_BY_NAME[unit],
            unit_number=unit_number,
            limit=bars_per_timeframe,
            include_partial_bar=False,
        )
        candle_sets[label] = store_market_candles(
            db,
            user_id=user_id,
            contract_id=contract_id,
            symbol=symbol,
            live=False,
            unit=unit,
            unit_number=unit_number,
            bars=bars,
        )

    return candle_sets


def fetch_and_store_market_candles(
    db: Session,
    *,
    user_id: str,
    client: ProjectXClient,
    contract_id: str,
    symbol: str | None,
    live: bool,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    limit: int,
    include_partial_bar: bool = False,
) -> list[ProjectXMarketCandle]:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _PROJECTX_UNIT_BY_NAME:
        raise ValueError("unsupported candle unit")
    if start > end:
        raise ValueError("start must be before end")
    resolved_contract_id, resolved_symbol = resolve_market_contract(
        client,
        contract_id=contract_id,
        symbol=symbol,
        live=live,
    )
    bars = client.retrieve_bars(
        contract_id=resolved_contract_id,
        live=live,
        start=start,
        end=end,
        unit=_PROJECTX_UNIT_BY_NAME[normalized_unit],
        unit_number=unit_number,
        limit=limit,
        include_partial_bar=include_partial_bar,
    )
    return store_market_candles(
        db,
        user_id=user_id,
        contract_id=resolved_contract_id,
        symbol=resolved_symbol,
        live=live,
        unit=normalized_unit,
        unit_number=unit_number,
        bars=bars,
    )


def list_market_candles(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    live: bool,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    limit: int,
    include_partial_bar: bool = False,
) -> list[ProjectXMarketCandle]:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _PROJECTX_UNIT_BY_NAME:
        raise ValueError("unsupported candle unit")
    if start > end:
        raise ValueError("start must be before end")

    query = (
        db.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == normalized_unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp >= _as_utc(start))
        .filter(ProjectXMarketCandle.candle_timestamp <= _as_utc(end))
    )
    if not include_partial_bar:
        query = query.filter(ProjectXMarketCandle.is_partial.is_(False))

    rows = (
        query.order_by(ProjectXMarketCandle.candle_timestamp.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    rows.sort(key=lambda row: _as_utc(row.candle_timestamp))
    return rows


def prune_market_candle_cache_range(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    live: bool,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    keep_timestamps: Iterable[datetime],
) -> int:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _PROJECTX_UNIT_BY_NAME:
        raise ValueError("unsupported candle unit")
    if start > end:
        raise ValueError("start must be before end")

    query = (
        db.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == normalized_unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp >= _as_utc(start))
        .filter(ProjectXMarketCandle.candle_timestamp <= _as_utc(end))
    )

    timestamps_to_keep = [_as_utc(timestamp) for timestamp in keep_timestamps]
    if timestamps_to_keep:
        query = query.filter(ProjectXMarketCandle.candle_timestamp.notin_(timestamps_to_keep))

    deleted = query.delete(synchronize_session=False)
    db.flush()
    return int(deleted or 0)


def market_candle_cache_needs_refresh(
    cached_candles: list[ProjectXMarketCandle],
    *,
    end: datetime,
    unit: str,
    unit_number: int,
    include_partial_bar: bool = False,
) -> bool:
    if not cached_candles:
        return True

    interval = _market_candle_interval(unit=unit, unit_number=unit_number)
    latest_timestamp = max(_as_utc(row.candle_timestamp) for row in cached_candles)
    end_utc = _as_utc(end)
    if include_partial_bar:
        return latest_timestamp + interval <= end_utc
    if latest_timestamp + interval + interval <= end_utc:
        return True
    if latest_timestamp + interval <= end_utc:
        return _market_candle_tail_revalidation_due(cached_candles)
    return False


def market_candle_cache_covers_request(
    cached_candles: list[ProjectXMarketCandle],
    *,
    start: datetime,
    unit: str,
    unit_number: int,
    limit: int,
) -> bool:
    if not cached_candles:
        return False

    normalized_limit = max(1, int(limit))
    if len(cached_candles) >= normalized_limit:
        return True

    interval = _market_candle_interval(unit=unit, unit_number=unit_number)
    earliest_timestamp = min(_as_utc(row.candle_timestamp) for row in cached_candles)
    return earliest_timestamp <= _as_utc(start) + interval


def next_market_candle_fetch_start(
    cached_candles: list[ProjectXMarketCandle],
    *,
    start: datetime,
    unit: str,
    unit_number: int,
) -> datetime:
    start_utc = _as_utc(start)
    if not cached_candles:
        return start_utc

    interval = _market_candle_interval(unit=unit, unit_number=unit_number)
    latest_timestamp = max(_as_utc(row.candle_timestamp) for row in cached_candles)
    overlap = interval * _MARKET_CANDLE_TAIL_REVALIDATION_BARS
    return max(start_utc, latest_timestamp - overlap)


def _market_candle_tail_revalidation_due(cached_candles: list[ProjectXMarketCandle]) -> bool:
    latest_timestamp = max(_as_utc(row.candle_timestamp) for row in cached_candles)
    fetched_values = [
        row.fetched_at
        for row in cached_candles
        if _as_utc(row.candle_timestamp) == latest_timestamp and row.fetched_at is not None
    ]
    if not fetched_values:
        return True

    newest_fetch = max(_as_utc(value) for value in fetched_values)
    return newest_fetch + _MARKET_CANDLE_TAIL_REVALIDATION_TTL <= datetime.now(timezone.utc)


def resolve_market_contract(
    client: ProjectXClient,
    *,
    contract_id: str,
    symbol: str | None,
    live: bool,
) -> tuple[str, str | None]:
    normalized_contract_id = str(contract_id).strip()
    normalized_symbol = _normalized_optional_text(symbol)
    if _looks_like_projectx_contract_id(normalized_contract_id):
        return normalized_contract_id, normalized_symbol

    candidates = _unique_text_values([normalized_contract_id, normalized_symbol])
    for candidate in candidates:
        rows = client.search_contracts(search_text=candidate, live=live)
        resolved = _pick_market_contract(rows)
        if resolved is None:
            continue
        resolved_id = _normalized_optional_text(resolved.get("id"))
        if resolved_id is None:
            continue
        resolved_symbol = _normalized_optional_text(resolved.get("symbol_id")) or normalized_symbol
        return resolved_id, resolved_symbol

    return normalized_contract_id, normalized_symbol


def store_market_candles(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    symbol: str | None,
    live: bool,
    unit: str,
    unit_number: int,
    bars: Iterable[dict[str, Any]],
) -> list[ProjectXMarketCandle]:
    normalized = _dedupe_market_candle_bars(bars)
    if not normalized:
        return []

    timestamps = [bar["timestamp"] for bar in normalized]
    fetched_at = datetime.now(timezone.utc)

    if _session_dialect_name(db) == "postgresql":
        _upsert_market_candle_rows(
            db,
            values=[
                _market_candle_insert_values(
                    user_id=user_id,
                    contract_id=contract_id,
                    symbol=symbol,
                    live=live,
                    unit=unit,
                    unit_number=unit_number,
                    bar=bar,
                    fetched_at=fetched_at,
                )
                for bar in normalized
            ],
        )
        return _query_market_candles_by_timestamps(
            db,
            user_id=user_id,
            contract_id=contract_id,
            live=live,
            unit=unit,
            unit_number=unit_number,
            timestamps=timestamps,
        )

    existing_rows = (
        db.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp >= min(timestamps))
        .filter(ProjectXMarketCandle.candle_timestamp <= max(timestamps))
        .all()
    )
    existing_by_timestamp = {_as_utc(row.candle_timestamp): row for row in existing_rows}
    output: list[ProjectXMarketCandle] = []
    for bar in normalized:
        timestamp = bar["timestamp"]
        row = existing_by_timestamp.get(timestamp)
        if row is None:
            row = ProjectXMarketCandle(
                user_id=user_id,
                contract_id=contract_id,
                live=bool(live),
                unit=unit,
                unit_number=unit_number,
                candle_timestamp=timestamp,
            )
            db.add(row)
        row.symbol = symbol
        row.open_price = float(bar.get("open") or 0.0)
        row.high_price = float(bar.get("high") or 0.0)
        row.low_price = float(bar.get("low") or 0.0)
        row.close_price = float(bar.get("close") or 0.0)
        row.volume = float(bar.get("volume") or 0.0)
        row.is_partial = bool(bar.get("is_partial") or False)
        row.raw_payload = bar.get("raw_payload")
        row.fetched_at = fetched_at
        output.append(row)

    output.sort(key=lambda row: _as_utc(row.candle_timestamp))
    db.flush()
    return output


def _dedupe_market_candle_bars(bars: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_timestamp: dict[datetime, dict[str, Any]] = {}
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        timestamp = bar.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        timestamp_utc = _as_utc(timestamp)
        by_timestamp[timestamp_utc] = {**bar, "timestamp": timestamp_utc}
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def _session_dialect_name(db: Session) -> str:
    bind = db.get_bind()
    return str(bind.dialect.name) if bind is not None else ""


def _market_candle_insert_values(
    *,
    user_id: str,
    contract_id: str,
    symbol: str | None,
    live: bool,
    unit: str,
    unit_number: int,
    bar: dict[str, Any],
    fetched_at: datetime,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "contract_id": contract_id,
        "symbol": symbol,
        "live": bool(live),
        "unit": unit,
        "unit_number": unit_number,
        "candle_timestamp": bar["timestamp"],
        "open_price": float(bar.get("open") or 0.0),
        "high_price": float(bar.get("high") or 0.0),
        "low_price": float(bar.get("low") or 0.0),
        "close_price": float(bar.get("close") or 0.0),
        "volume": float(bar.get("volume") or 0.0),
        "is_partial": bool(bar.get("is_partial") or False),
        "raw_payload": bar.get("raw_payload"),
        "fetched_at": fetched_at,
    }


def _upsert_market_candle_rows(db: Session, *, values: list[dict[str, Any]]) -> None:
    if not values:
        return

    table = ProjectXMarketCandle.__table__
    insert_stmt = postgresql_insert(table).values(values)
    excluded = insert_stmt.excluded
    db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=[
                table.c.user_id,
                table.c.contract_id,
                table.c.live,
                table.c.unit,
                table.c.unit_number,
                table.c.candle_timestamp,
            ],
            set_={
                "symbol": excluded.symbol,
                "open_price": excluded.open_price,
                "high_price": excluded.high_price,
                "low_price": excluded.low_price,
                "close_price": excluded.close_price,
                "volume": excluded.volume,
                "is_partial": excluded.is_partial,
                "raw_payload": excluded.raw_payload,
                "fetched_at": excluded.fetched_at,
            },
        )
    )


def _query_market_candles_by_timestamps(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    live: bool,
    unit: str,
    unit_number: int,
    timestamps: list[datetime],
) -> list[ProjectXMarketCandle]:
    rows = (
        db.query(ProjectXMarketCandle)
        .populate_existing()
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp.in_(timestamps))
        .all()
    )
    rows.sort(key=lambda row: _as_utc(row.candle_timestamp))
    return rows


def evaluate_sma_cross(
    candles: list[ProjectXMarketCandle],
    *,
    fast_period: int,
    slow_period: int,
) -> SignalResult:
    _validate_strategy_periods(fast_period, slow_period)
    closed = [candle for candle in candles if not bool(candle.is_partial)]
    closed.sort(key=lambda candle: _as_utc(candle.candle_timestamp))
    if len(closed) < slow_period + 1:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {slow_period + 1} closed candles; found {len(closed)}.",
            candle_timestamp=_as_utc(closed[-1].candle_timestamp) if closed else None,
            price=float(closed[-1].close_price) if closed else None,
            raw_payload={"fast_period": fast_period, "slow_period": slow_period, "closed_count": len(closed)},
        )

    closes = [float(candle.close_price) for candle in closed]
    previous_fast = _average(closes[-fast_period - 1 : -1])
    previous_slow = _average(closes[-slow_period - 1 : -1])
    current_fast = _average(closes[-fast_period:])
    current_slow = _average(closes[-slow_period:])
    latest = closed[-1]

    action = "HOLD"
    if previous_fast <= previous_slow and current_fast > current_slow:
        action = "BUY"
    elif previous_fast >= previous_slow and current_fast < current_slow:
        action = "SELL"

    if action == "HOLD":
        reason = "No SMA crossover on the latest closed candle."
    else:
        reason = f"{fast_period}/{slow_period} SMA crossover generated {action}."

    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=_as_utc(latest.candle_timestamp),
        price=float(latest.close_price),
        raw_payload={
            "fast_period": fast_period,
            "slow_period": slow_period,
            "previous_fast": previous_fast,
            "previous_slow": previous_slow,
            "current_fast": current_fast,
            "current_slow": current_slow,
        },
    )


def evaluate_support_resistance_levels(
    *,
    higher_timeframe_candles: list[ProjectXMarketCandle],
    lower_timeframe_candles: list[ProjectXMarketCandle],
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_SUPPORT_RESISTANCE, strategy_params)
    bars_per_timeframe = int(params["bars_per_timeframe"])
    swing_window = int(params["swing_window"])
    tolerance_percent = float(params["level_tolerance_percent"])
    stop_beyond_percent = float(params["stop_beyond_level_percent"])
    reward_multiple = float(params["take_profit_r_multiple"])

    higher_closed = _closed_candles(higher_timeframe_candles)[-bars_per_timeframe:]
    lower_closed = _closed_candles(lower_timeframe_candles)[-bars_per_timeframe:]
    minimum_required = swing_window
    latest = lower_closed[-1] if lower_closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None

    if len(higher_closed) < minimum_required or len(lower_closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {minimum_required} closed 4H and 1H candles; "
                f"found {len(higher_closed)} 4H and {len(lower_closed)} 1H."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload={
                "strategy_type": _STRATEGY_SUPPORT_RESISTANCE,
                "settings": params,
                "closed_counts": {"4H": len(higher_closed), "1H": len(lower_closed)},
            },
        )

    assert latest is not None
    price = float(latest.close_price)
    raw_levels = [
        *_detect_support_resistance_levels(higher_closed, timeframe="4H", window_size=swing_window),
        *_detect_support_resistance_levels(lower_closed, timeframe="1H", window_size=swing_window),
    ]
    supports = _filter_clustered_levels(
        [level for level in raw_levels if level.side == "support"],
        tolerance_percent=tolerance_percent,
    )
    resistances = _filter_clustered_levels(
        [level for level in raw_levels if level.side == "resistance"],
        tolerance_percent=tolerance_percent,
    )
    support_touch = _nearest_level_touch(
        levels=supports,
        price=price,
        side="support",
        tolerance_percent=tolerance_percent,
    )
    resistance_touch = _nearest_level_touch(
        levels=resistances,
        price=price,
        side="resistance",
        tolerance_percent=tolerance_percent,
    )
    touch = _choose_nearest_touch(support_touch, resistance_touch)

    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_SUPPORT_RESISTANCE,
        "settings": params,
        "closed_counts": {"4H": len(higher_closed), "1H": len(lower_closed)},
        "raw_level_count": len(raw_levels),
        "support_levels": [_serialize_support_resistance_level(level) for level in supports],
        "resistance_levels": [_serialize_support_resistance_level(level) for level in resistances],
    }

    if touch is None:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Price {_format_strategy_price(price)} is not within "
                f"{_format_percent(tolerance_percent)}% of a filtered support or resistance level."
            ),
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    level, distance_percent = touch
    is_support = level.side == "support"
    action = "BUY" if is_support else "SELL"
    if is_support:
        stop_loss = level.price * (1 - stop_beyond_percent / 100)
        risk = price - stop_loss
        take_profit = price + risk * reward_multiple
    else:
        stop_loss = level.price * (1 + stop_beyond_percent / 100)
        risk = stop_loss - price
        take_profit = price - risk * reward_multiple

    if risk <= 0:
        raw_payload["trigger_level"] = _serialize_support_resistance_level(level)
        raw_payload["distance_percent"] = distance_percent
        raw_payload["stop_loss"] = stop_loss
        raw_payload["take_profit"] = take_profit
        return SignalResult(
            action="HOLD",
            reason="Support/resistance level was touched, but the calculated risk was not positive.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    raw_payload.update(
        {
            "trigger_level": _serialize_support_resistance_level(level),
            "distance_percent": distance_percent,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
        }
    )
    side_name = "support" if is_support else "resistance"
    reason = (
        f"{action} within {_format_percent(distance_percent)}% of {level.timeframe} {side_name} "
        f"{_format_strategy_price(level.price)}. SL {_format_strategy_price(stop_loss)}, "
        f"TP {_format_strategy_price(take_profit)} ({_format_percent(reward_multiple)}R)."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=_as_utc(latest.candle_timestamp),
        price=price,
        raw_payload=raw_payload,
    )


def _closed_candles(candles: list[ProjectXMarketCandle]) -> list[ProjectXMarketCandle]:
    closed = [candle for candle in candles if not bool(candle.is_partial)]
    closed.sort(key=lambda candle: _as_utc(candle.candle_timestamp))
    return closed


def _detect_support_resistance_levels(
    candles: list[ProjectXMarketCandle],
    *,
    timeframe: str,
    window_size: int,
) -> list[SupportResistanceLevel]:
    span = max(3, int(window_size))
    if span % 2 == 0:
        span += 1
    radius = span // 2
    if len(candles) < span:
        return []

    levels: list[SupportResistanceLevel] = []
    timeframe_weight = 2.0 if timeframe == "4H" else 1.0
    last_index = len(candles) - 1
    for index in range(radius, len(candles) - radius):
        center = candles[index]
        window = candles[index - radius : index + radius + 1]
        highs = [float(candle.high_price) for candle in window]
        lows = [float(candle.low_price) for candle in window]
        center_high = float(center.high_price)
        center_low = float(center.low_price)
        recency_score = index / last_index if last_index > 0 else 0
        score = timeframe_weight + recency_score
        timestamp = _as_utc(center.candle_timestamp)

        if center_high == max(highs) and highs.count(center_high) == 1:
            levels.append(
                SupportResistanceLevel(
                    side="resistance",
                    price=center_high,
                    timestamp=timestamp,
                    timeframe=timeframe,
                    source_index=index,
                    score=score,
                )
            )
        if center_low == min(lows) and lows.count(center_low) == 1:
            levels.append(
                SupportResistanceLevel(
                    side="support",
                    price=center_low,
                    timestamp=timestamp,
                    timeframe=timeframe,
                    source_index=index,
                    score=score,
                )
            )

    return levels


def _filter_clustered_levels(
    levels: list[SupportResistanceLevel],
    *,
    tolerance_percent: float,
) -> list[SupportResistanceLevel]:
    kept: list[SupportResistanceLevel] = []
    for level in sorted(levels, key=lambda item: (-item.score, -item.timestamp.timestamp())):
        if any(_level_distance_percent(level.price, existing.price) <= tolerance_percent for existing in kept):
            continue
        kept.append(level)
    return sorted(kept, key=lambda item: item.price)


def _nearest_level_touch(
    *,
    levels: list[SupportResistanceLevel],
    price: float,
    side: str,
    tolerance_percent: float,
) -> tuple[SupportResistanceLevel, float] | None:
    touches: list[tuple[SupportResistanceLevel, float]] = []
    for level in levels:
        if side == "support" and price < level.price:
            continue
        if side == "resistance" and price > level.price:
            continue
        distance_percent = _level_distance_percent(price, level.price)
        if distance_percent <= tolerance_percent:
            touches.append((level, distance_percent))
    if not touches:
        return None
    return min(touches, key=lambda item: (item[1], -item[0].score))


def _choose_nearest_touch(
    support_touch: tuple[SupportResistanceLevel, float] | None,
    resistance_touch: tuple[SupportResistanceLevel, float] | None,
) -> tuple[SupportResistanceLevel, float] | None:
    if support_touch is None:
        return resistance_touch
    if resistance_touch is None:
        return support_touch
    return min([support_touch, resistance_touch], key=lambda item: (item[1], -item[0].score))


def _level_distance_percent(left: float, right: float) -> float:
    denominator = (abs(left) + abs(right)) / 2
    if denominator <= 0:
        return 0.0 if left == right else float("inf")
    return abs(left - right) / denominator * 100


def _serialize_support_resistance_level(level: SupportResistanceLevel) -> dict[str, Any]:
    return {
        "side": level.side,
        "price": level.price,
        "timestamp": level.timestamp.isoformat(),
        "timeframe": level.timeframe,
        "source_index": level.source_index,
        "score": level.score,
    }


def _format_strategy_price(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_percent(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def evaluate_risk_gates(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    account: Account,
    latest_candle: ProjectXMarketCandle | None,
    action: str,
    dry_run: bool,
    confirm_live_order_routing: bool,
) -> list[RiskBlock]:
    blocks: list[RiskBlock] = []
    if not bool(config.enabled):
        blocks.append(RiskBlock(code="bot_disabled", message="Bot is disabled.", severity="critical"))
    if account.account_state != ACCOUNT_STATE_ACTIVE:
        blocks.append(
            RiskBlock(
                code="account_not_active",
                message=f"Account state is {account.account_state}; only ACTIVE accounts can execute bot orders.",
                severity="critical",
            )
        )
    if not dry_run and account.can_trade is False:
        blocks.append(RiskBlock(code="account_cannot_trade", message="Provider marks this account as not tradable.", severity="critical"))
    if not dry_run and _looks_like_live_funded_account(account):
        blocks.append(
            RiskBlock(
                code="live_funded_api_blocked",
                message="Live Funded Account naming detected; ProjectX API automation is blocked for this account type.",
                severity="critical",
            )
        )
    if not dry_run and not confirm_live_order_routing:
        blocks.append(
            RiskBlock(
                code="live_order_confirmation_missing",
                message="Live order routing requires explicit confirmation for this request.",
                severity="critical",
            )
        )
    if str(config.contract_id) not in _effective_allowed_contracts(config):
        blocks.append(RiskBlock(code="contract_not_allowed", message="Contract is outside this bot's allowed contract list."))
    order_size = float(config.order_size)
    if abs(order_size - round(order_size)) > 1e-9:
        blocks.append(RiskBlock(code="fractional_contract_size", message="ProjectX futures order size must be a whole number."))
    if order_size > float(config.max_contracts):
        blocks.append(RiskBlock(code="max_contracts", message="Order size exceeds max contracts."))
    if order_size > float(config.max_open_position):
        blocks.append(RiskBlock(code="max_open_position", message="Order size exceeds max open position setting."))
    if _todays_bot_trade_count(db, user_id=user_id, config=config) >= int(config.max_trades_per_day):
        blocks.append(RiskBlock(code="max_trades_per_day", message="Daily bot trade limit has been reached."))
    daily_pnl = _todays_account_net_pnl(db, user_id=user_id, account_id=int(config.account_id))
    if daily_pnl <= -float(config.max_daily_loss):
        blocks.append(RiskBlock(code="max_daily_loss", message="Account has reached the configured daily loss limit.", severity="critical"))
    if latest_candle is None:
        blocks.append(RiskBlock(code="missing_market_data", message="No closed candle data is available.", severity="critical"))
    else:
        staleness = (datetime.now(timezone.utc) - _as_utc(latest_candle.candle_timestamp)).total_seconds()
        if staleness > int(config.max_data_staleness_seconds):
            blocks.append(RiskBlock(code="stale_market_data", message="Latest candle is stale.", severity="critical"))
    if not _is_inside_trading_session(str(config.trading_start_time), str(config.trading_end_time)):
        blocks.append(RiskBlock(code="outside_session", message="Current time is outside the bot trading session."))
    cooldown_block = _cooldown_block(db, user_id=user_id, config=config)
    if cooldown_block is not None:
        blocks.append(cooldown_block)
    if action not in {"BUY", "SELL"}:
        blocks.append(RiskBlock(code="unsupported_action", message="Only BUY and SELL actions can create order attempts."))
    return blocks


def serialize_bot_config(row: BotConfig) -> dict[str, Any]:
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


def serialize_evaluation(result: EvaluationResult) -> dict[str, Any]:
    return {
        "config": serialize_bot_config(result.config),
        "run": serialize_bot_run(result.run) if result.run is not None else None,
        "decision": serialize_bot_decision(result.decision),
        "order_attempt": serialize_bot_order_attempt(result.order_attempt) if result.order_attempt is not None else None,
        "risk_events": [serialize_bot_risk_event(row) for row in result.risk_events],
        "candles": [serialize_market_candle(row) for row in result.candles[-50:]],
    }


def _require_bot_config(db: Session, *, user_id: str, bot_config_id: int) -> BotConfig:
    row = get_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    if row is None:
        raise LookupError("bot_config_not_found")
    return row


def _require_owned_account(db: Session, *, user_id: str, account_id: int) -> Account:
    account = get_projectx_account_row(db, account_id, user_id=user_id)
    if account is None:
        raise LookupError("account_not_found")
    return account


def _create_risk_event(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    run: BotRun | None,
    block: RiskBlock,
) -> BotRiskEvent:
    row = BotRiskEvent(
        user_id=user_id,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id) if run is not None and run.id is not None else None,
        account_id=int(config.account_id),
        severity=block.severity,
        code=block.code,
        message=block.message,
        raw_payload=block.__dict__,
    )
    db.add(row)
    return row


def _create_order_attempt(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    run: BotRun | None,
    decision: BotDecision,
    action: str,
) -> BotOrderAttempt:
    request_payload = {
        "accountId": int(config.account_id),
        "contractId": str(config.contract_id),
        "type": _ORDER_TYPE_MARKET,
        "side": _SIDE_BY_ACTION[action],
        "size": int(round(float(config.order_size))),
        "customTag": f"topsignal-bot-{int(config.id)}-{int(decision.id)}",
    }
    if isinstance(decision.raw_payload, dict):
        strategy_order_plan = {
            key: decision.raw_payload[key]
            for key in ["strategy_type", "trigger_level", "stop_loss", "take_profit", "risk"]
            if key in decision.raw_payload
        }
        if strategy_order_plan:
            request_payload["strategyOrderPlan"] = strategy_order_plan
    row = BotOrderAttempt(
        user_id=user_id,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id) if run is not None and run.id is not None else None,
        bot_decision_id=int(decision.id),
        account_id=int(config.account_id),
        contract_id=str(config.contract_id),
        side=action,
        order_type="market",
        size=float(config.order_size),
        status="pending",
        raw_request=request_payload,
    )
    db.add(row)
    return row


def _submit_order_attempt(*, client: ProjectXClient, order_attempt: BotOrderAttempt) -> None:
    request_payload = order_attempt.raw_request or {}
    try:
        response = client.place_order(
            account_id=int(request_payload["accountId"]),
            contract_id=str(request_payload["contractId"]),
            order_type=int(request_payload["type"]),
            side=int(request_payload["side"]),
            size=int(request_payload["size"]),
            custom_tag=str(request_payload.get("customTag")) if request_payload.get("customTag") else None,
        )
        order_attempt.status = "submitted"
        order_attempt.provider_order_id = response.get("order_id")
        order_attempt.raw_response = response.get("raw_payload")
    except Exception as exc:
        order_attempt.status = "error"
        order_attempt.rejection_reason = str(exc)
        order_attempt.raw_response = {"error": str(exc)}


def _effective_allowed_contracts(config: BotConfig) -> set[str]:
    values = _normalize_allowed_contracts(config.allowed_contracts)
    if not values:
        return {str(config.contract_id)}
    return set(values)


def _todays_bot_trade_count(db: Session, *, user_id: str, config: BotConfig) -> int:
    start, end = trading_day_bounds_utc(trading_day_date(datetime.now(timezone.utc)))
    count = (
        db.query(func.count(BotOrderAttempt.id))
        .filter(BotOrderAttempt.user_id == user_id)
        .filter(BotOrderAttempt.bot_config_id == int(config.id))
        .filter(BotOrderAttempt.created_at >= start)
        .filter(BotOrderAttempt.created_at <= end)
        .filter(BotOrderAttempt.status.in_(["dry_run", "submitted"]))
        .scalar()
    )
    return int(count or 0)


def _todays_account_net_pnl(db: Session, *, user_id: str, account_id: int) -> float:
    start, end = trading_day_bounds_utc(trading_day_date(datetime.now(timezone.utc)))
    rows = (
        db.query(ProjectXTradeEvent.pnl, ProjectXTradeEvent.fees)
        .filter(ProjectXTradeEvent.user_id == user_id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(ProjectXTradeEvent.trade_timestamp >= start)
        .filter(ProjectXTradeEvent.trade_timestamp <= end)
        .filter(ProjectXTradeEvent.pnl.isnot(None))
        .all()
    )
    total = 0.0
    for row in rows:
        total += float(row.pnl or 0.0) - float(row.fees or 0.0)
    return total


def _cooldown_block(db: Session, *, user_id: str, config: BotConfig) -> RiskBlock | None:
    cooldown_seconds = int(config.cooldown_seconds)
    if cooldown_seconds <= 0:
        return None
    threshold = datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)
    recent_attempt = (
        db.query(BotOrderAttempt)
        .filter(BotOrderAttempt.user_id == user_id)
        .filter(BotOrderAttempt.bot_config_id == int(config.id))
        .filter(BotOrderAttempt.created_at >= threshold)
        .filter(BotOrderAttempt.status.in_(["blocked", "rejected", "error"]))
        .order_by(BotOrderAttempt.created_at.desc())
        .first()
    )
    if recent_attempt is not None:
        return RiskBlock(code="cooldown_after_rejection", message="Cooldown is active after a rejected or failed order.")

    recent_loss = (
        db.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.user_id == user_id)
        .filter(ProjectXTradeEvent.account_id == int(config.account_id))
        .filter(ProjectXTradeEvent.contract_id == str(config.contract_id))
        .filter(ProjectXTradeEvent.trade_timestamp >= threshold)
        .filter(ProjectXTradeEvent.pnl < 0)
        .order_by(ProjectXTradeEvent.trade_timestamp.desc())
        .first()
    )
    if recent_loss is not None:
        return RiskBlock(code="cooldown_after_loss", message="Cooldown is active after a losing trade.")
    return None


def _looks_like_live_funded_account(account: Account) -> bool:
    text = " ".join(
        value
        for value in [account.name, account.display_name, account.external_id]
        if isinstance(value, str)
    )
    return bool(_LIVE_ACCOUNT_PATTERN.search(text))


def _is_inside_trading_session(start_text: str, end_text: str) -> bool:
    start = _parse_session_time(start_text)
    end = _parse_session_time(end_text)
    current = datetime.now(TRADING_TZ).time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _parse_session_time(value: str) -> time:
    _validate_session_time(value)
    hour_text, minute_text = value.split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))


def _validate_session_time(value: str) -> None:
    if not re.fullmatch(r"\d{2}:\d{2}", str(value)):
        raise ValueError("session times must use HH:MM format")
    hour_text, minute_text = str(value).split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour > 23 or minute > 59:
        raise ValueError("session times must use HH:MM format")


def _validate_strategy_periods(fast_period: int, slow_period: int) -> None:
    if int(fast_period) <= 0:
        raise ValueError("fast_period must be positive")
    if int(slow_period) <= int(fast_period):
        raise ValueError("slow_period must be greater than fast_period")


def _validate_strategy_type(value: Any) -> str:
    strategy_type = str(value or _STRATEGY_SMA_CROSS).strip()
    if strategy_type not in _SUPPORTED_STRATEGY_TYPES:
        raise ValueError("unsupported bot strategy type")
    return strategy_type


def _normalize_strategy_params(strategy_type: Any, params: Any) -> dict[str, Any]:
    normalized_strategy_type = _validate_strategy_type(strategy_type)
    if normalized_strategy_type != _STRATEGY_SUPPORT_RESISTANCE:
        return {}

    raw_params = params if isinstance(params, dict) else {}
    bars_per_timeframe = _bounded_int_param(raw_params, "bars_per_timeframe", 100, minimum=25, maximum=500)
    swing_window = _bounded_int_param(raw_params, "swing_window", 5, minimum=3, maximum=51)
    if swing_window % 2 == 0:
        swing_window += 1
    return {
        "bars_per_timeframe": bars_per_timeframe,
        "swing_window": swing_window,
        "level_tolerance_percent": _bounded_float_param(
            raw_params,
            "level_tolerance_percent",
            float(_SUPPORT_RESISTANCE_DEFAULTS["level_tolerance_percent"]),
            minimum=0.01,
            maximum=10,
        ),
        "stop_beyond_level_percent": _bounded_float_param(
            raw_params,
            "stop_beyond_level_percent",
            float(_SUPPORT_RESISTANCE_DEFAULTS["stop_beyond_level_percent"]),
            minimum=0.01,
            maximum=20,
        ),
        "take_profit_r_multiple": _bounded_float_param(
            raw_params,
            "take_profit_r_multiple",
            float(_SUPPORT_RESISTANCE_DEFAULTS["take_profit_r_multiple"]),
            minimum=0.1,
            maximum=20,
        ),
    }


def _bounded_int_param(
    params: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_float_param(
    params: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not value == value:
        value = default
    return max(minimum, min(maximum, value))


def _validate_unique_bot_name(
    db: Session,
    *,
    user_id: str,
    name: Any,
    exclude_bot_config_id: int | None = None,
) -> str:
    normalized_name = str(name).strip()
    if not normalized_name:
        raise ValueError("Bot name is required.")

    query = (
        db.query(BotConfig.id)
        .filter(BotConfig.user_id == user_id)
        .filter(func.lower(func.trim(BotConfig.name)) == normalized_name.lower())
    )
    if exclude_bot_config_id is not None:
        query = query.filter(BotConfig.id != int(exclude_bot_config_id))
    if query.first() is not None:
        raise ValueError("A bot with this name already exists.")
    return normalized_name


def _looks_like_projectx_contract_id(value: str) -> bool:
    return value.upper().startswith("CON.")


def _pick_market_contract(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    rows_list = [row for row in rows if isinstance(row, dict)]
    for row in rows_list:
        if bool(row.get("active_contract")):
            return row
    return rows_list[0] if rows_list else None


def _unique_text_values(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalized_optional_text(value)
        if text is None:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _normalize_allowed_contracts(values: Any) -> list[str]:
    if not values:
        return []
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _normalized_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _market_candle_interval(*, unit: str, unit_number: int) -> timedelta:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _UNIT_SECONDS_BY_NAME:
        raise ValueError("unsupported candle unit")
    return timedelta(seconds=_UNIT_SECONDS_BY_NAME[normalized_unit] * max(1, int(unit_number)))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
