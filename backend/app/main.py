from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .db import get_db
from .models import Trade
from .schemas import TradeOut

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
def list_trades(limit: int = 100, db: Session = Depends(get_db)):
    return (
        db.query(Trade)
        .order_by(Trade.opened_at.desc())
        .limit(limit)
        .all()
    )