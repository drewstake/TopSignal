"""Read-only worker evidence replay in a release-owned, below-normal-priority child."""
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from threading import Event, Thread
from time import monotonic

from . import blocked_trades as store
from .databento_cache import default_cache_root
from .recorded_signals import Bracket, CoverageEnd, LATENCY, seed_before, simulated_fill, sweep_price
from .topbot_depth import DepthState, utc
from .topbot_replay_index import Capture
from .topbot_runner import desired_stop

logger = logging.getLogger(__name__)


class Position:
    """One signal pinned to the book at its final risk check. Reconstructible from immutable batches."""

    def __init__(self, row):
        self.settings = settings = json.loads(row["snapshot"])
        self.contract = row["contract"]
        self.owner = row["owner"]
        root = Path(os.environ.get("TOPSIGNAL_TOPBOT_EVIDENCE_DIR") or default_cache_root() / "topbot-worker-evidence-v1")
        self.path = root / sha256(self.owner.encode()).hexdigest() / (settings["capture"] + ".sqlite3")
        self.start = utc(row["at"])
        self.side, self.quantity = row["side"], row["quantity"]
        self.sign = 1 if self.side == "BUY" else -1
        self.book = DepthState(self.contract)
        self.deadline = utc(settings["plan"]["deadline"])
        runner = settings["plan"]["runner"]
        self.bracket = Bracket(runner["initial_stop_points"], runner["take_profit_points"])
        self.initial_stop = self.bracket.stop
        self.next_management = self.start + timedelta(seconds=5)
        self.tighten = None
        self.trail_close_at = None
        self.mfe = 0
        self.entry = self.previous = self.anchor = None
        self.seeded = False
        self.halted_at = None
        self.result = {}

    def gap(self, why):
        # A position with an unknown exit may still occupy the session until flatten time.
        self.result.update(status="unresolved", detail=why, occupied_until=self.deadline.isoformat())

    def step(self, row, prior_index):
        index = row["event_index"]
        now = utc(row["received_at"])
        event = row["event"]
        sequence = event["data"].get("sequence")
        hole = index != prior_index + 1
        prior_sequence, prior_stamp = self.book.sequence, self.book.last_at
        discontinuity = (hole or event["event"] == "observation_gap" or
                         (sequence is not None and self.book.sequence is not None and sequence > self.book.sequence + 1))
        if self.previous is not None and (discontinuity or not timedelta(0) <= now-self.previous <= timedelta(seconds=2)):
            raise CoverageEnd("Interrupted quote recording")
        if hole:
            self.book.reset("capture_gap", now)
            self.seeded = False
        self.book.apply(event, now)
        quote = self.book.snapshot(now)
        if event["event"] == "snapshot" and event["data"].get("contract_id") == self.contract:
            self.seeded = quote["ready"] and not event["data"].get("reset") and self.book.sequence == sequence
        if not quote["ready"]:
            self.seeded = False
        if index < self.settings["index"]:
            return
        if self.halted_at and now >= self.halted_at:
            raise CoverageEnd("Confirmed instrument halt")
        if not self.seeded or event["event"] == "observation_gap":
            raise CoverageEnd("Executable quote unavailable")
        bid, ask = quote["bid"], quote["ask"]
        if self.entry is None:
            if (index != self.settings["index"] or row["received_at"] != self.settings["receipt"]
                    or (bid, ask) != (self.settings["bid"], self.settings["ask"])
                    or not self.book.snapshot(self.start)["ready"]):
                raise CoverageEnd("Entry quote could not be verified")
            levels, best = (self.book.asks, ask) if self.sign == 1 else (self.book.bids, bid)
            self.entry = sweep_price(levels, best, self.quantity, self.sign == 1)
            if self.entry is None:
                self.result.update(status="unresolved", detail="Insufficient displayed entry size")
                return
            self.result.update(status="open", entry=self.entry, sl=self.entry-self.sign*self.initial_stop,
                               tp=self.bracket.target_price(self.sign, self.entry))
            now = self.start
        elif (event["event"] not in ("snapshot", "update") or event["data"].get("contract_id") != self.contract
                or self.book.sequence == prior_sequence):
            return
        elif prior_stamp and not timedelta(0) <= self.book.last_at-prior_stamp <= timedelta(seconds=2):
            raise CoverageEnd("Interrupted provider timestamps")
        self.previous = now
        levels, best = (self.book.bids, bid) if self.sign == 1 else (self.book.asks, ask)
        sweep = sweep_price(levels, best, self.quantity, self.sign == -1)
        if sweep is None:
            raise CoverageEnd("Insufficient displayed exit size")
        if self.tighten and now >= self.tighten[0]:
            self.bracket.stop = self.sign * (self.entry - self.tighten[1])
            self.tighten = None
        fill = self.bracket.exit(now, self.start, self.sign, self.entry, best, levels[best], sweep, self.quantity)
        if fill:
            reason, price = fill
            if reason == "Stop" and self.bracket.stop != self.initial_stop:
                reason = "Trailing stop"
        elif self.trail_close_at and now >= self.trail_close_at:
            reason, price = "Trailing level crossed", sweep
        elif now >= self.deadline + LATENCY:
            reason, price = "Session close", sweep
        else:
            if self.bracket.target is None and not self.bracket.triggered:
                self.mfe = max(self.mfe, self.sign*(best-self.entry))
                if now >= self.next_management:
                    self.next_management = now + timedelta(seconds=5)
                    current = self.entry - self.sign*self.bracket.stop
                    wanted = desired_stop(self.side, self.entry, self.mfe, current)
                    if wanted != current:
                        if self.sign * (best-wanted) <= 0:
                            self.trail_close_at = now + LATENCY
                        else:
                            self.tighten = (now + LATENCY, wanted)
            return
        fill = simulated_fill(reason, price, now, self.sign, self.quantity, self.entry)
        self.result.update(status={"Target":"TP", "Stop":"SL"}.get(reason, "other"), detail=reason,
                           exit_at=fill["at"], occupied_until=fill["at"], exit_price=price, net=fill["net"])

    def advance(self, now, *, batch_limit=16):
        if not self.path.exists():
            if now > self.start + timedelta(seconds=90):
                self.gap("Entry capture unavailable")
            return
        with closing(Capture(self.path)) as cap:
            cap.check_scope(self.owner, self.contract)
            if cap.meta["capture_id"] != self.settings["capture"]:
                raise ValueError("Capture identity changed")
            if self.anchor is None:
                self.anchor = seed_before(cap, self.contract, utc(self.settings["receipt"]).timestamp())
            batch, digest, last = self.anchor
            if not cap.anchor_matches(batch, digest):
                raise ValueError("Capture checkpoint mismatch")
            for count, (batch, digest, rows) in enumerate(cap.batches(batch, digest, last), 1):
                for item in rows:
                    self.step(item, last)
                    last = item["event_index"]
                    if self.result.get("status") in ("TP", "SL", "other", "unresolved"):
                        return
                self.anchor = batch, digest, last
                if count >= batch_limit:
                    return
            status = cap.db.execute("SELECT value FROM status WHERE id=1").fetchone()
            state = json.loads(status[0]).get("state") if status else None
            if state == "closed" or now - (self.previous or self.start) > timedelta(seconds=90):
                self.gap("End of continuous worker recording")


def skip_reason(db, row):
    scope = (row["owner"], row["account"], row["config"])
    if db.execute("""SELECT 1 FROM signals WHERE owner=? AND account=? AND config=?
            AND occupied_until>=? AND id!=? LIMIT 1""", (*scope,row["at"],row["id"])).fetchone():
        return "Previous simulated position"
    settings = json.loads(row["snapshot"])
    limits = settings["limits"]
    count, loss_at = db.execute("""SELECT count(*),max(CASE WHEN net<0 THEN exit_at END) FROM signals
        WHERE owner=? AND account=? AND config=? AND session=? AND entry IS NOT NULL AND at<?""",
        (*scope, row["session"],row["at"])).fetchone()
    if (settings["params"]["daily_entry_limit_enabled"]
            and count + settings["actual_entries"] >= int(limits["max_trades_per_day"])):
        return "Daily entry count"
    if loss_at and utc(row["at"])-utc(loss_at) < timedelta(seconds=int(limits["cooldown_seconds"])):
        return "Entry cooldown"
    return None


def cycle(db, active, now):
    # Serialize each account/config timeline even when recording is ahead of the reader.
    rows = db.execute("""SELECT * FROM (SELECT *,row_number() OVER
        (PARTITION BY owner,account,config ORDER BY (status='open') DESC,at,id) AS n
        FROM signals WHERE status IN ('queued','open'))
        WHERE n=1 ORDER BY at,id""").fetchall()
    for row in rows:
        key = row["id"]
        if key not in active:
            reason = skip_reason(db,row) if row["status"] == "queued" else None
            if reason:
                with db:
                    db.execute("UPDATE signals SET status='skipped',detail=? WHERE id=?", (reason,key))
                continue
            active[key] = Position(row)
        trade = active[key]
        halt = db.execute("""SELECT min(at) FROM halts WHERE owner=? AND account=? AND config=?
                             AND contract=? AND at>=?""",
                          (row["owner"],row["account"],row["config"],row["contract"],row["at"])).fetchone()[0]
        trade.halted_at = utc(halt) if halt else None
        try:
            trade.advance(now)
        except (CoverageEnd, ValueError, KeyError, TypeError) as exc:
            trade.gap(str(exc) if isinstance(exc, CoverageEnd) else "Unverified worker recording")
        result = trade.result
        if result and (result["status"] != row["status"] or result["status"] != "open"):
            with db:
                db.execute("UPDATE signals SET " + ",".join(k+"=?" for k in result) + " WHERE id=?",
                           (*result.values(), key))
        if result.get("status") not in (None, "open"):
            active.pop(key)
    return bool(rows)


def follow():
    halt = Event()
    def parent_closed():
        sys.stdin.buffer.read()
        halt.set()
    Thread(target=parent_closed, daemon=True).start()
    store.initialize()
    active = {}
    heartbeat = 0
    with closing(store.connect()) as db:
        while not halt.is_set():
            now = datetime.now(UTC)
            try:
                pending = cycle(db, active, now)
                if monotonic() >= heartbeat:
                    with db:
                        db.execute("INSERT OR REPLACE INTO reader VALUES(1,?)", (now.isoformat(),))
                    heartbeat = monotonic()+15
            except (OSError, sqlite3.Error):
                logger.exception("blocked_replay_io_failed")
                active.clear()  # Replay from the durable entry on retry.
                pending = False
            halt.wait(.25 if pending else 2)


if __name__ == "__main__":
    if os.name != "nt":
        os.nice(10)
    follow()
