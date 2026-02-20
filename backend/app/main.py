from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .db import get_db
from .metrics_schemas import (
    BehaviorMetricsOut,
    DayPnlOut,
    HourPnlOut,
    StreakMetricsOut,
    SummaryMetricsOut,
    SymbolPnlOut,
)
from .models import Trade
from .schemas import TradeOut
from .services.metrics import (
    get_behavior_metrics,
    get_pnl_by_day,
    get_pnl_by_hour,
    get_pnl_by_symbol,
    get_streak_metrics,
    get_summary_metrics,
)

app = FastAPI(title="TopSignal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/trades", response_model=list[TradeOut])
def list_trades(
    limit: int = 100,
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Trade)
    if account_id is not None:
        query = query.filter(Trade.account_id == account_id)
    return query.order_by(Trade.opened_at.desc()).limit(limit).all()


@app.get("/metrics/summary", response_model=SummaryMetricsOut)
def metrics_summary(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    return get_summary_metrics(db, account_id=account_id)


@app.get("/metrics/pnl-by-hour", response_model=list[HourPnlOut])
def metrics_pnl_by_hour(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    return get_pnl_by_hour(db, account_id=account_id)


@app.get("/metrics/pnl-by-day", response_model=list[DayPnlOut])
def metrics_pnl_by_day(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    return get_pnl_by_day(db, account_id=account_id)


@app.get("/metrics/pnl-by-symbol", response_model=list[SymbolPnlOut])
def metrics_pnl_by_symbol(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    return get_pnl_by_symbol(db, account_id=account_id)


@app.get("/metrics/streaks", response_model=StreakMetricsOut)
def metrics_streaks(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    return get_streak_metrics(db, account_id=account_id)


@app.get("/metrics/behavior", response_model=BehaviorMetricsOut)
def metrics_behavior(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    return get_behavior_metrics(db, account_id=account_id)
