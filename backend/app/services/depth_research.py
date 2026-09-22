"""Causal, bounded MBP reconstruction for research, independent of trading/DB.

ProjectX has no documented snapshot-complete or sequence contract. Raw updates
therefore expose observed capability but cannot assert synchronization. The
explicit_snapshot_v1 adapter supports complete, externally verified MBP snapshots
and deterministic fixtures. Fixture provenance never establishes real capability.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

TICK = .25
POINT_VALUE = 2.
SCHEMA = "mnq-depth-events-v1"
L1_NAMES = ("spread_ticks", "imbalance_1", "weighted_mid_ticks", "ofi_30s")
SHAPE_NAMES = ("imbalance_5", "weighted_imbalance_5", "shape_asymmetry", "concentration_asymmetry", "log_depth")
FLOW_NAMES = ("depth_change_30s", "replenishment_30s", "depletion_30s", "persistence_30s")
L2_NAMES = SHAPE_NAMES + FLOW_NAMES


def timestamp(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("An explicit UTC offset is required.")
    return result.astimezone(timezone.utc)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a market quantity.")
    x = float(value)
    if not math.isfinite(x) or x < 0 or (positive and x <= 0):
        raise ValueError("Invalid market quantity.")
    return x


def price(value: Any) -> float:
    x = number(value, positive=True)
    if abs(x / TICK - round(x / TICK)) > 1e-7:
        raise ValueError("MNQ price is not tick aligned.")
    return x


@dataclass(frozen=True)
class Event:
    owner_hash: str
    contract_id: str
    data_live: bool
    capture_id: str
    index: int  # Local receive order ONLY. Never an exchange sequence.
    received_at: datetime
    monotonic_ns: int
    kind: str
    payload: dict
    provider_at: datetime | None = None
    source: str = "projectx"
    synthetic: bool = False

    def __post_init__(self):
        if (len(self.owner_hash) != 64 or any(c not in "0123456789abcdef" for c in self.owner_hash)
                or not self.contract_id.startswith("CON.F.US.MNQ.") or type(self.data_live) is not bool
                or self.index < 1 or self.monotonic_ns < 0 or not self.capture_id
                or self.source not in {"projectx", "explicit_snapshot_v1"}
                or type(self.synthetic) is not bool or not isinstance(self.payload, dict)):
            raise ValueError("Invalid event scope or envelope.")
        timestamp(self.received_at)
        if self.provider_at is not None:
            timestamp(self.provider_at)
        if len(canonical(self.payload)) > 65536:
            raise ValueError("Oversized event payload.")

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, **self.__dict__, "received_at": self.received_at.isoformat(),
                "provider_at": self.provider_at.isoformat() if self.provider_at else None}

    @classmethod
    def from_dict(cls, row: dict) -> Event:
        data = dict(row)
        if data.pop("schema", None) != SCHEMA:
            raise ValueError("Unknown event schema.")
        data["received_at"] = timestamp(data["received_at"])
        data["provider_at"] = timestamp(data["provider_at"]) if data["provider_at"] else None
        return cls(**data)


class ResearchBook:
    def __init__(self, owner_hash: str, contract_id: str, data_live: bool = False, *, levels: int = 5):
        if levels not in {3, 5, 10}:
            raise ValueError("Unsupported depth sensitivity.")
        self.scope = (owner_hash, contract_id, data_live)
        self.levels = levels
        self.counts: Counter = Counter()
        self.capture_id = None
        self.index = 0
        self.received_at: datetime | None = None
        self.monotonic_ns = -1
        self.synthetic = False
        self.observed_depth = False
        self.observed_l1 = False
        self._clear("no_snapshot")

    def _clear(self, reason: str):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.provider_at: datetime | None = None
        self.depth_received_at: datetime | None = None
        self.synced = False
        self.reason = reason
        self.history: deque = deque(maxlen=100000)
        self.recent: deque = deque(maxlen=4096)
        self.recent_set: set = set()
        self.quote = None
        self.sequence = None
        self.trades: deque = deque(maxlen=10000)

    def _invalidate(self, reason: str):
        self.counts["invalid:" + reason] += 1
        self._clear(reason)

    def apply(self, e: Event):
        if (e.owner_hash, e.contract_id, e.data_live) != self.scope:
            raise ValueError("Cross-owner, contract or subscription event.")
        if self.capture_id != e.capture_id:
            self._clear("new_capture_requires_snapshot")
            self.capture_id, self.index, self.received_at, self.monotonic_ns = e.capture_id, 0, None, -1
        if e.index <= self.index:
            self._invalidate("duplicate_or_reversed_receive_index")
            return
        if e.index != self.index + 1:
            self._invalidate("missing_local_event")
        if ((self.received_at is not None and e.received_at < self.received_at)
                or e.monotonic_ns < self.monotonic_ns):
            self._invalidate("receive_clock_reversal")
            self.index = e.index
            return
        self.index, self.received_at, self.monotonic_ns = e.index, e.received_at, e.monotonic_ns
        self.synthetic = self.synthetic or e.synthetic
        self.counts[e.kind] += 1
        if e.kind == "candle":
            return  # Bar opening time is not a fresh depth-event timestamp.
        if e.kind == "depth" and type(e.payload.get("type")) is int and e.payload["type"] in {0, 5, 7, 8, 11}:
            return  # Preserve raw prints/extremes; they do not update resting liquidity.
        if e.kind in {"connect", "disconnect", "gap", "roll", "reset", "end"}:
            self._invalidate(e.kind)
            return
        try:
            if e.provider_at is None:
                raise ValueError("Missing provider timestamp.")
            lag = (e.received_at - e.provider_at).total_seconds()
            if not 0 <= lag <= 2:
                self._invalidate("provider_time_lag_or_clock_skew")
                return
            if e.kind == "trade":
                # TradeLogType labels are preserved raw, not promoted to aggressor semantics.
                self.trades.append((e.received_at, price(e.payload["price"]), number(e.payload["volume"])))
                return
            if e.kind == "quote":
                bid, ask = price(e.payload["bestBid"]), price(e.payload["bestAsk"])
                if bid >= ask:
                    raise ValueError("Crossed quote.")
                self.observed_l1 = True
                self.quote = (bid, ask, e.received_at)
                return
            if e.kind not in {"depth", "snapshot"}:
                return
            if self.provider_at is not None and e.provider_at < self.provider_at:
                self._invalidate("out_of_order_provider_event")
                return
            if self.depth_received_at and (e.received_at - self.depth_received_at).total_seconds() > 2:
                self._invalidate("depth_gap_requires_snapshot")
            if e.kind == "snapshot":
                if e.source != "explicit_snapshot_v1" or e.payload.get("complete") is not True:
                    self._invalidate("unverified_snapshot_boundary")
                    return
                self._clear("snapshot_warmup")
                for name in ("bids", "asks"):
                    pairs = [(price(p), number(q, positive=True)) for p, q in e.payload[name]]
                    if len(pairs) != len(dict(pairs)) or not 1 <= len(pairs) <= 100:
                        raise ValueError("Duplicate or excessive snapshot levels.")
                    setattr(self, name, dict(pairs))
                self.synced = True
            else:
                dom_type = e.payload.get("type")
                if type(dom_type) is not int:
                    raise ValueError("Unknown depth type.")
                if dom_type == 6:
                    self._invalidate("provider_reset_requires_snapshot")
                    return
                if dom_type not in {1, 2, 3, 4, 9, 10}:
                    return  # Session extremes, prints and fills aren't resting depth.
                p, q = price(e.payload["price"]), number(e.payload["volume"])
                if e.payload.get("currentVolume") is not None:
                    number(e.payload["currentVolume"])
                fingerprint = digest({"t": e.provider_at.isoformat(), "payload": e.payload})
                if fingerprint in self.recent_set:
                    self.counts["duplicate_absolute_update"] += 1
                    return
                if len(self.recent) == self.recent.maxlen:
                    self.recent_set.discard(self.recent[0])
                self.recent.append(fingerprint)
                self.recent_set.add(fingerprint)
                side = self.bids if dom_type in {2, 4, 9} else self.asks
                if dom_type in {3, 4, 9, 10}:
                    self.observed_l1 = True
                    # Best-only updates do not prove additional resting levels.
                    if e.source == "projectx":
                        self.synced = False
                        self.reason = "unverified_snapshot_boundary"
                        return
                if q == 0:
                    side.pop(p, None)
                else:
                    side[p] = q  # Documented total quantity; never sum a delta.
                if len(side) > 100:
                    self._invalidate("book_size_limit")
                    return
                if e.source == "projectx":
                    self.synced = False
                    self.reason = "unverified_snapshot_boundary"
            if e.source == "explicit_snapshot_v1" and ("sequence" in e.payload or self.sequence is not None):
                seq = e.payload.get("sequence")
                if type(seq) is not int or (self.sequence is not None and seq != self.sequence + 1):
                    self._invalidate("provider_sequence_gap")
                    return
                self.sequence = seq
            self.provider_at, self.depth_received_at = e.provider_at, e.received_at
            if len(self.bids) >= 2 and len(self.asks) >= 2:
                self.observed_depth = True
            if self.bids and self.asks and max(self.bids) >= min(self.asks):
                self._invalidate("crossed_book_requires_snapshot")
                return
            if self.synced and len(self.bids) >= self.levels and len(self.asks) >= self.levels:
                if len(self.history) == self.history.maxlen and (e.received_at - self.history[0][0]).total_seconds() <= 30:
                    self._invalidate("feature_history_overflow")
                    return
                bids = sorted(self.bids.items(), reverse=True)[:self.levels]
                asks = sorted(self.asks.items())[:self.levels]
                self.history.append((e.received_at, bids, asks))
                while len(self.history) > 1 and (e.received_at - self.history[1][0]).total_seconds() > 30:
                    self.history.popleft()
            elif self.synced:
                self.history.clear()  # Never bridge a period with too few levels.
        except (KeyError, TypeError, ValueError, OverflowError):
            self._invalidate("malformed_market_event")

    def status(self, as_of: datetime) -> dict:
        as_of = timestamp(as_of)
        age = (as_of - self.depth_received_at).total_seconds() if self.depth_received_at else None
        lag = (as_of - self.provider_at).total_seconds() if self.provider_at else None
        fresh = age is not None and lag is not None and 0 <= age <= 2 and 0 <= lag <= 2
        enough = min(len(self.bids), len(self.asks)) >= self.levels
        warm = bool(self.history) and (as_of - self.history[0][0]).total_seconds() >= 30
        ready = self.synced and fresh and enough and warm
        reason = (self.reason if not self.synced else "stale_book" if not fresh else "insufficient_levels"
                  if not enough else "feature_window_warmup" if not warm else "ready")
        capability = "fixture_level2" if self.synthetic else "verified_level2" if self.observed_depth else "level1" if self.observed_l1 else "unverified"
        return {"capability": capability, "synchronized": self.synced and fresh and enough,
                "feature_ready": ready, "age_seconds": age, "provider_age_seconds": lag,
                "bid_levels": len(self.bids), "ask_levels": len(self.asks), "reason": reason,
                "synthetic": self.synthetic, "received_at": self.depth_received_at.isoformat() if self.depth_received_at else None,
                "sequencing": "local_receive_order; provider_sequence_unavailable" if self.sequence is None else "explicit_adapter_sequence"}

    def features(self, as_of: datetime) -> dict[str, float] | None:
        if not self.status(as_of)["feature_ready"]:
            return None
        history = list(self.history)
        # No future events may enter a historical feature query.
        if any(t > as_of for t, _, _ in history):
            return None
        _, bids, asks = history[-1]
        bp, bq = bids[0]
        ap, aq = asks[0]
        b, a = sum(q for _, q in bids), sum(q for _, q in asks)
        mid = (bp + ap) / 2
        imbalance = (b - a) / (b + a)
        wb = sum(q / (1 + (bp - p) / TICK) for p, q in bids)
        wa = sum(q / (1 + (p - ap) / TICK) for p, q in asks)
        db = sum(q * (bp - p) / TICK for p, q in bids) / b
        da = sum(q * (p - ap) / TICK for p, q in asks) / a
        ofi = replenished = depleted = persistence = duration = 0.
        start = as_of - timedelta(seconds=30)
        for (t0, prev_b, prev_a), (t1, next_b, next_a) in zip(history, history[1:]):
            p0, q0 = prev_b[0]; p1, q1 = next_b[0]
            s0, v0 = prev_a[0]; s1, v1 = next_a[0]
            if t1 > start:
                ofi += (q1 if p1 >= p0 else 0) - (q0 if p1 <= p0 else 0) - (v1 if s1 <= s0 else 0) + (v0 if s1 >= s0 else 0)
                # Only same-price displayed quantities: no cancellation/intent inference.
                for old, new in ((dict(prev_b), dict(next_b)), (dict(prev_a), dict(next_a))):
                    for p in old.keys() & new.keys():
                        change = new[p] - old[p]
                        replenished += max(change, 0)
                        depleted += max(-change, 0)
            dt = max(0., (t1 - max(t0, start)).total_seconds())
            qb, qa = sum(q for _, q in prev_b), sum(q for _, q in prev_a)
            persistence += dt * (qb - qa) / (qb + qa); duration += dt
        dt = max(0., (as_of - max(history[-1][0], start)).total_seconds())
        persistence += dt * imbalance; duration += dt
        if duration < 30 - 1e-6:
            return None
        old_b, old_a = history[0][1:]
        old_depth = sum(q for _, q in old_b + old_a)
        result = {"spread_ticks": (ap - bp) / TICK, "imbalance_1": (bq - aq) / (bq + aq),
                  "weighted_mid_ticks": ((ap * bq + bp * aq) / (bq + aq) - mid) / TICK,
                  "ofi_30s": ofi / (bq + aq), "imbalance_5": imbalance,
                  "weighted_imbalance_5": (wb - wa) / (wb + wa),
                  "shape_asymmetry": (db - da) / (1 + db + da),
                  "concentration_asymmetry": sum((q / b) ** 2 for _, q in bids) - sum((q / a) ** 2 for _, q in asks),
                  "log_depth": math.log1p(b + a), "depth_change_30s": (b + a - old_depth) / old_depth,
                  "replenishment_30s": replenished / old_depth, "depletion_30s": depleted / old_depth,
                  "persistence_30s": persistence / duration}
        return result if all(math.isfinite(v) for v in result.values()) else None

    def sweep(self, side: str, quantity: int, as_of: datetime) -> dict | None:
        if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity < 1:
            raise ValueError("Invalid sweep request.")
        if not self.status(as_of)["feature_ready"]:
            return None
        levels = sorted(self.asks.items()) if side == "BUY" else sorted(self.bids.items(), reverse=True)
        remaining, cash = float(quantity), 0.
        for p, q in levels[:self.levels]:
            taken = min(remaining, q); cash += taken * p; remaining -= taken
        if remaining:
            return None  # Never extrapolate beyond the observed book.
        mid = (max(self.bids) + min(self.asks)) / 2
        vwap = cash / quantity
        return {"displayed_vwap": vwap, "cost_from_mid_usd": (1 if side == "BUY" else -1) * (vwap - mid) * POINT_VALUE * quantity,
                "basis": "Instantaneous displayed liquidity scenario; not a fill or queue-position estimate."}
