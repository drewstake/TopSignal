"""Prespecified exit-only research. No broker or execution imports/mutations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
import random
from pathlib import Path
from statistics import mean

from .topbot_evidence import canonical, binding
from .topbot_depth import utc

PROTOCOL = {
    "schema": "dom-exit-study-v1", "registered_on": "2026-11-04",
    "ratios": [1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3],
    "stop_points": 25, "trailing_activation": 25, "trailing_distance": 25,
    "tick_size": .25, "point_value": 2, "quantity": 1,
    "latency_ms": 250, "stop_slippage_ticks": 1, "limit_trade_through_ticks": 1,
    "trailing_management_seconds": 5,
    "round_turn_cost_usd": 1.72, "max_quote_gap_seconds": 2,
    "max_horizon_minutes": 120, "milestone_entries": 100,
    "cohort": "All ten resolved outcomes on the same entry; MNQ one-contract 25-point initial risk",
    "cutoff": "Earlier of saved session flatten deadline and entry plus 120 minutes; unresolved is censored",
    "fill_model": "Observed executable BBO, one tick through limit, adverse tick on stop after 250ms; displayed size >= quantity",
    "clock_assumption": "Provider fill and local receipt clocks assumed aligned; offset is unverified",
    "cost_model": "Fixed $1.72 round turn; actual entry fill already includes entry slippage; no second entry-slippage charge",
    "validation": "First 100 prospectively registered entries exploratory; subsequent entries held out, no automatic promotion",
    "limitations": ["Exit-only, not a complete strategy trading schedule: holding periods change later entry eligibility.",
        "Daily account risk/profit rules are captured for actual trades, not counterfactual portfolio rules.",
        "Observed quotes do not certify exchange completeness or queue priority; fills are model outcomes.",
        "Trailing reference uses a five-second management interval plus 250ms amendment latency; actual worker delays vary.",
        "Coverage exclusions may be selective. Highest in-sample mean has selection uncertainty.",
        "95% session-cluster bootstrap intervals are pointwise, not simultaneous selection guarantees."],
}
MODEL_CODE = {name: sha256((Path(__file__).parent/name).read_text(encoding="utf-8").encode()).hexdigest()
              for name in ("topbot_study.py", "topbot_study_store.py", "topbot_depth.py")}
EXPERIMENT_ID = "dom-rr-"+sha256(canonical({"protocol": PROTOCOL, "model_code": MODEL_CODE})).hexdigest()[:24]


def entry_snapshot(config, *, account, contract, side, quantity, plan, now=None):
    from .trading_day import trading_day_key
    from .topbot_profit_guard import TARGET
    now = now or datetime.now(timezone.utc)
    rules = {"entry": deepcopy(config.strategy_params), "exit": {k: plan["runner"].get(k) for k in
             ("exit_mode", "initial_stop_points", "take_profit_points", "activation_points", "trail_points")}}
    implementation = binding()
    limits = {k: str(getattr(config, k)) if getattr(config, k, None) is not None else None for k in
              ("max_daily_loss", "max_contracts", "max_trades_per_day", "max_open_position", "cooldown_seconds",
               "trading_start_time", "trading_end_time", "max_data_staleness_seconds")}
    limits["allowed_contracts"] = deepcopy(getattr(config, "allowed_contracts", None))
    limits.update(daily_profit_target=TARGET, daily_entry_limit_enabled=config.strategy_params.get("daily_entry_limit_enabled", True))
    parameters = {"rules": rules, "limits": limits, "quantity": float(quantity), "contract": contract}
    params_hash = sha256(canonical(parameters)).hexdigest()
    return {"schema": "strategy-entry-v1", "experiment_id": EXPERIMENT_ID,
        "strategy_version_id": "dom-"+sha256(canonical({"parameters": parameters, "code": implementation})).hexdigest(),
        "parameter_hash": params_hash, "implementation": implementation, "rules": rules,
        "recorded_at": now.isoformat(), "account_id": account, "environment": "practice_api",
        "execution_mode": config.execution_mode, "contract_id": contract, "side": side, "quantity": float(quantity),
        "initial_risk_points": 25, "initial_risk_usd": 25*2*float(quantity), "risk_limits": limits,
        "session": trading_day_key(now), "session_cutoff": plan["deadline"],
        "research_cutoff": min(utc(plan["deadline"]), now+timedelta(minutes=120)).isoformat(),
        "study_protocol": deepcopy(PROTOCOL), "study_model_code": MODEL_CODE}


def classify(request):
    snapshot = request.get("strategySnapshot") or {}
    runner = (request.get("timeExit") or {}).get("runner") or {}
    rules = snapshot.get("rules", {}).get("exit", {})
    mode = rules.get("exit_mode") or runner.get("exit_mode")
    if mode in {"fixed", "trailing"}:
        basis = "entry_snapshot" if snapshot else "saved_plan"
    elif (runner.get("initial_stop_points") == 25 and runner.get("activation_points") == 25
          and runner.get("trail_points") == 25 and "take_profit_points" not in runner):
        mode, basis = "trailing", "inferred_from_saved_trailing_plan"
    else:
        mode, basis = "unknown", "unsupported"
    version = snapshot.get("strategy_version_id")
    if not version:
        saved = {k: runner.get(k) for k in ("version", "exit_mode", "initial_stop_points", "take_profit_points", "activation_points", "trail_points")}
        code = (request.get("entryEvidence") or {}).get("implementation")
        version = "historical-"+sha256(canonical({"plan": saved, "code": code})).hexdigest()[:24] if mode != "unknown" else "unknown"
    return {"strategy_class": mode, "classification_basis": basis, "strategy_version_id": version,
            "experiment_id": snapshot.get("experiment_id"), "strategy_snapshot": deepcopy(snapshot) or None}


def exit_classification(runner, plan, order_id):
    order_id = str(order_id)
    if runner.get("target_order_id") is not None and order_id == str(runner["target_order_id"]):
        return "take_profit", "verified_target_order_id"
    if runner.get("stop_order_id") is not None and order_id == str(runner["stop_order_id"]):
        stop, entry = runner.get("confirmed_stop"), runner.get("entry_price")
        initial = entry + (-1 if runner.get("side") == "BUY" else 1)*runner.get("initial_stop_points", 25) if entry is not None else None
        if stop is not None and initial is not None and abs(stop-initial) < .001:
            return "initial_stop", "verified_stop_order_and_price"
        if stop is not None and stop == runner.get("last_verified_trail_stop"):
            return "trailing_stop", "verified_trailing_stop_order_and_price"
        return "unknown", "verified_stop_order_but_stop_origin_unknown"
    # A saved intention alone is not proof of which order filled.
    audit = plan.get("audit") or {}
    closing = audit.get("close_response") or {}
    close_id = closing.get("orderId", closing.get("order_id")) if isinstance(closing, dict) else None
    if close_id is not None and order_id == str(close_id):
        reason = plan.get("runner_exit_reason", "")
        if reason == "session_cutoff": return "session_close", "verified_close_order_id"
        if reason.startswith("daily_"): return "daily_risk_profit_rule", "verified_close_order_id"
        if reason == "trailing_level_crossed": return "trailing_stop", "verified_trailing_close_order_id"
        if reason in {"manual", "manual_external", "external"}: return "manual_external", "verified_manual_external_close_order_id"
        return "unknown", "verified_close_order_reason_unclassified"
    return "unknown", "exit_order_not_linked_to_verified_reason"


def simulate(entry, quotes, *, gap_reason=None):
    """quotes are chronological, verified capture observations, never candle extrema.

    Every event carries receipt time, executable BBO, sizes and optional gap flag.
    A gap while any alternative is pending invalidates those alternatives only.
    All variants must resolve to enter the paired cohort.
    """
    spec = entry["strategy_snapshot"]
    start, cutoff = utc(entry["entry_at"]), utc(spec["research_cutoff"])
    sign = 1 if entry["side"] == "BUY" else -1
    price, tick = entry["entry_price"], PROTOCOL["tick_size"]
    latency = timedelta(milliseconds=PROTOCOL["latency_ms"])
    risk = spec["initial_risk_usd"]
    variants = [{"key": str(r), "ratio": r, "target_points": 25*r} for r in PROTOCOL["ratios"]]
    variants.append({"key": "trailing", "ratio": None, "target_points": None})
    for v in variants:
        v.update(status="pending", stop=price-sign*25, active_at=start+latency, mfe=0.,
                 next_management_at=start+timedelta(seconds=PROTOCOL["trailing_management_seconds"]))
    reason = gap_reason
    if spec.get("quantity") != 1 or spec.get("initial_risk_points") != 25 or not str(spec.get("contract_id", "")).startswith("CON.F.US.MNQ."):
        reason = "unsupported_contract_quantity_or_risk"
    previous = start
    observed = 0
    verified_index = None
    for q in quotes:
        at = utc(q["at"])
        if at < start: continue
        if at > cutoff: break
        if at < previous: reason = "nonchronological_evidence"
        if (at-previous).total_seconds() > PROTOCOL["max_quote_gap_seconds"]: reason = "quote_gap"
        if q.get("gap"): reason = str(q["gap"])
        if reason: break
        values = [q.get(k) for k in ("bid", "ask", "bid_size", "ask_size")]
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values): reason = "invalid_quote"
        elif q["bid"] <= 0 or q["ask"] < q["bid"]: reason = "crossed_or_invalid_quote"
        if reason: break
        observed += 1
        verified_index = q.get("capture_event_index")
        previous = at
        mark = q["bid"] if sign == 1 else q["ask"]
        size = q["bid_size"] if sign == 1 else q["ask_size"]
        points = sign*(mark-price)
        for v in variants:
            if v["status"] != "pending": continue
            if v.get("pending_stop") and at >= utc(v["pending_stop"]["at"]):
                v["stop"] = v.pop("pending_stop")["price"]
            # Stop trigger is latched; subsequent recovery does not cancel it.
            if sign*(mark-v["stop"]) <= 0 and "trigger_at" not in v:
                v["trigger_at"] = max(start, at)+latency
            fill, why = None, None
            if "trigger_at" in v:
                if at >= v["trigger_at"] and size >= 1:
                    fill = mark-sign*tick*PROTOCOL["stop_slippage_ticks"]
                    fill = (math.floor(fill/tick) if sign == 1 else math.ceil(fill/tick))*tick
                    why = "trailing_stop" if v["key"] == "trailing" and sign*(v["stop"]-price) >= 0 else "initial_stop"
            elif at >= v["active_at"] and v["ratio"] is not None:
                target = price+sign*v["target_points"]
                target = (math.ceil(target/tick) if sign == 1 else math.floor(target/tick))*tick
                if sign*(mark-target) >= tick*PROTOCOL["limit_trade_through_ticks"] and size >= 1:
                    fill, why = target, "take_profit"
            if fill is not None:
                gross = sign*(fill-price)*2
                net = gross-PROTOCOL["round_turn_cost_usd"]
                v.update(status="resolved", exit_at=at.isoformat(), exit_price=fill, exit_reason=why,
                    gross_pnl_usd=gross, costs_usd=PROTOCOL["round_turn_cost_usd"], net_pnl_usd=net, realized_r=net/risk)
            elif v["key"] == "trailing" and "trigger_at" not in v:
                v["mfe"] = max(v["mfe"], points)
                if at >= v["next_management_at"] and "pending_stop" not in v:
                    v["next_management_at"] = at+timedelta(seconds=PROTOCOL["trailing_management_seconds"])
                    if v["mfe"] < 25: continue
                    wanted = price+sign*(v["mfe"]-25)
                    wanted = (math.floor(wanted/tick) if sign == 1 else math.ceil(wanted/tick))*tick
                    if sign*(wanted-v["stop"]) > 0:
                        v["pending_stop"] = {"price": wanted, "at": (at+latency).isoformat()}
        if all(v["status"] == "resolved" for v in variants): break
    for v in variants:
        if not reason and (cutoff-previous).total_seconds() > PROTOCOL["max_quote_gap_seconds"]:
            reason = "missing_evidence_tail"
        if v["status"] == "pending": v.update(status="missing" if reason else "censored", reason=reason or "horizon_or_session_cutoff")
        for k in ("stop", "active_at", "mfe", "pending_stop", "trigger_at", "next_management_at"): v.pop(k, None)
    return {"experiment_id": EXPERIMENT_ID, "entry_id": entry["entry_id"], "session": spec["session"],
        "model_code": MODEL_CODE,
        "entry_at": entry["entry_at"], "strategy_version_id": entry["strategy_version_id"],
        "strategy_class": entry.get("strategy_class", "unknown"),
        "variants": variants, "paired_valid": all(v["status"] == "resolved" for v in variants),
        "observed_quotes": observed, "coverage_complete": False,
        "verified_through_event_index": verified_index,
        "coverage_basis": "Verified recorder continuity and observed quotes only; exchange completeness unverified"}


def metrics(rows, field="net_pnl_usd"):
    values = [r[field] for r in rows if isinstance(r.get(field), (int, float)) and math.isfinite(r[field])]
    wins, losses = [v for v in values if v>0], [v for v in values if v<0]
    sessions = {}
    for row in rows:
        if row.get(field) is not None and row.get("session"):
            sessions.setdefault(row["session"], []).append(row[field])
    interval = None
    if len(sessions) >= 5:
        rng = random.Random(2401104)
        groups = list(sessions.values())
        estimates = sorted(mean([v for group in rng.choices(groups, k=len(groups)) for v in group]) for _ in range(1000))
        interval = [estimates[24], estimates[974]]
    return {"count": len(values), "sessions": len(sessions), "wins": len(wins), "losses": len(losses),
        "breakevens": len(values)-len(wins)-len(losses), "average_win": mean(wins) if wins else None,
        "average_loss": mean(losses) if losses else None, "expectancy": mean(values) if values else None,
        "profit_factor": sum(wins)/-sum(losses) if losses else None, "net": sum(values),
        "interval_95": interval, "interval_method": "Session-cluster bootstrap; unavailable below 5 sessions"}
