from datetime import date, datetime
from pydantic import BaseModel


class ProjectXAccountOut(BaseModel):
    id: int
    name: str
    balance: float
    status: str


class ProjectXTradeOut(BaseModel):
    id: int
    account_id: int
    contract_id: str
    symbol: str
    side: str
    size: float
    price: float
    timestamp: datetime
    fees: float
    pnl: float | None = None
    order_id: str
    source_trade_id: str | None = None


class ProjectXTradeSummaryOut(BaseModel):
    realized_pnl: float
    gross_pnl: float
    fees: float
    net_pnl: float
    win_rate: float
    win_count: int
    loss_count: int
    breakeven_count: int
    profit_factor: float
    avg_win: float
    avg_loss: float
    expectancy_per_trade: float
    tail_risk_5pct: float
    max_drawdown: float
    average_drawdown: float
    risk_drawdown_score: float
    max_drawdown_length_hours: float
    recovery_time_hours: float
    average_recovery_length_hours: float
    trade_count: int
    half_turn_count: int
    execution_count: int
    day_win_rate: float
    green_days: int
    red_days: int
    flat_days: int
    avg_trades_per_day: float
    active_days: int
    efficiency_per_hour: float
    profit_per_day: float


class ProjectXTradeRefreshOut(BaseModel):
    fetched_count: int
    inserted_count: int


class ProjectXPnlCalendarDayOut(BaseModel):
    date: date
    trade_count: int
    gross_pnl: float
    fees: float
    net_pnl: float
