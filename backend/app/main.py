import calendar
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .db import get_db, init_db
from .journal_schemas import (
    JournalEntryCreateIn,
    JournalEntryCreateOut,
    JournalEntryListOut,
    JournalDaysOut,
    JournalImageOut,
    JournalEntryUpdateIn,
    JournalEntryOut,
    JournalMood,
    PullTradeStatsIn,
)
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
    ProjectXPnlCalendarDayOut,
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
from .services.journal import (
    VersionConflictError,
    archive_journal_entry,
    create_journal_entry_image,
    create_journal_entry,
    delete_journal_entry,
    delete_journal_entry_image,
    get_journal_entry_image,
    get_journal_image_file_path,
    list_journal_days,
    list_journal_entry_images,
    list_journal_entries,
    pull_journal_entry_trade_stats,
    serialize_journal_entry,
    serialize_journal_entry_image,
    unarchive_journal_entry,
    update_journal_entry,
    validate_date_range as validate_journal_date_range,
)
from .services.projectx_client import ProjectXClient, ProjectXClientError
from .services.projectx_trades import (
    ensure_trade_cache_for_request,
    get_trade_event_pnl_calendar,
    has_local_trades,
    list_trade_events,
    refresh_account_trades,
    serialize_trade_event,
    summarize_trade_events,
)

app = FastAPI(title="TopSignal API")
_DEFAULT_PNL_CALENDAR_LOOKBACK_MONTHS = 6
_LOCAL_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_origin_regex=_LOCAL_ORIGIN_REGEX,
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


@app.get("/api/accounts/{account_id}/journal", response_model=JournalEntryListOut)
def list_projectx_account_journal_entries(
    account_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    mood: JournalMood | None = None,
    q: str | None = Query(default=None, max_length=200),
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    _validate_journal_date_range(start_date=start_date, end_date=end_date)

    try:
        rows, total = list_journal_entries(
            db,
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            mood=mood,
            text_query=q,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [serialize_journal_entry(row) for row in rows],
            "total": total,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accounts/{account_id}/journal", response_model=JournalEntryCreateOut, status_code=201)
def create_projectx_account_journal_entry(
    account_id: int,
    payload: JournalEntryCreateIn,
    response: Response,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)

    try:
        row, already_existed = create_journal_entry(
            db,
            account_id=account_id,
            entry_date=payload.entry_date,
            title=payload.title,
            mood=payload.mood,
            tags=payload.tags,
            body=payload.body,
        )
        if already_existed:
            response.status_code = 200
        payload_out = serialize_journal_entry(row)
        payload_out["already_existed"] = already_existed
        return payload_out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/accounts/{account_id}/journal/days", response_model=JournalDaysOut)
def list_projectx_account_journal_days(
    account_id: int,
    start_date: date = Query(...),
    end_date: date = Query(...),
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    _validate_journal_date_range(start_date=start_date, end_date=end_date)

    try:
        days = list_journal_days(
            db,
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            include_archived=include_archived,
        )
        return {"days": days}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/accounts/{account_id}/journal/{entry_id}", response_model=JournalEntryOut)
def update_projectx_account_journal_entry(
    account_id: int,
    entry_id: int,
    payload: JournalEntryUpdateIn,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")
    if _journal_update_payload_is_empty(payload):
        raise HTTPException(status_code=400, detail="at least one field must be provided")

    try:
        if _journal_update_payload_is_archive_only(payload) and payload.is_archived is not None:
            if payload.is_archived:
                row = archive_journal_entry(db, account_id=account_id, entry_id=entry_id, version=payload.version)
            else:
                row = unarchive_journal_entry(db, account_id=account_id, entry_id=entry_id, version=payload.version)
        else:
            row = update_journal_entry(
                db,
                account_id=account_id,
                entry_id=entry_id,
                version=payload.version,
                entry_date=payload.entry_date,
                title=payload.title,
                mood=payload.mood,
                tags=payload.tags,
                body=payload.body,
                is_archived=payload.is_archived,
            )
        return serialize_journal_entry(row)
    except VersionConflictError as exc:
        return JSONResponse(
            status_code=409,
            content=jsonable_encoder(
                {
                    "detail": "version_conflict",
                    "server": serialize_journal_entry(exc.server_row),
                }
            ),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/accounts/{account_id}/journal/{entry_id}", status_code=204)
def delete_projectx_account_journal_entry(
    account_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    try:
        delete_journal_entry(db, account_id=account_id, entry_id=entry_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(status_code=204)


@app.post("/api/accounts/{account_id}/journal/{entry_id}/images", response_model=JournalImageOut)
async def upload_projectx_account_journal_image(
    account_id: int,
    entry_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    file_bytes = await file.read()
    await file.close()

    try:
        row = create_journal_entry_image(
            db,
            account_id=account_id,
            entry_id=entry_id,
            file_bytes=file_bytes,
            mime_type=file.content_type,
        )
        return serialize_journal_entry_image(
            row,
            url=_journal_image_url(image_id=int(row.id), account_id=int(row.account_id)),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/accounts/{account_id}/journal/{entry_id}/images", response_model=list[JournalImageOut])
def list_projectx_account_journal_images(
    account_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    try:
        rows = list_journal_entry_images(db, account_id=account_id, entry_id=entry_id)
        return [
            serialize_journal_entry_image(
                row,
                url=_journal_image_url(image_id=int(row.id), account_id=int(row.account_id)),
            )
            for row in rows
        ]
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/journal-images/{image_id}")
def serve_journal_image(
    image_id: int,
    account_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    if image_id <= 0:
        raise HTTPException(status_code=400, detail="image_id must be a positive integer")

    try:
        row = get_journal_entry_image(db, image_id=image_id, account_id=account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    path = get_journal_image_file_path(row.filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="journal image file not found")

    return FileResponse(path=path, media_type=row.mime_type, filename=Path(row.filename).name)


@app.delete("/api/accounts/{account_id}/journal/{entry_id}/images/{image_id}", status_code=204)
def delete_projectx_account_journal_image(
    account_id: int,
    entry_id: int,
    image_id: int,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")
    if image_id <= 0:
        raise HTTPException(status_code=400, detail="image_id must be a positive integer")

    try:
        delete_journal_entry_image(
            db,
            account_id=account_id,
            entry_id=entry_id,
            image_id=image_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(status_code=204)


@app.post("/api/accounts/{account_id}/journal/{entry_id}/pull-trade-stats", response_model=JournalEntryOut)
def pull_projectx_account_journal_trade_stats(
    account_id: int,
    entry_id: int,
    payload: PullTradeStatsIn,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    try:
        row = pull_journal_entry_trade_stats(
            db,
            account_id=account_id,
            entry_id=entry_id,
            trade_ids=payload.trade_ids,
            entry_date=payload.entry_date,
        )
        return serialize_journal_entry(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        ensure_trade_cache_for_request(
            db,
            account_id=account_id,
            start=start,
            end=end,
            refresh=refresh,
            client_factory=ProjectXClient.from_env,
        )

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


@app.get("/api/accounts/{account_id}/pnl-calendar", response_model=list[ProjectXPnlCalendarDayOut])
def get_projectx_account_pnl_calendar(
    account_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    all_time: bool = False,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    _validate_account_id(account_id)
    _validate_time_range(start=start, end=end)
    if all_time and (start is not None or end is not None):
        raise HTTPException(status_code=400, detail="all_time cannot be combined with start/end")

    use_default_window = not all_time and start is None and end is None
    if all_time:
        effective_start = None
        effective_end = None
    elif use_default_window:
        effective_end = datetime.now(timezone.utc)
        effective_start = _subtract_utc_months(effective_end, _DEFAULT_PNL_CALENDAR_LOOKBACK_MONTHS)
    else:
        effective_start = start
        effective_end = end

    try:
        # Avoid expensive provider backfills on routine page refreshes.
        # Use explicit refresh (or empty local cache) as the sync trigger.
        needs_sync = refresh or not has_local_trades(db, account_id)
        if needs_sync:
            client = ProjectXClient.from_env()
            refresh_account_trades(
                db,
                client,
                account_id=account_id,
                start=effective_start,
                end=effective_end,
            )

        return get_trade_event_pnl_calendar(
            db,
            account_id=account_id,
            start=effective_start,
            end=effective_end,
        )
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc


def _validate_account_id(account_id: int) -> None:
    if account_id <= 0:
        raise HTTPException(status_code=400, detail="account_id must be a positive integer")


def _validate_time_range(*, start: datetime | None, end: datetime | None) -> None:
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be before end")


def _validate_journal_date_range(*, start_date: date | None, end_date: date | None) -> None:
    try:
        validate_journal_date_range(start_date=start_date, end_date=end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _journal_update_payload_is_empty(payload: JournalEntryUpdateIn) -> bool:
    return (
        payload.entry_date is None
        and payload.title is None
        and payload.mood is None
        and payload.tags is None
        and payload.body is None
        and payload.is_archived is None
    )


def _journal_update_payload_is_archive_only(payload: JournalEntryUpdateIn) -> bool:
    return (
        payload.is_archived is not None
        and payload.entry_date is None
        and payload.title is None
        and payload.mood is None
        and payload.tags is None
        and payload.body is None
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _subtract_utc_months(value: datetime, months: int) -> datetime:
    months = max(0, int(months))
    if months == 0:
        return _as_utc(value)

    current = _as_utc(value)
    month_index = (current.year * 12 + (current.month - 1)) - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day)


def _journal_image_url(*, image_id: int, account_id: int) -> str:
    return f"/api/journal-images/{image_id}?account_id={account_id}"


def _to_http_exception(exc: ProjectXClientError) -> HTTPException:
    # Missing env or local configuration errors are server configuration issues.
    if exc.status_code is None:
        return HTTPException(status_code=500, detail=str(exc))

    # Upstream API errors should be surfaced as a gateway error to the frontend.
    return HTTPException(status_code=502, detail=str(exc))
