import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text

from ..db import get_db
from . import store as db

# Public market information is shared by authenticated workspace users. The
# existing /api middleware also enforces the project's offline loopback rules.
router = APIRouter(prefix="/api/scanner", tags=["Scanner"])


def connection(session):
    url = session.get_bind().url
    if url.get_backend_name() != "postgresql" or url.host not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        return None
    c = session.connection()
    if not c.scalar(
        text("SELECT to_regclass('topsignal_local.scanner_event') IS NOT NULL")
    ):
        return None
    return c


def source_health(c):
    if c is None:
        return {
            "sources": [],
            "worker": "Scanner worker not configured",
            "spend": None,
        }
    now = time.time()
    sources = [
        dict(row)
        for row in c.execute(
            select(
                db.health.c.name,
                db.health.c.status,
                db.health.c.success,
                db.health.c.next_check,
            ).order_by(db.health.c.name)
        ).mappings()
    ]
    worker = next((s for s in sources if s["name"] == "Scanner worker"), {})
    alive = now - (worker.get("success") or 0) < 120
    for s in sources:
        if (
            s["name"] == "Equity prices"
            and s["status"].startswith(("Live", "Delayed"))
            and (not alive or now - (s["success"] or 0) > 120)
        ):
            s["status"] = "Stale · " + s["status"].split(" · ", 1)[-1]
    budget = (
        c.execute(select(db.spend).order_by(db.spend.c.cycle.desc()).limit(1))
        .mappings()
        .first()
    )
    return {
        "sources": sources,
        "worker": "Running" if alive else "Scanner worker offline",
        "spend": dict(budget) if budget else None,
    }


@router.get("/health")
def scanner_health(session=Depends(get_db)):
    return source_health(connection(session))


@router.get("")
def scanner_feed(session=Depends(get_db)):
    c = connection(session)
    return {**source_health(c), "events": db.feed(c) if c is not None else []}


@router.get("/alerts")
def alerts(after: int | None = Query(None, ge=0), session=Depends(get_db)):
    c = connection(session)
    if c is None:
        return {"cursor": after or 0, "events": []}
    if after is None:
        return {"cursor": c.scalar(select(func.max(db.events.c.id))) or 0, "events": []}
    rows = (
        c.execute(
            select(
                db.events.c.id,
                db.events.c.title,
                db.events.c.tickers,
                db.events.c.source,
                db.events.c.link,
                db.events.c.received,
            )
            .where(db.events.c.urgent, db.events.c.id > after)
            .order_by(db.events.c.id)
            .limit(30)
        )
        .mappings()
        .all()
    )
    return {
        "cursor": rows[-1].id if rows else after,
        "events": [{**e, "tickers": e.tickers.split()} for e in rows],
    }
