"""Fixed, causal research hypotheses. These are not operator strategy choices.

All observations end at the latest closed five-minute candle. Portfolio state is
only used to flatten an existing position at a predetermined time. The runner
snapshots this file and these hypotheses before looking at results.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
import math

from app.services import bot_service as indicators
from app.services import topbot_strategy as baseline
from app.services.trading_day import TRADING_TZ, futures_holiday_schedule, trading_day_date


REVISION = "mnq_research_fixed_hypotheses_20260904_v1"
COMMON = {"stop_cap_points": 100.0, "stop_floor_points": 10.0,
          "flatten_decision": "earlier of 15:55 ET or known holiday close minus 5 minutes",
          "flatten_clock": "first observed minute at or after deadline, independent of signal bars",
          "position_size": 1}
CANDIDATES = {
    "baseline_v5": {
        "description": "Original v5 control, including overnight bracket holds.",
        "hypothesis": "Reproduce the losing baseline under corrected execution assumptions.",
        "parameters": dict(baseline.RULES),
    },
    "v5_long": {
        "description": "Original v5 entries and brackets, long entries only.",
        "hypothesis": "Removing shorts isolates whether the original long signal covers costs.",
        "parameters": {**baseline.RULES, "direction": "long_only"},
    },
    "v5_long_atr": {
        "description": "V5 long entry, volatility-scaled stop, 2R target, daily flatten.",
        "hypothesis": "A volatility-scaled bracket and larger reward capture trend extensions better than fixed 50/50 points.",
        "parameters": {**COMMON, "atr_period": 20, "atr_stop_multiple": 2.0,
                       "reward_multiple": 2.0, "entry_cutoff": "14:30"},
    },
    "orb30_both": {
        "description": "First closed breakout of the 30-minute opening range, either direction.",
        "hypothesis": "The first opening-range escape captures intraday continuation with fewer transactions than repeated EMA pullbacks.",
        "parameters": {**COMMON, "opening_minutes": 30, "range_stop_multiple": 0.5,
                       "reward_multiple": 2.0, "entry_cutoff": "12:00", "direction": "both"},
    },
    "orb30_long": {
        "description": "Same first opening-range breakout, long only.",
        "hypothesis": "Restricting opening-range continuation to upside breaks avoids structurally weak short trades.",
        "parameters": {**COMMON, "opening_minutes": 30, "range_stop_multiple": 0.5,
                       "reward_multiple": 2.0, "entry_cutoff": "12:00", "direction": "long"},
    },
    "vwap_reversion": {
        "description": "Confirmed reversion from a 2-ATR VWAP deviation in a locally flat trend.",
        "hypothesis": "After an extended move stalls, session-value reversion offers an alternative to the losing momentum entry.",
        "parameters": {**COMMON, "atr_period": 20, "deviation_atr": 2.0,
                       "ema_period": 50, "ema_slope_bars": 6, "slope_cap_atr": 1.0,
                       "atr_stop_multiple": 2.0, "reward_multiple": 1.0,
                       "entry_start": "10:30", "entry_cutoff": "14:30"},
    },
    "opening_drive": {
        "description": "One 10:00 entry following a directional opening half hour.",
        "hypothesis": "A strong opening displacement relative to its range predicts continuation without waiting for a pullback.",
        "parameters": {**COMMON, "opening_minutes": 30, "displacement_fraction": 0.65,
                       "range_stop_multiple": 0.5, "reward_multiple": 2.0,
                       "entry_cutoff": "10:00"},
    },
    "afternoon_momentum": {
        "description": "One 15:00 entry aligned with the session return and EMA trend.",
        "hypothesis": "Late-session continuation may concentrate directional opportunity while reducing trading frequency and exposure.",
        "parameters": {**COMMON, "atr_period": 20, "atr_stop_multiple": 2.0,
                       "reward_multiple": 2.0, "entry_cutoff": "15:00"},
    },
}


def required_warmup_bars(variant):
    return 200


def get_settings(variant):
    if variant in {"baseline_v5", "v5_long"}:
        return {}
    return {"trading_end_time": "16:00", "max_trades_per_day": 3,
            "strategy_params": {"research_revision": REVISION,
                                **CANDIDATES[variant]["parameters"]}}


def _atr(rows, period=20):
    return sum(max(float(row.high_price) - float(row.low_price),
                   abs(float(row.high_price) - float(previous.close_price)),
                   abs(float(row.low_price) - float(previous.close_price)))
               for previous, row in zip(rows[-period - 1:-1], rows[-period:])) / period


def _flatten_deadline(timestamp):
    day = trading_day_date(timestamp)
    holiday = futures_holiday_schedule(day, symbol="MNQ")
    close = min(time(16), holiday.early_close) if holiday and holiday.early_close else time(16)
    return (datetime.combine(day, close, tzinfo=TRADING_TZ) - timedelta(minutes=5)).astimezone(timezone.utc)


def should_flatten(entry_timestamp, event_time, variant):
    """Known clock/risk rule independent of future prices or complete signal bars.

    An outage delays the fill until an observed open, but never resets an old
    position's deadline at midnight. Live integration needs this same clock.
    """
    return variant not in {"baseline_v5", "v5_long"} and event_time >= _flatten_deadline(entry_timestamp)


def _vwap(rows):
    volume = sum(float(row.volume or 0) for row in rows)
    if volume <= 0:
        return None
    return sum((float(row.high_price) + float(row.low_price) + float(row.close_price))
               / 3 * float(row.volume or 0) for row in rows) / volume


def evaluate(candles, variant, position_qty=0.0):
    definition = CANDIDATES[variant]
    params = definition["parameters"]
    if variant in {"baseline_v5", "v5_long"}:
        signal = baseline.evaluate(candles)
        if variant == "v5_long" and signal.action == "SELL":
            return replace(signal, action="HOLD", reason="Research: long-only entry control")
        return signal

    rows = indicators._closed_candles(candles)[-200:]
    latest = rows[-1] if rows else None
    timestamp = indicators._as_utc(latest.candle_timestamp) if latest else None
    price = float(latest.close_price) if latest else None
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": REVISION,
               "research_variant": variant, "settings": dict(params)}

    def result(action="HOLD", reason="Research: no setup"):
        return indicators.SignalResult(action=action, reason=reason,
                                       candle_timestamp=timestamp, price=price,
                                       raw_payload=payload)

    if latest is None or len(rows) < 200:
        return result(reason="Research: requires 200 closed signal bars")
    from app.services.instruments import normalize_symbol_key
    if (str(latest.unit) != "minute" or int(latest.unit_number) != 5
            or (normalize_symbol_key(latest.symbol) or normalize_symbol_key(latest.contract_id)) != "MNQ"):
        return result(reason="Research: requires MNQ five-minute bars")
    decision = (timestamp + timedelta(minutes=5)).astimezone(TRADING_TZ)
    if decision.weekday() >= 5:
        return result()
    past_flatten = decision.astimezone(timezone.utc) >= _flatten_deadline(timestamp)
    if past_flatten and position_qty:
        payload.update(signal_category="exit", target_position_qty=0.0,
                       exit_reason="scheduled_session_flatten")
        return result("SELL" if position_qty > 0 else "BUY", "Research: scheduled daily flatten")
    if past_flatten or position_qty or not time(9, 30) <= decision.time() <= time.fromisoformat(params["entry_cutoff"]):
        return result()
    session_start = datetime.combine(decision.date(), time(9, 30), tzinfo=TRADING_TZ).astimezone(timezone.utc)
    session = [row for row in rows if indicators._as_utc(row.candle_timestamp) >= session_start]
    if not session or any(indicators._as_utc(row.candle_timestamp) != session_start + timedelta(minutes=5 * i)
                          for i, row in enumerate(session)):
        return result(reason="Research: incomplete regular-session prefix")
    atr = _atr(rows)
    if not math.isfinite(atr) or atr <= 0:
        return result()
    direction, risk = 0, 0.0
    if variant == "v5_long_atr":
        # Preserve the engine's already-validated input wrapper; baseline itself
        # selects the same trailing 200 bars. Revalidating a plain slice here
        # would repeat every OHLC check at every signal event.
        signal = baseline.evaluate(candles)
        if signal.action == "BUY":
            direction, risk = 1, atr * params["atr_stop_multiple"]
    elif variant.startswith("orb"):
        count = params["opening_minutes"] // 5
        if len(session) <= count:
            return result()
        high = max(float(row.high_price) for row in session[:count])
        low = min(float(row.low_price) for row in session[:count])
        # Only the FIRST observed close outside either boundary may enter.
        if any(float(row.close_price) > high or float(row.close_price) < low for row in session[count:-1]):
            return result()
        direction = 1 if price > high else -1 if price < low else 0
        if params["direction"] == "long" and direction < 0:
            direction = 0
        risk = (high - low) * params["range_stop_multiple"]
    elif variant == "opening_drive":
        if decision.time() != time(10) or len(session) != 6:
            return result()
        width = max(float(row.high_price) for row in session) - min(float(row.low_price) for row in session)
        displacement = price - float(session[0].open_price)
        if width > 0 and abs(displacement) / width >= params["displacement_fraction"]:
            direction = 1 if displacement > 0 else -1
        risk = width * params["range_stop_multiple"]
    elif variant == "vwap_reversion":
        if decision.time() < time.fromisoformat(params["entry_start"]) or len(session) < 2:
            return result()
        current_vwap, previous_vwap = _vwap(session), _vwap(session[:-1])
        if current_vwap is None or previous_vwap is None:
            return result()
        ema = indicators._ema_series(indicators._candle_close_values(rows), period=params["ema_period"])
        if abs(ema[-1] - ema[-1 - params["ema_slope_bars"]]) > params["slope_cap_atr"] * atr:
            return result()
        prior = float(rows[-2].close_price)
        previous_atr = _atr(rows[:-1])
        if prior < previous_vwap - params["deviation_atr"] * previous_atr and prior < price < current_vwap and price > float(latest.open_price):
            direction = 1
        elif prior > previous_vwap + params["deviation_atr"] * previous_atr and prior > price > current_vwap and price < float(latest.open_price):
            direction = -1
        risk = atr * params["atr_stop_multiple"]
    elif variant == "afternoon_momentum":
        if decision.time() != time(15):
            return result()
        ema = indicators._ema_series(indicators._candle_close_values(rows), period=20)
        displacement = price - float(session[0].open_price)
        if displacement > atr and price > ema[-1] > ema[-4]:
            direction = 1
        elif displacement < -atr and price < ema[-1] < ema[-4]:
            direction = -1
        risk = atr * params["atr_stop_multiple"]
    if not direction:
        return result()
    # Quarter-point arithmetic and a finite per-contract risk bound.
    risk = math.ceil(max(params["stop_floor_points"], min(params["stop_cap_points"], risk)) * 4) / 4
    reward = math.ceil(risk * params["reward_multiple"] * 4) / 4
    payload.update(signal_category="entry", target_position_qty=float(direction),
                   entry_price=price, stop_loss=price - direction * risk,
                   take_profit=price + direction * reward, planned_risk_points=risk,
                   planned_reward_points=reward, risk=risk, reward_r_multiple=reward / risk,
                   exit_policy="bracket_or_scheduled_session_flatten")
    return result("BUY" if direction > 0 else "SELL", f"Research: {variant} confirmed")
