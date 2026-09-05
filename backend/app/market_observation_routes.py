from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .auth import auth_required, get_authenticated_user, get_authenticated_user_id
from .db import get_db
from .models import Account
from .market_observation_models import DecisionResearchSnapshot
from .services.decision_research import evaluate_pending, research_status
from .services.market_observations import observation_status

router = APIRouter()


def observation_user_id() -> str:
    if auth_required() and get_authenticated_user() is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    return get_authenticated_user_id()


def _owned_account(db: Session, user_id: str, account_id: int) -> None:
    # Existing trading records use provider external account ID; local imports
    # can use their local account row ID. Ownership must precede every query.
    found = db.query(Account.id).filter(Account.user_id == user_id,
        or_(Account.external_id == str(account_id), Account.id == account_id)).first()
    if found is None:
        raise HTTPException(status_code=404, detail="account_not_found")


@router.get("/api/market-observations/status")
def get_observation_status(contract_id: str | None = Query(default=None, max_length=120), db: Session = Depends(get_db), user_id: str = Depends(observation_user_id)):
    return observation_status(db, user_id=user_id, contract_id=contract_id)


@router.get("/api/decision-research")
def get_decision_research(account_id: int = Query(gt=0), limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db), user_id: str = Depends(observation_user_id)):
    _owned_account(db, user_id, account_id)
    return research_status(db, user_id=user_id, account_id=account_id, limit=limit)


@router.post("/api/decision-research/evaluate")
def evaluate_decision_research(account_id: int = Query(gt=0), db: Session = Depends(get_db), user_id: str = Depends(observation_user_id)):
    _owned_account(db, user_id, account_id)
    return evaluate_pending(db, user_id=user_id, account_id=account_id)


@router.get("/api/decision-research/{snapshot_id}")
def get_decision_snapshot(snapshot_id: int, account_id: int = Query(gt=0), db: Session = Depends(get_db), user_id: str = Depends(observation_user_id)):
    _owned_account(db, user_id, account_id)
    row = db.query(DecisionResearchSnapshot).filter(DecisionResearchSnapshot.id == snapshot_id,
        DecisionResearchSnapshot.user_id == user_id, DecisionResearchSnapshot.account_id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    return {column.name: getattr(row, column.name) for column in DecisionResearchSnapshot.__table__.columns if column.name != "user_id"}
