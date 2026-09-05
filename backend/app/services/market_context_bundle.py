"""Read collected context at decision time; never fetch a feed from an order path."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_

from ..market_observation_models import MarketObservation
from .market_data_context import stored_market_context
from .market_events import get_market_event_context


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _book_context(db, *, user_id, contract_id, as_of):
    row = (db.query(MarketObservation).filter(
        MarketObservation.user_id == user_id, MarketObservation.contract_id == contract_id,
        MarketObservation.source == "projectx_gateway_depth",
        MarketObservation.event_type.in_(["quote", "depth"]),
        MarketObservation.received_at <= as_of,
        or_(MarketObservation.provider_timestamp.is_(None), MarketObservation.provider_timestamp <= as_of),
    ).order_by(MarketObservation.received_at.desc(), MarketObservation.id.desc()).first())
    if row is None:
        return {"status": "missing", "reason": "No recorded bid/ask for this contract."}
    if row.bid is None or row.ask is None or row.provider_timestamp is None or row.bid > row.ask:
        return {"status": "unavailable", "reason": "The latest recorded book has no valid timestamped two-sided quote."}
    boundary = (db.query(MarketObservation).filter(
        MarketObservation.user_id == user_id, MarketObservation.contract_id == contract_id,
        MarketObservation.source == "projectx_gateway_depth", MarketObservation.event_type.in_(["reset", "gap"]),
        MarketObservation.received_at >= row.received_at, MarketObservation.received_at <= as_of,
    ).order_by(MarketObservation.received_at.desc(), MarketObservation.id.desc()).first())
    if boundary is not None and (_utc(boundary.received_at), boundary.id) > (_utc(row.received_at), row.id):
        return {"status": "unavailable", "reason": "A feed reset or connection gap invalidated the previous quote."}
    age = max((as_of - _utc(row.received_at)).total_seconds(), (as_of - _utc(row.provider_timestamp)).total_seconds())
    bid, ask = float(row.bid), float(row.ask)
    return {"status": "fresh" if age <= 10 and bid <= ask else "stale", "bid": bid, "ask": ask,
            "spread": ask-bid, "age_seconds": age, "received_at": row.received_at.isoformat(),
            "provider_timestamp": row.provider_timestamp.isoformat(), "source": row.source,
            "coverage": "observed_top_of_book", "data_mode": "provider_stream", "full_depth_verified": False}


def _profile_context(db, *, user_id, contract_id, as_of):
    local = as_of.astimezone(ZoneInfo("America/New_York"))
    day = local.date() if local.hour >= 18 else local.date()-timedelta(days=1)
    start = datetime.combine(day, time(18), tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    q = db.query(MarketObservation).filter(
        MarketObservation.user_id == user_id, MarketObservation.contract_id == contract_id,
        MarketObservation.source == "projectx_gateway_trade",
        MarketObservation.event_type == "trade", MarketObservation.received_at <= as_of,
        MarketObservation.provider_timestamp >= start, MarketObservation.provider_timestamp <= as_of,
    )
    prices = q.with_entities(MarketObservation.price, func.sum(MarketObservation.size)).group_by(MarketObservation.price).order_by(MarketObservation.price).limit(10001).all()
    if not prices:
        return {"status": "missing", "reason": "No recorded trade prints in this session.", "partial": True}
    if len(prices) > 10000:
        return {"status": "unavailable", "reason": "Profile exceeds bounded price-level limit.", "partial": True}
    levels = [(float(price), float(size)) for price, size in prices if price is not None and size is not None and float(size) > 0]
    if not levels:
        return {"status": "missing", "partial": True}
    total = sum(size for _, size in levels)
    poc_index = max(range(len(levels)), key=lambda index: levels[index][1])
    low = high = poc_index
    included = levels[poc_index][1]
    while included < total * .7 and (low > 0 or high < len(levels)-1):
        left = levels[low-1][1] if low else -1
        right = levels[high+1][1] if high < len(levels)-1 else -1
        if left >= right:
            low -= 1; included += levels[low][1]
        else:
            high += 1; included += levels[high][1]
    sides = dict(q.with_entities(MarketObservation.side, func.sum(MarketObservation.size)).group_by(MarketObservation.side).all())
    buy, sell = float(sides.get("buy") or 0), float(sides.get("sell") or 0)
    classified = buy + sell
    return {"status": "partial", "partial": True, "session_start": start.isoformat(),
            "poc": levels[poc_index][0], "value_area_low": levels[low][0], "value_area_high": levels[high][0],
            "value_area_fraction": .7, "recorded_volume": total, "recorded_trade_count": q.count(),
            "vwap": sum(price * size for price, size in levels)/total,
            "classification_coverage": classified/total,
            "cumulative_delta": buy-sell if abs(classified-total) < 1e-9 else None,
            "basis": "observed_session_trade_prints", "data_mode": "provider_stream",
            "reason": "Viewer-driven observations; session coverage is incomplete."}


def build_collected_context(db, *, user_id: str, contract_id: str, live: bool = False, as_of=None):
    now = _utc(as_of or datetime.now(timezone.utc))
    result = {"as_of": now.isoformat(), "version": "collected_context_v1"}
    reads = {
        "events": lambda: get_market_event_context(db, user_id=user_id, as_of=now),
        "related_markets": lambda: stored_market_context(db, user_id=user_id, live=live, as_of=now, collected_by_as_of=True).model_dump(mode="json"),
        "order_book": lambda: _book_context(db, user_id=user_id, contract_id=contract_id, as_of=now),
        "volume_profile": lambda: _profile_context(db, user_id=user_id, contract_id=contract_id, as_of=now),
    }
    for key, read in reads.items():
        try:
            # Missing optional tables/failed reads must not poison the execution transaction.
            with db.begin_nested():
                result[key] = read()
        except Exception:
            result[key] = {"status": "unavailable", "reason": "Collected context could not be read."}
    return result
