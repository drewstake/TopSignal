"""Frozen hourly Reddit range-breakout adaptation for offline MNQ research."""
from datetime import time, timedelta
import math

from app.services import bot_service as indicators
from app.services.trading_day import TRADING_TZ
from tools.fixtures.topbot_research import _flatten_deadline

REVISION = "reddit_hourly_range_mnq_20260905_v1"
VARIANT = "reddit_hourly_range_mnq"
HISTORY_BARS = 200
CANDIDATES = {VARIANT: {
    "description": "MNQ adaptation of Reddit's Micro Russell 10-hour range breakout against the broader trend.",
    "hypothesis": "A breakout against an hourly EMA100 trend may capture reversals after consolidation.",
    "parameters": {
        "source": "https://www.reddit.com/r/algotrading/comments/1gchopm/range_breakout_strategy/",
        "source_instrument": "Micro Russell futures", "tested_instrument": "MNQ",
        "timeframe": "native complete 1-hour candles", "range_bars": 10,
        "trend": "close below EMA100: long breakouts only; above EMA100: short breakouts only",
        "ema_seed": "SMA100 seeded on trailing 200 closed same-delivery hourly bars",
        "entry": "closed candle crosses prior 10-bar range; next observed minute open",
        "entry_window_et": "08:00 inclusive to 14:00 exclusive",
        "stop": "absolute opposite range boundary; reject risk above 100 points at signal or fill",
        "target": "actual slipped entry plus/minus 1.5 times range width, rounded outward to tick",
        "position_size": 1, "stop_cap_points": 100, "max_daily_loss": 250,
        "max_trades_per_day": 30, "cooldown_seconds": 0,
        "flatten": "15:55 ET or known early close minus five minutes",
        "replication_status": "adaptation: source instrument, exact trend filter, crossing confirmation and account/clock policy differ or were unspecified",
    },
}}


def required_warmup_bars(variant):
    CANDIDATES[variant]
    return HISTORY_BARS


def get_settings(variant):
    return {
        "timeframe_unit": "hour", "timeframe_unit_number": 1,
        "lookback_bars": HISTORY_BARS, "order_size": 1,
        "max_contracts": 1, "max_open_position": 1, "max_daily_loss": 250,
        "max_trades_per_day": 30, "cooldown_seconds": 0,
        "trading_start_time": "08:00", "trading_end_time": "14:00",
        "strategy_params": {"research_revision": REVISION, **CANDIDATES[variant]["parameters"]},
    }


def should_flatten(entry_timestamp, event_time, variant):
    CANDIDATES[variant]
    return event_time >= _flatten_deadline(entry_timestamp)


def evaluate(candles, variant, position_qty=0.0):
    params = CANDIDATES[variant]["parameters"]
    rows = indicators._closed_candles(candles)[-HISTORY_BARS:]
    last = rows[-1] if rows else None
    stamp = indicators._as_utc(last.candle_timestamp) if last else None
    price = float(last.close_price) if last else None
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": REVISION,
               "research_variant": variant, "settings": params}

    def result(action="HOLD", reason="No hourly range reversal setup"):
        return indicators.SignalResult(action=action, reason=reason,
            candle_timestamp=stamp, price=price, raw_payload=payload)

    if len(rows) < HISTORY_BARS or position_qty:
        return result()
    if str(last.unit) != "hour" or int(last.unit_number) != 1 or str(last.symbol) != "MNQ":
        return result(reason="Requires MNQ complete native hourly bars")
    decision = (stamp + timedelta(hours=1)).astimezone(TRADING_TZ)
    if decision.weekday() >= 5 or not time(8) <= decision.time() < time(14):
        return result()
    previous = rows[-11:-1]
    high = max(float(row.high_price) for row in previous)
    low = min(float(row.low_price) for row in previous)
    width = high - low
    ema = indicators._ema_series([float(row.close_price) for row in rows], period=100)[-1]
    direction = (1 if price > high and float(last.open_price) <= high and price < ema
                 else -1 if price < low and float(last.open_price) >= low and price > ema else 0)
    if not direction or not math.isfinite(width) or width <= 0:
        return result()
    stop = low if direction > 0 else high
    risk = direction * (price - stop)
    if not 0 < risk <= params["stop_cap_points"]:
        return result(reason="Range stop exceeds fixed 100-point research risk cap")
    reward = math.ceil(width * 1.5 / .25) * .25
    payload.update(signal_category="entry", target_position_qty=float(direction),
                   entry_price=price, stop_loss=stop, absolute_range_stop=stop,
                   take_profit=price + direction * reward, range_width=width,
                   planned_risk_points=risk, planned_reward_points=reward,
                   risk=risk, reward_r_multiple=reward/risk)
    return result("BUY" if direction > 0 else "SELL", "Hourly range breakout against EMA100")
