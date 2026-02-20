from pydantic import BaseModel


class SummaryMetricsOut(BaseModel):
    trade_count: int
    net_pnl: float
    win_rate: float
    profit_factor: float
    expectancy: float
    average_win: float
    average_loss: float
    average_win_loss_ratio: float
    max_drawdown: float
    largest_losing_trade: float
    average_hold_minutes: float
    average_hold_minutes_winners: float
    average_hold_minutes_losers: float


class HourPnlOut(BaseModel):
    hour: int
    trade_count: int
    pnl: float


class DayPnlOut(BaseModel):
    day_of_week: int
    day_label: str
    trade_count: int
    pnl: float


class SymbolPnlOut(BaseModel):
    symbol: str
    trade_count: int
    pnl: float
    win_rate: float


class PnlAfterLossOut(BaseModel):
    loss_streak: int
    trade_count: int
    total_pnl: float
    average_pnl: float


class StreakMetricsOut(BaseModel):
    current_win_streak: int
    current_loss_streak: int
    longest_win_streak: int
    longest_loss_streak: int
    pnl_after_losses: list[PnlAfterLossOut]


class BehaviorMetricsOut(BaseModel):
    trade_count: int
    average_position_size: float
    max_position_size: float
    rule_break_count: int
    rule_break_pnl: float
    rule_following_pnl: float
