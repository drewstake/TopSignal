"""Replay fixed qualifications from the account's live Worker study and its recorded DOM."""
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from time import monotonic

from .databento_cache import default_cache_root
from .imbalance_study import ET, HORIZONS, batch_before, directory
from .topbot_depth import DepthState, utc
from .topbot_replay_index import Capture
from .topbot_session import flat_deadline, runner_entry_boundary_reason

TICK = 0.25
POINT_VALUE = 2
ROUND_TURN = 1.22
LATENCY = timedelta(milliseconds=250)
WORKER_SIGNALS = "source='Worker' AND side IN (-1,1) AND contract LIKE 'CON.F.US.MNQ.%'"


class SignalNotFound(LookupError):
    pass


def _study_path(owner, account):
    return directory(default_cache_root(), owner, account) / "study.sqlite3"


def _open(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def _day(at):
    return datetime.fromtimestamp(at, ET).date() if at is not None else None


def signals(owner, account, day: date | None, before: int | None, limit=25):
    path = _study_path(owner, account)
    result = dict(account_id=account, day=day, previous_day=None, next_day=None, next_before=None, signals=[])
    if not path.exists():
        return result
    with closing(_open(path)) as db:
        db.execute("BEGIN")
        base = f"FROM anchors WHERE {WORKER_SIGNALS}"
        if day is None:
            day = _day(db.execute(f"SELECT max(at) {base}").fetchone()[0])
            if day is None:
                return result
        start = datetime.combine(day, time.min, ET)
        bounds = (start.timestamp(), (start + timedelta(days=1)).timestamp())
        result.update(day=day,
                      previous_day=_day(db.execute(f"SELECT max(at) {base} AND at<?", bounds[:1]).fetchone()[0]),
                      next_day=_day(db.execute(f"SELECT min(at) {base} AND at>=?", bounds[1:]).fetchone()[0]))
        rows = db.execute(f"SELECT id,at,side,contract,rule {base} AND at>=? AND at<? "
                          "AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?", (*bounds, before, before, limit+1)).fetchall()
        result["signals"] = [dict(id=r["id"], at=datetime.fromtimestamp(r["at"], timezone.utc).isoformat(),
                                  imbalance="ask-heavy" if r["side"] == 1 else "bid-heavy",
                                  contract=r["contract"], rule=r["rule"]) for r in rows[:limit]]
        if len(rows) > limit:
            result["next_before"] = rows[limit-1]["id"]
    return result


def _sweep(book, best, quantity, buy):
    """One or two marketable MNQ contracts at demonstrated book sizes."""
    if book[best] >= quantity:
        return best
    if quantity != 2 or book[best] != 1:
        return None
    next_price = (min if buy else max)((p for p, size in book.items() if p != best and size >= 1), default=None)
    return (best + next_price) / 2 if next_price is not None else None


@dataclass
class Bracket:
    stop: float
    target: float
    triggered: datetime | None = None

    def exit(self, now, start, sign, entry, best, size, sweep, quantity):
        if self.triggered is None and sign * (best-entry) <= -self.stop:
            self.triggered = now + LATENCY
        if self.triggered is not None:
            if now >= self.triggered:
                return "Stop", sweep - sign*TICK
        elif now >= start + LATENCY and size >= quantity:
            target = sign * math.ceil((sign*entry + self.target) / TICK) * TICK
            if sign * (best-target) >= TICK:
                return "Target", target
        return None


class CoverageEnd(Exception):
    """An unresolved exit has no further usable quotes in this capture."""


def _quotes(cap, anchor, gap_at, start, cutoff):
    book = DepthState(anchor["contract"])
    # Seed from a full snapshot. Only the original durable qualification can start a replay.
    batch, digest, index = batch_before(cap, anchor["at"])
    while batch:
        prior = cap.db.execute("SELECT id,hash,last_index FROM batches WHERE id=?", (batch-1,)).fetchone()
        prior = tuple(prior) if prior else (0, cap.meta_hash, 0)
        _, _, records = next(cap.batches(*prior))
        batch, digest, index = prior
        if any(r["event"]["event"] == "snapshot" and r["event"]["data"].get("contract_id") == anchor["contract"]
               and not r["event"]["data"].get("reset") for r in records):
            break
    previous = None
    seeded = False
    for _, _, records in cap.batches(batch, digest, index):
        for row in records:
            now = utc(row["received_at"])
            event = row["event"]
            sequence = event["data"].get("sequence")
            hole = row["event_index"] != index + 1
            index = row["event_index"]
            if previous is not None and (hole or now < previous or now-previous > timedelta(seconds=2)
                    or event["event"] == "observation_gap" or (sequence is not None and book.sequence is not None
                    and sequence > book.sequence + 1)):
                raise CoverageEnd("Interrupted recording")
            if hole:
                book.reset("capture_gap", now)
                seeded = False
            if gap_at is not None and now.timestamp() >= gap_at:
                raise CoverageEnd("Recorded quote gap")
            if now >= cutoff:
                raise CoverageEnd("Session or two-hour cutoff")
            book.apply(event, now)
            if event["event"] == "snapshot" and event["data"].get("contract_id") == anchor["contract"]:
                seeded = bool(book.bids and book.asks and book.sequence == sequence)
            if not book.bids or not book.asks:
                seeded = False
            if now < start:
                continue
            if previous is None:
                if now != start:
                    raise CoverageEnd("Qualification missing from capture")
                if not any(t["kind"] == "episode_qualified" and t["episode_at"] == anchor["episode"]
                           and t["direction"] == -anchor["side"] for t in row["transitions"]):
                    continue
            if not seeded or not book.connected or book.last_at is None or not timedelta(0) <= now-book.last_at <= timedelta(seconds=2):
                raise CoverageEnd("Executable quote unavailable")
            bid, ask = max(book.bids), min(book.asks)
            if bid >= ask:
                raise CoverageEnd("Crossed quote")
            if previous is None and (bid, ask) != (anchor["bid"], anchor["ask"]):
                raise CoverageEnd("Qualification quote mismatch")
            previous = now
            yield now, book, bid, ask
    raise CoverageEnd("End of recorded capture")


def _run(cap, anchor, gap_at, brackets, direction, quantity, timed, result):
    start = datetime.fromtimestamp(anchor["at"], timezone.utc)
    if runner_entry_boundary_reason(start):
        return "Past session entry cutoff"
    cutoff = min(start + timedelta(hours=2), flat_deadline(start))
    sign = anchor["side"] if direction == "contrarian" else -anchor["side"]
    try:
        for now, book, bid, ask in _quotes(cap, anchor, gap_at, start, cutoff):
            if result["entry"] is None:
                entry_book, entry_best = (book.asks, ask) if sign == 1 else (book.bids, bid)
                result["entry"] = _sweep(entry_book, entry_best, quantity, sign == 1)
                if result["entry"] is None:
                    return "Insufficient displayed entry size"
            levels, best = (book.bids, bid) if sign == 1 else (book.asks, ask)
            sweep = _sweep(levels, best, quantity, sign == -1)
            if sweep is None:
                return "Insufficient displayed exit size"
            for key, bracket in brackets.items():
                if key in result["exits"]:
                    continue
                fill = bracket.exit(now, start, sign, result["entry"], best, levels[best], sweep, quantity)
                if fill:
                    result["exits"][key] = _fill(*fill, now, sign, quantity, result["entry"])
            if timed:
                elapsed = (now-start).total_seconds()
                for h in HORIZONS:
                    if str(h) not in result["exits"] and h <= elapsed <= h+2:
                        result["exits"][str(h)] = _fill("Timed", sweep, now, sign, quantity, result["entry"])
            if all(k in result["exits"] for k in brackets) and (not timed or (now-start).total_seconds() >= 302
                    or all(str(h) in result["exits"] for h in HORIZONS)):
                return None
    except CoverageEnd as exc:
        return str(exc)


def _fill(reason, price, at, sign, quantity, entry):
    gross = (price-entry)*sign*POINT_VALUE*quantity
    fees = ROUND_TURN*quantity
    return dict(reason=reason, price=price, at=at.isoformat(), gross=gross, fees=fees, net=round(gross-fees, 2))


def replay_signal(owner, account, signal_id, *, direction, quantity, stop_a, target_a, stop_b, target_b, timed=False):
    path = _study_path(owner, account)
    if not path.exists():
        raise SignalNotFound("signal_not_found")
    with closing(_open(path)) as db:
        anchor = db.execute(f"SELECT * FROM anchors WHERE id=? AND {WORKER_SIGNALS}", (signal_id,)).fetchone()
        if anchor is None:
            raise SignalNotFound("signal_not_found")
        gap_at = db.execute("SELECT min(at) FROM gaps WHERE source='Worker' AND capture=? AND at>?",
                            (anchor["capture"], anchor["at"])).fetchone()[0]
    root = Path(os.getenv("TOPSIGNAL_TOPBOT_EVIDENCE_DIR") or default_cache_root() / "topbot-worker-evidence-v1")
    capture_id = anchor["capture"]
    if not re.fullmatch("[a-f0-9]{32}", capture_id):
        raise ValueError("invalid_capture_id")
    capture_path = root / path.parent.parent.name / (capture_id + ".sqlite3")
    long = (anchor["side"] == 1) == (direction == "contrarian")
    result = dict(account_id=account, id=signal_id, at=datetime.fromtimestamp(anchor["at"], timezone.utc).isoformat(),
                  side="Long" if long else "Short", quantity=quantity, entry=None, exits={}, coverage=None)
    if not capture_path.is_file():
        result["coverage"] = "Worker capture unavailable"
    else:
        with closing(Capture(capture_path)) as cap:
            cap.check_scope(owner, anchor["contract"])
            if cap.meta["capture_id"] != capture_id:
                raise ValueError("capture_id_mismatch")
            brackets = {"a": Bracket(stop_a, target_a), "b": Bracket(stop_b, target_b)}
            result["coverage"] = _run(cap, anchor, gap_at, brackets, direction, quantity, timed, result)
    return result


def replay_in_worker(query, disconnected):
    """Keep archive decoding off the trading API's interpreter. Only one reader is admitted."""
    flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
    with subprocess.Popen([sys.executable, "-B", "-m", "app.services.recorded_signals"],
                          cwd=Path(__file__).resolve().parents[2], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, creationflags=flags) as worker:
        payload = json.dumps(query).encode()
        deadline = monotonic() + 300
        try:
            while True:
                try:
                    raw, _ = worker.communicate(payload, timeout=.5)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
                    if disconnected() or monotonic() >= deadline:
                        raise TimeoutError("replay_read_interrupted")
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
        if worker.returncode == 3:
            raise SignalNotFound("signal_not_found")
        if worker.returncode:
            raise ValueError("unreadable_capture")
        return json.loads(raw)


if __name__ == "__main__":
    if os.name == "posix":
        os.nice(10)
    try:
        print(json.dumps(replay_signal(**json.load(sys.stdin)), allow_nan=False))
    except SignalNotFound:
        sys.exit(3)
