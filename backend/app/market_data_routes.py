from datetime import datetime
from threading import BoundedSemaphore

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .auth import auth_required, get_authenticated_user, get_authenticated_user_id
from .db import get_db
from .market_data_schemas import (CandleImportOut, MarketContextOut, MarketDataInventoryOut, MarketRefreshIn,
                                 MarketRefreshOut, PublicMarketRefreshIn, PublicMarketRefreshOut, PublicMarketStatusOut)
from .services.market_data_context import refresh_market_context, stored_market_context
from .services.market_data_inventory import CaptureIntegrityError, import_local_history, market_data_inventory
from .services.public_market_context import public_market_status, refresh_public_market_context


router = APIRouter(prefix="/api/market-data", tags=["market-data"])
_MUTATION_SLOT = BoundedSemaphore(1)


def market_data_user_id() -> str:
    # The host app's auth middleware binds this context. Fail closed even if
    # this router is accidentally mounted in an app without that middleware.
    if auth_required() and get_authenticated_user() is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    return get_authenticated_user_id()


@router.get("/inventory", response_model=MarketDataInventoryOut)
def inventory(db: Session = Depends(get_db), user_id: str = Depends(market_data_user_id)):
    return market_data_inventory(db, user_id=user_id)


@router.get("/context", response_model=MarketContextOut)
def context(live: bool = False, as_of: datetime | None = None, db: Session = Depends(get_db), user_id: str = Depends(market_data_user_id)):
    try:
        return stored_market_context(db, user_id=user_id, live=live, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="as_of_must_not_be_in_the_future") from exc


@router.get("/public-status", response_model=PublicMarketStatusOut)
def public_status(db: Session = Depends(get_db), user_id: str = Depends(market_data_user_id)):
    return public_market_status(db, user_id=user_id)


@router.post("/refresh-public", response_model=PublicMarketRefreshOut)
def refresh_public(payload: PublicMarketRefreshIn, db: Session = Depends(get_db), user_id: str = Depends(market_data_user_id)):
    if not _MUTATION_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="market_data_operation_in_progress")
    try:
        return refresh_public_market_context(db, user_id=user_id, symbols=payload.symbols, days=payload.days)
    finally:
        _MUTATION_SLOT.release()


@router.post("/import-local-history", response_model=CandleImportOut)
def import_capture(db: Session = Depends(get_db), user_id: str = Depends(market_data_user_id)):
    if not _MUTATION_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="market_data_operation_in_progress")
    try:
        return import_local_history(db, user_id=user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="local_capture_not_installed") from exc
    except (CaptureIntegrityError, OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="local_capture_integrity_check_failed") from exc
    finally:
        _MUTATION_SLOT.release()


@router.post("/refresh", response_model=MarketRefreshOut)
def refresh(payload: MarketRefreshIn, db: Session = Depends(get_db), user_id: str = Depends(market_data_user_id)):
    if not _MUTATION_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="market_data_operation_in_progress")
    try:
        # Reuse the host's tenant credential resolver, including its restricted
        # local-development fallback. It releases the DB transaction before I/O.
        from .main import _projectx_client_for_user_without_open_transaction
        client = _projectx_client_for_user_without_open_transaction(db, user_id=user_id)
        return refresh_market_context(db, user_id=user_id, client=client,
            symbols=payload.symbols, days=payload.days, live=payload.live)
    finally:
        _MUTATION_SLOT.release()
