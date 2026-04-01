import calendar
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .auth import (
    AuthError,
    auth_required,
    authenticate_request_token,
    bind_authenticated_user,
    extract_access_token,
    get_authenticated_user_id,
    get_authenticated_user_or_default,
    reset_authenticated_user,
)
from .db import (
    get_db,
    guard_against_local_database_url,
    init_db,
    log_runtime_connection_targets,
    resolve_supabase_mode,
)
from .expense_schemas import (
    ExpenseCategory,
    ExpenseCreateIn,
    ExpenseListOut,
    ExpenseOut,
    ExpenseRange,
    ExpenseTotalsOut,
    ExpenseUpdateIn,
    WeekStart,
)
from .journal_schemas import (
    JournalEntryCreateIn,
    JournalEntryCreateOut,
    JournalEntryListOut,
    JournalDaysOut,
    JournalImageOut,
    JournalEntrySaveOut,
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
from .models import Expense, ProjectXTradeEvent, Trade
from .projectx_schemas import (
    AuthMeOut,
    ProjectXAccountMainOut,
    ProjectXAccountOut,
    ProjectXCredentialsStatusOut,
    ProjectXPointPayoffOut,
    ProjectXCredentialsUpsertIn,
    ProjectXAccountLastTradeOut,
    ProjectXPnlCalendarDayOut,
    ProjectXTradeOut,
    ProjectXTradeRefreshOut,
    ProjectXTradeSummaryOut,
    ProjectXTradeSummaryWithPointBasesOut,
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
    delete_journal_entry_image_record,
    get_journal_entry_image,
    get_journal_image_bytes,
    get_journal_image_file_path,
    list_journal_days,
    list_journal_entry_images,
    list_journal_entries,
    pull_journal_entry_trade_stats,
    serialize_journal_entry,
    serialize_journal_entry_save,
    serialize_journal_entry_image,
    unarchive_journal_entry,
    update_journal_entry,
    validate_date_range as validate_journal_date_range,
)
from .services.journal_storage import delete_journal_image as delete_journal_image_file, journal_storage_backend
from .services.projectx_accounts import (
    ACCOUNT_STATE_ACTIVE,
    ACCOUNT_STATE_MISSING,
    account_id_from_external_id,
    get_projectx_account_row,
    get_projectx_account_rows,
    set_main_projectx_account,
    should_include_account,
    sync_projectx_accounts,
)
from .services.projectx_credentials import (
    delete_projectx_credentials,
    get_projectx_credentials,
    has_projectx_credentials,
    ProjectXCredentialsUnavailable,
    upsert_projectx_credentials,
)
from .services.projectx_client import ProjectXClient, ProjectXClientError
from .services.instruments import POINTS_BASIS_SYMBOLS, normalize_points_basis
from .services.projectx_trades import (
    derive_trade_execution_lifecycles,
    ensure_trade_cache_for_request,
    get_trade_event_pnl_calendar,
    has_local_trades,
    list_trade_events,
    refresh_account_trades,
    serialize_trade_event,
    summarize_trade_events,
    summarize_trade_events_with_point_bases,
)

app = FastAPI(title="TopSignal API")
logger = logging.getLogger(__name__)
_DEFAULT_PNL_CALENDAR_LOOKBACK_MONTHS = 6
_LOCAL_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$"
_NEW_YORK_TZ = ZoneInfo("America/New_York")
_PRACTICE_ERROR_DETAIL = "practice_accounts_are_free"
_PAID_ACCOUNT_TYPES_FOR_150K = {"no_activation", "standard"}
_streaming_runtime = None
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
_ALLOW_ORIGIN_REGEX = os.getenv("ALLOWED_ORIGIN_REGEX", _LOCAL_ORIGIN_REGEX)
_EXPOSE_HEADERS = "Server-Timing, X-Server-Time-Ms, Content-Length"
_ALLOW_ORIGIN_PATTERN = re.compile(_ALLOW_ORIGIN_REGEX) if _ALLOW_ORIGIN_REGEX else None

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_ALLOW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[header.strip() for header in _EXPOSE_HEADERS.split(",")],
)


def _origin_is_allowed(origin: str) -> bool:
    if origin in _ALLOWED_ORIGINS:
        return True
    if _ALLOW_ORIGIN_PATTERN is not None and _ALLOW_ORIGIN_PATTERN.fullmatch(origin):
        return True
    return False


def _append_vary_origin_header(response: Response) -> None:
    existing_vary = response.headers.get("Vary")
    if not existing_vary:
        response.headers["Vary"] = "Origin"
        return
    values = [value.strip().lower() for value in existing_vary.split(",") if value.strip()]
    if "origin" in values:
        return
    response.headers["Vary"] = f"{existing_vary}, Origin"


def _apply_cors_headers(request: Request, response: Response) -> Response:
    origin = request.headers.get("origin")
    if not origin or not _origin_is_allowed(origin):
        return response

    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Expose-Headers"] = _EXPOSE_HEADERS
    _append_vary_origin_header(response)
    return response


def _is_authenticated_route(path: str) -> bool:
    return path.startswith("/api/") or path.startswith("/metrics/") or path == "/trades"


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    path = request.url.path
    should_time_request = _is_authenticated_route(path)
    started_at = perf_counter() if should_time_request else None

    def _with_timing_headers(response: Response) -> Response:
        if started_at is None:
            return response
        duration_ms = max((perf_counter() - started_at) * 1000.0, 0.0)
        duration_text = f"{duration_ms:.2f}"
        response.headers["Server-Timing"] = f"app;dur={duration_text}"
        response.headers["X-Server-Time-Ms"] = duration_text
        return response

    if not _is_authenticated_route(path):
        response = await call_next(request)
        return _with_timing_headers(response)
    if request.method.upper() == "OPTIONS":
        response = await call_next(request)
        return _with_timing_headers(response)

    token = extract_access_token(request)
    should_require_auth = auth_required()
    user = None

    if token:
        try:
            user = authenticate_request_token(token)
        except AuthError as exc:
            response = JSONResponse(status_code=401, content={"detail": str(exc)})
            return _with_timing_headers(_apply_cors_headers(request, response))
    elif should_require_auth:
        response = JSONResponse(status_code=401, content={"detail": "missing_bearer_token"})
        return _with_timing_headers(_apply_cors_headers(request, response))
    else:
        user = get_authenticated_user_or_default()

    context_token = bind_authenticated_user(user)
    try:
        response = await call_next(request)
        return _with_timing_headers(response)
    finally:
        reset_authenticated_user(context_token)


@app.on_event("startup")
def on_startup():
    guard_against_local_database_url()
    log_runtime_connection_targets()
    init_db()
    _start_streaming_runtime_if_enabled()


@app.on_event("shutdown")
def on_shutdown():
    _stop_streaming_runtime()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/me", response_model=AuthMeOut)
def get_auth_me():
    user = get_authenticated_user_or_default()
    return {
        "user_id": user.user_id,
        "email": user.email,
    }


@app.get("/api/me/providers/projectx/credentials/status", response_model=ProjectXCredentialsStatusOut)
def get_projectx_credentials_status(db: Session = Depends(get_db)):
    user_id = get_authenticated_user_id()
    return {"configured": has_projectx_credentials(db, user_id=user_id)}


@app.put("/api/me/providers/projectx/credentials", status_code=204)
def put_projectx_credentials(payload: ProjectXCredentialsUpsertIn, db: Session = Depends(get_db)):
    user_id = get_authenticated_user_id()
    try:
        upsert_projectx_credentials(
            db,
            user_id=user_id,
            username=payload.username,
            api_key=payload.api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@app.delete("/api/me/providers/projectx/credentials", status_code=204)
def delete_projectx_credentials_for_user(db: Session = Depends(get_db)):
    user_id = get_authenticated_user_id()
    delete_projectx_credentials(db, user_id=user_id)
    return Response(status_code=204)


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


@app.post("/api/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(
    payload: ExpenseCreateIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if payload.account_id is not None:
        _validate_account_id(payload.account_id)

    amount_cents = _resolve_amount_cents(payload.amount_cents)
    description = _normalize_expense_description(payload.description)
    tags = _normalize_expense_tags(payload.tags)

    _validate_expense_practice_guard(
        account_type=payload.account_type,
        is_practice=payload.is_practice,
        description=description,
        tags=tags,
        plan_size=payload.plan_size,
    )
    _validate_expense_amount(amount_cents=amount_cents, category=payload.category)

    row = Expense(
        user_id=user_id,
        account_id=payload.account_id,
        provider=payload.provider,
        expense_date=payload.expense_date,
        amount_cents=amount_cents,
        currency=payload.currency,
        category=payload.category,
        account_type=payload.account_type,
        plan_size=payload.plan_size,
        description=description,
        tags=tags,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _to_expense_integrity_http_exception(exc) from exc

    db.refresh(row)
    return ExpenseOut.model_validate(row)


@app.get("/api/expenses", response_model=ExpenseListOut)
def list_expenses(
    start_date: date | None = None,
    end_date: date | None = None,
    account_id: int | None = None,
    category: ExpenseCategory | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be before or equal to end_date")
    if account_id is not None:
        _validate_account_id(account_id)

    query = db.query(Expense).filter(Expense.user_id == user_id)
    if start_date is not None:
        query = query.filter(Expense.expense_date >= start_date)
    if end_date is not None:
        query = query.filter(Expense.expense_date <= end_date)
    if account_id is not None:
        query = query.filter(Expense.account_id == account_id)
    if category is not None:
        query = query.filter(Expense.category == category)

    total = query.count()
    rows = query.order_by(Expense.expense_date.desc(), Expense.id.desc()).offset(offset).limit(limit).all()
    return {
        "items": [ExpenseOut.model_validate(row) for row in rows],
        "total": total,
    }


@app.get("/api/expenses/totals", response_model=ExpenseTotalsOut)
def get_expense_totals(
    range: ExpenseRange = Query(...),
    account_id: int | None = None,
    week_start: WeekStart = Query(default="monday"),
    start_date: date | None = None,
    end_date: date | None = None,
    start_created_at: datetime | None = None,
    end_created_at: datetime | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)

    if start_date is not None or end_date is not None:
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=400, detail="start_date must be before or equal to end_date")
        effective_start_date = start_date
        effective_end_date = end_date or datetime.now(_NEW_YORK_TZ).date()
    else:
        effective_start_date, effective_end_date = _resolve_expense_range_dates(range=range, week_start=week_start)

    if start_created_at and end_created_at and start_created_at > end_created_at:
        raise HTTPException(status_code=400, detail="start_created_at must be before or equal to end_created_at")

    query = (
        db.query(Expense)
        .filter(Expense.user_id == user_id)
        .filter(Expense.expense_date <= effective_end_date)
    )
    if effective_start_date is not None:
        query = query.filter(Expense.expense_date >= effective_start_date)
    if start_created_at is not None:
        query = query.filter(Expense.created_at >= _as_utc(start_created_at))
    if end_created_at is not None:
        query = query.filter(Expense.created_at <= _as_utc(end_created_at))
    if account_id is not None:
        query = query.filter(Expense.account_id == account_id)

    total_amount_cents, count = query.with_entities(
        func.coalesce(func.sum(Expense.amount_cents), 0),
        func.count(Expense.id),
    ).one()

    grouped_rows = query.with_entities(
        Expense.category,
        func.coalesce(func.sum(Expense.amount_cents), 0),
        func.count(Expense.id),
    ).group_by(Expense.category).all()

    by_category = {
        str(category): {
            "amount": _cents_to_dollars(cents),
            "amount_cents": int(cents),
            "count": int(row_count),
        }
        for category, cents, row_count in grouped_rows
    }

    return {
        "range": range,
        "start_date": effective_start_date,
        "end_date": effective_end_date,
        "total_amount": _cents_to_dollars(total_amount_cents),
        "total_amount_cents": int(total_amount_cents),
        "by_category": by_category,
        "count": int(count),
    }


@app.patch("/api/expenses/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdateIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if expense_id <= 0:
        raise HTTPException(status_code=400, detail="expense_id must be a positive integer")

    row = (
        db.query(Expense)
        .filter(Expense.user_id == user_id)
        .filter(Expense.id == expense_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="expense not found")

    fields = payload.model_fields_set
    if "expense_date" in fields:
        if payload.expense_date is None:
            raise HTTPException(status_code=400, detail="expense_date cannot be null")
        row.expense_date = payload.expense_date
    if "category" in fields:
        if payload.category is None:
            raise HTTPException(status_code=400, detail="category cannot be null")
        row.category = payload.category
    if "amount_cents" in fields:
        if payload.amount_cents is None:
            raise HTTPException(status_code=400, detail="amount_cents cannot be null")
        row.amount_cents = payload.amount_cents
    if "description" in fields:
        row.description = _normalize_expense_description(payload.description)
    if "tags" in fields:
        row.tags = _normalize_expense_tags(payload.tags)
    if "account_id" in fields:
        if payload.account_id is not None:
            _validate_account_id(payload.account_id)
        row.account_id = payload.account_id
    if "account_type" in fields:
        row.account_type = payload.account_type
    if "plan_size" in fields:
        row.plan_size = payload.plan_size

    _validate_expense_practice_guard(
        account_type=row.account_type,
        is_practice=payload.is_practice is True,
        description=row.description,
        tags=row.tags,
        plan_size=row.plan_size,
    )
    _validate_expense_amount(amount_cents=row.amount_cents, category=row.category)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _to_expense_integrity_http_exception(exc) from exc

    db.refresh(row)
    return ExpenseOut.model_validate(row)


@app.delete("/api/expenses/{expense_id}", status_code=204)
def delete_expense(
    expense_id: int,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if expense_id <= 0:
        raise HTTPException(status_code=400, detail="expense_id must be a positive integer")

    row = (
        db.query(Expense)
        .filter(Expense.user_id == user_id)
        .filter(Expense.id == expense_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="expense not found")

    db.delete(row)
    db.commit()
    return Response(status_code=204)


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
def list_projectx_accounts(
    show_inactive: bool = False,
    show_missing: bool = False,
    only_active_accounts: bool | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if only_active_accounts is not None:
        show_inactive = not only_active_accounts
        show_missing = not only_active_accounts

    provider_accounts: list[dict[str, object]] = []
    try:
        client = _projectx_client_for_user(db, user_id=user_id)
        provider_accounts = client.list_accounts(only_active_accounts=False)
        sync_projectx_accounts(
            db,
            provider_accounts,
            user_id=user_id,
            missing_buffer=timedelta(
                seconds=_read_int_env("PROJECTX_ACCOUNT_MISSING_BUFFER_SECONDS", 300),
            ),
        )
        db.commit()
    except ProjectXClientError as exc:
        db.rollback()
        raise _to_http_exception(exc) from exc
    except Exception:
        db.rollback()
        raise

    provider_by_external_id = {
        str(account["id"]): account
        for account in provider_accounts
        if isinstance(account, dict) and account.get("id") is not None
    }
    rows = get_projectx_account_rows(db, user_id=user_id)
    visible_rows = [
        row
        for row in rows
        if should_include_account(
            row,
            show_inactive=show_inactive,
            show_missing=show_missing,
        )
    ]

    account_ids = [
        account_id
        for account_id in (account_id_from_external_id(row.external_id) for row in visible_rows)
        if account_id is not None
    ]
    last_trade_by_account_id = _load_last_trade_timestamps(db, user_id=user_id, account_ids=account_ids)

    payload: list[dict[str, object]] = []
    for row in visible_rows:
        account_id = account_id_from_external_id(row.external_id)
        if account_id is None:
            continue

        provider_payload = provider_by_external_id.get(row.external_id, {})
        provider_name = provider_payload.get("name") if isinstance(provider_payload, dict) else None
        provider_balance = provider_payload.get("balance") if isinstance(provider_payload, dict) else None
        provider_can_trade = provider_payload.get("can_trade") if isinstance(provider_payload, dict) else None
        provider_is_visible = provider_payload.get("is_visible") if isinstance(provider_payload, dict) else None

        account_state = row.account_state or ACCOUNT_STATE_ACTIVE
        can_trade = provider_can_trade if isinstance(provider_can_trade, bool) else row.can_trade
        is_visible = provider_is_visible if isinstance(provider_is_visible, bool) else row.is_visible

        payload.append(
            {
                "id": account_id,
                "name": str(provider_name or row.name or f"Account {account_id}"),
                "balance": float(provider_balance) if provider_balance is not None else 0.0,
                "status": account_state,
                "account_state": account_state,
                "is_main": bool(row.is_main),
                "can_trade": can_trade,
                "is_visible": is_visible,
                "last_trade_at": last_trade_by_account_id.get(account_id),
            }
        )

    payload.sort(key=lambda row: (-int(bool(row["is_main"])), int(row["id"])))
    return payload


@app.post("/api/accounts/{account_id}/main", response_model=ProjectXAccountMainOut)
def set_projectx_main_account(
    account_id: int,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)

    try:
        set_main_projectx_account(db, account_id, user_id=user_id)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "account_id": account_id,
        "is_main": True,
    }


@app.get("/api/accounts/{account_id}/last-trade", response_model=ProjectXAccountLastTradeOut)
def get_projectx_account_last_trade(
    account_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)

    local_timestamp = _load_last_trade_timestamps(db, user_id=user_id, account_ids=[account_id]).get(account_id)
    if local_timestamp is not None and not refresh:
        return {
            "account_id": account_id,
            "last_trade_at": local_timestamp,
            "source": "local",
        }

    try:
        client = _projectx_client_for_user(db, user_id=user_id)
        provider_timestamp = client.fetch_last_trade_timestamp(
            account_id,
            lookback_days=_read_int_env("PROJECTX_LAST_TRADE_LOOKBACK_DAYS", 3650),
        )
    except ProjectXClientError as exc:
        if local_timestamp is not None:
            return {
                "account_id": account_id,
                "last_trade_at": local_timestamp,
                "source": "local",
            }
        raise _to_http_exception(exc) from exc

    if provider_timestamp is not None:
        return {
            "account_id": account_id,
            "last_trade_at": _as_utc(provider_timestamp),
            "source": "provider",
        }

    if local_timestamp is not None:
        return {
            "account_id": account_id,
            "last_trade_at": local_timestamp,
            "source": "local",
        }

    return {
        "account_id": account_id,
        "last_trade_at": None,
        "source": "none",
    }


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
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    _validate_journal_date_range(start_date=start_date, end_date=end_date)

    try:
        rows, total = list_journal_entries(
            db,
            user_id=user_id,
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
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)

    try:
        row, already_existed = create_journal_entry(
            db,
            user_id=user_id,
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
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    _validate_journal_date_range(start_date=start_date, end_date=end_date)

    try:
        days = list_journal_days(
            db,
            user_id=user_id,
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            include_archived=include_archived,
        )
        return {"days": days}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/accounts/{account_id}/journal/{entry_id}", response_model=JournalEntrySaveOut)
def update_projectx_account_journal_entry(
    account_id: int,
    entry_id: int,
    payload: JournalEntryUpdateIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")
    if _journal_update_payload_is_empty(payload):
        raise HTTPException(status_code=400, detail="at least one field must be provided")

    try:
        if _journal_update_payload_is_archive_only(payload) and payload.is_archived is not None:
            if payload.is_archived:
                row = archive_journal_entry(
                    db,
                    user_id=user_id,
                    account_id=account_id,
                    entry_id=entry_id,
                    version=payload.version,
                )
            else:
                row = unarchive_journal_entry(
                    db,
                    user_id=user_id,
                    account_id=account_id,
                    entry_id=entry_id,
                    version=payload.version,
                )
        else:
            row = update_journal_entry(
                db,
                user_id=user_id,
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
        return serialize_journal_entry_save(row)
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
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    try:
        delete_journal_entry(db, user_id=user_id, account_id=account_id, entry_id=entry_id)
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
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    file_bytes = await file.read()
    await file.close()

    try:
        row = create_journal_entry_image(
            db,
            user_id=user_id,
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
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    try:
        rows = list_journal_entry_images(db, user_id=user_id, account_id=account_id, entry_id=entry_id)
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
    user_id = get_authenticated_user_id()
    if image_id <= 0:
        raise HTTPException(status_code=400, detail="image_id must be a positive integer")

    try:
        row = get_journal_entry_image(db, user_id=user_id, image_id=image_id, account_id=account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if journal_storage_backend() == "supabase":
        try:
            file_bytes = get_journal_image_bytes(row.filename)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="journal image file not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(content=file_bytes, media_type=row.mime_type)

    path = get_journal_image_file_path(row.filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="journal image file not found")

    return FileResponse(path=path, media_type=row.mime_type, filename=Path(row.filename).name)


@app.delete("/api/accounts/{account_id}/journal/{entry_id}/images/{image_id}", status_code=204)
def delete_projectx_account_journal_image(
    account_id: int,
    entry_id: int,
    image_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")
    if image_id <= 0:
        raise HTTPException(status_code=400, detail="image_id must be a positive integer")

    try:
        filename = delete_journal_entry_image_record(
            db,
            user_id=user_id,
            account_id=account_id,
            entry_id=entry_id,
            image_id=image_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    background_tasks.add_task(_delete_journal_image_file_safely, object_key=filename)
    return Response(status_code=204)


@app.post("/api/accounts/{account_id}/journal/{entry_id}/pull-trade-stats", response_model=JournalEntryOut)
def pull_projectx_account_journal_trade_stats(
    account_id: int,
    entry_id: int,
    payload: PullTradeStatsIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="entry_id must be a positive integer")

    def sync_window(start: datetime | None, end: datetime | None) -> None:
        client = _projectx_client_for_user(db, user_id=user_id)
        refresh_account_trades(
            db,
            client,
            user_id=user_id,
            account_id=account_id,
            start=start,
            end=end,
        )

    try:
        try:
            row = pull_journal_entry_trade_stats(
                db,
                user_id=user_id,
                account_id=account_id,
                entry_id=entry_id,
                trade_ids=payload.trade_ids,
                entry_date=payload.entry_date,
                start_date=payload.start_date,
                end_date=payload.end_date,
                before_query_sync=sync_window,
            )
        except ProjectXClientError as exc:
            if not _should_fallback_to_local_metrics(db, user_id=user_id, account_id=account_id, exc=exc):
                raise

            row = pull_journal_entry_trade_stats(
                db,
                user_id=user_id,
                account_id=account_id,
                entry_id=entry_id,
                trade_ids=payload.trade_ids,
                entry_date=payload.entry_date,
                start_date=payload.start_date,
                end_date=payload.end_date,
            )
        return serialize_journal_entry(row)
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc
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
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    _validate_time_range(start=start, end=end)

    try:
        client = _projectx_client_for_user(db, user_id=user_id)
        return refresh_account_trades(
            db,
            client,
            user_id=user_id,
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
    limit: int = 200,
    start: datetime | None = None,
    end: datetime | None = None,
    symbol: str | None = None,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    if symbol is not None and len(symbol) > 50:
        raise HTTPException(status_code=400, detail="symbol must be <= 50 characters")
    _validate_time_range(start=start, end=end)

    try:
        try:
            ensure_trade_cache_for_request(
                db,
                user_id=user_id,
                account_id=account_id,
                start=start,
                end=end,
                refresh=refresh,
                client_factory=lambda: _projectx_client_for_user(db, user_id=user_id),
            )
        except ProjectXClientError as exc:
            if not _should_fallback_to_local_metrics(db, user_id=user_id, account_id=account_id, exc=exc):
                raise

        rows = list_trade_events(
            db,
            account_id=account_id,
            user_id=user_id,
            limit=limit,
            start=start,
            end=end,
            symbol_query=symbol,
        )
        lifecycle_by_trade_id = derive_trade_execution_lifecycles(
            db,
            user_id=user_id,
            account_id=account_id,
            closed_rows=rows,
        )
        return [
            serialize_trade_event(
                row,
                lifecycle=lifecycle_by_trade_id.get(int(row.id)),
            )
            for row in rows
        ]
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc


@app.get("/api/accounts/{account_id}/summary", response_model=ProjectXTradeSummaryOut)
def get_projectx_account_summary(
    account_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    refresh: bool = False,
    points_basis: str = Query(default="auto", alias="pointsBasis"),
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    _validate_time_range(start=start, end=end)
    raw_points_basis = points_basis if isinstance(points_basis, str) else "auto"
    try:
        normalized_points_basis = normalize_points_basis(raw_points_basis)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        if refresh or not has_local_trades(db, account_id, user_id=user_id):
            client = _projectx_client_for_user(db, user_id=user_id)
            try:
                refresh_account_trades(
                    db,
                    client,
                    user_id=user_id,
                    account_id=account_id,
                    start=start,
                    end=end,
                )
            except ProjectXClientError as exc:
                if not _should_fallback_to_local_metrics(db, user_id=user_id, account_id=account_id, exc=exc):
                    raise

        return summarize_trade_events(
            db,
            account_id=account_id,
            user_id=user_id,
            start=start,
            end=end,
            points_basis=normalized_points_basis,
        )
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc


@app.get("/api/accounts/{account_id}/summary-with-point-bases", response_model=ProjectXTradeSummaryWithPointBasesOut)
def get_projectx_account_summary_with_point_bases(
    account_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    _validate_time_range(start=start, end=end)

    point_bases = [normalize_points_basis(basis) for basis in POINTS_BASIS_SYMBOLS]

    try:
        if refresh or not has_local_trades(db, account_id, user_id=user_id):
            client = _projectx_client_for_user(db, user_id=user_id)
            try:
                refresh_account_trades(
                    db,
                    client,
                    user_id=user_id,
                    account_id=account_id,
                    start=start,
                    end=end,
                )
            except ProjectXClientError as exc:
                if not _should_fallback_to_local_metrics(db, user_id=user_id, account_id=account_id, exc=exc):
                    raise

        summary, point_payoff_by_basis = summarize_trade_events_with_point_bases(
            db,
            account_id=account_id,
            user_id=user_id,
            start=start,
            end=end,
            point_bases=point_bases,
        )
        return {
            "summary": summary,
            "point_payoff_by_basis": {
                basis: ProjectXPointPayoffOut(**values)
                for basis, values in point_payoff_by_basis.items()
            },
        }
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
    user_id = get_authenticated_user_id()
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
        needs_sync = refresh or not has_local_trades(db, account_id, user_id=user_id)
        if needs_sync:
            client = _projectx_client_for_user(db, user_id=user_id)
            try:
                refresh_account_trades(
                    db,
                    client,
                    user_id=user_id,
                    account_id=account_id,
                    start=effective_start,
                    end=effective_end,
                )
            except ProjectXClientError as exc:
                if not _should_fallback_to_local_metrics(db, user_id=user_id, account_id=account_id, exc=exc):
                    raise

        return get_trade_event_pnl_calendar(
            db,
            account_id=account_id,
            user_id=user_id,
            start=effective_start,
            end=effective_end,
        )
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc


def _projectx_client_for_user(db: Session, *, user_id: str) -> ProjectXClient:
    allow_legacy_env = _allow_legacy_projectx_env_credentials()
    try:
        credentials = get_projectx_credentials(db, user_id=user_id)
    except OperationalError:
        db.rollback()
        if allow_legacy_env:
            return ProjectXClient.from_env()
        raise HTTPException(status_code=500, detail="provider_credentials_table_missing")
    except ProjectXCredentialsUnavailable as exc:
        db.rollback()
        if allow_legacy_env:
            logger.warning(
                "ProjectX stored credentials unavailable for user %s; falling back to env credentials: %s",
                user_id,
                exc,
            )
            return ProjectXClient.from_env()
        raise HTTPException(status_code=500, detail="projectx_credentials_unavailable") from exc

    if credentials is None:
        if allow_legacy_env:
            return ProjectXClient.from_env()
        raise HTTPException(status_code=400, detail="projectx_credentials_not_configured")

    base_url = _first_nonempty_env(
        "PROJECTX_API_BASE_URL",
        "PROJECTX_BASE_URL",
        "PROJECTX_GATEWAY_URL",
        "TOPSTEP_API_BASE_URL",
        "TOPSTEPX_API_BASE_URL",
    )
    if not base_url:
        raise HTTPException(status_code=500, detail="projectx_api_base_url_not_configured")

    return ProjectXClient(
        base_url=base_url,
        username=credentials.username,
        api_key=credentials.api_key,
    )


def _allow_legacy_projectx_env_credentials() -> bool:
    if os.getenv("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS") is not None:
        return _read_bool_env("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", True)
    if not auth_required():
        return True
    if _uses_local_only_allowed_origins():
        return True
    return resolve_supabase_mode() == "local"


def _uses_local_only_allowed_origins() -> bool:
    allow_origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX", _LOCAL_ORIGIN_REGEX)
    if allow_origin_regex and allow_origin_regex != _LOCAL_ORIGIN_REGEX:
        return False
    allowed_origins = [
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ]
    return all(_is_local_origin(origin) for origin in allowed_origins)


def _is_local_origin(origin: str) -> bool:
    return bool(re.fullmatch(_LOCAL_ORIGIN_REGEX, origin))


def _resolve_amount_cents(amount_cents: int | None) -> int:
    if amount_cents is None:
        raise HTTPException(status_code=400, detail="amount_cents is required")
    return int(amount_cents)


def _normalize_expense_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_expense_tags(values: list[str] | None) -> list[str]:
    if not values:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = (raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


def _contains_practice(value: str | None) -> bool:
    if not value:
        return False
    return "practice" in value.lower()


def _validate_expense_practice_guard(
    *,
    account_type: str | None,
    is_practice: bool,
    description: str | None,
    tags: list[str] | None,
    plan_size: str | None,
) -> None:
    if is_practice:
        raise HTTPException(status_code=400, detail=_PRACTICE_ERROR_DETAIL)

    has_practice_text = _contains_practice(description) or any(_contains_practice(tag) for tag in tags or [])
    if account_type == "practice" or has_practice_text:
        raise HTTPException(status_code=400, detail=_PRACTICE_ERROR_DETAIL)

    if plan_size == "150k" and account_type not in _PAID_ACCOUNT_TYPES_FOR_150K:
        raise HTTPException(
            status_code=400,
            detail="plan_size_150k_requires_account_type_no_activation_or_standard",
        )


def _validate_expense_amount(*, amount_cents: int | None, category: str | None) -> None:
    if amount_cents is None:
        raise HTTPException(status_code=400, detail="amount_cents is required")
    if category is None:
        raise HTTPException(status_code=400, detail="category is required")
    if amount_cents < 0:
        raise HTTPException(status_code=400, detail="amount_cents must be >= 0")
    if category != "other" and amount_cents <= 0:
        raise HTTPException(status_code=400, detail="amount_cents must be > 0 for non-other categories")


def _resolve_expense_range_dates(*, range: ExpenseRange, week_start: WeekStart) -> tuple[date | None, date]:
    today_local = datetime.now(_NEW_YORK_TZ).date()
    end_date = today_local
    if range == "week":
        if week_start == "sunday":
            days_since_week_start = (today_local.weekday() + 1) % 7
        else:
            days_since_week_start = today_local.weekday()
        start_date = today_local - timedelta(days=days_since_week_start)
    elif range == "month":
        start_date = today_local.replace(day=1)
    elif range == "ytd":
        start_date = date(today_local.year, 1, 1)
    else:
        start_date = None
    return start_date, end_date


def _cents_to_dollars(amount_cents: int) -> float:
    return round(int(amount_cents) / 100, 2)


def _to_expense_integrity_http_exception(exc: IntegrityError) -> HTTPException:
    message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
    if "uq_expenses_dedupe" in message or "unique constraint" in message:
        return HTTPException(status_code=409, detail="duplicate_expense")
    return HTTPException(status_code=400, detail="invalid_expense_payload")


def _first_nonempty_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


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


def _load_last_trade_timestamps(db: Session, *, user_id: str, account_ids: list[int]) -> dict[int, datetime]:
    unique_ids = sorted({account_id for account_id in account_ids if account_id > 0})
    if not unique_ids:
        return {}

    rows = (
        db.query(
            ProjectXTradeEvent.account_id.label("account_id"),
            func.max(ProjectXTradeEvent.trade_timestamp).label("last_trade_at"),
        )
        .filter(ProjectXTradeEvent.user_id == user_id)
        .filter(ProjectXTradeEvent.account_id.in_(unique_ids))
        .group_by(ProjectXTradeEvent.account_id)
        .all()
    )

    output: dict[int, datetime] = {}
    for row in rows:
        if row.last_trade_at is None:
            continue
        output[int(row.account_id)] = _as_utc(row.last_trade_at)
    return output


def _should_fallback_to_local_metrics(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    exc: ProjectXClientError,
) -> bool:
    if exc.status_code not in {403, 404}:
        return False

    account = get_projectx_account_row(db, account_id, user_id=user_id)
    if account is None:
        return False

    return account.account_state == ACCOUNT_STATE_MISSING


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _start_streaming_runtime_if_enabled() -> None:
    global _streaming_runtime
    if not _read_bool_env("PROJECTX_STREAMING_ENABLED", False):
        return
    if _streaming_runtime is not None:
        return

    from .services.projectx_streaming_runtime import create_streaming_runtime

    _streaming_runtime = create_streaming_runtime()
    _streaming_runtime.start()


def _stop_streaming_runtime() -> None:
    global _streaming_runtime
    if _streaming_runtime is None:
        return
    _streaming_runtime.stop()
    _streaming_runtime = None


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


def _delete_journal_image_file_safely(*, object_key: str) -> None:
    try:
        delete_journal_image_file(object_key=object_key)
    except Exception:
        logger.exception("journal_image_background_delete_failed", extra={"object_key": object_key})


def _journal_image_url(*, image_id: int, account_id: int) -> str:
    return f"/api/journal-images/{image_id}?account_id={account_id}"


def _to_http_exception(exc: ProjectXClientError) -> HTTPException:
    # Missing env or local configuration errors are server configuration issues.
    if exc.status_code is None:
        return HTTPException(status_code=500, detail=str(exc))

    # Upstream API errors should be surfaced as a gateway error to the frontend.
    return HTTPException(status_code=502, detail=str(exc))
