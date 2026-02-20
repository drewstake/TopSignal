from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


@dataclass
class _OpenLot:
    sign: int
    qty: float
    price: float


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
    fee_values = [_safe_float(trade.fees) for trade in trades]
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


def _compute_realized_values(trades: list[TradeMetricSample]) -> tuple[list[float], list[float]]:
    open_lots: dict[str, list[_OpenLot]] = {}
    realized_values: list[float] = []
    closed_pnls: list[float] = []

    for trade in trades:
        if trade.pnl is not None:
            realized = _safe_float(trade.pnl)
            realized_values.append(realized)
            closed_pnls.append(realized)
            continue

        realized = _compute_fifo_realized(trade, open_lots)
        realized_values.append(realized)
        if abs(realized) > 1e-12:
            closed_pnls.append(realized)

    return realized_values, closed_pnls


def _compute_fifo_realized(trade: TradeMetricSample, open_lots: dict[str, list[_OpenLot]]) -> float:
    side = (trade.side or "").upper()
    size = abs(_safe_float(trade.size))
    price = trade.price
    if side not in {"BUY", "SELL"} or size <= 0.0 or price is None:
        return 0.0

    symbol = trade.symbol or "__DEFAULT__"
    lots = open_lots.setdefault(symbol, [])
    remaining = size
    realized = 0.0
    side_sign = 1 if side == "BUY" else -1

    while remaining > 0.0 and lots and lots[0].sign == -side_sign:
        lot = lots[0]
        matched = min(remaining, lot.qty)

        if side_sign == 1:
            # Closing a short by buying back.
            realized += (lot.price - float(price)) * matched
        else:
            # Closing a long by selling.
            realized += (float(price) - lot.price) * matched

        lot.qty -= matched
        remaining -= matched
        if lot.qty <= 1e-12:
            lots.pop(0)

    if remaining > 1e-12:
        lots.append(_OpenLot(sign=side_sign, qty=remaining, price=float(price)))

    return realized


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
