"""Worker-owned observed DOM, independent of browser viewers.

ProjectX has no documented full-snapshot boundary. Require 100 observed levels
per side and restart qualification after gaps; never claim exchange completeness.
"""
import asyncio
from copy import deepcopy
from collections import deque
from datetime import datetime, timezone
import math
from threading import RLock, Thread
from time import monotonic


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class DepthState:
    def __init__(self, contract, threshold=.1):
        self.contract, self.threshold = contract, threshold
        self.bids, self.asks = {}, {}
        self.last_at = self.episode_at = self.sequence = None
        self.direction = 0
        self.connected = False
        self.tracks = {}
        self.track_history = {}

    def reset(self):
        self.bids.clear(); self.asks.clear()
        self.last_at = self.episode_at = self.sequence = None
        self.direction = 0
        for track in self.tracks.values():
            track["coverage_complete"] = False

    def apply(self, event, now):
        data = event.get("data") or {}
        if event.get("event") == "state":
            self.connected = data.get("state") == "connected"
            if not self.connected: self.reset()
            return
        if data.get("contract_id") != self.contract: return
        if data.get("reset"):
            self.reset(); self.sequence = data.get("sequence")
            return
        try:
            if not data.get("timestamp"):
                self.reset(); self.sequence = data.get("sequence")
                return
            at = utc(data["timestamp"])
            if not 0 <= (now-at).total_seconds() <= 2:
                self.reset(); return
            sequence = data["sequence"]
            if self.sequence is not None and sequence <= self.sequence: return
            if self.last_at and (at-self.last_at).total_seconds() > 2: self.reset()
            if event["event"] == "snapshot":
                if self.sequence is None or sequence != self.sequence+1:
                    self.episode_at = None; self.direction = 0
                    for track in self.tracks.values(): track["coverage_complete"] = False
                self.bids = {float(r["price"]): float(r["size"]) for r in data["bids"]}
                self.asks = {float(r["price"]): float(r["size"]) for r in data["asks"]}
            else:
                if self.sequence is None or sequence != self.sequence+1:
                    self.reset(); return
                book = self.bids if data["side"] == "bid" else self.asks
                price, size = float(data["price"]), float(data["size"])
                if size == 0: book.pop(price, None)
                else: book[price] = size
            if (max(len(self.bids), len(self.asks)) > 2000 or
                any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0
                    for p, q in list(self.bids.items())+list(self.asks.items()))):
                self.reset(); return
            self.last_at, self.sequence = at, sequence
            self.connected = True
            if self.bids and self.asks and max(self.bids) < min(self.asks):
                for track in self.tracks.values():
                    mark = max(self.bids) if track["side"] == "BUY" else min(self.asks)
                    points = (mark-track["entry_price"]) * (1 if track["side"] == "BUY" else -1)
                    if at < utc(track["entry_at"]): continue
                    track["mfe_points"] = max(track["mfe_points"], points)
                    track["mae_points"] = min(track["mae_points"], points)
                    track["last_points"], track["observed_at"] = points, at.isoformat()
                    for label, crossed in (("plus_10", points >= 10), ("plus_25", points >= 25), ("minus_10", points <= -10)):
                        if crossed and label not in track:
                            track[label] = max(0, (at-utc(track["entry_at"])).total_seconds())
                    history = self.track_history[id(track)]
                    if not history or (at-utc(history[-1]["observed_at"])).total_seconds() >= .1:
                        history.append(deepcopy(track))
            result = self.snapshot(now)
            direction = (1 if result.get("imbalance", 0) > 0 else -1) if result.get("ready") and abs(result["imbalance"]) >= self.threshold and result["imbalance"] != 0 else 0
            if direction != self.direction or self.episode_at is None:
                self.episode_at = at if direction else None
            self.direction = direction
        except (KeyError, ValueError, TypeError, OverflowError):
            self.reset()

    def snapshot(self, now):
        base = {"ready": False, "reason": "Waiting for 100 observed levels per side", "bid_levels": min(100,len(self.bids)),
                "ask_levels": min(100,len(self.asks)), "depth_basis": "observed_levels_not_certified_snapshot"}
        if not self.connected or not self.last_at or not 0 <= (now-self.last_at).total_seconds() <= 2:
            return {**base, "reason": "Depth disconnected or stale"}
        if not self.bids or not self.asks or max(self.bids) >= min(self.asks):
            return {**base, "reason": "Depth is empty or crossed"}
        bid, ask = max(self.bids), min(self.asks)
        b = sum(self.bids[p] for p in sorted(self.bids, reverse=True)[:100])
        a = sum(self.asks[p] for p in sorted(self.asks)[:100])
        return {**base, "ready": min(len(self.bids),len(self.asks)) >= 100, "bid": bid, "ask": ask,
                "bid_total": b, "ask_total": a, "imbalance": (b-a)/(b+a),
                "observed_at": self.last_at.isoformat(), "episode_at": self.episode_at.isoformat() if self.episode_at else None,
                "persistence_seconds": max(0,(self.last_at-self.episode_at).total_seconds()) if self.episode_at else 0}


class DepthFeed:
    def __init__(self):
        self.lock = RLock()
        self.streams = {}

    def ensure(self, owner, contract, client, threshold=.1):
        key = (str(owner), contract)
        with self.lock:
            stream = self.streams.get(key)
            if stream:
                stream["touched"] = monotonic()
                if stream["state"].threshold != threshold:
                    stream["state"].threshold = threshold
                    stream["state"].episode_at = None
                return
            if len(self.streams) >= 8: return
            stream = {"state": DepthState(contract, threshold), "touched": monotonic()}
            self.streams[key] = stream
            Thread(target=self._run, args=(key, stream, client), daemon=True, name="topbot-depth").start()

    def _run(self, key, stream, client):
        async def consume():
            from .projectx_order_book import ProjectXOrderBookRegistry
            registry = ProjectXOrderBookRegistry()
            try:
                sub = await registry.subscribe(user_id=key[0], client=client, contract_id=key[1])
                for event in sub.initial_events:
                    with self.lock: stream["state"].apply(event, datetime.now(timezone.utc))
                while monotonic()-stream["touched"] < 120:
                    try: event = await asyncio.wait_for(sub.queue.get(), timeout=1)
                    except asyncio.TimeoutError: continue
                    with self.lock: stream["state"].apply(event, datetime.now(timezone.utc))
            finally:
                await registry.close()
        try: asyncio.run(consume())
        except Exception: pass  # Missing/stale state explicitly blocks entries.
        finally:
            with self.lock:
                if self.streams.get(key) is stream: self.streams.pop(key, None)

    def snapshot(self, owner, contract, *, now=None):
        with self.lock:
            stream = self.streams.get((str(owner),contract))
            return stream["state"].snapshot(now or datetime.now(timezone.utc)) if stream else {"ready": False, "reason": "Waiting for worker depth subscription"}

    def track(self, owner, contract, entry_id, initial, end_at=None):
        with self.lock:
            stream = self.streams.get((str(owner),contract))
            fields = ("side", "entry_price", "entry_at", "mfe_points", "mae_points", "coverage_complete",
                      "last_points", "observed_at", "plus_10", "plus_25", "minus_10")
            seed = {k: deepcopy(initial[k]) for k in fields if k in initial}
            if not stream: return seed
            tracks = stream["state"].tracks
            if entry_id not in tracks:
                tracks[entry_id] = {**seed, "coverage_complete": False}
                stream["state"].track_history[id(tracks[entry_id])] = deque(maxlen=2048)
            if end_at is not None:
                history = stream["state"].track_history[id(tracks[entry_id])]
                return deepcopy(next((r for r in reversed(history) if utc(r["observed_at"]) <= end_at), seed))
            tracks[entry_id]["mfe_points"] = max(tracks[entry_id]["mfe_points"], seed["mfe_points"])
            tracks[entry_id]["mae_points"] = min(tracks[entry_id]["mae_points"], seed["mae_points"])
            return deepcopy(tracks[entry_id])

    def untrack(self, owner, contract, entry_id):
        with self.lock:
            stream = self.streams.get((str(owner),contract))
            if stream:
                track = stream["state"].tracks.pop(entry_id, None)
                if track is not None: stream["state"].track_history.pop(id(track), None)


feed = DepthFeed()
