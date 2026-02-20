from datetime import datetime
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


class ProjectXTradeSummaryOut(BaseModel):
    realized_pnl: float
    gross_pnl: float
    fees: float
    net_pnl: float
    win_rate: float
    avg_win: float
    avg_loss: float
    max_drawdown: float
    trade_count: int


class ProjectXTradeRefreshOut(BaseModel):
    fetched_count: int
    inserted_count: int
