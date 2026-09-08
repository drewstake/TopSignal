"""Read collected context at decision time; never fetch a feed from an order path."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_

from ..market_observation_models import MarketObservation
from .market_data_context import stored_market_context
from .market_events import get_market_event_context


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    try:
        return _utc(value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _book_context(db, *, user_id, contract_id, as_of):
    row = (db.query(MarketObservation).filter(
        MarketObservation.user_id == user_id, MarketObservation.contract_id == contract_id,
        MarketObservation.source == "projectx_gateway_depth",
        MarketObservation.event_type.in_(["quote", "depth"]),
        MarketObservation.received_at <= as_of,
        or_(MarketObservation.provider_timestamp.is_(None), MarketObservation.provider_timestamp <= as_of),
    ).order_by(MarketObservation.received_at.desc(), MarketObservation.id.desc()).first())
    if row is None:
        return {"status": "missing", "contract_id": contract_id, "eligible": False,
                "reason": "No recorded bid/ask for this contract available by the closed-candle cutoff. Later quotes are separate live context."}
    bid, ask = _finite(row.bid), _finite(row.ask)
    if bid is None or ask is None or row.provider_timestamp is None or bid <= 0 or bid > ask:
        return {"status": "unavailable", "reason": "The latest recorded book has no valid timestamped two-sided quote."}
    boundary = (db.query(MarketObservation).filter(
        MarketObservation.user_id == user_id, MarketObservation.contract_id == contract_id,
        MarketObservation.source == "projectx_gateway_depth", MarketObservation.event_type.in_(["reset", "gap"]),
        MarketObservation.received_at >= row.received_at, MarketObservation.received_at <= as_of,
    ).order_by(MarketObservation.received_at.desc(), MarketObservation.id.desc()).first())
    if boundary is not None and (_utc(boundary.received_at), boundary.id) > (_utc(row.received_at), row.id):
        return {"status": "unavailable", "reason": "A feed reset or connection gap invalidated the previous quote."}
    age = max((as_of - _utc(row.received_at)).total_seconds(), (as_of - _utc(row.provider_timestamp)).total_seconds())
    details = row.details if isinstance(row.details, dict) else {}
    bid_size, ask_size = _finite(details.get("best_bid_size")), _finite(details.get("best_ask_size"))
    # Historical rows contain only the updated side's size. Never copy it to
    # the opposite side or confuse a deeper level's size with the best quote.
    if bid_size is None and row.side == "bid" and _finite(row.price) == bid:
        bid_size = _finite(row.size)
    if ask_size is None and row.side == "ask" and _finite(row.price) == ask:
        ask_size = _finite(row.size)
    fresh = age <= 10
    return {"status": "fresh" if fresh else "stale", "bid": bid, "ask": ask,
            "bid_size": bid_size if bid_size is not None and bid_size >= 0 else None,
            "ask_size": ask_size if ask_size is not None and ask_size >= 0 else None,
            "contract_id": contract_id, "eligible": fresh,
            "spread": ask-bid, "age_seconds": age, "received_at": _utc(row.received_at).isoformat(),
            "provider_timestamp": _utc(row.provider_timestamp).isoformat(), "source": row.source,
            "coverage": "observed_top_of_book", "data_mode": "provider_stream", "full_depth_verified": False,
            "reason": "Recorded Level 1 best bid/ask; no depth, queue position, full-book imbalance or order-flow delta."
                if fresh else "Recorded quote is older than 10 seconds at the candle close; excluded from current evidence.",
            "freshness_limit_seconds": 10}


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
    first_print, last_print, last_receipt = q.with_entities(func.min(MarketObservation.provider_timestamp), func.max(MarketObservation.provider_timestamp), func.max(MarketObservation.received_at)).one()
    age = (as_of - _utc(last_print)).total_seconds()
    sides = dict(q.with_entities(MarketObservation.side, func.sum(MarketObservation.size)).group_by(MarketObservation.side).all())
    buy, sell = float(sides.get("buy") or 0), float(sides.get("sell") or 0)
    classified = buy + sell
    return {"status": "partial" if age <= 300 else "stale", "partial": True, "session_start": start.isoformat(),
            "contract_id": contract_id, "eligible": age <= 300,
            "observation_start": _utc(first_print).isoformat(), "observation_end": _utc(last_print).isoformat(),
            "received_through": _utc(last_receipt).isoformat(),
            "age_seconds": age, "freshness_limit_seconds": 300,
            "poc": levels[poc_index][0], "value_area_low": levels[low][0], "value_area_high": levels[high][0],
            "value_area_fraction": .7, "recorded_volume": total, "recorded_trade_count": q.count(),
            "vwap": sum(price * size for price, size in levels)/total,
            "classification_coverage": classified/total,
            "cumulative_delta": None,
            "basis": "observed_window_trade_prints", "data_mode": "provider_stream",
            "reason": "Viewer-driven trade-print window; gaps and session coverage are unknown. This is not a complete session profile. "
                + ("The last print is older than five minutes; excluded from current evidence." if age > 300 else "No order-flow delta is inferred from Level 1 quotes.")}


def build_collected_context(db, *, user_id: str, contract_id: str, live: bool = False, as_of=None, captured_at=None):
    now = _utc(as_of or datetime.now(timezone.utc))
    result = {"as_of": now.isoformat(), "captured_at": _utc(captured_at or datetime.now(timezone.utc)).isoformat(),
              "contract_id": contract_id, "version": "collected_context_v2",
              "scope": "Only observations received and timestamped at or before the closed-candle cutoff. Live quotes after that cutoff are separate; optional observations do not change the strategy or risk checks."}
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


def integrate_collected_context(analysis: dict, collected: dict) -> None:
    """Add only eligible descriptive context, without changing strategy votes."""
    provenance = analysis.get("provenance", {})
    cutoff = _timestamp(provenance.get("latest_candle_end_timestamp"))
    contract = provenance.get("resolved_contract_id")
    aligned = bool(cutoff and contract and contract == collected.get("contract_id") and cutoff == _timestamp(collected.get("as_of")))
    if not aligned:
        for key in ("order_book", "volume_profile", "events", "related_markets"):
            collected[key] = {"status": "unavailable", "eligible": False,
                "reason": "Observation cutoff or contract does not match a closed analysis candle; kept separate from this interpretation."}
    else:
        for key in ("order_book", "volume_profile"):
            value = collected.get(key, {})
            if not value.get("eligible"):
                continue
            if key == "order_book":
                observed, received = _timestamp(value.get("provider_timestamp")), _timestamp(value.get("received_at"))
                valid = (value.get("status") == "fresh" and observed and received and
                         observed <= cutoff and received <= cutoff and max((cutoff-observed).total_seconds(), (cutoff-received).total_seconds()) <= 10)
            else:
                start, end, received = _timestamp(value.get("observation_start")), _timestamp(value.get("observation_end")), _timestamp(value.get("received_through"))
                valid = value.get("status") == "partial" and start and end and received and start <= end <= cutoff and received <= cutoff and (cutoff-end).total_seconds() <= 300
            if not valid or value.get("contract_id") != contract:
                value.update(eligible=False, status="unavailable", reason="Recorded observation timing or contract does not match the analysis cutoff; excluded from evidence.")
    analysis["collected_context"] = collected
    book, profile = collected.get("order_book", {}), collected.get("volume_profile", {})
    events, related = collected.get("events", {}), collected.get("related_markets", {})
    items = []

    def coverage(identifier, label, status, detail):
        items.append({"id": identifier, "label": label, "status": status, "detail": detail})

    candle_count = provenance.get("closed_candle_count", 0)
    coverage("closed_candles", "Closed candles", "available" if candle_count and not provenance.get("is_stale") else "limited" if candle_count else "missing",
             f"{candle_count} closed candles; candle integrity is separate from wider market context coverage.")
    coverage("level_1_quotes", "Level 1 quote", "limited" if book.get("eligible") else "missing", book.get("reason", "No eligible Level 1 quote at the candle close."))
    coverage("observed_volume_profile", "Observed trade-print profile", "limited" if profile.get("eligible") else "missing", profile.get("reason", "No eligible recorded trade-print window."))
    sources = events.get("sources") or []
    has_headlines = bool(events.get("headlines")) and any(source.get("status") == "connected" for source in sources)
    coverage("news_context", "News", "limited" if has_headlines else "missing", "Recorded publications are partial source coverage, not a comprehensive news feed." if has_headlines else "No eligible fresh recorded news coverage; silence is not confirmation.")
    coverage("macro_context", "Economic calendar", "available" if events.get("coverage_trusted") else "limited" if events.get("nearby_events") else "missing", events.get("reason", "No complete fresh calendar coverage; event risk remains unknown."))
    # The evaluated instrument itself (including another delivery month) is not
    # cross-market evidence.
    from .instruments import normalize_symbol_key
    contract = collected.get("contract_id")
    root_symbol = normalize_symbol_key(provenance.get("resolved_symbol")) or normalize_symbol_key(contract)
    other_items = [item for item in related.get("items", []) if item.get("contract_id") != contract
                   and normalize_symbol_key(item.get("symbol")) != root_symbol and item.get("status") == "fresh"]
    coverage("cross_market_context", "Related markets", "limited" if other_items else "missing", "Fresh stored observations exist for " + ", ".join(item["symbol"] for item in other_items) + "; reference windows differ and no directional agreement is inferred." if other_items else "No eligible fresh related-market observations.")
    limited = [item["label"] for item in items if item["status"] == "limited"]
    missing = [item["label"] for item in items if item["status"] == "missing"]
    analysis["context_coverage"] = {
        "summary": "Context is incomplete" if limited or missing else "Context available within stated scopes",
        "available": [item["label"] for item in items if item["status"] == "available"],
        "limited": limited, "missing": missing, "items": items,
        "scope": "Coverage categories describe available inputs, not predictive confidence. Missing observations are not zero, neutral or confirmation.",
    }
    eligible_ids = {item["id"] for item in items if item["status"] != "missing"}
    quality = analysis.get("data_quality", {})
    if isinstance(quality.get("missing_inputs"), list):
        quality["missing_inputs"] = [item for item in quality["missing_inputs"] if item not in eligible_ids]
    if isinstance(analysis.get("missing_inputs"), list):
        analysis["missing_inputs"] = [item for item in analysis["missing_inputs"] if item not in eligible_ids]
    explanation = analysis.get("explanation")
    if isinstance(explanation, dict):
        # Keep candle-derived directional evidence distinct from optional
        # execution context; a spread or POC is not bullish confirmation.
        explanation["context_evidence"] = []
        if book.get("eligible"):
            explanation["context_evidence"].append(f"At the candle close, the recorded Level 1 spread was {book['spread']:g} points ({book['bid']:g} bid / {book['ask']:g} ask). It describes quoted transaction cost, not deeper liquidity.")
        if profile.get("eligible"):
            explanation["context_evidence"].append(f"Recorded trade-print POC was {profile['poc']:g}, limited to observations from {profile['observation_start']} to {profile['observation_end']}; session coverage is incomplete.")
        explanation["scope"] = "Direction and levels use closed candles. Eligible observations add explicitly scoped context only; the actual strategy and risk checks decide whether an order is permitted."
