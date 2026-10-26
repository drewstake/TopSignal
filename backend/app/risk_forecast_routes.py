from datetime import datetime, timezone
from threading import BoundedSemaphore

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from .auth import auth_required, get_authenticated_user, get_authenticated_user_id
from .db import get_db
from .input_bounds import MAX_DATABASE_ID
from .risk_forecast_schemas import RiskForecastRequest
from .services.account_risk_forecast import forecast, load_outcomes, sources

router = APIRouter(prefix="/api/accounts", tags=["risk-forecast"])
_SLOT = BoundedSemaphore(1)


def risk_forecast_user_id() -> str:
    if auth_required() and get_authenticated_user() is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    return get_authenticated_user_id()


@router.get("/{account_id}/risk-forecast/sources")
def risk_sources(account_id: int = Path(gt=0, le=MAX_DATABASE_ID), db: Session = Depends(get_db),
                 user_id: str = Depends(risk_forecast_user_id)):
    try:
        return sources(db, user_id, account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{account_id}/risk-forecast")
def account_risk_forecast(payload: RiskForecastRequest, account_id: int = Path(gt=0, le=MAX_DATABASE_ID),
                          db: Session = Depends(get_db), user_id: str = Depends(risk_forecast_user_id)):
    if not _SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A risk forecast is running. Try again shortly.")
    try:
        now = datetime.now(timezone.utc)
        outcomes, label, warnings = load_outcomes(db, user_id, account_id, payload, now)
        return forecast(outcomes, payload, label, warnings, now)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else "Source trade detail is missing or malformed.") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        _SLOT.release()
