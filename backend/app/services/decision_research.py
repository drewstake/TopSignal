"""Forward-observed decision snapshots and explicitly limited OHLC barrier labels."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import event, func
from sqlalchemy.orm import Session

from ..market_observation_models import DecisionResearchSnapshot
from ..models import BotDecision, BotOrderAttempt, ProjectXMarketCandle, ProjectXTradeEvent
from . import market_observations as observations

VERSION = "decision_snapshot_v1"
_PENDING_KEY = "market_research_pending_snapshots"
_ROUTING_KEY = "market_research_pending_routing"


def _safe_json(value: Any, depth: int = 0) -> Any:
    """Bounded analysis whitelist serializer; never store provider credentials."""
    if depth > 8:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value[:2000] if isinstance(value, str) else value
    if isinstance(value, float):
        return observations.number(value)
    if isinstance(value, datetime):
        return observations.utc(value).isoformat()
    if isinstance(value, dict):
        banned = ("token", "secret", "password", "credential", "authorization", "api_key", "raw_request", "raw_response")
        return {str(key)[:100]: _safe_json(item, depth + 1) for key, item in list(value.items())[:150]
                if not any(term in str(key).lower() for term in banned)}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, depth + 1) for item in value[:150]]
    return observations.number(value)


def stage_decision_snapshot(db: Session, *, decision, config, signal, analysis: dict,
                            observed_at: datetime | None = None, candles: list | None = None) -> None:
    """Copy pre-outcome context now, publish only when execution DB commit succeeds.

    This hook has no provider calls, database writes, or effect on order routing.
    Rollback (including outer rollback after nested work) discards staged records.
    """
    try:
        if not observations.enabled():
            return
        observed = observations.utc(observed_at or datetime.now(timezone.utc))
        payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
        action = str(decision.action)
        direction = "long" if action == "BUY" else "short" if action == "SELL" else None
        entry_price = observations.number(payload.get("entry_price")) or observations.number(decision.price)
        stop = observations.number(payload.get("stop_loss"))
        target = observations.number(payload.get("take_profit")) or observations.number(payload.get("final_take_profit"))
        valid = bool(entry_price and stop and target and (
            direction == "long" and stop < entry_price < target or direction == "short" and target < entry_price < stop))
        score = observations.number((analysis.get("trade_evaluation") or {}).get("total_score"))
        latest_candle = max(candles, key=lambda row: row.candle_timestamp) if candles else None
        candle_source = str(getattr(latest_candle, "source", None) or "projectx")
        candle_live = bool(getattr(latest_candle, "live", False))
        snapshot = _safe_json({"version": VERSION, "observed_at": observed, "score_kind": "heuristic_not_probability",
            "candle_stream": {"source": candle_source, "live": candle_live, "contract_id": str(decision.contract_id)},
            "strategy": {key: getattr(config, key, None) for key in (
                "id", "strategy_type", "strategy_params", "timeframe_unit", "timeframe_unit_number", "order_size",
                "fast_period", "slow_period", "max_daily_loss", "max_position_size", "live")},
            "signal": {"action": action, "reason": str(decision.reason), "payload": payload},
            "analysis": analysis})
        values = {"user_id": str(decision.user_id), "decision_id": int(decision.id), "account_id": int(decision.account_id),
            "contract_id": str(decision.contract_id), "observed_at": observed, "signal_timestamp": decision.candle_timestamp,
            "candle_source": candle_source, "candle_live": candle_live,
            "action": action, "reason": str(decision.reason)[:2000], "direction": direction,
            "entry_price": entry_price, "stop_loss": stop, "take_profit": target,
            "score": int(score) if score is not None else None, "snapshot_version": VERSION,
            "snapshot_hash": observations.digest(snapshot), "snapshot": snapshot,
            "outcome": "pending" if valid else "no_geometry", "outcome_details": None,
            "routing": {"status": "awaiting_final_disposition"}}
        pending = db.info.setdefault(_PENDING_KEY, [])
        if len(pending) < 256:
            pending.append((db.get_nested_transaction() or db.get_transaction(), values))
    except Exception:
        # Auxiliary research collection cannot stop/retry an order.
        return


def stage_routing_disposition(db: Session, *, decision, evaluation_status: str, order_attempt=None, risk_events=()) -> None:
    try:
        if not observations.enabled():
            return
        routing = {"status": str(evaluation_status), "final_action": str(decision.action),
            "final_reason": str(decision.reason)[:2000], "order_attempt_id": int(order_attempt.id) if order_attempt is not None else None,
            "order_status": str(order_attempt.status) if order_attempt is not None else None,
            "execution_mode": str(order_attempt.execution_mode) if order_attempt is not None else None,
            "risk_codes": [str(item.code)[:100] for item in list(risk_events)[:30]],
            "observed_at": datetime.now(timezone.utc).isoformat()}
        pending = db.info.setdefault(_ROUTING_KEY, [])
        if len(pending) < 256:
            values = {"user_id": str(decision.user_id), "decision_id": int(decision.id), "routing": routing,
                      "fingerprint": observations.digest([decision.id, routing])}
            pending.append((db.get_nested_transaction() or db.get_transaction(), values))
    except Exception:
        pass


@event.listens_for(Session, "after_commit")
def _commit_snapshots(db: Session) -> None:
    if db.in_nested_transaction():
        return
    pending = db.info.pop(_PENDING_KEY, [])
    for _, values in pending:
        try:
            observations.writer.enqueue("decision", values)
        except Exception:
            pass
    for _, values in db.info.pop(_ROUTING_KEY, []):
        try:
            observations.writer.enqueue("routing", values)
        except Exception:
            pass


@event.listens_for(Session, "after_soft_rollback")
def _rollback_snapshots(db: Session, previous_transaction) -> None:
    # A failed nested order claim does not roll back the enclosing decision.
    # Keep records created before that savepoint, but discard anything staged
    # inside the failed scope (including children of that scope).
    if previous_transaction.parent is None:
        db.info.pop(_PENDING_KEY, None)
        db.info.pop(_ROUTING_KEY, None)
        return
    def rolled_back(scope):
        while scope is not None:
            if scope is previous_transaction:
                return True
            scope = scope.parent
        return False
    for key in (_PENDING_KEY, _ROUTING_KEY):
        if key in db.info:
            db.info[key] = [(scope, values) for scope, values in db.info[key] if not rolled_back(scope)]


def label_barriers(snapshot, candles: list, *, now: datetime, horizon_minutes: int = 60) -> dict | None:
    """Label only complete minutes strictly after observation, never the signal bar.

    This is a hypothetical barrier outcome, not a simulated/executed trade or
    realized return. Missing minutes before a hit make its sequence unknowable.
    """
    observed = observations.utc(snapshot.observed_at)
    now = observations.utc(now)
    start = observed.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = start + timedelta(minutes=horizon_minutes)
    stop, target = float(snapshot.stop_loss), float(snapshot.take_profit)
    direction = snapshot.direction
    expected = start
    selected = sorted((row for row in candles if start <= observations.utc(row.candle_timestamp) < end
                       and observations.utc(row.candle_timestamp) + timedelta(minutes=1) <= now and not row.is_partial),
                      key=lambda row: observations.utc(row.candle_timestamp))
    details = {"method": "observed_1m_barrier_sequence_v1", "start": start.isoformat(), "deadline": end.isoformat(),
               "horizon_minutes": horizon_minutes, "price_basis": "fixed_plan_barriers_no_fill_simulation"}
    # Both historical/live copies of one minute with differing OHLC are ambiguous.
    seen = {}
    conflicts = set()
    for row in selected:
        when = observations.utc(row.candle_timestamp)
        prices = tuple(float(getattr(row, field)) for field in ("open_price", "high_price", "low_price", "close_price"))
        if when in seen:
            if seen[when] != prices:
                conflicts.add(when)
            continue
        seen[when] = prices
    for when, prices in sorted(seen.items()):
        if when != expected:
            return {"outcome": "gap", "outcome_at": when, "outcome_details": {**details, "reason": "missing_observed_minute", "first_missing_at": expected.isoformat()}}
        if when in conflicts:
            return {"outcome": "ambiguous", "outcome_at": when, "outcome_details": {**details, "reason": "conflicting_candle_sources"}}
        opening, high, low, _ = prices
        opening_target = opening >= target if direction == "long" else opening <= target
        opening_stop = opening <= stop if direction == "long" else opening >= stop
        hit_target = high >= target if direction == "long" else low <= target
        hit_stop = low <= stop if direction == "long" else high >= stop
        outcome = "target" if opening_target else "stop" if opening_stop else "ambiguous" if hit_target and hit_stop else "target" if hit_target else "stop" if hit_stop else None
        if outcome:
            return {"outcome": outcome, "outcome_at": when, "outcome_details": {**details, "reason": "both_barriers_in_one_minute" if outcome == "ambiguous" else "opening_beyond_barrier" if opening_target or opening_stop else "barrier_observed", "timestamp_resolution": "minute_not_exact_hit_time"}}
        expected = when + timedelta(minutes=1)
    if now >= end:
        outcome = "expired" if expected == end else "gap"
        return {"outcome": outcome, "outcome_at": end, "outcome_details": {**details, "reason": "horizon_completed" if outcome == "expired" else "missing_observed_minute", "first_missing_at": expected.isoformat() if outcome == "gap" else None}}
    return None


def evaluate_pending(db, *, user_id: str, account_id: int, now: datetime | None = None, limit: int = 200) -> dict:
    now = observations.utc(now or datetime.now(timezone.utc))
    rows = db.query(DecisionResearchSnapshot).filter(DecisionResearchSnapshot.user_id == user_id,
        DecisionResearchSnapshot.account_id == account_id, DecisionResearchSnapshot.outcome == "pending").order_by(DecisionResearchSnapshot.observed_at).limit(min(200, limit)).all()
    counts = Counter()
    for row in rows:
        start = observations.utc(row.observed_at).replace(second=0, microsecond=0) + timedelta(minutes=1)
        candles = db.query(ProjectXMarketCandle).filter(ProjectXMarketCandle.user_id == user_id,
            ProjectXMarketCandle.contract_id == row.contract_id, ProjectXMarketCandle.unit == "minute",
            ProjectXMarketCandle.source == row.candle_source, ProjectXMarketCandle.live == row.candle_live,
            ProjectXMarketCandle.unit_number == 1, ProjectXMarketCandle.candle_timestamp >= start,
            ProjectXMarketCandle.candle_timestamp < min(start + timedelta(minutes=60), now),
            ProjectXMarketCandle.is_partial.is_(False)).order_by(ProjectXMarketCandle.candle_timestamp).limit(121).all()
        result = label_barriers(row, candles, now=now)
        if result:
            for key, value in result.items():
                setattr(row, key, value)
            counts[result["outcome"]] += 1
    db.commit()
    return {"evaluated": len(rows), "updated": sum(counts.values()), "outcomes": dict(counts), "downloads": 0}


def execution_summary(db, *, user_id: str, account_id: int) -> dict:
    attempts = db.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == user_id, BotOrderAttempt.account_id == account_id).order_by(BotOrderAttempt.id.desc()).limit(1000).all()
    matched_orders = fill_count = 0
    differences: dict[str, list[float]] = {}
    seen_ids = set()
    for attempt in attempts:
        provider_id = str(attempt.provider_order_id or "").strip()
        if not provider_id or provider_id in seen_ids:
            continue
        seen_ids.add(provider_id)
        fills = db.query(ProjectXTradeEvent).filter(ProjectXTradeEvent.user_id == user_id,
            ProjectXTradeEvent.account_id == account_id, ProjectXTradeEvent.contract_id == attempt.contract_id,
            ProjectXTradeEvent.order_id == provider_id, ProjectXTradeEvent.side == attempt.side,
            ProjectXTradeEvent.import_batch_id.is_(None)).all()
        if not fills:
            continue
        matched_orders += 1
        fill_count += len(fills)
        decision = db.query(BotDecision).filter(BotDecision.id == attempt.bot_decision_id,
            BotDecision.user_id == user_id, BotDecision.account_id == account_id).first()
        if decision is not None and decision.price is not None:
            quantity = sum(float(fill.size) for fill in fills)
            average_fill = sum(float(fill.price) * float(fill.size) for fill in fills) / quantity
            differences.setdefault(attempt.contract_id, []).append((average_fill - float(decision.price)) * (1 if attempt.side == "BUY" else -1))
    averages = {contract: sum(values) / len(values) for contract, values in differences.items()}
    return {"order_attempts": len(attempts), "matched_orders": matched_orders, "matched_fill_count": fill_count,
            "mean_signed_price_difference": next(iter(averages.values())) if len(averages) == 1 else None,
            "price_difference_by_contract": averages,
            "latency_ms": None, "limitations": ["Latest 1,000 attempts; fills linked only by exact provider order ID, owner, account and contract.",
                "Price difference is versus the decision reference, not measured arrival-quote slippage.",
                "No provider submission/acknowledgement timestamps are captured; database timestamps are not execution latency."]}


def research_status(db, *, user_id: str, account_id: int, limit: int = 100) -> dict:
    query = db.query(DecisionResearchSnapshot).filter(DecisionResearchSnapshot.user_id == user_id, DecisionResearchSnapshot.account_id == account_id)
    counts = dict(query.with_entities(DecisionResearchSnapshot.outcome, func.count(DecisionResearchSnapshot.id)).group_by(DecisionResearchSnapshot.outcome).all())
    rows = query.order_by(DecisionResearchSnapshot.observed_at.desc(), DecisionResearchSnapshot.id.desc()).limit(limit).all()
    fields = ("id", "decision_id", "account_id", "contract_id", "action", "reason", "observed_at", "score", "direction", "entry_price", "stop_loss", "take_profit", "outcome", "outcome_at", "outcome_details", "snapshot_version", "snapshot_hash", "candle_source", "candle_live", "routing")
    buckets = {}
    for score, outcome, count in query.with_entities(DecisionResearchSnapshot.score, DecisionResearchSnapshot.outcome, func.count(DecisionResearchSnapshot.id)).group_by(DecisionResearchSnapshot.score, DecisionResearchSnapshot.outcome):
        if score is not None:
            key = min(90, int(score) // 10 * 10)
            bucket = buckets.setdefault(key, {"minimum_score": key, "maximum_score": key + 9 if key < 90 else 100, "target": 0, "stop": 0, "other": 0})
            bucket[outcome if outcome in {"target", "stop"} else "other"] += count
    score_buckets = [{**bucket, "resolved_barrier_count": bucket["target"] + bucket["stop"],
                     "target_first_rate": bucket["target"] / (bucket["target"] + bucket["stop"]) if bucket["target"] + bucket["stop"] else None}
                    for _, bucket in sorted(buckets.items())]
    return {"items": [{**{key: getattr(row, key) for key in fields}, "score_kind": "heuristic_not_probability"} for row in rows],
            "score_buckets": score_buckets,
            "summary": {"total": sum(counts.values()), "pending": counts.get("pending", 0), "labeled": counts.get("target", 0) + counts.get("stop", 0) + counts.get("expired", 0),
                "ambiguous": counts.get("ambiguous", 0), "gap": counts.get("gap", 0), "no_geometry": counts.get("no_geometry", 0),
                "outcomes": counts, "score_kind": "heuristic_not_probability", "retention_days": observations.writer.decision_retention_days, "record_cap": observations.writer.decision_record_cap},
            "execution": execution_summary(db, user_id=user_id, account_id=account_id)}
