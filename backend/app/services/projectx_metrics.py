from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional


@dataclass(frozen=True)
class TradeMetricSample:
    timestamp: datetime
    pnl: Optional[float]
    fees: Optional[float]
    symbol: Optional[str] = None
    side: Optional[str] = None
    size: Optional[float] = None
    price: Optional[float] = None


def compute_trade_summary(samples: Iterable[TradeMetricSample]) -> dict[str, float | int]:
    trades = sorted(samples, key=lambda sample: sample.timestamp)
    if not trades:
        return {
            "realized_pnl": 0.0,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "net_pnl": 0.0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "max_drawdown": 0.0,
            "trade_count": 0,
        }

    realized_values, closed_pnls = _compute_realized_values(trades)
    fee_values = [_effective_fee(trade) for trade in trades]
    net_values = [realized - fee for realized, fee in zip(realized_values, fee_values)]

    wins = [value for value in closed_pnls if value > 0]
    losses = [value for value in closed_pnls if value < 0]

    gross_pnl = sum(realized_values)
    total_fees = sum(fee_values)
    net_pnl = sum(net_values)

    return {
        "realized_pnl": _round(gross_pnl),
        "gross_pnl": _round(gross_pnl),
        "fees": _round(total_fees),
        "net_pnl": _round(net_pnl),
        "win_rate": _round((len(wins) / len(closed_pnls)) * 100, 2) if closed_pnls else 0.0,
        "avg_win": _round(_mean(wins)),
        "avg_loss": _round(_mean(losses)),
        "max_drawdown": _max_drawdown(net_values),
        "trade_count": len(trades),
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


def _max_drawdown(net_values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in net_values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return _round(drawdown)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
