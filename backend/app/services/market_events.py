"""Point-in-time observations, scoped freshness and conservative event proximity."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import BoundedSemaphore

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..market_event_models import MarketEventSourceSnapshot, MarketEventVersion
from ..market_event_schemas import MarketEventOut
from . import market_event_providers as providers

UTC = timezone.utc
_REFRESH_SLOTS = BoundedSemaphore(2)
_EVENT_FIELDS = [name for name in MarketEventOut.model_fields if name not in {"id", "source", "first_seen_at", "observed_at", "available_at"}]


def _now():
    return datetime.now(UTC)


def _aware(value, name):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(name + " requires a timezone offset")
    return value.astimezone(UTC)


def _latest_events(db, user_id, as_of, source=None):
    query = db.query(MarketEventVersion.id.label("id"), func.row_number().over(
        partition_by=(MarketEventVersion.source, MarketEventVersion.source_event_id),
        order_by=(MarketEventVersion.observed_at.desc(), MarketEventVersion.id.desc()),
    ).label("rank")).filter(MarketEventVersion.user_id == user_id, MarketEventVersion.available_at <= as_of)
    if source:
        query = query.filter(MarketEventVersion.source == source)
    ranked = query.subquery()
    return db.query(MarketEventVersion).join(ranked, MarketEventVersion.id == ranked.c.id).filter(ranked.c.rank == 1)


def ingest_batch(db, *, user_id, source, batch, observed_at):
    """Append changes only; revision availability is never backdated to release."""
    if source not in providers.SOURCES:
        raise ValueError("Unknown source")
    observed_at = _aware(observed_at, "observed_at")
    previous = {}
    for row in db.query(MarketEventVersion).filter_by(user_id=user_id, source=source).filter(
            MarketEventVersion.observed_at <= observed_at).order_by(MarketEventVersion.observed_at.desc()).all():
        previous.setdefault(row.source_event_id, row)
    incoming = {}
    for event in batch.events:
        identity = event["source_event_id"]
        if identity in incoming and incoming[identity] != event:
            raise providers.ProviderError("conflicting_event_identity")
        incoming[identity] = event
    # A complete successful retrieval of one calendar's stated window removes
    # missing entries from that source. Public calendars remain only partial
    # coverage of the whole macro universe.
    if source != "federal_reserve" and batch.coverage_start and batch.coverage_end:
        for identity, row in previous.items():
            stamp = providers.utc(row.scheduled_at) if row.scheduled_at else None
            in_retrieved_scope = source in {"bls", "federal_reserve_calendar"} or (
                stamp is not None and batch.coverage_start <= stamp < batch.coverage_end)
            if (identity not in incoming and stamp and observed_at <= stamp
                    and in_retrieved_scope and row.state != "cancelled"):
                event = {name: getattr(row, name) for name in _EVENT_FIELDS}
                event["raw_fields"] = {"removed_from_latest_source_calendar": True}
                event["state"] = "cancelled"
                incoming[identity] = event
    inserted = 0
    for identity, event in incoming.items():
        payload = {name: event.get(name) for name in _EVENT_FIELDS}
        payload["raw_fields"] = event.get("raw_fields", {})
        for key in ("scheduled_at", "scheduled_end_at", "published_at"):
            if payload[key] is not None:
                payload[key] = providers.utc(payload[key])
        # Actual/revision values are not released merely because a schedule is
        # in the past. An observation after release must explicitly contain it.
        if payload["kind"] == "calendar" and (payload["scheduled_at"] is None or payload["scheduled_at"] > observed_at):
            payload.update(actual=None, revised=None)
            if payload["state"] == "released":
                payload["state"] = "scheduled"
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        old = previous.get(identity)
        if old is not None and old.content_hash == digest:
            continue
        published = payload["published_at"]
        available = max(observed_at, published) if published else observed_at
        db.add(MarketEventVersion(user_id=user_id, source=source, content_hash=digest,
            first_seen_at=providers.utc(old.first_seen_at) if old else observed_at,
            observed_at=observed_at, available_at=available, **payload))
        inserted += 1
    db.flush()
    return inserted


def refresh_market_events(db: Session, *, user_id: str, sources=None, now=None, collector=None):
    sources = list(dict.fromkeys(sources or providers.SOURCES))
    if not sources or any(source not in providers.SOURCES for source in sources):
        raise ValueError("Unknown market-event source")
    fixed_time = now is not None
    now = _aware(now, "now") if now else _now()
    collector = collector or providers.collect
    if not _REFRESH_SLOTS.acquire(blocking=False):
        raise RuntimeError("market_event_refresh_busy")
    outcomes = []
    try:
        for source in sources:
            last = db.query(MarketEventSourceSnapshot).filter_by(user_id=user_id, source=source).order_by(MarketEventSourceSnapshot.observed_at.desc()).first()
            if last and now - providers.utc(last.observed_at) < timedelta(seconds=60):
                outcomes.append({"source": source, "status": "cooldown", "inserted_versions": 0})
                continue
            if not providers.configured(source):
                snapshot = MarketEventSourceSnapshot(user_id=user_id, source=source, observed_at=now,
                    status="not_configured", error_code="api_key_not_configured", event_count=0)
                inserted = 0
            else:
                try:
                    batch = collector(source, now)
                    observed = now if fixed_time else _now()
                    # Each provider is isolated: its failure never discards a
                    # different source's successful committed observation.
                    with db.begin_nested():
                        inserted = ingest_batch(db, user_id=user_id, source=source, batch=batch, observed_at=observed)
                    snapshot = MarketEventSourceSnapshot(user_id=user_id, source=source, observed_at=observed,
                        status="connected", event_count=len(batch.events), coverage_start=batch.coverage_start,
                        coverage_end=batch.coverage_end, coverage_complete=batch.coverage_complete)
                except providers.ProviderError as error:
                    inserted = 0
                    code = str(error)
                    # No request exception/URL/provider response body is stored.
                    if not __import__("re").fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
                        code = "provider_failure"
                    snapshot = MarketEventSourceSnapshot(user_id=user_id, source=source, observed_at=now if fixed_time else _now(),
                        status="error", error_code=code, event_count=0)
                except (ValueError, TypeError, KeyError):
                    inserted = 0
                    snapshot = MarketEventSourceSnapshot(user_id=user_id, source=source, observed_at=now if fixed_time else _now(),
                        status="error", error_code="provider_parse_error", event_count=0)
            db.add(snapshot)
            db.commit()
            outcomes.append({"source": source, "status": snapshot.status, "inserted_versions": inserted})
    finally:
        _REFRESH_SLOTS.release()
    finished = now if fixed_time else _now()
    result = list_market_events(db, user_id=user_id, as_of=finished, now=finished)
    result["refresh"] = outcomes
    return result


def source_statuses(db, *, user_id, as_of):
    rows = db.query(MarketEventSourceSnapshot).filter(MarketEventSourceSnapshot.user_id == user_id,
        MarketEventSourceSnapshot.observed_at <= as_of).order_by(MarketEventSourceSnapshot.observed_at.desc()).all()
    latest, success = {}, {}
    for row in rows:
        latest.setdefault(row.source, row)
        if row.status == "connected":
            success.setdefault(row.source, row)
    result = []
    for source, definition in providers.SOURCES.items():
        row, good = latest.get(source), success.get(source)
        status = row.status if row else ("never_refreshed" if providers.configured(source) else "not_configured")
        if status == "connected" and (as_of - providers.utc(row.observed_at)).total_seconds() > definition["ttl_seconds"]:
            status = "stale"
        result.append(dict(source=source, label=definition["label"], status=status,
            last_attempt_at=providers.utc(row.observed_at) if row else None,
            last_success_at=providers.utc(good.observed_at) if good else None,
            event_count=row.event_count if row else 0,
            coverage_start=providers.utc(row.coverage_start) if row and row.coverage_start else None,
            coverage_end=providers.utc(row.coverage_end) if row and row.coverage_end else None,
            coverage_scope=definition["scope"], coverage_complete=bool(row and row.coverage_complete),
            error_code=row.error_code if row else ("api_key_not_configured" if status == "not_configured" else None),
            actuals_available=source == "trading_economics" and status == "connected",
            consensus_available=source == "trading_economics" and status == "connected"))
    return result


def _serialize(row, as_of):
    data = {name: getattr(row, name) for name in MarketEventOut.model_fields}
    for key in ("scheduled_at", "scheduled_end_at", "published_at", "first_seen_at", "observed_at", "available_at"):
        if data[key] is not None:
            data[key] = providers.utc(data[key])
    if data["kind"] == "calendar" and data["state"] == "scheduled" and data["scheduled_at"] <= as_of:
        data["state"] = "awaiting_release"
    return data


def _risk(events, statuses, as_of):
    before, after = timedelta(minutes=30), timedelta(minutes=15)
    fresh = {row["source"]: row for row in statuses if row["status"] == "connected"}
    nearby = []
    for event in events:
        if event["kind"] != "calendar" or event["state"] == "cancelled" or event["importance"] != "high":
            continue
        stamp = event["scheduled_at"]
        date_only = event["time_precision"] == "date"
        close = event["scheduled_end_at"] or stamp + timedelta(days=1)
        in_window = stamp <= as_of < close if date_only else as_of - after <= stamp <= as_of + before
        if in_window:
            nearby.append({"id": event["id"], "source": event["source"], "title": event["title"],
                           "scheduled_at": stamp, "time_precision": event["time_precision"],
                           "source_fresh": event["source"] in fresh})
    te = fresh.get("trading_economics")
    trusted = bool(te and te["coverage_complete"] and te["coverage_start"] is not None
        and te["coverage_start"] <= as_of - after and te["coverage_end"] >= as_of + before)
    known_high = any(row["source_fresh"] for row in nearby)
    # A known nearby event can establish elevated risk even with partial
    # coverage. Absence of a BLS/FOMC event can never establish globally low risk.
    level = "high" if known_high else "low" if trusted else "unknown"
    return dict(level=level, coverage_trusted=trusted, scope="US macro calendar" if trusted else "partial or unavailable calendar",
        reason="Known high-impact event nearby" if known_high else "No high-impact event in the covered time window" if trusted else "Full fresh economic-calendar coverage is unavailable",
        nearby_events=nearby, window_before_minutes=30, window_after_minutes=15)


def list_market_events(db: Session, *, user_id: str, start=None, end=None, as_of=None, source=None, now=None):
    now = _aware(now, "now") if now else _now()
    as_of = _aware(as_of, "as_of") if as_of else now
    if as_of > now:
        raise ValueError("as_of cannot be in the future")
    start = _aware(start, "start") if start else as_of - timedelta(days=1)
    end = _aware(end, "end") if end else as_of + timedelta(days=7)
    if end <= start or end - start > timedelta(days=366):
        raise ValueError("event window must be positive and at most 366 days")
    if source is not None and source not in providers.SOURCES:
        raise ValueError("Unknown market-event source")
    # Select latest versions BEFORE applying event dates: a moved event must
    # not reappear at its superseded time or leak a later revision backwards.
    current = [_serialize(row, as_of) for row in _latest_events(db, user_id, as_of).all()]
    statuses = source_statuses(db, user_id=user_id, as_of=as_of)
    events = [row for row in current if (source is None or row["source"] == source)
        and (row["scheduled_at"] or row["published_at"]) < end
        and (row["scheduled_end_at"] or row["scheduled_at"] or row["published_at"]) >= start]
    events.sort(key=lambda row: (row["scheduled_at"] or row["published_at"], row["source"], row["source_event_id"]))
    return dict(as_of=as_of, start=start, end=end, events=events[:3000], truncated=len(events) > 3000,
                sources=statuses, risk=_risk(current, statuses, as_of))


def get_market_event_context(db: Session, *, user_id: str, as_of=None):
    """Caller may consume news_risk; failure must remain explicit unknown."""
    result = list_market_events(db, user_id=user_id, as_of=as_of)
    cutoff = result["as_of"]
    headlines = sorted((row for row in result["events"] if row["kind"] == "news"
        and row["published_at"] is not None and cutoff - timedelta(days=1) <= row["published_at"] <= cutoff),
        key=lambda row: row["published_at"], reverse=True)[:20]
    headlines = [{key: row[key] for key in ("id", "title", "source", "url", "published_at", "first_seen_at", "available_at")}
                 for row in headlines]
    return {"news_risk": result["risk"]["level"], **result["risk"], "as_of": cutoff,
            "sources": result["sources"], "headlines": headlines}
