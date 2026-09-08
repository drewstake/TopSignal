"""Explain recorded strategy/routing results without recomputing a decision."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_bot_decision_explanation(*, config: Any, signal: Any, decision: Any,
                                   status: str, risk_events: list[Any], dry_run: bool,
                                   analysis: dict, latest_candle: Any = None,
                                   evaluated_at: datetime | None = None,
                                   order_attempt: Any = None) -> dict:
    """All outcome labels come from the completed router, never market scores."""
    payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    is_topbot = str(config.strategy_type) == "topbot_adaptive"
    name = "TopBot EMA/VWAP pullback" if is_topbot else str(config.strategy_type).replace("_", " ").title()
    action = str(decision.action)
    strategy_action = str(signal.action)
    risk_checked = strategy_action in {"BUY", "SELL"} and status != "duplicate_skipped"
    blocked = status == "risk_blocked"
    mode = "dry_run" if dry_run else "live"
    checks = [{"id": "strategy", "label": "Strategy setup", "status": "passed" if strategy_action in {"BUY", "SELL"} else "failed",
               "detail": signal.reason}]
    provenance = analysis.get("provenance", {})
    stamp = _utc(decision.candle_timestamp).isoformat() if decision.candle_timestamp is not None else None
    closed_stamp = provenance.get("latest_candle_end_timestamp")
    if closed_stamp is None and latest_candle is not None:
        seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}.get(str(latest_candle.unit))
        if seconds:
            closed_stamp = (_utc(latest_candle.candle_timestamp) + timedelta(seconds=seconds * int(latest_candle.unit_number))).isoformat()
    checks.append({"id": "closed_candle", "label": "Decision candle", "status": "passed" if stamp and stamp == provenance.get("latest_candle_timestamp") else "not_evaluated",
                   "detail": f"Closed candle on {decision.contract_id}, opening {stamp}, closing {closed_stamp}. Live quotes are separate." if stamp else "No eligible closed decision candle."})

    # These observations were actually produced by TopBot's evaluator. Absence
    # means the evaluator exited before that check; never turn it into a pass.
    if is_topbot:
        if "pullback_touched" in payload:
            touched = payload["pullback_touched"] is True
            checks.append({"id": "pullback", "label": "20 EMA pullback", "status": "passed" if touched else "failed",
                           "detail": "Previous candle touched the 20 EMA." if touched else "Waiting for the previous candle to touch the 20 EMA."})
        if "ema_slope" in payload and "session_vwap" in payload:
            checks.append({"id": "ema_vwap", "label": "EMA and regular-session VWAP", "status": "passed" if strategy_action in {"BUY", "SELL"} else "not_evaluated",
                           "detail": f"20 EMA {payload['ema']:g}, 3-bar EMA change {payload['ema_slope']:g} points; regular-session candle VWAP {payload['session_vwap']:g}. Entry also requires a confirming close beyond the previous candle and in the candle's own direction."})
        if "short_entry_allowed" in payload:
            allowed = payload["short_entry_allowed"] is True
            checks.append({"id": "short_bias", "label": "Short-entry trend filter", "status": "passed" if allowed else "failed",
                           "detail": "Shorts require the 20 EMA below a falling 50 EMA; " + ("this condition passed." if allowed else "this condition failed.")})

    risk_by_code = {str(row.code): row for row in risk_events}
    freshness_events = [row for code, row in risk_by_code.items() if code in {
        "stale_market_data", "missing_market_data", "invalid_market_data_age", "missing_actionable_candle_timestamp"}]
    checks.append({"id": "freshness", "label": "Closed-candle delivery", "status": "failed" if freshness_events else "passed" if risk_checked else "not_evaluated",
                   "detail": "; ".join(row.message for row in freshness_events) if freshness_events else
                   f"The next closed candle is due after one full interval of scheduled trading time, plus {config.max_data_staleness_seconds} seconds of configured delivery grace. "
                   + ("Closures pause the age clock; exchange closure still blocks entry." if risk_checked else "This routing gate was not evaluated for entry permission.")})
    session_codes = {"outside_session", "outside_trading_session", "exchange_session_closed", "market_closed"}
    session_events = [row for code, row in risk_by_code.items() if code in session_codes]
    configured_session = f"{config.trading_start_time}–{config.trading_end_time} America/New_York"
    strategy_session = f"{settings.get('session_start', '09:30')}–{settings.get('session_end', '15:45')} America/New_York" if is_topbot else configured_session
    checks.append({"id": "session", "label": "Entry session", "status": "failed" if session_events else "passed" if risk_checked else "not_evaluated",
                   "detail": "; ".join(row.message for row in session_events) if session_events else
                   f"Strategy entry window: {strategy_session}; configured routing window: {configured_session}. " + ("Scheduled exchange/session routing checks ran at evaluation time." if risk_checked else "Routing session checks were not run because no new order was considered.")})
    checks.append({"id": "risk", "label": "Account and risk checks", "status": "failed" if blocked else "passed" if risk_checked else "not_evaluated",
                   "detail": "; ".join(row.message for row in risk_events) if risk_events else
                   ("Applicable risk checks passed for this dry-run attempt; no live provider preflight or order was performed." if dry_run and risk_checked else
                    "Applicable routing checks passed." if risk_checked else
                    "No new order considered; account limits, position exposure, cooldown and provider preflight were not evaluated for permission.")})
    for row in risk_events:
        checks.append({"id": str(row.code), "label": str(row.code).replace("_", " ").capitalize(), "status": "failed", "detail": str(row.message)})

    if status == "held":
        summary = f"Holding: {signal.reason}"
    elif status == "risk_blocked":
        reasons = "; ".join(row.message for row in risk_events)
        summary = f"{strategy_action} setup rejected by routing checks: {reasons or 'the recorded risk gate blocked this order.'}"
    elif status == "dry_run_attempt":
        summary = f"{strategy_action} permitted for a dry-run attempt. Applicable risk checks passed; no order was sent."
    elif status == "duplicate_skipped":
        summary = "Holding this evaluation: the same candle/action already has an order attempt; a duplicate was skipped."
    elif status == "submitted":
        summary = f"{strategy_action} order submitted after routing checks; submission does not establish a fill."
    elif status == "error":
        summary = f"{strategy_action} was not confirmed as submitted: {getattr(order_attempt, 'rejection_reason', None) or 'the recorded provider outcome is an error.'}"
    else:
        summary = f"{strategy_action} evaluated; this result does not establish entry permission."
    return {"status": status, "action": action, "strategy_action": strategy_action,
            "strategy": {"name": name, "revision": payload.get("strategy_revision")},
            "summary": summary, "strategy_reason": signal.reason, "execution_mode": mode,
            "contract_id": decision.contract_id, "candle_timestamp": stamp, "candle_close_timestamp": closed_stamp,
            "evaluated_at": _utc(evaluated_at or datetime.now(timezone.utc)).isoformat(), "checks": checks,
            "limits": {"max_contracts": float(config.max_contracts), "max_open_position": float(config.max_open_position),
                       "max_daily_loss": float(config.max_daily_loss), "max_trades_per_day": int(config.max_trades_per_day),
                       "delivery_grace_seconds": int(config.max_data_staleness_seconds)},
            "basis": "Recorded strategy result and final routing outcome. Descriptive market indicators, heuristic scores and optional context do not authorize an entry."}
