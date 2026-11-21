"""Prospective executable-quote study. No account, order, or provider mutations."""

import heapq
import json
import math
import sqlite3
import zlib
from bisect import bisect_left
from collections import Counter, defaultdict, deque
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

HORIZONS = (30, 120, 300)
SCHEMA = "imbalance-study-v1"
ET = ZoneInfo("America/New_York")


def directory(cache, owner, account):
    return Path(cache) / SCHEMA / sha256(str(owner).encode()).hexdigest() / str(int(account))


def batch_before(cap, stamp):
    """Seek to the batch preceding a receipt time. The caller must seed from a full snapshot."""
    last = cap.db.execute("SELECT coalesce(max(id),0) FROM batches").fetchone()[0]
    lo, hi = 1, last + 1
    while lo < hi:
        mid = (lo + hi) // 2
        raw = cap.db.execute("SELECT payload FROM batches WHERE id=?", (mid,)).fetchone()[0]
        decoder = zlib.decompressobj()
        data = decoder.decompress(raw, 6 * 1024**2)
        if not decoder.eof:
            raise ValueError("Invalid capture batch")
        end = datetime.fromisoformat(json.loads(data.splitlines()[-1])["received_at"]).timestamp()
        if end < stamp:
            lo = mid + 1
        else:
            hi = mid
    row = cap.db.execute("SELECT id,hash,last_index FROM batches WHERE id=?", (lo - 1,)).fetchone()
    return tuple(row) if row else (0, cap.meta_hash, 0)


def open_store(path):
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS anchors(
          id INTEGER PRIMARY KEY, source TEXT, capture TEXT, contract TEXT, rule TEXT,
          episode TEXT, at REAL NOT NULL, side INTEGER NOT NULL, bid REAL, ask REAL,
          bucket TEXT, control INTEGER UNIQUE, disposition TEXT, reason TEXT, entry_id INTEGER,
          UNIQUE(source,capture,contract,rule,episode));
        CREATE INDEX IF NOT EXISTS controls ON anchors(source,capture,contract,rule,bucket,side,at);
        CREATE INDEX IF NOT EXISTS signals ON anchors(source,contract,side,at);
        CREATE TABLE IF NOT EXISTS marks(anchor INTEGER, seconds INTEGER, bid REAL, ask REAL,
          reason TEXT, PRIMARY KEY(anchor,seconds));
        CREATE TABLE IF NOT EXISTS cursors(capture TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gaps(source TEXT, capture TEXT, at REAL, reason TEXT,
          UNIQUE(source,capture,at,reason));
        CREATE TABLE IF NOT EXISTS episode_ends(anchor INTEGER PRIMARY KEY, at REAL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    """)
    return db


class Observations:
    """One contiguous capture. Controls are chosen from entry-time covariates only."""

    def __init__(self, db, source, capture, contract, start, state=None):
        self.db, self.source, self.capture, self.contract = db, source, capture, contract
        self.start = start
        state = state or {}
        self.history = deque(state.get("history", []))
        self.control_slot = state.get("control_slot")
        self.last = state.get("last")
        self.last_gap = state.get("last_gap")
        self.due = [tuple(x) for x in state.get("due", [])]
        heapq.heapify(self.due)

    def checkpoint(self):
        return {
            "history": list(self.history),
            "control_slot": self.control_slot,
            "last": self.last,
            "last_gap": self.last_gap,
            "due": self.due,
        }

    def _mark(self, anchor, horizon, bid, ask, reason=None):
        self.db.execute(
            "INSERT OR IGNORE INTO marks VALUES(?,?,?,?,?)", (anchor, horizon, bid, ask, reason)
        )

    def gap(self, at, reason):
        if not self.last_gap and at >= self.start:
            self.db.execute(
                "INSERT OR IGNORE INTO gaps VALUES(?,?,?,?)", (self.source, self.capture, at, reason)
            )
        self.last_gap = reason
        self.history.clear()
        while self.due:
            _, anchor, horizon = heapq.heappop(self.due)
            self._mark(anchor, horizon, None, None, reason)

    def observe(self, at, quote, events, threshold, persistence):
        for event in events:
            if event["kind"] == "episode_reset" and event.get("qualified"):
                self.db.execute(
                    """INSERT OR IGNORE INTO episode_ends
                    SELECT id,? FROM anchors WHERE source=? AND capture=? AND contract=?
                    AND rule=? AND episode=? AND side!=0""",
                    (
                        at,
                        self.source,
                        self.capture,
                        self.contract,
                        f"{threshold:g}/{persistence:g}",
                        event["episode_at"],
                    ),
                )
        if self.last is not None and (at - self.last > 2 or at < self.last):
            self.gap(at, "quote gap" if at >= self.last else "receipt time reversed")
        self.last = at
        if any(
            e["kind"] == "episode_reset"
            and e.get("reason")
            in {
                "sequence_gap",
                "depth_gap",
                "provider_reset",
                "disconnected",
                "invalid_depth",
                "stale_timestamp",
            }
            for e in events
        ):
            self.gap(at, "depth continuity interrupted")
        if not quote or not quote.get("ready"):
            self.gap(at, "unavailable book")
        else:
            self.last_gap = None
        bid, ask = (quote["bid"], quote["ask"]) if quote and quote.get("ready") else (None, None)
        while self.due and self.due[0][0] <= at:
            due, anchor, horizon = heapq.heappop(self.due)
            if bid is None or at - due > 2:
                self._mark(anchor, horizon, None, None, "horizon quote unavailable")
            else:
                self._mark(anchor, horizon, bid, ask)
        second = math.floor(at)
        while self.history and self.history[0][0] < second - 63:
            self.history.popleft()
        rule = f"{threshold:g}/{persistence:g}"
        qualifications = [e for e in events if e["kind"] == "episode_qualified"]
        slot = second // 30
        baseline = (
            bid is not None
            and abs(quote["imbalance"]) < threshold
            and self.control_slot != slot
            and at - slot * 30 <= 2
        )
        if at >= self.start and (qualifications or baseline):
            # A midpoint at or immediately before t-60; never read forward to build matching features.
            prior = next((p for _, t, p in reversed(self.history) if 0 <= at - 60 - t <= 2), None)
            bucket = None
            if prior is not None and bid is not None:
                wall = datetime.fromtimestamp(at, ET)
                bucket = f"{wall:%Y%m%d%H%z}/{round((ask - bid) * 4)}/{math.floor(((bid + ask) / 2 - prior) / 2)}"
            if baseline:
                self.control_slot = slot
                if bucket:
                    self._anchor(rule, f"slot-{slot}", at, 0, bid, ask, bucket)
            for event in qualifications:
                # Keep the v1 ledger encoding for audit and existing replay readers.
                # EpisodeMatches resolves worker order side from the saved strategy separately.
                self._anchor(rule, event["episode_at"], at, -int(event["direction"]), bid, ask, bucket)
        if bid is not None:
            point = (second, at, (bid + ask) / 2)
            if self.history and self.history[-1][0] == second:
                self.history[-1] = point
            else:
                self.history.append(point)

    def _anchor(self, rule, episode, at, side, bid, ask, bucket):
        control = None
        if side and bucket:
            row = self.db.execute(
                """SELECT a.id FROM anchors a WHERE source=? AND capture=?
                AND contract=? AND rule=? AND bucket=? AND side=0 AND at<=?
                AND NOT EXISTS(SELECT 1 FROM anchors s WHERE s.control=a.id)
                ORDER BY at DESC LIMIT 1""",
                (self.source, self.capture, self.contract, rule, bucket, at - 300),
            ).fetchone()
            control = row[0] if row else None
        result = self.db.execute(
            """INSERT OR IGNORE INTO anchors
            (source,capture,contract,rule,episode,at,side,bid,ask,bucket,control)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (self.source, self.capture, self.contract, rule, episode, at, side, bid, ask, bucket, control),
        )
        if not result.rowcount:
            return
        for horizon in HORIZONS:
            if bid is None:
                self._mark(result.lastrowid, horizon, None, None, "qualification quote unavailable")
            else:
                heapq.heappush(self.due, (at + horizon, result.lastrowid, horizon))


def broker_ids(runner, name):
    """Accept both the original single-fill receipt and split-fill receipts."""
    ids = runner.get(name + "s") or []
    first = runner.get(name)
    if not isinstance(ids, list) or (ids and first and str(first) not in {str(i) for i in ids}):
        return ()
    return tuple(str(i) for i in (ids or [first]) if i)


class EpisodeMatches:
    """One owner/account/config. Only worker episodes have an intended order side."""
    def __init__(self, attempts, fills, owner, account, config):
        self.episodes = defaultdict(list)
        self.timeline = defaultdict(list)
        self.legs = defaultdict(list)
        self.orders = Counter()
        self.claims = Counter()
        for f in fills:
            if str(f["user_id"]) == owner and f["account_id"] == account and f["source_trade_id"]:
                self.legs[str(f["source_trade_id"])].append(f)
        for a in attempts:
            if (str(a["user_id"]), a["account_id"], a["bot_config_id"]) != (owner, account, config):
                continue
            order = str(a["provider_order_id"] or "")
            if order:
                self.orders[order] += 1
            ids = broker_ids(a["runner"], "entry_fill_id")
            self.claims.update(ids)
            key = a["contract_id"], a["capture"]
            if not all(key) or not a["episode"]:
                continue
            self.episodes[(*key, a["episode"])].append(a)
            self.timeline[key].append((a["at"], a["version"], a["direction"]))
        for rows in self.timeline.values():
            rows.sort(key=lambda r: r[0])

    def verified(self, a):
        order = str(a["provider_order_id"] or "")
        runner = a["runner"]
        ids = broker_ids(runner, "entry_fill_id")
        if (a["status"] != "submitted" or not ids or not order or self.orders[order] != 1
                or str(runner.get("entry_order_id")) != order):
            return ()
        for i in ids:
            if self.claims[i] != 1 or len(self.legs[i]) != 1:
                return ()
            leg = self.legs[i][0]
            if ((leg["contract_id"], str(leg["order_id"]), leg["side"]) != (a["contract_id"], order, a["side"])
                    or leg.get("voided") or str(leg.get("status")).lower() == "voided"):
                return ()
        return ids

    def lookup(self, contract, capture, episode, at, heavy):
        rows = self.episodes.get((contract, capture, episode), ())
        attempt = rows[0] if len(rows) == 1 else None
        version = direction = None
        if attempt:
            version, direction = attempt["version"], attempt["direction"]
        elif not rows:
            history = self.timeline.get((contract, capture), [])
            i = bisect_left(history, at, key=lambda r: r[0])
            adjacent = {r[1:] for r in history[max(0, i-1):i+1]}
            if len(adjacent) == 1:
                version, direction = adjacent.pop()
        side = (heavy if direction == "following" else -heavy if direction == "contrarian" else None) if version else None
        result = dict(version=version, order_side=side, disposition="Unknown", entry_id=None,
                      entry_order_id=None, entry_fill_ids=(), reason=None)
        if not attempt or not side or attempt["side"] != ("BUY" if side == 1 else "SELL"):
            return result
        ids = self.verified(attempt)
        if ids:
            result.update(disposition="Filled", entry_id=attempt["id"],
                          entry_order_id=str(attempt["provider_order_id"]), entry_fill_ids=ids)
        elif attempt["status"] in {"blocked", "rejected", "cancelled", "dry_run"} and not attempt["provider_order_id"]:
            result.update(disposition="Skipped", reason=attempt.get("reason") or attempt["status"])
        return result


def saved_direction(entry):
    return entry.get("imbalance_direction", "contrarian") if entry.get("revision") == "dom_contrarian_v1" else None
