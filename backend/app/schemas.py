from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class TradeOut(BaseModel):
    id: int
    account_id: int
    symbol: str
    side: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    qty: float
    entry_price: float
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    fees: Optional[float] = None
    notes: Optional[str] = None
    is_rule_break: bool = False
    rule_break_type: Optional[str] = None

    class Config:
        from_attributes = True
