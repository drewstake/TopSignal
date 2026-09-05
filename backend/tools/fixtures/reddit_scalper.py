"""Independent one-minute MNQ mean-reversion proxy, NOT the Reddit author's algo.

The public source withholds its script and uses five-second NQ chart signals to
execute CFDs. Authentic local data only supports one-minute OHLC. These fixed
rules were declared before replay; no fitting or synthetic five-second bars.
"""
from __future__ import annotations

from datetime import time, timedelta
import math

from app.services import bot_service as indicators
from app.services.instruments import normalize_symbol_key
from app.services.trading_day import TRADING_TZ
from tools.fixtures.topbot_research import _flatten_deadline


REVISION = "reddit_scalper_independent_mnq_1m_proxy_20260905_v1"
CANDIDATES = {
    "reddit_scalper_1m_proxy": {
        "description": "Independent 1-minute MNQ two-speed mean-reversion proxy; original proprietary 5-second CFD strategy cannot be reproduced.",
        "hypothesis": "A closed-bar reversal after a two-ATR departure from either short or long local mean may cover MNQ execution costs over a six-minute holding horizon.",
        "parameters": {
            "source_url": "https://www.reddit.com/r/algotrading/comments/1rtepah/how_i_improved_results_on_a_scalping_algo_mean/",
            "replication_status": "independent approximation, not author's strategy",
            "signal_minutes": 1, "mean_periods": [10, 30], "atr_periods": [10, 30],
            "departure_atr": 2.0, "confirmation": "latest close reverses previous close while remaining outside selected mean",
            "combination": "either setting triggers; fast wins deterministic tie; one total position",
            "reversion_exit": "close crosses midpoint of SMA10 and SMA30, filled next observed minute open",
            "max_holding_minutes": 6, "stop_atr_period": 20, "stop_atr_multiple": 2.0,
            "stop_floor_points": 5.0, "stop_cap_points": 100.0, "reward_r_multiple": 1.0,
            "position_size": 1, "pyramiding": False, "fractional_contracts": False,
            "entry_start_et": "10:00", "entry_end_et_exclusive": "15:45",
            "flatten": "earlier of entry plus six minutes or known session close minus five minutes",
            "max_trades_per_day": 30, "cooldown_seconds": 60, "daily_loss_entry_gate": 250,
            "observations": "32 consecutive closed observed minutes of the same delivery required",
        },
    },
}


def required_warmup_bars(variant):
    return 32


def get_settings(variant):
    return {"timeframe_unit": "minute", "timeframe_unit_number": 1,
            "trading_start_time": "10:00", "trading_end_time": "16:00",
            "max_trades_per_day": 30, "cooldown_seconds": 60,
            "strategy_params": {"research_revision": REVISION,
                                **CANDIDATES[variant]["parameters"]}}


def should_flatten(entry_timestamp, event_time, variant):
    """Clock uses entry time and observed opens; missing data never resets it."""
    deadline = min(entry_timestamp + timedelta(minutes=6), _flatten_deadline(entry_timestamp))
    return event_time >= deadline


def _atr(rows, period):
    return sum(max(float(row.high_price) - float(row.low_price),
                   abs(float(row.high_price) - float(previous.close_price)),
                   abs(float(row.low_price) - float(previous.close_price)))
               for previous, row in zip(rows[-period - 1:-1], rows[-period:])) / period


def evaluate(candles, variant, position_qty=0.0):
    closed = indicators._closed_candles(candles)
    latest = closed[-1] if closed else None
    timestamp = indicators._as_utc(latest.candle_timestamp) if latest else None
    price = float(latest.close_price) if latest else None
    payload = {"strategy_type": "topbot_adaptive", "strategy_revision": REVISION,
               "research_variant": variant, "replication_status": "independent one-minute approximation"}

    def result(action="HOLD", reason="Research proxy: no setup"):
        return indicators.SignalResult(action=action, reason=reason,
                                       candle_timestamp=timestamp, price=price, raw_payload=payload)

    if latest is None or len(closed) < 32:
        return result(reason="Research proxy: requires 32 closed observed minutes")
    if (str(latest.unit) != "minute" or int(latest.unit_number) != 1
            or (normalize_symbol_key(latest.symbol) or normalize_symbol_key(latest.contract_id)) != "MNQ"):
        return result(reason="Research proxy: requires genuine MNQ one-minute bars")
    decision = (timestamp + timedelta(minutes=1)).astimezone(TRADING_TZ)
    if not position_qty and (decision.weekday() >= 5 or not time(10) <= decision.time() < time(15, 45)):
        return result()
    rows = closed[-32:]
    if any(indicators._as_utc(row.candle_timestamp) != timestamp - timedelta(minutes=31 - i)
           or getattr(row, "source_instrument_id", None) != getattr(latest, "source_instrument_id", None)
           for i, row in enumerate(rows)):
        return result(reason="Research proxy: missing minute or delivery transition")
    closes = [float(row.close_price) for row in rows]
    means = [sum(closes[-period:]) / period for period in (10, 30)]
    if position_qty:
        midpoint = sum(means) / len(means)
        if (position_qty > 0 and price >= midpoint) or (position_qty < 0 and price <= midpoint):
            payload.update(signal_category="exit", target_position_qty=0.0,
                           exit_reason="proxy_mean_reversion", mean_midpoint=midpoint)
            return result("SELL" if position_qty > 0 else "BUY", "Research proxy: mean crossed")
        return result()
    if decision.astimezone(timestamp.tzinfo) >= _flatten_deadline(timestamp):
        return result()
    direction = 0
    for period, mean in zip((10, 30), means):
        prior_mean = sum(closes[-period - 1:-1]) / period
        prior_atr = _atr(rows[:-1], period)
        if not math.isfinite(prior_atr) or prior_atr <= 0:
            continue
        if closes[-2] < prior_mean - 2.0 * prior_atr and closes[-2] < price < mean:
            direction = 1
        elif closes[-2] > prior_mean + 2.0 * prior_atr and closes[-2] > price > mean:
            direction = -1
        if direction:
            payload.update(trigger_period=period, prior_mean=prior_mean, prior_atr=prior_atr)
            break
    if not direction:
        return result()
    atr = _atr(rows, 20)
    if not math.isfinite(atr) or atr <= 0:
        return result()
    risk = math.ceil(max(5.0, min(100.0, atr * 2.0)) * 4.0) / 4.0
    payload.update(signal_category="entry", target_position_qty=float(direction),
                   entry_price=price, stop_loss=price - direction * risk,
                   take_profit=price + direction * risk, planned_risk_points=risk,
                   planned_reward_points=risk, risk=risk, reward_r_multiple=1.0,
                   exit_policy="bracket_or_mean_cross_or_six_minute_or_session_deadline")
    return result("BUY" if direction > 0 else "SELL", "Research proxy: confirmed departure reversal")
