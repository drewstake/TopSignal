"""Phase 2: four predeclared overnight-drift hypotheses, not live settings.

Rules are fixed before any phase-2 replay; no reserved data was used. An overnight
long premium is a hypothesis to test after costs, not established profitability.
Holding through maintenance and overnight gaps can exceed the nominal stop.
The existing runner supplies the daily $250 proposed-stop gate and actual
observed-minute fills; this module neither routes orders nor manufactures bars.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math

from app.services import bot_service as indicators
from app.services.instruments import normalize_symbol_key
from app.services.trading_day import (
    TRADING_TZ, futures_holiday_schedule, futures_session_is_open,
)


REVISION = "mnq_overnight_drift_fixed_phase2_20260904_v1"
HISTORY_BARS = 200
COMMON = {
    "position_size": 1,
    "entry_time_et": "16:00",
    "entry_weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday"],
    "exit_time_et_next_local_date": "09:25",
    "entry_window_start": "15:30",
    "entry_window_end": "16:30",
    "tick_size": 0.25,
    "reward_multiple": 2.0,
    "stop_cap_points": 100.0,
    "max_daily_loss": 250.0,
    "max_trades_per_day": 1,
    "calendar_rule": "skip current early/full closure and next full closure or next early close before 09:30 ET",
    "exit_clock": "first observed minute at or after original entry's next-local-date deadline; never reset after midnight",
}
CANDIDATES = {
    "overnight_long_75": {
        "description": "One Monday-through-Thursday 16:00 ET MNQ long; 75-point stop, 150-point target, next-date 09:25 clock exit.",
        "hypothesis": "A positive overnight long return may cover costs with fewer entries than intraday momentum; the fixed bracket limits ordinary price risk but cannot prevent gap losses.",
        "parameters": {**COMMON, "direction": "long", "stop_points": 75.0, "target_points": 150.0},
    },
    "overnight_long_50": {
        "description": "Predeclared lower-risk neighbor: the same overnight long with a 50-point stop and 100-point target.",
        "hypothesis": "If overnight long drift is robust, nearby tighter risk should preserve useful expectancy rather than profitability depending on the 75-point bracket alone.",
        "parameters": {**COMMON, "direction": "long", "stop_points": 50.0, "target_points": 100.0},
    },
    "overnight_long_100": {
        "description": "Predeclared higher-risk neighbor: the same overnight long with a 100-point stop and 200-point target.",
        "hypothesis": "A wider nearby bracket may tolerate overnight noise; it must still fit the fixed daily loss budget and support the same economic hypothesis after costs.",
        "parameters": {**COMMON, "direction": "long", "stop_points": 100.0, "target_points": 200.0},
    },
    "overnight_short_control_75": {
        "description": "Directional control: the identical timing and 75/150-point bracket applied to one MNQ short.",
        "hypothesis": "The short control tests whether any apparent advantage is specific to the proposed overnight long direction rather than symmetric timing or fill-model effects.",
        "parameters": {**COMMON, "direction": "short", "stop_points": 75.0, "target_points": 150.0},
    },
}


def required_warmup_bars(variant):
    CANDIDATES[variant]
    return HISTORY_BARS


def get_settings(variant):
    params = CANDIDATES[variant]["parameters"]
    return {
        "trading_start_time": params["entry_window_start"],
        "trading_end_time": params["entry_window_end"],
        "max_daily_loss": params["max_daily_loss"],
        "max_trades_per_day": params["max_trades_per_day"],
        "order_size": 1, "max_contracts": 1, "max_open_position": 1,
        "lookback_bars": HISTORY_BARS,
        "strategy_params": {"research_revision": REVISION, **params},
    }


def _exit_deadline(entry_timestamp):
    local_entry = indicators._as_utc(entry_timestamp).astimezone(TRADING_TZ)
    next_date = local_entry.date() + timedelta(days=1)
    return datetime.combine(next_date, time(9, 25), tzinfo=TRADING_TZ).astimezone(timezone.utc)


def should_flatten(entry_timestamp, event_time, variant):
    CANDIDATES[variant]
    return indicators._as_utc(event_time) >= _exit_deadline(entry_timestamp)


def _calendar_rejection(decision):
    local = decision.astimezone(TRADING_TZ)
    day = local.date()
    current = futures_holiday_schedule(day, symbol="MNQ")
    if current is not None and (
        current.full_close or current.early_close is not None and current.early_close < time(16)
    ):
        return "Current known calendar does not permit a 16:00 entry"
    if not futures_session_is_open(decision, symbol="MNQ"):
        return "Exchange is closed at the intended entry"
    next_day = day + timedelta(days=1)
    following = futures_holiday_schedule(next_day, symbol="MNQ")
    if following is not None and (
        following.full_close or following.early_close is not None and following.early_close < time(9, 30)
    ):
        return "Next known session is fully closed or closes before 09:30"
    return None


def evaluate(candles, variant, position_qty=0.0):
    params = CANDIDATES[variant]["parameters"]
    rows = indicators._closed_candles(candles)[-HISTORY_BARS:]
    latest = rows[-1] if rows else None
    timestamp = indicators._as_utc(latest.candle_timestamp) if latest is not None else None
    price = float(latest.close_price) if latest is not None else None
    payload = {
        "strategy_type": "topbot_adaptive", "strategy_revision": REVISION,
        "research_variant": variant, "settings": dict(params),
    }

    def result(action="HOLD", reason="Research: waiting for exact overnight entry time"):
        return indicators.SignalResult(action=action, reason=reason,
                                       candle_timestamp=timestamp, price=price, raw_payload=payload)

    if latest is None or len(rows) < HISTORY_BARS:
        return result(reason="Research: requires 200 closed signal bars")
    if (str(latest.unit) != "minute" or int(latest.unit_number) != 5
            or (normalize_symbol_key(latest.symbol) or normalize_symbol_key(latest.contract_id)) != "MNQ"):
        return result(reason="Research: requires MNQ five-minute bars")
    if price is None or not math.isfinite(price) or price <= 0:
        return result(reason="Research: invalid closed price")
    if position_qty:
        return result(reason="Research: an existing position retains brackets and its original clock deadline")
    decision = timestamp + timedelta(minutes=5)
    local = decision.astimezone(TRADING_TZ)
    if local.weekday() not in (0, 1, 2, 3) or local.time() != time(16):
        return result()
    rejection = _calendar_rejection(decision)
    if rejection is not None:
        return result(reason=f"Research: {rejection}")
    direction = 1 if params["direction"] == "long" else -1
    risk, reward = params["stop_points"], params["target_points"]
    payload.update(
        signal_category="entry", target_position_qty=float(direction),
        entry_price=price, stop_loss=price - direction * risk,
        take_profit=price + direction * reward, planned_risk_points=risk,
        planned_reward_points=reward, risk=risk, reward_r_multiple=2.0,
        exit_deadline=_exit_deadline(decision).isoformat(),
        exit_policy="bracket_or_original_next_local_date_0925_clock",
    )
    return result("BUY" if direction > 0 else "SELL", f"Research: {variant} fixed overnight hypothesis")
