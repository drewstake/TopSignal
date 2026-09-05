"""One fixed, predeclared Reddit opening-range hypothesis adapted to MNQ.

Use tools/research_reddit_orb.py, whose execution adapter preserves the absolute
opening-range-low stop. This is offline research and never a live strategy.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math

from app.services import bot_service as indicators
from app.services.instruments import normalize_symbol_key
from tools.fixtures.topbot_research import _flatten_deadline
from app.services.trading_day import TRADING_TZ

REVISION = "reddit_orb15_mnq_absolute_stop_20260905_v1"
VARIANT = "reddit_orb15_long"
CANDIDATES = {VARIANT: {
    "description": "15-minute opening-range long breakout; absolute range-low stop and 1.5R target from actual slipped entry.",
    "hypothesis": "The Reddit S&P CFD opening-range continuation effect may generalize to MNQ after actual futures fees, adverse fills, and a fixed daily risk gate.",
    "source_url": "https://www.reddit.com/r/algotrading/comments/1j9pxsr/backtest_results_for_the_opening_range_breakout/",
    "parameters": {
        "opening_range_et": "09:30-09:45", "signal_minutes": 15,
        "breakout": "15m candle open <= range high and close > range high",
        "entry": "next 15m boundary observed minute open, strictly before noon ET; missing exact minute rejects entry",
        "direction": "long", "stop": "absolute 09:30-09:45 range low",
        "reward_multiple": 1.5, "target_rounding": "ceil to next MNQ quarter point",
        "stop_cap_points": 100.0, "wide_stop_policy": "skip; never tighten source stop",
        "max_daily_loss": 250.0, "max_trades_per_day": 30,
        "reentry": "new crossing candle while flat; no one-trade-per-day restriction",
        "position_size": 1, "tick_size": 0.25,
        "flatten": "15:55 ET or known holiday close minus five minutes, next observed open if missing",
        "missing_signals": "all five-minute bars since 09:30 must form a complete prefix",
        "evidence": "all periods reused; no untouched holdout and no parameter search",
    },
}}


def required_warmup_bars(variant):
    CANDIDATES[variant]
    return 200


def get_settings(variant):
    return {"trading_end_time": "16:00", "cooldown_seconds": 0,
            "max_trades_per_day": 30,
            "strategy_params": {"research_revision": REVISION,
                                **CANDIDATES[variant]["parameters"]}}


def should_flatten(entry_timestamp, event_time, variant):
    CANDIDATES[variant]
    return indicators._as_utc(event_time) >= _flatten_deadline(entry_timestamp)


def fill_plan(entry_price, range_low):
    """Preserve the source stop and re-evaluate risk using observed slipped fill."""
    if not all(math.isfinite(value) for value in (entry_price, range_low)):
        return None
    risk = entry_price - range_low
    if risk < .25 or risk > 100:
        return None
    # Input MNQ prices and slipped fills are on quarter ticks. The half-risk
    # target can land between ticks; round reward up once, never round the stop.
    reward = math.ceil(risk * 1.5 * 4) / 4
    return {"entry_price": entry_price, "stop_loss": range_low,
            "take_profit": entry_price + reward, "planned_risk_points": risk,
            "planned_reward_points": reward, "risk": risk,
            "reward_r_multiple": reward / risk}


def evaluate(candles, variant, position_qty=0.0):
    definition = CANDIDATES[variant]
    rows = indicators._closed_candles(candles)[-200:]
    latest = rows[-1] if rows else None
    stamp = indicators._as_utc(latest.candle_timestamp) if latest else None
    price = float(latest.close_price) if latest else None
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": REVISION,
               "research_variant": variant, "settings": dict(definition["parameters"])}

    def result(action="HOLD", reason="Reddit ORB: no setup"):
        return indicators.SignalResult(action=action, reason=reason,
                                       candle_timestamp=stamp, price=price,
                                       raw_payload=payload)

    if latest is None or len(rows) < 200 or position_qty:
        return result()
    if (str(latest.unit) != "minute" or int(latest.unit_number) != 5
            or (normalize_symbol_key(latest.symbol) or normalize_symbol_key(latest.contract_id)) != "MNQ"):
        return result(reason="Reddit ORB requires MNQ five-minute source bars")
    decision = (stamp + timedelta(minutes=5)).astimezone(TRADING_TZ)
    if (decision.weekday() >= 5 or not time(10) <= decision.time() < time(12)
            or decision.minute % 15 or decision.second):
        return result()
    start = datetime.combine(decision.date(), time(9, 30), tzinfo=TRADING_TZ).astimezone(timezone.utc)
    session = [row for row in rows if indicators._as_utc(row.candle_timestamp) >= start]
    if len(session) < 6 or len(session) % 3 or any(
        indicators._as_utc(row.candle_timestamp) != start + timedelta(minutes=5 * index)
        for index, row in enumerate(session)
    ):
        return result(reason="Reddit ORB: incomplete regular-session prefix")
    high = max(float(row.high_price) for row in session[:3])
    low = min(float(row.low_price) for row in session[:3])
    if not float(session[-3].open_price) <= high < price:
        return result()
    risk = price - low
    if not math.isfinite(risk) or risk <= 0:
        return result()
    payload.update(signal_category="entry", target_position_qty=1.0,
                   entry_price=price, stop_loss=low,
                   take_profit=price + math.ceil(risk * 1.5 * 4) / 4,
                   range_low=low, range_high=high,
                   exit_policy="absolute_bracket_or_scheduled_flatten")
    return result("BUY", "Reddit ORB: completed 15-minute candle crossed above opening range")
