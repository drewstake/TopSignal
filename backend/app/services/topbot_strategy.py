"""Contrarian observed-depth entries; position management is separate."""
from datetime import date, datetime, timezone
import math

REVISION = "dom_contrarian_v1"
CONFIGURED = True
MESSAGE = "TopBot has no strategy configured. Define your strategy before starting a run."
DEFAULTS = {"revision": REVISION, "imbalance_threshold": 0.10, "persistence_seconds": 30,
            "depth_levels": 100, "initial_stop_points": 25, "trail_activation_points": 25,
            "trail_distance_points": 25, "practice_auto_resume": False,
            "daily_entry_limit_enabled": True, "fixed_bracket_session": None}


def selected(params):
    return isinstance(params, dict) and params.get("revision") == REVISION


def daily_entry_limit_enabled(config):
    """Only an explicit DOM account preference disables the entry-count cap."""
    params = config.strategy_params
    return not (config.strategy_type == "topbot_adaptive" and selected(params)
                and params.get("daily_entry_limit_enabled") is False)


def normalize_params(params=None):
    if params and not selected(params):
        return {"revision": "custom_unconfigured"}
    result = dict(DEFAULTS)
    session = (params or {}).get("fixed_bracket_session")
    if session is not None:
        if not isinstance(session, str) or date.fromisoformat(session).isoformat() != session:
            raise ValueError("Invalid fixed_bracket_session; expected YYYY-MM-DD")
    result["fixed_bracket_session"] = session
    auto_resume = (params or {}).get("practice_auto_resume", False)
    if not isinstance(auto_resume, bool):
        raise ValueError("Invalid practice_auto_resume")
    result["practice_auto_resume"] = auto_resume
    entry_limit = (params or {}).get("daily_entry_limit_enabled", True)
    if not isinstance(entry_limit, bool):
        raise ValueError("Invalid daily_entry_limit_enabled")
    result["daily_entry_limit_enabled"] = entry_limit
    for name, low, high in (("imbalance_threshold", 0, .9), ("persistence_seconds", 0, 300)):
        value = (params or {}).get(name, result[name])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid {name}")
        result[name] = value
    return result


def exit_mode(params, now=None):
    """A session override affects new entries only; saved trade plans never expire."""
    from .trading_day import trading_day_key
    session = (params or {}).get("fixed_bracket_session")
    return "fixed" if session and session == trading_day_key(now or datetime.now(timezone.utc)) else "trailing"


def require_configured():
    if not CONFIGURED:
        raise ValueError(MESSAGE)


def qualifying_action(observation, params):
    """Pure signal rule shared by runtime and offline worker-evidence replay."""
    imbalance = observation.get("imbalance", 0)
    if not (observation.get("ready") and abs(imbalance) > 0
            and abs(imbalance) >= params["imbalance_threshold"] and observation.get("episode_at")
            and observation.get("persistence_seconds", 0) >= params["persistence_seconds"]):
        return "HOLD"
    return "SELL" if imbalance > 0 else "BUY"


def evaluate(candles, *, strategy_params=None, owner=None, contract_id=None, observation=None, now=None):
    from .bot_service import SignalResult
    from .topbot_depth import feed
    now = now or datetime.now(timezone.utc)
    params = normalize_params(strategy_params)
    from .topbot_evidence import binding
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": params["revision"],
               "strategy_configured": selected(params), "live_routing_allowed": False,
               "implementation": binding()}
    payload["daily_entry_limit_enabled"] = params.get("daily_entry_limit_enabled", True)
    payload["exit_mode"] = exit_mode(params, now)
    if not CONFIGURED or not selected(params):
        return SignalResult("HOLD", MESSAGE, None, None, payload)
    from .topbot_session import runner_entry_pause_reason
    pause_reason = runner_entry_pause_reason(now, symbol=contract_id or "MNQ")
    if pause_reason:
        payload["hold_reason"] = pause_reason
        return SignalResult("HOLD", "Trading session paused; waiting for the next session to open.",
                            None, None, payload)
    observation = observation if observation is not None else feed.snapshot(owner, contract_id, now=now)
    payload["depth_entry"] = observation
    if not observation.get("ready"):
        return SignalResult("HOLD", observation.get("reason", "Waiting for depth"), None, None, payload)
    action = qualifying_action(observation, params)
    if action == "HOLD":
        return SignalResult("HOLD", "Waiting for persistent contrarian imbalance", None, None, payload)
    price = observation["bid"] if action == "SELL" else observation["ask"]
    at = datetime.fromisoformat(observation["episode_at"])
    payload.update(live_routing_allowed=True, signal_category="entry", protective_stop_ticks=100,
                   stop_loss=price + (25 if action == "SELL" else -25),
                   current_position_qty=0, target_position_qty=-1 if action == "SELL" else 1, effective_order_size=1)
    if payload["exit_mode"] == "fixed":
        payload.update(take_profit_ticks=100, take_profit=price + (-25 if action == "SELL" else 25),
                       fixed_bracket_session=params["fixed_bracket_session"])
    return SignalResult(action, f"Contrarian {action}: {observation['bid_total']:g} bid / {observation['ask_total']:g} ask contracts",
                        at, price, payload)
