"""Selected candle-probability strategy for forward Dry Run decisions.

The research model remains pure. This adapter turns supported proposals into
ordinary signals, so the existing account, risk and idempotency gates apply.
Experimental Practice routing uses protective brackets and a durable timed exit.
The probabilities remain unvalidated; this is not a validated trading model.
"""
from datetime import datetime, timezone
import math

from .probabilistic_shadow import explain_shadow, unavailable
from .probabilistic_strategy import BAR, SPEC, utc

REVISION = "mnq_bayesian_payoff_v1"
HISTORY_BARS = 25
RULES = {
    "revision": REVISION, "model_version": "bayesian_cells_v1",
    "position_size": 1, "horizon_minutes": 15,
    "exit_policy": "bracket_or_15_minute_horizon", "minimum_net_edge_usd": 1.,
    "minimum_training_paths": SPEC["minimum_training_examples"], "minimum_effective_days": SPEC["minimum_effective_days_per_forecast"],
    "session_start": SPEC["entry_window_et"][0], "session_end": SPEC["entry_window_et"][1],
    "routing_policy": "experimental_practice", "level2_enabled": False,
}


def selected(params):
    return isinstance(params, dict) and params.get("revision") == REVISION


def normalize_params(_params=None):
    return dict(RULES)


def require_live_worker():
    import os
    from .bot_execution_safety import live_execution_environment_enabled
    enabled = {"1", "true", "yes", "y", "on"}
    if (not live_execution_environment_enabled()
            or os.getenv("TOPSIGNAL_BOT_WORKER_ENABLED", "").lower() not in enabled
            or os.getenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "").lower() not in enabled):
        raise ValueError("Experimental Practice routing requires the live worker for timed exits.")


def evaluate(candles, *, as_of=None, root=None, owner=None, contract_id=None):
    from .bot_service import SignalResult

    now = as_of or datetime.now(timezone.utc)
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": REVISION,
               "settings": dict(RULES), "live_routing_allowed": False, "validation_status": "unvalidated",
               "probabilistic_research": unavailable(as_of=now, reason="Model inputs have not passed validation.")}
    stamp = price = None

    def hold(reason):
        forecast = payload["probabilistic_research"]
        if forecast["forecasts"] is None:
            forecast["reasons"] = [reason, "Experimental mathematical strategy; unvalidated probabilities."]
        return SignalResult("HOLD", reason, stamp, price, payload)

    if not candles:
        return hold("NO TRADE: no candle observations for the mathematical model.")
    from .topbot_session import entry_boundary_reason
    boundary = entry_boundary_reason(now)
    if boundary:
        payload["hold_reason"] = boundary
        return hold(f"NO TRADE: {boundary}.")
    try:
        latest = candles[-1]
        payload["input_revisions"] = [{"timestamp": utc(row.candle_timestamp).isoformat(),
                                       "sha256": getattr(row, "revision_hash", None)} for row in candles[-21:]]
        expected_owner = str(owner if owner is not None else latest.user_id)
        expected_contract = str(contract_id if contract_id is not None else latest.contract_id)
        if any(str(row.user_id) != expected_owner or str(row.contract_id) != expected_contract for row in candles):
            return hold("NO TRADE: candle owner or contract does not match this strategy.")
        for row in candles[-22:]:
            received = getattr(row, "fetched_at", None)
            if not row.is_partial and received is not None and not (
                utc(row.candle_timestamp) + BAR <= utc(received) <= utc(now)
            ):
                return hold("NO TRADE: a candle was received before its close or after the decision time.")
        forecast = explain_shadow(candles=candles, owner=expected_owner,
                                  contract_id=expected_contract, as_of=now, root=root, all_sessions=False)
        # This is the exact forecast used for the decision, not a second read of
        # an artifact which could change between evaluation and serialization.
        forecast = dict(forecast)
        forecast["reasons"] = [r for r in forecast["reasons"] if not r.startswith((
            "Research only.", "Unvalidated research model:"))]
        forecast["reasons"].append("Live routing requires reviewed offline, untouched and forward evidence.")
        payload["probabilistic_research"] = forecast
        payload["validation_status"] = forecast["validation_status"]
        closed = [row for row in candles if not row.is_partial]
        if closed:
            stamp, price = closed[-1].candle_timestamp, float(closed[-1].close_price)
        if forecast["model_version"] != RULES["model_version"]:
            return hold("NO TRADE: the selected strategy requires the Bayesian cells model artifact.")
        if (forecast["data_status"] != "fresh"
                or forecast["probability_basis"] != "uncalibrated_model_estimate" or forecast["forecasts"] is None):
            return hold("NO TRADE: " + " ".join(forecast["reasons"]))
        action = forecast["research_action"]
        if forecast.get("validation_status") not in {"offline_passed", "passed"}:
            return hold("NO TRADE: model has no passed experiment and reviewed evidence binding.")
        if action not in {"BUY", "SELL"}:
            return hold("NO TRADE: " + " ".join(forecast["reasons"]))
        choice = forecast["forecasts"][action]
        if (forecast["training_paths"] < RULES["minimum_training_paths"] or choice["effective_days"] < RULES["minimum_effective_days"]
                or not math.isfinite(choice["lower_utility_usd"]) or choice["lower_utility_usd"] <= SPEC["minimum_net_edge_usd"]):
            return hold("NO TRADE: insufficient independent evidence or net utility after costs and uncertainty.")
        stop, target = float(forecast["stop_points"]), float(forecast["target_points"])
        if (price is None or not math.isfinite(price) or price <= 0 or not 4 <= stop <= 25
                or not math.isfinite(target) or target != math.ceil(stop * 1.5 / .25) * .25):
            return hold("NO TRADE: invalid mathematical-model price or risk bracket.")
        side = 1 if action == "BUY" else -1
        payload.update(live_routing_allowed=forecast["routing_allowed"], signal_category="entry", target_position_qty=float(side), order_size=1.,
                       entry_price=price, stop_loss=price-side*stop, take_profit=price+side*target,
                       planned_risk_points=stop, planned_reward_points=target, risk=stop,
                       reward_r_multiple=target/stop, exit_policy=RULES["exit_policy"])
        reason = (f"{action}: Bayesian expected net ${choice['expected_net_usd']:.2f}; "
                  f"lower utility ${choice['lower_utility_usd']:.2f} exceeds $1 after costs and uncertainty. "
                  "15-minute horizon; experimental decision, unvalidated probabilities.")
        return SignalResult(action, reason, stamp, price, payload)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return hold("NO TRADE: mathematical-model inputs failed integrity checks.")
