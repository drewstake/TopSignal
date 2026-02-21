from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable, Optional


@dataclass(frozen=True)
class TradeMetricSample:
    timestamp: datetime
    pnl: Optional[float]
    fees: Optional[float]
    order_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    size: Optional[float] = None
    price: Optional[float] = None


@dataclass(frozen=True)
class DrawdownEpisode:
    peak_equity: float
    start_ts: datetime
    trough_ts: datetime
    end_ts: Optional[datetime]
    trough_drawdown: float


def compute_trade_summary(samples: Iterable[TradeMetricSample]) -> dict[str, float | int]:
    trades = sorted(samples, key=lambda sample: sample.timestamp)
    if not trades:
        return _empty_trade_summary()

    realized_values, closed_pnls = _compute_realized_values(trades)
    fee_values = [_effective_fee(trade) for trade in trades]
    net_values = [realized - fee for realized, fee in zip(realized_values, fee_values)]
    closed_net_values = [net for trade, net in zip(trades, net_values) if trade.pnl is not None]

    wins = [value for value in closed_net_values if value > 0]
    losses = [value for value in closed_net_values if value < 0]
    breakeven_count = len(closed_net_values) - len(wins) - len(losses)

    gross_wins = [value for value in closed_pnls if value > 0]
    gross_losses = [value for value in closed_pnls if value < 0]
    gross_profit = sum(gross_wins)
    gross_loss_abs = abs(sum(gross_losses))

    gross_pnl = sum(realized_values)
    total_fees = sum(fee_values)
    net_pnl = sum(net_values)
    trade_count = len(closed_net_values)
    execution_count = len(trades)
    order_ids = {trade.order_id for trade in trades if trade.order_id}
    half_turn_count = len(order_ids) if order_ids else execution_count

    daily_net = _compute_daily_net_values(trades, net_values)
    active_days = len(daily_net)
    green_days = sum(1 for value in daily_net.values() if value > 0)
    red_days = sum(1 for value in daily_net.values() if value < 0)
    flat_days = active_days - green_days - red_days

    drawdown_stats = _compute_drawdown_stats(trades, net_values)
    active_hours = _compute_active_hours(trades)

    return {
        "realized_pnl": _round(gross_pnl),
        "gross_pnl": _round(gross_pnl),
        "fees": _round(total_fees),
        "net_pnl": _round(net_pnl),
        "win_rate": _round((len(wins) / trade_count) * 100, 2) if trade_count else 0.0,
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": breakeven_count,
        "profit_factor": _round(gross_profit / gross_loss_abs, 4) if gross_loss_abs > 0 else 0.0,
        "avg_win": _round(_mean(wins)),
        "avg_loss": _round(_mean(losses)),
        "expectancy_per_trade": _round(_mean(closed_net_values)),
        "tail_risk_5pct": _round(_tail_risk_worst_5pct(closed_net_values)),
        "max_drawdown": _round(drawdown_stats["max_drawdown"]),
        "average_drawdown": _round(drawdown_stats["average_drawdown"]),
        "risk_drawdown_score": _round(drawdown_stats["risk_drawdown_score"], 2),
        "max_drawdown_length_hours": _round(drawdown_stats["max_drawdown_length_hours"]),
        "recovery_time_hours": _round(drawdown_stats["recovery_time_hours"]),
        "average_recovery_length_hours": _round(drawdown_stats["average_recovery_length_hours"]),
        "trade_count": trade_count,
        "half_turn_count": half_turn_count,
        "execution_count": execution_count,
        "day_win_rate": _round((green_days / active_days) * 100, 2) if active_days else 0.0,
        "green_days": green_days,
        "red_days": red_days,
        "flat_days": flat_days,
        "avg_trades_per_day": _round((trade_count / active_days), 2) if active_days else 0.0,
        "active_days": active_days,
        "efficiency_per_hour": _round((net_pnl / active_hours), 2) if active_hours > 0 else 0.0,
        "profit_per_day": _round((net_pnl / active_days), 2) if active_days else 0.0,
    }


def compute_daily_pnl_calendar(samples: Iterable[TradeMetricSample]) -> list[dict[str, str | int | float]]:
    trades = sorted(samples, key=lambda sample: sample.timestamp)
    if not trades:
        return []

    realized_values, _ = _compute_realized_values(trades)
    buckets: dict[str, dict[str, str | int | float]] = {}

    for trade, realized in zip(trades, realized_values):
        if trade.pnl is None:
            # Calendar trade counts should reflect closed trades only.
            continue

        day_key = _as_utc(trade.timestamp).date().isoformat()
        fees = _effective_fee(trade)
        bucket = buckets.setdefault(
            day_key,
            {
                "date": day_key,
                "trade_count": 0,
                "gross_pnl": 0.0,
                "fees": 0.0,
                "net_pnl": 0.0,
            },
        )
        bucket["trade_count"] = int(bucket["trade_count"]) + 1
        bucket["gross_pnl"] = float(bucket["gross_pnl"]) + realized
        bucket["fees"] = float(bucket["fees"]) + fees
        bucket["net_pnl"] = float(bucket["net_pnl"]) + realized - fees

    output: list[dict[str, str | int | float]] = []
    for day in sorted(buckets):
        bucket = buckets[day]
        output.append(
            {
                "date": day,
                "trade_count": int(bucket["trade_count"]),
                "gross_pnl": _round(float(bucket["gross_pnl"])),
                "fees": _round(float(bucket["fees"])),
                "net_pnl": _round(float(bucket["net_pnl"])),
            }
        )
    return output


def _compute_realized_values(trades: list[TradeMetricSample]) -> tuple[list[float], list[float]]:
    realized_values: list[float] = []
    closed_pnls: list[float] = []

    for trade in trades:
        # ProjectX Trade/search returns null profitAndLoss for half-turn/open-leg rows.
        # Those rows should not contribute realized PnL.
        realized = _safe_float(trade.pnl)
        realized_values.append(realized)
        if trade.pnl is not None:
            closed_pnls.append(realized)

    return realized_values, closed_pnls


def _empty_trade_summary() -> dict[str, float | int]:
    return {
        "realized_pnl": 0.0,
        "gross_pnl": 0.0,
        "fees": 0.0,
        "net_pnl": 0.0,
        "win_rate": 0.0,
        "win_count": 0,
        "loss_count": 0,
        "breakeven_count": 0,
        "profit_factor": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "expectancy_per_trade": 0.0,
        "tail_risk_5pct": 0.0,
        "max_drawdown": 0.0,
        "average_drawdown": 0.0,
        "risk_drawdown_score": 0.0,
        "max_drawdown_length_hours": 0.0,
        "recovery_time_hours": 0.0,
        "average_recovery_length_hours": 0.0,
        "trade_count": 0,
        "half_turn_count": 0,
        "execution_count": 0,
        "day_win_rate": 0.0,
        "green_days": 0,
        "red_days": 0,
        "flat_days": 0,
        "avg_trades_per_day": 0.0,
        "active_days": 0,
        "efficiency_per_hour": 0.0,
        "profit_per_day": 0.0,
    }


def _effective_fee(trade: TradeMetricSample) -> float:
    # Keep fee accounting aligned with Topstep's closed-trade PnL:
    # rows without broker-reported realized PnL are open-leg events and should
    # not reduce net PnL. Closing rows already carry round-trip fees.
    if trade.pnl is None:
        return 0.0
    return _safe_float(trade.fees)


def _safe_float(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    return float(value)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _round(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def _compute_daily_net_values(trades: list[TradeMetricSample], net_values: list[float]) -> dict[str, float]:
    daily_net: dict[str, float] = {}
    for trade, net in zip(trades, net_values):
        day_key = _as_utc(trade.timestamp).date().isoformat()
        daily_net[day_key] = daily_net.get(day_key, 0.0) + net
    return daily_net


def _tail_risk_worst_5pct(values: list[float]) -> float:
    if not values:
        return 0.0

    worst_count = max(1, math.ceil(len(values) * 0.05))
    sorted_values = sorted(values)
    worst_slice = sorted_values[:worst_count]
    worst_avg = _mean(worst_slice)
    return min(0.0, worst_avg)


def _compute_active_hours(trades: list[TradeMetricSample]) -> float:
    if not trades:
        return 0.0

    first_by_day: dict[str, datetime] = {}
    last_by_day: dict[str, datetime] = {}
    for trade in trades:
        ts = _as_utc(trade.timestamp)
        day_key = ts.date().isoformat()
        if day_key not in first_by_day or ts < first_by_day[day_key]:
            first_by_day[day_key] = ts
        if day_key not in last_by_day or ts > last_by_day[day_key]:
            last_by_day[day_key] = ts

    total_hours = 0.0
    for day_key, first in first_by_day.items():
        last = last_by_day[day_key]
        span_hours = max((last - first).total_seconds() / 3600.0, 1.0 / 60.0)
        total_hours += span_hours
    return total_hours


def _compute_drawdown_stats(trades: list[TradeMetricSample], net_values: list[float]) -> dict[str, float]:
    if not trades:
        return {
            "max_drawdown": 0.0,
            "average_drawdown": 0.0,
            "risk_drawdown_score": 0.0,
            "max_drawdown_length_hours": 0.0,
            "recovery_time_hours": 0.0,
            "average_recovery_length_hours": 0.0,
        }

    episodes = _build_drawdown_episodes(trades, net_values)
    if not episodes:
        return {
            "max_drawdown": 0.0,
            "average_drawdown": 0.0,
            "risk_drawdown_score": 0.0,
            "max_drawdown_length_hours": 0.0,
            "recovery_time_hours": 0.0,
            "average_recovery_length_hours": 0.0,
        }

    last_ts = _as_utc(trades[-1].timestamp)
    max_episode = min(episodes, key=lambda episode: episode.trough_drawdown)

    drawdown_lengths = [_duration_hours(episode.start_ts, episode.end_ts or last_ts) for episode in episodes]
    recovery_lengths = [
        _duration_hours(episode.trough_ts, episode.end_ts)
        for episode in episodes
        if episode.end_ts is not None
    ]

    max_drawdown = max_episode.trough_drawdown
    denominator = max(max_episode.peak_equity, abs(max_drawdown), 1.0)
    recovery_end = max_episode.end_ts or last_ts

    return {
        "max_drawdown": max_drawdown,
        "average_drawdown": _mean([episode.trough_drawdown for episode in episodes]),
        "risk_drawdown_score": (abs(max_drawdown) / denominator) * 100.0,
        "max_drawdown_length_hours": max(drawdown_lengths) if drawdown_lengths else 0.0,
        "recovery_time_hours": _duration_hours(max_episode.trough_ts, recovery_end),
        "average_recovery_length_hours": _mean(recovery_lengths),
    }


def _build_drawdown_episodes(trades: list[TradeMetricSample], net_values: list[float]) -> list[DrawdownEpisode]:
    if not trades:
        return []

    equity = 0.0
    peak = 0.0

    in_drawdown = False
    current_peak = 0.0
    current_start: Optional[datetime] = None
    current_trough = 0.0
    current_trough_ts: Optional[datetime] = None
    episodes: list[DrawdownEpisode] = []

    for trade, net in zip(trades, net_values):
        ts = _as_utc(trade.timestamp)
        equity += net

        if equity >= peak:
            if in_drawdown and current_start is not None and current_trough_ts is not None:
                episodes.append(
                    DrawdownEpisode(
                        peak_equity=current_peak,
                        start_ts=current_start,
                        trough_ts=current_trough_ts,
                        end_ts=ts,
                        trough_drawdown=current_trough,
                    )
                )
            peak = equity
            in_drawdown = False
            continue

        drawdown = equity - peak
        if not in_drawdown:
            in_drawdown = True
            current_peak = peak
            current_start = ts
            current_trough = drawdown
            current_trough_ts = ts
            continue

        if drawdown < current_trough:
            current_trough = drawdown
            current_trough_ts = ts

    if in_drawdown and current_start is not None and current_trough_ts is not None:
        episodes.append(
            DrawdownEpisode(
                peak_equity=current_peak,
                start_ts=current_start,
                trough_ts=current_trough_ts,
                end_ts=None,
                trough_drawdown=current_trough,
            )
        )

    return episodes


def _duration_hours(start: datetime, end: Optional[datetime]) -> float:
    if end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
