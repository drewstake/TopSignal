"""Nonblocking, bounded capture of observations actually delivered by the feed.

No provider credentials, arbitrary raw payloads, synthetic depth or guessed trade
direction are stored. Persistence failures never run inside a trading transaction.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Any, Callable, Mapping
from uuid import uuid4

from sqlalchemy import func

from ..db import SessionLocal
from ..market_observation_models import DecisionResearchSnapshot, MarketObservation


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def timestamp(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return utc(parsed) if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def number(value: Any) -> float | None:
    try:
        result = float(value) if value is not None and not isinstance(value, bool) else None
        return result if result is not None and math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _setting(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def enabled() -> bool:
    return os.getenv("TOPSIGNAL_MARKET_CAPTURE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()).hexdigest()


class ObservationWriter:
    def __init__(self, session_factory: Callable = SessionLocal, *, capacity: int | None = None,
                 retention_days: int | None = None, record_cap: int | None = None, auto_start: bool = True):
        self.session_factory = session_factory
        self.capacity = capacity or _setting("TOPSIGNAL_MARKET_CAPTURE_QUEUE", 4096, 16, 50000)
        self.retention_days = retention_days or _setting("TOPSIGNAL_MARKET_CAPTURE_RETENTION_DAYS", 3, 1, 30)
        self.record_cap = record_cap or _setting("TOPSIGNAL_MARKET_CAPTURE_RECORD_CAP", 250000, 100, 2000000)
        self.decision_retention_days = _setting("TOPSIGNAL_DECISION_RETENTION_DAYS", 3650, 1, 36500)
        self.decision_record_cap = _setting("TOPSIGNAL_DECISION_RECORD_CAP", 1000000, 100, 10000000)
        self.queue: Queue = Queue(maxsize=self.capacity)
        self.auto_start = auto_start
        self._thread: Thread | None = None
        self._lock = Lock()
        self._seen: OrderedDict = OrderedDict()
        self._stats: OrderedDict = OrderedDict()
        self._closing = False

    def _stat(self, owner: str) -> dict:
        result = self._stats.setdefault(owner, {"dropped": 0, "write_errors": 0, "persisted": 0, "queued": 0})
        self._stats.move_to_end(owner)
        while len(self._stats) > 256:
            self._stats.popitem(last=False)
        return result

    def enqueue(self, kind: str, values: dict) -> bool:
        """All data is copied before enqueue; callers retain no mutable references."""
        owner = str(values.get("user_id") or "").strip()
        if not owner:
            return False
        key = (kind, owner, values.get("fingerprint", values.get("decision_id")))
        with self._lock:
            if self._closing:
                self._stat(owner)["dropped"] += 1
                return False
            if key in self._seen:
                return False
            try:
                self.queue.put_nowait((kind, dict(values)))
            except Full:
                self._stat(owner)["dropped"] += 1
                return False
            self._seen[key] = None
            self._stat(owner)["queued"] += 1
            while len(self._seen) > self.capacity * 2:
                self._seen.popitem(last=False)
            if self.auto_start and (self._thread is None or not self._thread.is_alive()):
                self._thread = Thread(target=self._run, name="market-observation-writer", daemon=True)
                self._thread.start()
        return True

    def _run(self) -> None:
        # Lazily started and idle-exits; no perpetual connection or bot arming.
        while True:
            try:
                first = self.queue.get(timeout=1.0)
            except Empty:
                with self._lock:
                    if self.queue.empty():
                        self._thread = None
                        return
                continue
            batch = [first]
            while len(batch) < 256:
                try:
                    batch.append(self.queue.get_nowait())
                except Empty:
                    break
            self._persist(batch)

    def flush(self) -> None:
        """Synchronous bounded test/shutdown helper, never called by feed hooks."""
        batch = []
        while len(batch) < self.capacity:
            try:
                batch.append(self.queue.get_nowait())
            except Empty:
                break
        if batch:
            self._persist(batch)

    def shutdown(self, *, timeout_seconds: float = 5.0) -> bool:
        """Stop accepting observations and drain in a daemon, with a bounded join."""
        with self._lock:
            self._closing = True
            if not self.queue.empty() and (self._thread is None or not self._thread.is_alive()):
                self._thread = Thread(target=self._run, name="market-observation-shutdown", daemon=True)
                self._thread.start()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0, min(30, timeout_seconds)))
        return self.queue.empty() and (thread is None or not thread.is_alive())

    def _persist(self, batch: list) -> None:
        owners = {values["user_id"] for _, values in batch}
        try:
            with self.session_factory() as db:
                # Dialect conflict-ignore prevents reconnect duplicates from
                # invalidating an entire batch; immutable decision snapshots win first-write.
                dialect = db.bind.dialect.name
                if dialect == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                elif dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert
                else:
                    raise RuntimeError("unsupported_observation_database")
                for kind, values in batch:
                    if kind == "routing":
                        db.query(DecisionResearchSnapshot).filter(DecisionResearchSnapshot.user_id == values["user_id"],
                            DecisionResearchSnapshot.decision_id == values["decision_id"]).update({"routing": values["routing"]}, synchronize_session=False)
                        continue
                    model = MarketObservation if kind == "market" else DecisionResearchSnapshot
                    db.execute(insert(model).values(**values).on_conflict_do_nothing())
                cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
                for owner in owners:
                    db.query(MarketObservation).filter(MarketObservation.user_id == owner, MarketObservation.received_at < cutoff).delete(synchronize_session=False)
                    # Keep the newest N. SQL offset lookup avoids loading IDs for
                    # a large retained archive into memory.
                    excess = db.query(MarketObservation.id).filter(MarketObservation.user_id == owner).order_by(MarketObservation.id.desc()).offset(self.record_cap).first()
                    if excess:
                        db.query(MarketObservation).filter(MarketObservation.user_id == owner, MarketObservation.id <= excess[0]).delete(synchronize_session=False)
                    decision_cutoff = datetime.now(timezone.utc) - timedelta(days=self.decision_retention_days)
                    db.query(DecisionResearchSnapshot).filter(DecisionResearchSnapshot.user_id == owner, DecisionResearchSnapshot.observed_at < decision_cutoff).delete(synchronize_session=False)
                    excess = db.query(DecisionResearchSnapshot.id).filter(DecisionResearchSnapshot.user_id == owner).order_by(DecisionResearchSnapshot.id.desc()).offset(self.decision_record_cap).first()
                    if excess:
                        db.query(DecisionResearchSnapshot).filter(DecisionResearchSnapshot.user_id == owner, DecisionResearchSnapshot.id <= excess[0]).delete(synchronize_session=False)
                db.commit()
            with self._lock:
                for _, values in batch:
                    self._stat(values["user_id"])["persisted"] += 1
        except Exception:
            # No raw DB error/payload logging: errors can contain sensitive data.
            with self._lock:
                for _, values in batch:
                    self._stat(values["user_id"])["write_errors"] += 1
        finally:
            with self._lock:
                for _, values in batch:
                    stats = self._stat(values["user_id"])
                    stats["queued"] = max(0, stats["queued"] - 1)
            for _ in batch:
                self.queue.task_done()

    def stats(self, owner: str) -> dict:
        with self._lock:
            return dict(self._stats.get(owner, {"dropped": 0, "write_errors": 0, "persisted": 0, "queued": 0}))


writer = ObservationWriter()


def capture_depth(*, user_id: str, contract_id: str, entry: Mapping, snapshot: Mapping | None = None,
                  received_at: datetime | None = None, source_epoch: str = "") -> bool:
    """DOM Trade/Fill volume is not documented as individual print size: omit it."""
    if not enabled():
        return False
    kind = number(entry.get("type"))
    when = timestamp(entry.get("timestamp"))
    if when is None or kind not in {1, 2, 3, 4, 6, 9, 10}:
        return False
    event_type = "reset" if kind == 6 else "depth" if kind in {1, 2} else "quote"
    price, size = number(entry.get("price")), number(entry.get("volume"))
    if event_type != "reset" and (price is None or price <= 0 or size is None or size < 0):
        return False
    side = "bid" if kind in {2, 4, 9} else "ask" if kind in {1, 3, 10} else None
    current_volume = number(entry.get("currentVolume"))
    bid = ask = None
    if snapshot:
        bids, asks = snapshot.get("bids") or [], snapshot.get("asks") or []
        bid = number(bids[0].get("price")) if bids else None
        ask = number(asks[0].get("price")) if asks else None
    if bid is not None and ask is not None and bid > ask:
        bid = ask = None
    values = dict(user_id=user_id, contract_id=contract_id, source="projectx_gateway_depth", event_type=event_type,
                  provider_timestamp=when, received_at=utc(received_at or datetime.now(timezone.utc)),
                  price=price if event_type != "reset" else None, size=size if event_type != "reset" else None,
                  side=side, bid=bid, ask=ask,
                  details={"dom_type": int(kind), "current_volume": current_volume,
                           "volume_semantics": "resting_aggregate" if event_type != "reset" else "reset", "source_epoch": source_epoch})
    values["fingerprint"] = digest([contract_id, event_type, when.isoformat(), kind, price, size, current_volume, source_epoch])
    return writer.enqueue("market", values)


def capture_gap(*, user_id: str, contract_id: str, reason: str, source_epoch: str = "") -> bool:
    if not enabled():
        return False
    when = datetime.now(timezone.utc)
    values = dict(user_id=user_id, contract_id=contract_id, source="projectx_gateway_depth", event_type="gap",
                  provider_timestamp=None, received_at=when, details={"reason": reason[:80], "source_epoch": source_epoch})
    values["fingerprint"] = digest([contract_id, when.isoformat(), reason, source_epoch])
    return writer.enqueue("market", values)


def capture_trade(*, user_id: str, contract_id: str, payload: Mapping, received_at: datetime | None = None) -> bool:
    """Only GatewayTrade records; never DOM trade events or account executions.

    Aggressor side stays unknown unless explicitly supplied under a named
    aggressor field. Provider `type`/`side` enums are not guessed.
    """
    if not enabled():
        return False
    when = timestamp(payload.get("timestamp"))
    price, size = number(payload.get("price")), number(payload.get("volume"))
    if when is None or price is None or price <= 0 or size is None or size <= 0:
        return False
    aggressor = str(payload.get("aggressorSide") or "").strip().lower()
    side = aggressor if aggressor in {"buy", "sell"} else None
    trade_id = str(payload.get("tradeId") or payload.get("id") or "")[:128] or None
    # Without an exchange trade ID, identical prints can be distinct; retain
    # every delivery rather than undercount volume with a guessed duplicate key.
    received = utc(received_at or datetime.now(timezone.utc))
    values = dict(user_id=user_id, contract_id=contract_id, source="projectx_gateway_trade", event_type="trade",
                  provider_timestamp=when, received_at=received, price=price, size=size, side=side,
                  details={"trade_id": trade_id, "provider_trade_log_type": int(number(payload["type"])) if number(payload.get("type")) in {0, 1} else None,
                           "classification": "explicit_aggressor" if side else "unavailable", "deduplication": "provider_id" if trade_id else "single_source_delivery"})
    values["fingerprint"] = digest([contract_id, "trade", trade_id]) if trade_id else uuid4().hex
    return writer.enqueue("market", values)


def observation_status(db, *, user_id: str, contract_id: str | None = None) -> dict:
    query = db.query(MarketObservation).filter(MarketObservation.user_id == user_id)
    if contract_id:
        query = query.filter(MarketObservation.contract_id == contract_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=writer.retention_days)
    query = query.filter(MarketObservation.received_at >= cutoff)
    count, first, last = query.with_entities(func.count(MarketObservation.id), func.min(MarketObservation.received_at), func.max(MarketObservation.received_at)).one()
    counts = {key: 0 for key in ("quote", "depth", "reset", "gap", "trade")}
    counts.update(dict(query.with_entities(MarketObservation.event_type, func.count(MarketObservation.id)).group_by(MarketObservation.event_type).all()))
    contracts = [row[0] for row in query.with_entities(MarketObservation.contract_id).distinct().limit(100)]
    # Profiles must never mix contracts. Require a single selected/observed contract.
    selected = contract_id or (contracts[0] if len(contracts) == 1 else None)
    trades = query.filter(MarketObservation.event_type == "trade", MarketObservation.contract_id == selected) if selected else query.filter(False)
    total_volume, classified_volume = 0.0, 0.0
    levels = []
    side_totals = dict(trades.with_entities(MarketObservation.side, func.sum(MarketObservation.size)).group_by(MarketObservation.side).all())
    total_volume = sum(float(v or 0) for v in side_totals.values())
    classified_volume = sum(float(side_totals.get(side) or 0) for side in ("buy", "sell"))
    if selected:
        levels = [{"price": float(price), "volume": float(volume)} for price, volume in trades.with_entities(MarketObservation.price, func.sum(MarketObservation.size)).group_by(MarketObservation.price).order_by(MarketObservation.price).limit(2000)]
    spreads = query.filter(MarketObservation.contract_id == selected, MarketObservation.bid.isnot(None), MarketObservation.ask.isnot(None)) if selected else query.filter(False)
    spread_count, mean_spread = spreads.with_entities(func.count(MarketObservation.id), func.avg(MarketObservation.ask - MarketObservation.bid)).one()
    latest = spreads.order_by(MarketObservation.id.desc()).first()
    stats = writer.stats(user_id)
    warnings = ["Capture runs while the order-book stream has a viewer; it is not a complete exchange archive.", "Profile uses GatewayTrade prints only. DOM volume is resting size, not traded volume.",
                "GatewayTrade without trade IDs preserves every single-source delivery. Replayed provider deliveries cannot be proven distinct.",
                "Provider Buy/Sell TradeLogType is retained, but is not assumed to establish an explicit aggressor side."]
    if not selected:
        warnings.append("Select one contract to calculate a volume profile and spread summary.")
    if stats["dropped"] or stats["write_errors"]:
        warnings.append("Some observations were lost; process counters report dropped or failed writes.")
    return {"enabled": enabled(), "capture_mode": "viewer_driven", "retention_days": writer.retention_days,
            "record_cap": writer.record_cap, "queue_capacity": writer.capacity,
            **stats, "event_count": count, "first_received_at": first, "last_received_at": last, "counts": counts,
            "contracts": contracts, "profile": {"contract_id": selected, "trade_count": trades.count(), "total_volume": total_volume,
                "classified_volume": classified_volume, "classification_coverage": classified_volume / total_volume if total_volume else None,
                "delta": float(side_totals.get("buy") or 0) - float(side_totals.get("sell") or 0) if total_volume and classified_volume == total_volume else None,
                "levels": levels, "basis": "observed_gateway_trade_prints", "partial": True},
            "spread": {"sample_count": spread_count, "latest": float(latest.ask - latest.bid) if latest else None,
                       "mean": float(mean_spread) if mean_spread is not None else None, "basis": "event_weighted_not_time_weighted"},
            "warnings": warnings}
