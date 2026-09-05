from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .auth import get_authenticated_user
from .db import get_db
from .market_event_schemas import MarketEventRefreshIn, MarketEventSource
from .services.market_events import list_market_events, refresh_market_events

router = APIRouter(prefix="/api/market-events", tags=["Market events"])


def require_market_event_user():
    user = get_authenticated_user()
    if user is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    return user.user_id


@router.get("")
def get_market_events(start: datetime | None = None, end: datetime | None = None,
                      as_of: datetime | None = None, source: MarketEventSource | None = Query(default=None),
                      db: Session = Depends(get_db), user_id: str = Depends(require_market_event_user)):
    try:
        return list_market_events(db, user_id=user_id, start=start, end=end, as_of=as_of, source=source)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/refresh")
def post_market_events_refresh(payload: MarketEventRefreshIn = MarketEventRefreshIn(),
                               db: Session = Depends(get_db), user_id: str = Depends(require_market_event_user)):
    try:
        return refresh_market_events(db, user_id=user_id, sources=payload.sources)
    except RuntimeError as error:
        if str(error) == "market_event_refresh_busy":
            raise HTTPException(status_code=429, detail="market_event_refresh_busy") from None
        raise
