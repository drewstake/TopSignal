import calendar
import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
    AIJournalRecapIn,
    AIJournalRecapOut,
    JournalEntryCreateIn,
    JournalEntryCreateOut,
    JournalEntryListOut,
    JournalDaysOut,
    JournalImageOut,
    JournalMergeIn,
    JournalMergeOut,
    JournalEntrySaveOut,
    JournalEntryUpdateIn,
    JournalEntryOut,
    JournalMood,
    PullTradeStatsIn,
)
from .bot_schemas import (
    BotActivityOut,
    BotConfigCreateIn,
    BotConfigListOut,
    BotConfigOut,
    BotConfigUpdateIn,
    BotEvaluationOut,
    BotRunOut,
    BotStartIn,
    ProjectXContractOut,
    ProjectXMarketCandleOut,
)
from .trade_plan_schemas import TradeEvaluationResultOut, TradePlanEvaluationIn
from .metrics_schemas import (
    BehaviorMetricsOut,
    DayPnlOut,
    HourPnlOut,
    StreakMetricsOut,
    SummaryMetricsOut,
    SymbolPnlOut,
)
from .models import Expense, Payout, ProjectXMarketCandle, ProjectXTradeEvent, Trade
from .payout_schemas import PayoutCreateIn, PayoutListOut, PayoutOut, PayoutTotalsOut, PayoutUpdateIn
from .projectx_schemas import (
    AuthMeOut,
    ProjectXAccountMainOut,
    ProjectXAccountRenameIn,
    ProjectXAccountRenameOut,
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
    MAX_JOURNAL_IMAGE_BYTES,
    JournalEntryDateConflictError,
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
    merge_journal_entries,
    pull_journal_entry_trade_stats,
    serialize_journal_entry,
    serialize_journal_entry_save,
    serialize_journal_entry_image,
    unarchive_journal_entry,
    update_journal_entry,
    validate_date_range as validate_journal_date_range,
)
from .services.gemini_client import GeminiClientError
from .services.journal_ai_recap import generate_ai_journal_recap
from .services.journal_storage import delete_journal_image as delete_journal_image_file, journal_storage_backend
from .services.projectx_accounts import (
    ACCOUNT_STATE_ACTIVE,
    ACCOUNT_STATE_MISSING,
    account_id_from_external_id,
    get_projectx_account_row,
    get_projectx_account_rows,
    normalize_projectx_account_external_id,
    resolve_projectx_account_effective_name,
    resolve_projectx_account_provider_name,
    set_projectx_account_display_name,
    set_main_projectx_account,
    should_include_account,
    sync_projectx_accounts,
)
from .services.projectx_credentials import (
    delete_projectx_credentials,
    get_projectx_credentials,
    has_projectx_credentials,
    ProjectXCredentialsEncryptionKeyMissing,
    ProjectXCredentialsUnavailable,
    upsert_projectx_credentials,
)
from .services.projectx_client import ProjectXClient, ProjectXClientError
from .services.instruments import POINTS_BASIS_SYMBOLS, normalize_points_basis
from .services.projectx_trades import (
    derive_trade_execution_lifecycles,
    ensure_trade_cache_for_request,
    get_trade_event_pnl_calendar,
    list_trade_events,
    refresh_account_trades,
    serialize_trade_event,
    summarize_trade_events,
    summarize_trade_events_with_point_bases,
)
from .services.bot_service import (
    _looks_like_projectx_contract_id,
    create_bot_config,
    delete_bot_config,
    evaluate_bot_config,
    fetch_and_store_market_candles,
    get_bot_activity,
    get_bot_config,
    list_market_candles,
    list_bot_configs,
    market_candle_cache_covers_request,
    market_candle_cache_needs_refresh,
    market_candle_rows_are_stale,
    next_market_candle_fetch_start,
    prune_market_candle_cache_range,
    resolve_market_contract,
    serialize_bot_config,
    serialize_bot_decision,
    serialize_bot_order_attempt,
    serialize_bot_risk_event,
    serialize_bot_run,
    serialize_evaluation,
    serialize_market_candle,
    start_bot_run,
    stop_latest_bot_run,
    update_bot_config,
)
from .services.trade_plan_evaluator import MarketContext, TradePlan, TradePlanEvaluator

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


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    guard_against_local_database_url()
    log_runtime_connection_targets()
    init_db()
    _start_streaming_runtime_if_enabled()
    try:
        yield
    finally:
        _stop_streaming_runtime()


app = FastAPI(title="TopSignal API", lifespan=app_lifespan)

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
    return path.startswith("/api/") or path.startswith("/metrics/") or path.startswith("/projectx/") or path == "/trades"


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
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)

    query = db.query(Trade).filter(Trade.user_id == user_id)
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
    _validate_date_range(start_date=start_date, end_date=end_date)
    _validate_pagination(limit=limit, offset=offset, max_limit=500)
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
        _validate_date_range(start_date=start_date, end_date=end_date)
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

    grouped_rows = query.with_entities(
        Expense.category,
        func.coalesce(func.sum(Expense.amount_cents), 0),
        func.count(Expense.id),
    ).group_by(Expense.category).all()

    total_amount_cents = sum(int(cents) for _, cents, _ in grouped_rows)
    count = sum(int(row_count) for _, _, row_count in grouped_rows)

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
        "total_amount_cents": total_amount_cents,
        "by_category": by_category,
        "count": count,
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
    next_expense_date = row.expense_date
    next_category = row.category
    next_amount_cents = row.amount_cents
    next_description = row.description
    next_tags = list(row.tags or [])
    next_account_id = row.account_id
    next_account_type = row.account_type
    next_plan_size = row.plan_size

    if "expense_date" in fields:
        if payload.expense_date is None:
            raise HTTPException(status_code=400, detail="expense_date cannot be null")
        next_expense_date = payload.expense_date
    if "category" in fields:
        if payload.category is None:
            raise HTTPException(status_code=400, detail="category cannot be null")
        next_category = payload.category
    if "amount_cents" in fields:
        if payload.amount_cents is None:
            raise HTTPException(status_code=400, detail="amount_cents cannot be null")
        next_amount_cents = payload.amount_cents
    if "description" in fields:
        next_description = _normalize_expense_description(payload.description)
    if "tags" in fields:
        next_tags = _normalize_expense_tags(payload.tags)
    if "account_id" in fields:
        if payload.account_id is not None:
            _validate_account_id(payload.account_id)
        next_account_id = payload.account_id
    if "account_type" in fields:
        next_account_type = payload.account_type
    if "plan_size" in fields:
        next_plan_size = payload.plan_size

    _validate_expense_practice_guard(
        account_type=next_account_type,
        is_practice=payload.is_practice is True,
        description=next_description,
        tags=next_tags,
        plan_size=next_plan_size,
    )
    _validate_expense_amount(amount_cents=next_amount_cents, category=next_category)

    row.expense_date = next_expense_date
    row.category = next_category
    row.amount_cents = next_amount_cents
    row.description = next_description
    row.tags = next_tags
    row.account_id = next_account_id
    row.account_type = next_account_type
    row.plan_size = next_plan_size

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


@app.post("/api/payouts", response_model=PayoutOut, status_code=201)
def create_payout(
    payload: PayoutCreateIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    amount_cents = _resolve_amount_cents(payload.amount_cents)
    notes = _normalize_payout_notes(payload.notes)

    _validate_payout_amount(amount_cents)

    row = Payout(
        user_id=user_id,
        payout_date=payload.payout_date,
        amount_cents=amount_cents,
        currency=payload.currency,
        notes=notes,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _to_payout_integrity_http_exception(exc) from exc

    db.refresh(row)
    return PayoutOut.model_validate(row)


@app.get("/api/payouts", response_model=PayoutListOut)
def list_payouts(
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_date_range(start_date=start_date, end_date=end_date)
    _validate_pagination(limit=limit, offset=offset, max_limit=500)

    query = db.query(Payout).filter(Payout.user_id == user_id)
    if start_date is not None:
        query = query.filter(Payout.payout_date >= start_date)
    if end_date is not None:
        query = query.filter(Payout.payout_date <= end_date)

    total = query.count()
    rows = query.order_by(Payout.payout_date.desc(), Payout.id.desc()).offset(offset).limit(limit).all()
    return {
        "items": [PayoutOut.model_validate(row) for row in rows],
        "total": total,
    }


@app.get("/api/payouts/totals", response_model=PayoutTotalsOut)
def get_payout_totals(
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_date_range(start_date=start_date, end_date=end_date)

    query = db.query(Payout).filter(Payout.user_id == user_id)
    if start_date is not None:
        query = query.filter(Payout.payout_date >= start_date)
    if end_date is not None:
        query = query.filter(Payout.payout_date <= end_date)

    total_amount_cents, count = query.with_entities(
        func.coalesce(func.sum(Payout.amount_cents), 0),
        func.count(Payout.id),
    ).one()

    total_amount_cents = int(total_amount_cents)
    count = int(count)
    average_amount_cents = _calculate_average_amount_cents(total_amount_cents, count)

    return {
        "total_amount": _cents_to_dollars(total_amount_cents),
        "total_amount_cents": total_amount_cents,
        "average_amount": _cents_to_dollars(average_amount_cents),
        "average_amount_cents": average_amount_cents,
        "count": count,
    }


@app.patch("/api/payouts/{payout_id}", response_model=PayoutOut)
def update_payout(
    payout_id: int,
    payload: PayoutUpdateIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if payout_id <= 0:
        raise HTTPException(status_code=400, detail="payout_id must be a positive integer")

    row = (
        db.query(Payout)
        .filter(Payout.user_id == user_id)
        .filter(Payout.id == payout_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="payout not found")

    fields = payload.model_fields_set
    next_payout_date = row.payout_date
    next_amount_cents = row.amount_cents
    next_notes = row.notes

    if "payout_date" in fields:
        if payload.payout_date is None:
            raise HTTPException(status_code=400, detail="payout_date cannot be null")
        next_payout_date = payload.payout_date
    if "amount_cents" in fields:
        if payload.amount_cents is None:
            raise HTTPException(status_code=400, detail="amount_cents cannot be null")
        next_amount_cents = payload.amount_cents
    if "notes" in fields:
        next_notes = _normalize_payout_notes(payload.notes)

    _validate_payout_amount(next_amount_cents)

    row.payout_date = next_payout_date
    row.amount_cents = next_amount_cents
    row.notes = next_notes

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _to_payout_integrity_http_exception(exc) from exc

    db.refresh(row)
    return PayoutOut.model_validate(row)


@app.delete("/api/payouts/{payout_id}", status_code=204)
def delete_payout(
    payout_id: int,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if payout_id <= 0:
        raise HTTPException(status_code=400, detail="payout_id must be a positive integer")

    row = (
        db.query(Payout)
        .filter(Payout.user_id == user_id)
        .filter(Payout.id == payout_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="payout not found")

    db.delete(row)
    db.commit()
    return Response(status_code=204)


@app.get("/metrics/summary", response_model=SummaryMetricsOut)
def metrics_summary(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)
    return get_summary_metrics(db, account_id=account_id, user_id=user_id)


@app.get("/metrics/pnl-by-hour", response_model=list[HourPnlOut])
def metrics_pnl_by_hour(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)
    return get_pnl_by_hour(db, account_id=account_id, user_id=user_id)


@app.get("/metrics/pnl-by-day", response_model=list[DayPnlOut])
def metrics_pnl_by_day(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)
    return get_pnl_by_day(db, account_id=account_id, user_id=user_id)


@app.get("/metrics/pnl-by-symbol", response_model=list[SymbolPnlOut])
def metrics_pnl_by_symbol(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)
    return get_pnl_by_symbol(db, account_id=account_id, user_id=user_id)


@app.get("/metrics/streaks", response_model=StreakMetricsOut)
def metrics_streaks(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)
    return get_streak_metrics(db, account_id=account_id, user_id=user_id)


@app.get("/metrics/behavior", response_model=BehaviorMetricsOut)
def metrics_behavior(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)
    return get_behavior_metrics(db, account_id=account_id, user_id=user_id)


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

    provider_by_external_id = _provider_account_payloads_by_external_id(provider_accounts)
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
        provider_balance = provider_payload.get("balance") if isinstance(provider_payload, dict) else None
        provider_can_trade = provider_payload.get("can_trade") if isinstance(provider_payload, dict) else None
        provider_is_visible = provider_payload.get("is_visible") if isinstance(provider_payload, dict) else None

        provider_name = resolve_projectx_account_provider_name(row.name, account_id=account_id)
        effective_name = resolve_projectx_account_effective_name(
            provider_name=provider_name,
            display_name=row.display_name,
        )
        account_state = row.account_state or ACCOUNT_STATE_ACTIVE
        can_trade = provider_can_trade if isinstance(provider_can_trade, bool) else row.can_trade
        is_visible = provider_is_visible if isinstance(provider_is_visible, bool) else row.is_visible

        payload.append(
            {
                "id": account_id,
                "name": effective_name,
                "provider_name": provider_name,
                "custom_display_name": row.display_name,
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


@app.patch("/api/accounts/{account_id}/display-name", response_model=ProjectXAccountRenameOut)
def rename_projectx_account(
    account_id: int,
    payload: ProjectXAccountRenameIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)

    try:
        account_row = set_projectx_account_display_name(
            db,
            account_id,
            payload.display_name,
            user_id=user_id,
        )
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="Account not found.") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    provider_name = resolve_projectx_account_provider_name(account_row.name, account_id=account_id)
    effective_name = resolve_projectx_account_effective_name(
        provider_name=provider_name,
        display_name=account_row.display_name,
    )
    return {
        "account_id": account_id,
        "name": effective_name,
        "provider_name": provider_name,
        "custom_display_name": account_row.display_name,
    }


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
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="Account not found.") from exc
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


@app.get("/api/projectx/contracts/search", response_model=list[ProjectXContractOut])
def search_projectx_contracts(
    search_text: str = Query(..., min_length=1, max_length=50),
    live: bool = False,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    try:
        client = _projectx_client_for_user(db, user_id=user_id)
        rows = client.search_contracts(search_text=search_text, live=live)
    except ProjectXClientError as exc:
        raise _to_http_exception(exc) from exc
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row.get("description"),
            "tick_size": row.get("tick_size"),
            "tick_value": row.get("tick_value"),
            "active_contract": row.get("active_contract"),
            "symbol_id": row.get("symbol_id"),
        }
        for row in rows
    ]


@app.get("/api/projectx/candles", response_model=list[ProjectXMarketCandleOut])
def get_projectx_market_candles(
    contract_id: str = Query(..., min_length=1, max_length=120),
    symbol: str | None = Query(default=None, max_length=40),
    start: datetime | None = None,
    end: datetime | None = None,
    live: bool = False,
    unit: str = Query(default="minute", pattern="^(second|minute|hour|day|week|month)$"),
    unit_number: int = Query(default=5, ge=1, le=1440),
    limit: int = Query(default=500, ge=1, le=20000),
    include_partial_bar: bool = False,
    refresh: bool = False,
    repair: bool = False,
    db: Session = Depends(get_db),
):
    """Serve candles from the per-user cache, fetching from ProjectX when needed.

    `refresh` forces a full re-fetch and prunes cached rows the provider no longer
    returns. `repair` also forces a full-window fetch (so interior holes in the
    cache get backfilled) but merges with existing cache instead of pruning.
    """
    user_id = get_authenticated_user_id()
    end_utc = _as_utc(end) if end is not None else datetime.now(timezone.utc)
    start_utc = _as_utc(start) if start is not None else end_utc - timedelta(days=5)
    requested_symbol = symbol.strip() if isinstance(symbol, str) and symbol.strip() else None
    _validate_time_range(start=start_utc, end=end_utc)

    fallback_candles = []
    try:
        cached_candles = list_market_candles(
            db,
            user_id=user_id,
            contract_id=contract_id,
            live=live,
            start=start_utc,
            end=end_utc,
            unit=unit,
            unit_number=unit_number,
            limit=limit,
            include_partial_bar=include_partial_bar,
        )
        fallback_candles = cached_candles
        cached_covers_request = market_candle_cache_covers_request(
            cached_candles,
            start=start_utc,
            unit=unit,
            unit_number=unit_number,
            limit=limit,
        )
        if (
            cached_candles
            and not refresh
            and not repair
            and cached_covers_request
            and not market_candle_cache_needs_refresh(
                cached_candles,
                end=end_utc,
                unit=unit,
                unit_number=unit_number,
                include_partial_bar=include_partial_bar,
            )
        ):
            return [serialize_market_candle(row) for row in cached_candles]

        client = _projectx_client_for_user(db, user_id=user_id)
        resolved_contract_id, resolved_symbol = resolve_market_contract(
            client,
            contract_id=contract_id,
            symbol=requested_symbol,
            live=live,
        )
        if resolved_contract_id != contract_id:
            cached_candles = list_market_candles(
                db,
                user_id=user_id,
                contract_id=resolved_contract_id,
                live=live,
                start=start_utc,
                end=end_utc,
                unit=unit,
                unit_number=unit_number,
                limit=limit,
                include_partial_bar=include_partial_bar,
            )
            if cached_candles:
                fallback_candles = cached_candles
            cached_covers_request = market_candle_cache_covers_request(
                cached_candles,
                start=start_utc,
                unit=unit,
                unit_number=unit_number,
                limit=limit,
            )
            if (
                cached_candles
                and not refresh
                and not repair
                and cached_covers_request
                and not market_candle_cache_needs_refresh(
                    cached_candles,
                    end=end_utc,
                    unit=unit,
                    unit_number=unit_number,
                    include_partial_bar=include_partial_bar,
                )
            ):
                return [serialize_market_candle(row) for row in cached_candles]

        fetch_start_utc = start_utc
        # A repair request always fetches the full window so interior cache holes
        # are refilled; the normal path only extends the cached tail.
        if cached_candles and not refresh and not repair and cached_covers_request:
            fetch_start_utc = next_market_candle_fetch_start(
                cached_candles,
                start=start_utc,
                unit=unit,
                unit_number=unit_number,
            )
            if fetch_start_utc > end_utc:
                return [serialize_market_candle(row) for row in cached_candles]

        active_lookup_symbol = requested_symbol or resolved_symbol
        active_contract_fallback_attempted = False
        active_contract_lookup_attempted = False
        candles: list[ProjectXMarketCandle] = []
        if _should_fetch_active_symbol_contract_first(
            current_contract_id=resolved_contract_id,
            lookup_symbol=active_lookup_symbol,
            end=end_utc,
        ):
            active_contract_lookup_attempted = True
            candles = _fetch_active_symbol_market_candles(
                db,
                user_id=user_id,
                client=client,
                current_contract_id=resolved_contract_id,
                lookup_symbol=active_lookup_symbol,
                live=live,
                start=fetch_start_utc,
                end=end_utc,
                unit=unit,
                unit_number=unit_number,
                limit=limit,
                include_partial_bar=include_partial_bar,
            )

        if not candles:
            try:
                candles = fetch_and_store_market_candles(
                    db,
                    user_id=user_id,
                    client=client,
                    contract_id=resolved_contract_id,
                    symbol=resolved_symbol,
                    live=live,
                    start=fetch_start_utc,
                    end=end_utc,
                    unit=unit,
                    unit_number=unit_number,
                    limit=limit,
                    include_partial_bar=include_partial_bar,
                )
            except ProjectXClientError:
                active_contract_fallback_attempted = True
                active_candles = []
                if not active_contract_lookup_attempted:
                    active_candles = _fetch_active_symbol_market_candles(
                        db,
                        user_id=user_id,
                        client=client,
                        current_contract_id=resolved_contract_id,
                        lookup_symbol=active_lookup_symbol,
                        live=live,
                        start=fetch_start_utc,
                        end=end_utc,
                        unit=unit,
                        unit_number=unit_number,
                        limit=limit,
                        include_partial_bar=include_partial_bar,
                    )
                if not active_candles:
                    raise
                candles = active_candles

        if (
            active_lookup_symbol
            and not active_contract_fallback_attempted
            and not active_contract_lookup_attempted
            and _looks_like_projectx_contract_id(resolved_contract_id)
            and market_candle_rows_are_stale(
                candles,
                end=end_utc,
                unit=unit,
                unit_number=unit_number,
                include_partial_bar=include_partial_bar,
            )
        ):
            active_candles = _fetch_active_symbol_market_candles(
                db,
                user_id=user_id,
                client=client,
                current_contract_id=resolved_contract_id,
                lookup_symbol=active_lookup_symbol,
                live=live,
                start=fetch_start_utc,
                end=end_utc,
                unit=unit,
                unit_number=unit_number,
                limit=limit,
                include_partial_bar=include_partial_bar,
            )
            if _market_candle_rows_have_newer_tail(active_candles, candles):
                candles = active_candles

        response_contract_id = str(candles[-1].contract_id) if candles else resolved_contract_id
        if refresh and not include_partial_bar:
            prune_contract_id = response_contract_id
            prune_market_candle_cache_range(
                db,
                user_id=user_id,
                contract_id=prune_contract_id,
                live=live,
                start=start_utc,
                end=end_utc,
                unit=unit,
                unit_number=unit_number,
                keep_timestamps=[row.candle_timestamp for row in candles],
            )
        db.commit()
        if cached_candles and not refresh:
            combined_candles = list_market_candles(
                db,
                user_id=user_id,
                contract_id=response_contract_id,
                live=live,
                start=start_utc,
                end=end_utc,
                unit=unit,
                unit_number=unit_number,
                limit=limit,
                include_partial_bar=include_partial_bar,
            )
            if combined_candles:
                return [serialize_market_candle(row) for row in combined_candles]
    except ProjectXClientError as exc:
        db.rollback()
        if fallback_candles:
            return [serialize_market_candle(row) for row in fallback_candles]
        raise _to_http_exception(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return [serialize_market_candle(row) for row in candles]


def _should_fetch_active_symbol_contract_first(
    *,
    current_contract_id: str,
    lookup_symbol: str | None,
    end: datetime,
) -> bool:
    if not lookup_symbol or not _looks_like_projectx_contract_id(current_contract_id):
        return False

    # Initial chart loads always ask for a window ending at "now"; try the
    # active symbol contract first so an expired saved futures contract cannot
    # hold the page in a slow history request.
    return _as_utc(end) >= datetime.now(timezone.utc) - timedelta(days=1)


def _fetch_active_symbol_market_candles(
    db: Session,
    *,
    user_id: str,
    client: ProjectXClient,
    current_contract_id: str,
    lookup_symbol: str | None,
    live: bool,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    limit: int,
    include_partial_bar: bool,
) -> list[ProjectXMarketCandle]:
    normalized_symbol = str(lookup_symbol or "").strip()
    if not normalized_symbol or not _looks_like_projectx_contract_id(current_contract_id):
        return []

    symbol_contract_id: str | None = None
    symbol_resolved_symbol: str | None = None
    for candidate in _market_symbol_lookup_candidates(normalized_symbol):
        resolved_contract_id, resolved_symbol = resolve_market_contract(
            client,
            contract_id=candidate,
            symbol=normalized_symbol,
            live=live,
        )
        if resolved_contract_id == current_contract_id:
            return []
        if not _looks_like_projectx_contract_id(resolved_contract_id):
            continue
        symbol_contract_id = resolved_contract_id
        symbol_resolved_symbol = resolved_symbol
        break

    if symbol_contract_id is None:
        return []

    return fetch_and_store_market_candles(
        db,
        user_id=user_id,
        client=client,
        contract_id=symbol_contract_id,
        symbol=symbol_resolved_symbol,
        live=live,
        start=start,
        end=end,
        unit=unit,
        unit_number=unit_number,
        limit=limit,
        include_partial_bar=include_partial_bar,
    )


def _market_symbol_lookup_candidates(symbol: str) -> list[str]:
    normalized_symbol = symbol.strip()
    candidates = [normalized_symbol]
    if "." in normalized_symbol:
        candidates.append(normalized_symbol.rsplit(".", 1)[-1])

    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = candidate.strip()
        key = value.upper()
        if not value or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _market_candle_rows_have_newer_tail(
    candidate: list[ProjectXMarketCandle],
    current: list[ProjectXMarketCandle],
) -> bool:
    candidate_latest = _latest_market_candle_timestamp(candidate)
    if candidate_latest is None:
        return False

    current_latest = _latest_market_candle_timestamp(current)
    return current_latest is None or candidate_latest > current_latest


def _latest_market_candle_timestamp(candles: list[ProjectXMarketCandle]) -> datetime | None:
    timestamps = [_as_utc(row.candle_timestamp) for row in candles]
    return max(timestamps) if timestamps else None


@app.get("/api/projectx/market-price/stream")
async def stream_projectx_market_price(
    request: Request,
    contract_id: str = Query(..., min_length=1, max_length=120),
    symbol: str | None = Query(default=None, max_length=40),
    throttle_ms: int = Query(default=250, ge=50, le=5000),
):
    get_authenticated_user_id()
    runtime = _streaming_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="ProjectX streaming is not enabled.")

    interval_seconds = max(throttle_ms, 50) / 1000.0

    async def events():
        last_event_key = None
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break

            update = runtime.tracker.get_market_price_update(contract_id=contract_id, symbol=symbol)
            if update is not None:
                event_key = (update.contract_id, update.mark_price, update.timestamp.isoformat())
                if event_key != last_event_key:
                    payload = {
                        "contract_id": update.contract_id,
                        "symbol": update.symbol,
                        "price": update.mark_price,
                        "timestamp": update.timestamp.isoformat(),
                    }
                    yield f"event: price\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    last_event_key = event_key

            await asyncio.sleep(interval_seconds)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/trade-plan/evaluate", response_model=TradeEvaluationResultOut)
def evaluate_trade_plan(payload: TradePlanEvaluationIn):
    try:
        result = TradePlanEvaluator().evaluate(
            TradePlan(**payload.trade_plan.model_dump()),
            MarketContext(**payload.market_context.model_dump()),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.to_payload()


@app.get("/api/bots", response_model=BotConfigListOut)
def list_trading_bots(
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if account_id is not None:
        _validate_account_id(account_id)
    rows = list_bot_configs(db, user_id=user_id, account_id=account_id)
    return {
        "items": [serialize_bot_config(row) for row in rows],
        "total": len(rows),
    }


@app.post("/api/bots", response_model=BotConfigOut, status_code=201)
def create_trading_bot(
    payload: BotConfigCreateIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    try:
        row = create_bot_config(db, user_id=user_id, payload=payload)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return serialize_bot_config(row)


@app.patch("/api/bots/{bot_config_id}", response_model=BotConfigOut)
def update_trading_bot(
    bot_config_id: int,
    payload: BotConfigUpdateIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if bot_config_id <= 0:
        raise HTTPException(status_code=400, detail="bot_config_id must be a positive integer")
    try:
        row = update_bot_config(db, user_id=user_id, bot_config_id=bot_config_id, payload=payload)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return serialize_bot_config(row)


@app.delete("/api/bots/{bot_config_id}", status_code=204)
def delete_trading_bot(
    bot_config_id: int,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if bot_config_id <= 0:
        raise HTTPException(status_code=400, detail="bot_config_id must be a positive integer")
    try:
        delete_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return Response(status_code=204)


@app.post("/api/bots/{bot_config_id}/start", response_model=BotEvaluationOut)
def start_trading_bot(
    bot_config_id: int,
    payload: BotStartIn | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if bot_config_id <= 0:
        raise HTTPException(status_code=400, detail="bot_config_id must be a positive integer")
    body = payload or BotStartIn()
    try:
        client = _projectx_client_for_user(db, user_id=user_id)
        result = start_bot_run(
            db,
            user_id=user_id,
            bot_config_id=bot_config_id,
            client=client,
            dry_run=body.dry_run,
            confirm_live_order_routing=body.confirm_live_order_routing,
        )
        db.commit()
    except ProjectXClientError as exc:
        db.rollback()
        raise _to_http_exception(exc) from exc
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return serialize_evaluation(result)


@app.post("/api/bots/{bot_config_id}/evaluate", response_model=BotEvaluationOut)
def evaluate_trading_bot(
    bot_config_id: int,
    payload: BotStartIn | None = None,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if bot_config_id <= 0:
        raise HTTPException(status_code=400, detail="bot_config_id must be a positive integer")
    body = payload or BotStartIn(dry_run=True)
    if body.dry_run is False:
        raise HTTPException(status_code=400, detail="evaluate endpoint is dry-run only; use /start for live order routing")
    try:
        config = get_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
        if config is None:
            raise LookupError("bot_config_not_found")
        account = _require_owned_projectx_account(db, user_id=user_id, account_id=int(config.account_id))
        client = _projectx_client_for_user(db, user_id=user_id)
        result = evaluate_bot_config(
            db,
            user_id=user_id,
            config=config,
            account=account,
            client=client,
            dry_run=True,
            confirm_live_order_routing=body.confirm_live_order_routing,
        )
        db.commit()
    except ProjectXClientError as exc:
        db.rollback()
        raise _to_http_exception(exc) from exc
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return serialize_evaluation(result)


@app.post("/api/bots/{bot_config_id}/stop", response_model=BotRunOut)
def stop_trading_bot(
    bot_config_id: int,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if bot_config_id <= 0:
        raise HTTPException(status_code=400, detail="bot_config_id must be a positive integer")
    try:
        run = stop_latest_bot_run(db, user_id=user_id, bot_config_id=bot_config_id)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return serialize_bot_run(run)


@app.get("/api/bots/{bot_config_id}/activity", response_model=BotActivityOut)
def get_trading_bot_activity(
    bot_config_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    if bot_config_id <= 0:
        raise HTTPException(status_code=400, detail="bot_config_id must be a positive integer")
    try:
        payload = get_bot_activity(db, user_id=user_id, bot_config_id=bot_config_id, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "config": serialize_bot_config(payload["config"]),
        "runs": [serialize_bot_run(row) for row in payload["runs"]],
        "decisions": [serialize_bot_decision(row) for row in payload["decisions"]],
        "order_attempts": [serialize_bot_order_attempt(row) for row in payload["order_attempts"]],
        "risk_events": [serialize_bot_risk_event(row) for row in payload["risk_events"]],
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


@app.post("/api/accounts/{account_id}/journal/ai-recap", response_model=AIJournalRecapOut)
@app.post("/projectx/accounts/{account_id}/journal/ai-recap", response_model=AIJournalRecapOut)
def create_projectx_account_ai_journal_recap(
    account_id: int,
    payload: AIJournalRecapIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    _require_owned_projectx_account(db, user_id=user_id, account_id=account_id)

    try:
        return generate_ai_journal_recap(
            db,
            user_id=user_id,
            account_id=account_id,
            entry_date=payload.entry_date,
            mode=payload.mode,
            include_existing_notes=payload.include_existing_notes,
        )
    except GeminiClientError as exc:
        db.rollback()
        raise _to_gemini_http_exception(exc) from exc
    except VersionConflictError as exc:
        db.rollback()
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
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@app.post("/api/journal/merge", response_model=JournalMergeOut)
def merge_projectx_account_journal_entries(
    payload: JournalMergeIn,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _require_owned_projectx_account(
        db,
        user_id=user_id,
        account_id=payload.from_account_id,
        error_detail="Source account not found.",
    )
    _require_owned_projectx_account(
        db,
        user_id=user_id,
        account_id=payload.to_account_id,
        error_detail="Destination account not found.",
    )

    try:
        return merge_journal_entries(
            db,
            user_id=user_id,
            from_account_id=payload.from_account_id,
            to_account_id=payload.to_account_id,
            on_conflict=payload.on_conflict,
            include_images=payload.include_images,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="journal image file not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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
    except JournalEntryDateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    try:
        file_bytes = await file.read(MAX_JOURNAL_IMAGE_BYTES + 1)
    finally:
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
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="journal image file not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(content=file_bytes, media_type=row.mime_type)

    try:
        path = get_journal_image_file_path(row.filename)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="journal image file not found") from exc
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
    _require_owned_projectx_account(db, user_id=user_id, account_id=account_id)

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
    include_lifecycle: bool = True,
    db: Session = Depends(get_db),
):
    user_id = get_authenticated_user_id()
    _validate_account_id(account_id)
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    if symbol is not None and len(symbol) > 50:
        raise HTTPException(status_code=400, detail="symbol must be <= 50 characters")
    _validate_time_range(start=start, end=end)
    _require_owned_projectx_account(db, user_id=user_id, account_id=account_id)

    try:
        _ensure_trade_cache_or_fallback(
            db,
            user_id=user_id,
            account_id=account_id,
            start=start,
            end=end,
            refresh=refresh,
        )

        rows = list_trade_events(
            db,
            account_id=account_id,
            user_id=user_id,
            limit=limit,
            start=start,
            end=end,
            symbol_query=symbol,
        )
        lifecycle_by_trade_id = (
            derive_trade_execution_lifecycles(
                db,
                user_id=user_id,
                account_id=account_id,
                closed_rows=rows,
            )
            if include_lifecycle
            else {}
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
    _require_owned_projectx_account(db, user_id=user_id, account_id=account_id)

    try:
        _ensure_trade_cache_or_fallback(
            db,
            user_id=user_id,
            account_id=account_id,
            start=start,
            end=end,
            refresh=refresh,
        )

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
    _require_owned_projectx_account(db, user_id=user_id, account_id=account_id)

    point_bases = [normalize_points_basis(basis) for basis in POINTS_BASIS_SYMBOLS]

    try:
        _ensure_trade_cache_or_fallback(
            db,
            user_id=user_id,
            account_id=account_id,
            start=start,
            end=end,
            refresh=refresh,
        )

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
    _require_owned_projectx_account(db, user_id=user_id, account_id=account_id)

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
        _ensure_trade_cache_or_fallback(
            db,
            user_id=user_id,
            account_id=account_id,
            start=effective_start,
            end=effective_end,
            refresh=refresh,
        )

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
            log_method = (
                logger.debug if isinstance(exc, ProjectXCredentialsEncryptionKeyMissing) else logger.warning
            )
            log_method(
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


def _normalize_payout_notes(value: str | None) -> str | None:
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


def _validate_payout_amount(amount_cents: int | None) -> None:
    if amount_cents is None:
        raise HTTPException(status_code=400, detail="amount_cents is required")
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="amount_cents must be > 0")


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


def _calculate_average_amount_cents(total_amount_cents: int, count: int) -> int:
    if count <= 0:
        return 0
    return int((Decimal(total_amount_cents) / Decimal(count)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_expense_integrity_http_exception(exc: IntegrityError) -> HTTPException:
    message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
    if "uq_expenses_dedupe" in message or "unique constraint" in message:
        return HTTPException(status_code=409, detail="duplicate_expense")
    return HTTPException(status_code=400, detail="invalid_expense_payload")


def _to_payout_integrity_http_exception(exc: IntegrityError) -> HTTPException:
    return HTTPException(status_code=400, detail="invalid_payout_payload")


def _first_nonempty_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _validate_account_id(account_id: int) -> None:
    if account_id <= 0:
        raise HTTPException(status_code=400, detail="account_id must be a positive integer")


def _validate_date_range(*, start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be before or equal to end_date")


def _validate_pagination(*, limit: int, offset: int, max_limit: int) -> None:
    if limit < 1 or limit > max_limit:
        raise HTTPException(status_code=400, detail=f"limit must be between 1 and {max_limit}")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")


def _validate_time_range(*, start: datetime | None, end: datetime | None) -> None:
    if start and end and _as_utc(start) > _as_utc(end):
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


def _provider_account_payloads_by_external_id(
    provider_accounts: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for account in provider_accounts:
        if not isinstance(account, dict):
            continue
        external_id = normalize_projectx_account_external_id(account.get("id"))
        if external_id is None:
            continue
        output[external_id] = account
    return output


def _require_owned_projectx_account(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    error_detail: str = "Account not found.",
):
    account = get_projectx_account_row(db, account_id, user_id=user_id)
    if account is None:
        raise HTTPException(status_code=404, detail=error_detail)
    return account


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


def _ensure_trade_cache_or_fallback(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    start: datetime | None,
    end: datetime | None,
    refresh: bool,
) -> None:
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
        db.rollback()
        if refresh and not _should_fallback_to_local_metrics(db, user_id=user_id, account_id=account_id, exc=exc):
            raise
        logger.warning(
            "projectx_trade_cache_sync_failed_using_local",
            extra={"account_id": account_id, "user_id": user_id, "status_code": exc.status_code},
        )
    except Exception:
        db.rollback()
        if refresh:
            raise
        logger.exception(
            "projectx_trade_cache_sync_failed_using_local",
            extra={"account_id": account_id, "user_id": user_id},
        )


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

    if exc.status_code == 504:
        return HTTPException(status_code=504, detail=str(exc))

    # Upstream API errors should be surfaced as a gateway error to the frontend.
    return HTTPException(status_code=502, detail=str(exc))


def _to_gemini_http_exception(exc: GeminiClientError) -> HTTPException:
    message = str(exc)
    if exc.status_code is None and "Missing Gemini configuration" in message:
        return HTTPException(status_code=503, detail="Gemini is not configured on the backend.")
    if exc.status_code == 504:
        return HTTPException(status_code=504, detail="Gemini recap generation timed out.")
    if exc.status_code in {429, 503}:
        return HTTPException(status_code=503, detail=message or "Gemini is temporarily unavailable.")
    return HTTPException(status_code=502, detail=message or "Gemini recap generation failed.")
