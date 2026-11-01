"""Contrarian observed-depth entries; position management is separate."""
from datetime import datetime, timezone
import math

REVISION = "dom_contrarian_v1"
CONFIGURED = True
MESSAGE = "TopBot has no strategy configured. Define your strategy before starting a run."
DEFAULTS = {"revision": REVISION, "imbalance_threshold": 0.10, "persistence_seconds": 30,
            "depth_levels": 100, "initial_stop_points": 25, "trail_activation_points": 25,
            "trail_distance_points": 25, "practice_auto_resume": False}


def selected(params):
    return isinstance(params, dict) and params.get("revision") == REVISION


def normalize_params(params=None):
    if params and not selected(params):
        return {"revision": "custom_unconfigured"}
    result = dict(DEFAULTS)
    auto_resume = (params or {}).get("practice_auto_resume", False)
    if not isinstance(auto_resume, bool):
        raise ValueError("Invalid practice_auto_resume")
    result["practice_auto_resume"] = auto_resume
    for name, low, high in (("imbalance_threshold", 0, .9), ("persistence_seconds", 0, 300)):
        value = (params or {}).get(name, result[name])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid {name}")
        result[name] = value
    return result


def require_configured():
    if not CONFIGURED:
        raise ValueError(MESSAGE)


def evaluate(candles, *, strategy_params=None, owner=None, contract_id=None, observation=None, now=None):
    from .bot_service import SignalResult
    from .topbot_depth import feed
    now = now or datetime.now(timezone.utc)
    params = normalize_params(strategy_params)
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": params["revision"],
               "strategy_configured": selected(params), "live_routing_allowed": False}
    if not CONFIGURED or not selected(params):
        return SignalResult("HOLD", MESSAGE, None, None, payload)
    observation = observation if observation is not None else feed.snapshot(owner, contract_id, now=now)
    payload["depth_entry"] = observation
    if not observation.get("ready"):
        return SignalResult("HOLD", observation.get("reason", "Waiting for depth"), None, None, payload)
    imbalance = observation["imbalance"]
    if not (abs(imbalance) > 0 and abs(imbalance) >= params["imbalance_threshold"] and observation.get("episode_at")
            and observation.get("persistence_seconds", 0) >= params["persistence_seconds"]):
        return SignalResult("HOLD", "Waiting for persistent contrarian imbalance", None, None, payload)
    action = "SELL" if imbalance > 0 else "BUY"
    price = observation["bid"] if action == "SELL" else observation["ask"]
    at = datetime.fromisoformat(observation["episode_at"])
    payload.update(live_routing_allowed=True, signal_category="entry", protective_stop_ticks=100,
                   stop_loss=price + (25 if action == "SELL" else -25),
                   current_position_qty=0, target_position_qty=-1 if action == "SELL" else 1, effective_order_size=1)
    return SignalResult(action, f"Contrarian {action}: {observation['bid_total']:g} bid / {observation['ask_total']:g} ask contracts",
                        at, price, payload)
