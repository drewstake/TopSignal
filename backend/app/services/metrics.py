from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, case, cast, func
from sqlalchemy.orm import Session

from ..models import Trade

DAY_LABELS = {
    1: "Mon",
    2: "Tue",
    3: "Wed",
    4: "Thu",
    5: "Fri",
    6: "Sat",
    7: "Sun",
}


@dataclass(frozen=True)
class ClosedTradeSample:
    id: int
    symbol: str
    opened_at: datetime
    closed_at: datetime
    qty: float
    net_pnl: float
    is_rule_break: bool


def _round(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_float(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    return float(value)


def _trade_pnl_before_fees(trade: Trade) -> float:
    if trade.pnl is not None:
        return float(trade.pnl)

    # TODO: add contract multipliers when futures specs are available in DB.
    if trade.exit_price is None:
        return 0.0

    direction = 1 if trade.side == "LONG" else -1
    return _safe_float(trade.qty) * (_safe_float(trade.exit_price) - _safe_float(trade.entry_price)) * direction


def _trade_net_pnl(trade: Trade) -> float:
    return _trade_pnl_before_fees(trade) - _safe_float(trade.fees)


def _net_pnl_sql_expr():
    return func.coalesce(Trade.pnl, 0) - func.coalesce(Trade.fees, 0)


def _closed_trade_query(db: Session, account_id: Optional[int]):
    query = db.query(Trade).filter(Trade.closed_at.isnot(None))
    if account_id is not None:
        query = query.filter(Trade.account_id == account_id)
    return query


def _load_closed_trades(db: Session, account_id: Optional[int] = None) -> list[ClosedTradeSample]:
    rows = (
        _closed_trade_query(db, account_id)
        .order_by(Trade.closed_at.asc(), Trade.id.asc())
        .all()
    )

    trades: list[ClosedTradeSample] = []
    for trade in rows:
        if trade.closed_at is None:
            continue
        trades.append(
            ClosedTradeSample(
                id=int(trade.id),
                symbol=trade.symbol,
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
                qty=abs(_safe_float(trade.qty)),
                net_pnl=_trade_net_pnl(trade),
                is_rule_break=bool(trade.is_rule_break),
            )
        )
    return trades


def _hold_minutes(trade: ClosedTradeSample) -> float:
    seconds = (trade.closed_at - trade.opened_at).total_seconds()
    return max(0.0, seconds / 60.0)


def _max_drawdown(trades: list[ClosedTradeSample]) -> float:
    # Closed-trade drawdown using cumulative equity from earliest to latest trade.
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return _round(max_drawdown, 2)


def get_summary_metrics(db: Session, account_id: Optional[int] = None) -> dict:
    trades = _load_closed_trades(db, account_id)
    if not trades:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "average_win_loss_ratio": 0.0,
            "max_drawdown": 0.0,
            "largest_losing_trade": 0.0,
            "average_hold_minutes": 0.0,
            "average_hold_minutes_winners": 0.0,
            "average_hold_minutes_losers": 0.0,
        }

    pnl_values = [trade.net_pnl for trade in trades]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    gross_profit = sum(wins)
    gross_loss_abs = abs(sum(losses))

    hold_all = [_hold_minutes(trade) for trade in trades]
    hold_winners = [_hold_minutes(trade) for trade in trades if trade.net_pnl > 0]
    hold_losers = [_hold_minutes(trade) for trade in trades if trade.net_pnl < 0]

    average_loss = abs(_mean(losses))
    return {
        "trade_count": len(trades),
        "net_pnl": _round(sum(pnl_values), 2),
        "win_rate": _round((len(wins) / len(trades)) * 100, 2),
        "profit_factor": _round(gross_profit / gross_loss_abs, 4) if gross_loss_abs > 0 else 0.0,
        "expectancy": _round(_mean(pnl_values), 2),
        "average_win": _round(_mean(wins), 2),
        "average_loss": _round(average_loss, 2),
        "average_win_loss_ratio": _round(_mean(wins) / average_loss, 4) if average_loss > 0 else 0.0,
        "max_drawdown": _max_drawdown(trades),
        "largest_losing_trade": _round(min(losses), 2) if losses else 0.0,
        "average_hold_minutes": _round(_mean(hold_all), 2),
        "average_hold_minutes_winners": _round(_mean(hold_winners), 2),
        "average_hold_minutes_losers": _round(_mean(hold_losers), 2),
    }


def get_pnl_by_hour(db: Session, account_id: Optional[int] = None) -> list[dict]:
    hour_expr = cast(func.extract("hour", Trade.opened_at), Integer)
    net_expr = _net_pnl_sql_expr()

    query = (
        db.query(
            hour_expr.label("hour"),
            func.count(Trade.id).label("trade_count"),
            func.coalesce(func.sum(net_expr), 0).label("pnl"),
        )
        .filter(Trade.closed_at.isnot(None))
    )
    if account_id is not None:
        query = query.filter(Trade.account_id == account_id)

    rows = query.group_by(hour_expr).order_by(hour_expr).all()
    row_map = {
        int(row.hour): {
            "trade_count": int(row.trade_count),
            "pnl": _round(float(row.pnl), 2),
        }
        for row in rows
    }

    return [
        {
            "hour": hour,
            "trade_count": row_map.get(hour, {}).get("trade_count", 0),
            "pnl": row_map.get(hour, {}).get("pnl", 0.0),
        }
        for hour in range(24)
    ]


def get_pnl_by_day(db: Session, account_id: Optional[int] = None) -> list[dict]:
    day_expr = cast(func.extract("isodow", Trade.opened_at), Integer)
    net_expr = _net_pnl_sql_expr()

    query = (
        db.query(
            day_expr.label("day_of_week"),
            func.count(Trade.id).label("trade_count"),
            func.coalesce(func.sum(net_expr), 0).label("pnl"),
        )
        .filter(Trade.closed_at.isnot(None))
    )
    if account_id is not None:
        query = query.filter(Trade.account_id == account_id)

    rows = query.group_by(day_expr).order_by(day_expr).all()
    row_map = {
        int(row.day_of_week): {
            "trade_count": int(row.trade_count),
            "pnl": _round(float(row.pnl), 2),
        }
        for row in rows
    }

    return [
        {
            "day_of_week": day,
            "day_label": DAY_LABELS[day],
            "trade_count": row_map.get(day, {}).get("trade_count", 0),
            "pnl": row_map.get(day, {}).get("pnl", 0.0),
        }
        for day in range(1, 8)
    ]


def get_pnl_by_symbol(db: Session, account_id: Optional[int] = None) -> list[dict]:
    net_expr = _net_pnl_sql_expr()
    win_expr = func.sum(case((net_expr > 0, 1), else_=0))
    pnl_sum_expr = func.coalesce(func.sum(net_expr), 0)

    query = (
        db.query(
            Trade.symbol.label("symbol"),
            func.count(Trade.id).label("trade_count"),
            win_expr.label("win_count"),
            pnl_sum_expr.label("pnl"),
        )
        .filter(Trade.closed_at.isnot(None))
    )
    if account_id is not None:
        query = query.filter(Trade.account_id == account_id)

    rows = (
        query.group_by(Trade.symbol)
        .order_by(pnl_sum_expr.desc(), Trade.symbol.asc())
        .all()
    )

    output: list[dict] = []
    for row in rows:
        trade_count = int(row.trade_count)
        win_count = int(row.win_count or 0)
        output.append(
            {
                "symbol": row.symbol,
                "trade_count": trade_count,
                "pnl": _round(float(row.pnl), 2),
                "win_rate": _round((win_count / trade_count) * 100, 2) if trade_count > 0 else 0.0,
            }
        )
    return output


def get_streak_metrics(db: Session, account_id: Optional[int] = None) -> dict:
    trades = _load_closed_trades(db, account_id)
    if not trades:
        return {
            "current_win_streak": 0,
            "current_loss_streak": 0,
            "longest_win_streak": 0,
            "longest_loss_streak": 0,
            "pnl_after_losses": [
                {"loss_streak": 1, "trade_count": 0, "total_pnl": 0.0, "average_pnl": 0.0},
                {"loss_streak": 2, "trade_count": 0, "total_pnl": 0.0, "average_pnl": 0.0},
                {"loss_streak": 3, "trade_count": 0, "total_pnl": 0.0, "average_pnl": 0.0},
            ],
        }

    current_win = 0
    current_loss = 0
    longest_win = 0
    longest_loss = 0
    consecutive_losses = 0
    pnl_after_losses: dict[int, list[float]] = {1: [], 2: [], 3: []}

    for trade in trades:
        # Bucket current trade by how many losses happened immediately before it.
        if consecutive_losses > 0:
            bucket = 3 if consecutive_losses >= 3 else consecutive_losses
            pnl_after_losses[bucket].append(trade.net_pnl)

        if trade.net_pnl > 0:
            current_win += 1
            current_loss = 0
            consecutive_losses = 0
        elif trade.net_pnl < 0:
            current_loss += 1
            current_win = 0
            consecutive_losses += 1
        else:
            current_win = 0
            current_loss = 0
            consecutive_losses = 0

        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)

    buckets: list[dict] = []
    for streak in [1, 2, 3]:
        values = pnl_after_losses[streak]
        buckets.append(
            {
                "loss_streak": streak,
                "trade_count": len(values),
                "total_pnl": _round(sum(values), 2),
                "average_pnl": _round(_mean(values), 2),
            }
        )

    return {
        "current_win_streak": current_win,
        "current_loss_streak": current_loss,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "pnl_after_losses": buckets,
    }


def get_behavior_metrics(db: Session, account_id: Optional[int] = None) -> dict:
    trades = _load_closed_trades(db, account_id)
    if not trades:
        return {
            "trade_count": 0,
            "average_position_size": 0.0,
            "max_position_size": 0.0,
            "rule_break_count": 0,
            "rule_break_pnl": 0.0,
            "rule_following_pnl": 0.0,
        }

    sizes = [trade.qty for trade in trades]
    rule_break_trades = [trade for trade in trades if trade.is_rule_break]
    rule_following_trades = [trade for trade in trades if not trade.is_rule_break]

    return {
        "trade_count": len(trades),
        "average_position_size": _round(_mean(sizes), 4),
        "max_position_size": _round(max(sizes), 4),
        "rule_break_count": len(rule_break_trades),
        "rule_break_pnl": _round(sum(trade.net_pnl for trade in rule_break_trades), 2),
        "rule_following_pnl": _round(sum(trade.net_pnl for trade in rule_following_trades), 2),
    }
