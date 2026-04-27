from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func
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
_ORDER_TYPE_MARKET = 2
_SIDE_BY_ACTION = {"BUY": 0, "SELL": 1}
_LIVE_ACCOUNT_PATTERN = re.compile(r"\b(LIVE|LFA|BROKERAGE|FUNDED\s+LIVE)\b", re.IGNORECASE)


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
    _validate_strategy_periods(payload.fast_period, payload.slow_period)
    _validate_session_time(payload.trading_start_time)
    _validate_session_time(payload.trading_end_time)

    row = BotConfig(
        user_id=user_id,
        account_id=payload.account_id,
        name=payload.name.strip(),
        enabled=bool(payload.enabled),
        execution_mode=payload.execution_mode,
        strategy_type=payload.strategy_type,
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
        update_data["name"] = str(update_data["name"]).strip()
    if "contract_id" in update_data and update_data["contract_id"] is not None:
        update_data["contract_id"] = str(update_data["contract_id"]).strip()
    if "symbol" in update_data:
        update_data["symbol"] = _normalized_optional_text(update_data["symbol"])
    if "allowed_contracts" in update_data and update_data["allowed_contracts"] is not None:
        update_data["allowed_contracts"] = _normalize_allowed_contracts(update_data["allowed_contracts"])
    if "trading_start_time" in update_data and update_data["trading_start_time"] is not None:
        _validate_session_time(update_data["trading_start_time"])
    if "trading_end_time" in update_data and update_data["trading_end_time"] is not None:
        _validate_session_time(update_data["trading_end_time"])

    for key, value in update_data.items():
        setattr(row, key, value)

    _validate_strategy_periods(int(row.fast_period), int(row.slow_period))
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


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
    candles = fetch_and_store_candles(db, user_id=user_id, config=config, client=client)
    signal = evaluate_sma_cross(candles, fast_period=int(config.fast_period), slow_period=int(config.slow_period))
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
    return latest_timestamp + interval + interval <= end_utc


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
    return max(start_utc, latest_timestamp + interval)


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
    normalized = [bar for bar in bars if isinstance(bar.get("timestamp"), datetime)]
    if not normalized:
        return []

    timestamps = [_as_utc(bar["timestamp"]) for bar in normalized]
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
    fetched_at = datetime.now(timezone.utc)
    for bar in normalized:
        timestamp = _as_utc(bar["timestamp"])
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
