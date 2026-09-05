"""TopBot v4: long-biased MNQ pullbacks with a fixed 50/50-point bracket.

Tune the constants here, then validate a new revision. There are no source
strategies, votes, quality scores, or learned parameters in this strategy.
"""

from datetime import datetime, time, timedelta, timezone

from .trading_day import TRADING_TZ


REVISION = "mnq_ema_vwap_pullback_v4_long_bias"
HISTORY_BARS = 200
RULES = {
    "revision": REVISION,
    "ema_period": 20,
    "ema_slope_bars": 3,
    "directional_bias": "long",
    "short_trend_ema_period": 50,
    "short_trend_slope_bars": 3,
    "stop_points": 50.0,
    "target_points": 50.0,
    "tick_size": 0.25,
    "session_start": "09:30",
    "session_end": "15:45",
}


def normalize_params(_params=None):
    """Replace old persisted ensemble settings with the current code preset."""
    return dict(RULES)


def evaluate(candles):
    # Lazy import keeps the shared indicator library independent of this setup.
    from . import bot_service as indicators

    closed = indicators._closed_candles(candles)[-HISTORY_BARS:]
    latest = closed[-1] if closed else None
    timestamp = indicators._as_utc(latest.candle_timestamp) if latest is not None else None
    price = float(latest.close_price) if latest is not None else None
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": REVISION, "settings": dict(RULES)}

    def hold(reason):
        return indicators.SignalResult(
            action="HOLD", reason=reason, candle_timestamp=timestamp,
            price=price, raw_payload=payload,
        )

    if latest is None or len(closed) < HISTORY_BARS:
        return hold(f"TopBot needs {HISTORY_BARS} closed candles for its EMA warmup.")
    if str(latest.unit) != "minute" or int(latest.unit_number) != 5:
        return hold("TopBot requires 5-minute MNQ candles.")
    from .instruments import normalize_symbol_key
    if (normalize_symbol_key(latest.symbol) or normalize_symbol_key(latest.contract_id)) != "MNQ":
        return hold("TopBot trades MNQ only.")

    local = timestamp.astimezone(TRADING_TZ)
    start_time = time.fromisoformat(RULES["session_start"])
    end_time = time.fromisoformat(RULES["session_end"])
    # The decision becomes available at this bar's close, never its open.
    decision_local = local + timedelta(minutes=5)
    if local.weekday() >= 5 or not start_time <= decision_local.time() <= end_time:
        return hold("TopBot is outside its entry session.")
    session_start = datetime.combine(local.date(), start_time, tzinfo=TRADING_TZ).astimezone(timezone.utc)
    session = [row for row in closed if indicators._as_utc(row.candle_timestamp) >= session_start]
    if len(session) < 2 or indicators._as_utc(session[0].candle_timestamp) != session_start:
        return hold("TopBot needs the complete regular-session candle history for VWAP.")
    if any(indicators._as_utc(row.candle_timestamp) != session_start + timedelta(minutes=5 * index)
           for index, row in enumerate(session)):
        return hold("TopBot skipped a session with missing candles.")

    closes = indicators._candle_close_values(closed)
    ema = indicators._ema_series(closes, period=RULES["ema_period"])
    volume = sum(float(row.volume or 0) for row in session)
    if volume <= 0:
        return hold("TopBot needs positive session volume for VWAP.")
    vwap = sum((float(row.high_price) + float(row.low_price) + float(row.close_price)) / 3
               * float(row.volume or 0) for row in session) / volume
    previous = closed[-2]
    slope = ema[-1] - ema[-1 - RULES["ema_slope_bars"]]
    previous_ema = ema[-2]
    pullback_touched = float(previous.low_price) <= previous_ema <= float(previous.high_price)
    payload.update(ema=ema[-1], previous_ema=previous_ema, ema_slope=slope,
                   session_vwap=vwap, pullback_touched=pullback_touched)

    if not pullback_touched:
        return hold("TopBot is waiting for a pullback to the 20 EMA.")
    long_setup = (slope > 0 and price > ema[-1] and price > vwap
                  and price > float(previous.high_price) and price > float(latest.open_price))
    short_setup = (slope < 0 and price < ema[-1] and price < vwap
                   and price < float(previous.low_price) and price < float(latest.open_price))
    if not long_setup and not short_setup:
        return hold("TopBot is waiting for the pullback to confirm in the EMA/VWAP direction.")

    if short_setup:
        trend_ema = indicators._ema_series(closes, period=RULES["short_trend_ema_period"])
        trend_slope = trend_ema[-1] - trend_ema[-1 - RULES["short_trend_slope_bars"]]
        short_entry_allowed = ema[-1] < trend_ema[-1] and trend_slope < 0
        payload.update(short_trend_ema=trend_ema[-1], short_trend_ema_slope=trend_slope,
                       short_entry_allowed=short_entry_allowed)
        if not short_entry_allowed:
            # Preserve the original opposite-signal exit, but forbid a new short.
            # The existing router verifies target zero against the provider position;
            # replay likewise uses this signal only to flatten an open long.
            payload.update(signal_category="exit", target_position_qty=0.0,
                           exit_reason="opposite_signal_flatten")
            return indicators.SignalResult(
                action="SELL",
                reason=("TopBot long bias: short entry blocked until the 20 EMA is below "
                        "a falling 50 EMA. Exit an existing long only."),
                candle_timestamp=timestamp, price=price, raw_payload=payload,
            )

    action = "BUY" if long_setup else "SELL"
    direction = 1 if long_setup else -1
    risk = RULES["stop_points"]
    reward = RULES["target_points"]
    stop = price - direction * risk
    target = price + direction * reward
    payload.update(planned_risk_points=risk, planned_reward_points=reward)
    payload.update(entry_price=price, stop_loss=stop, take_profit=target, risk=risk,
                   reward_r_multiple=reward / risk)
    return indicators.SignalResult(
        action=action, reason=(f"TopBot {action}: 20 EMA pullback confirmed with VWAP"
                              f"{' and a falling 50 EMA' if short_setup else ''}; "
                              f"{risk:g}-point stop and {reward:g}-point target."),
        candle_timestamp=timestamp, price=price, raw_payload=payload,
    )
