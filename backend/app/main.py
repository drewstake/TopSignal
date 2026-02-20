from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .db import get_db, init_db
from .metrics_schemas import (
    BehaviorMetricsOut,
    DayPnlOut,
    HourPnlOut,
    StreakMetricsOut,
    SummaryMetricsOut,
    SymbolPnlOut,
)
from .models import Trade
from .projectx_schemas import (
    ProjectXAccountOut,
    ProjectXTradeOut,
    ProjectXTradeRefreshOut,
    ProjectXTradeSummaryOut,
)
from .schemas import TradeOut
from .services.metrics import (
    get_behavior_metrics,
    get_pnl_by_day,
    get_pnl_by_hour,
    get_pnl_by_symbol,
    get_streak_metrics,
    get_summary_metrics,
)
from .services.projectx_client import ProjectXClient, ProjectXClientError
from .services.projectx_trades import (
    has_local_trades,
    list_trade_events,
    refresh_account_trades,
    serialize_trade_event,
    summarize_trade_events,
)

app = FastAPI(title="TopSignal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


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


@app.get("/api/accounts", response_model=list[ProjectXAccountOut])
def list_projectx_accounts():
    try:
        client = ProjectXClient.from_env()
        return client.list_accounts()
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc


@app.post("/api/accounts/{account_id}/trades/refresh", response_model=ProjectXTradeRefreshOut)
def refresh_projectx_account_trades(
    account_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    _validate_time_range(start=start, end=end)

    try:
        client = ProjectXClient.from_env()
        return refresh_account_trades(
            db,
            client,
            account_id=account_id,
            start=start,
            end=end,
        )
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/accounts/{account_id}/trades", response_model=list[ProjectXTradeOut])
def list_projectx_account_trades(
    account_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    start: datetime | None = None,
    end: datetime | None = None,
    symbol: str | None = Query(default=None, max_length=50),
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    _validate_time_range(start=start, end=end)

    try:
        if refresh or not has_local_trades(db, account_id):
            client = ProjectXClient.from_env()
            refresh_account_trades(db, client, account_id=account_id, start=start, end=end)

        rows = list_trade_events(
            db,
            account_id=account_id,
            limit=limit,
            start=start,
            end=end,
            symbol_query=symbol,
        )
        return [serialize_trade_event(row) for row in rows]
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc


@app.get("/api/accounts/{account_id}/summary", response_model=ProjectXTradeSummaryOut)
def get_projectx_account_summary(
    account_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    _validate_time_range(start=start, end=end)

    try:
        if refresh or not has_local_trades(db, account_id):
            client = ProjectXClient.from_env()
            refresh_account_trades(db, client, account_id=account_id, start=start, end=end)

        return summarize_trade_events(db, account_id=account_id, start=start, end=end)
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc


def _validate_account_id(account_id: int) -> None:
    if account_id <= 0:
        raise HTTPException(status_code=400, detail="account_id must be a positive integer")


def _validate_time_range(*, start: datetime | None, end: datetime | None) -> None:
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be before end")


def _to_http_exception(exc: ProjectXClientError) -> HTTPException:
    # Missing env or local configuration errors are server configuration issues.
    if exc.status_code is None:
        return HTTPException(status_code=500, detail=str(exc))

    # Upstream API errors should be surfaced as a gateway error to the frontend.
    return HTTPException(status_code=502, detail=str(exc))
