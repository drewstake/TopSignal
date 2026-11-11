"""Historical estimates and read-only recording, stored on the backend filesystem.

No candle rows or replay arrays are written to the application database. Estimates
use the exact delivery contract, FIFO entry legs and one-minute OHLC extremes.
Boundary bars can include prices outside a fill's lifetime, so these are always
labelled estimates, never tick-exact observations.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import math
import os
import sqlite3
import threading
from pathlib import Path

from .probabilistic_shadow import cache_root
from .instruments import build_point_value_lookup, load_instrument_specs, normalize_symbol_key
from .projectx_trades import derive_trade_execution_lifecycles, list_trade_events, serialize_trade_event
from .trading_day import futures_session_is_open

logger = logging.getLogger(__name__)
MINUTE = timedelta(minutes=1)
ESTIMATE_NOTE = ("Estimated from 1-minute historical candles for this delivery contract and FIFO entry fills, "
                 "before fees. Entry/exit candles may include moves outside the trade.")


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda obj: utc(obj).isoformat() if isinstance(obj, datetime) else str(obj))


def location():
    return cache_root() / "trade-excursions" / "v1.sqlite3"


@contextmanager
def connect():
    path = location()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS scopes(owner TEXT NOT NULL, account INTEGER NOT NULL,
                live INTEGER NOT NULL, enabled INTEGER NOT NULL, heartbeat TEXT, error TEXT,
                PRIMARY KEY(owner,account));
            CREATE TABLE IF NOT EXISTS results(owner TEXT NOT NULL, account INTEGER NOT NULL,
                trade INTEGER NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, retry_at TEXT,
                PRIMARY KEY(owner,account,trade));
            CREATE TABLE IF NOT EXISTS observations(owner TEXT NOT NULL, account INTEGER NOT NULL,
                contract TEXT NOT NULL, opened TEXT NOT NULL, closed TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(owner,account,contract,opened,closed));
        """)
        yield db
        db.commit()
    finally:
        db.close()


def enable_tracking(owner, account, *, live=False):
    """Call only after verifying the account belongs to the explicitly authorized owner."""
    if not owner or int(account) <= 0:
        raise ValueError("Account scope required")
    with connect() as db:
        db.execute("INSERT INTO scopes(owner,account,live,enabled) VALUES(?,?,?,1) "
                   "ON CONFLICT(owner,account) DO UPDATE SET enabled=1,live=excluded.live",
                   (str(owner), int(account), int(live)))


def scopes():
    if not location().exists():
        return []
    with connect() as db:
        return db.execute("SELECT owner,account,live FROM scopes WHERE enabled=1").fetchall()


def tracking_status(owner, account):
    if not location().exists():
        return None
    with connect() as db:
        row = db.execute("SELECT enabled,heartbeat,error FROM scopes WHERE owner=? AND account=?",
                         (str(owner), account)).fetchone()
    return dict(enabled=bool(row[0]), heartbeat=row[1], error=row[2]) if row else None


def inputs(trade, lifecycle=None, *, imported=False):
    legs = list(lifecycle.entry_legs) if lifecycle is not None else []
    if imported and trade.get("entry_time") is not None and trade.get("entry_price") is not None:
        legs = [(trade["entry_time"], trade["entry_price"], trade["size"])]
    return {key: trade.get(key) for key in (
        "id", "contract_id", "symbol", "side", "size", "entry_time", "entry_price", "exit_time", "exit_price"
    )} | {"legs": legs}


def fingerprint(trade):
    return sha256(encoded(trade).encode()).hexdigest()


def calculate(trade, bars, *, point_value):
    """All required trading-session minutes must be present; never fabricate zeros."""
    legs = [(utc(at), float(price), float(qty)) for at, price, qty in trade["legs"]]
    end = utc(trade["exit_time"])
    direction = {"SELL": 1, "LONG": 1, "BUY": -1, "SHORT": -1}.get(str(trade["side"]).upper())
    if (not legs or direction is None or not math.isfinite(point_value) or point_value <= 0
            or any(not math.isfinite(price) or not math.isfinite(qty) or price <= 0 or qty <= 0 or at > end
                   for at, price, qty in legs)
            or abs(sum(qty for _, _, qty in legs) - float(trade["size"])) > 1e-8):
        return {"excursion_note": "Complete entry fills and instrument point value are required."}
    start = min(at for at, _, _ in legs)
    if end - start > timedelta(days=7):
        return {"excursion_note": "Trade exceeds the seven-day historical calculation window."}
    first = start.replace(second=0, microsecond=0)
    # An exit exactly on the minute boundary has no exposure to the following bar.
    stop = end.replace(second=0, microsecond=0) + (MINUTE if end.second or end.microsecond or end == start else timedelta())
    by_time = {}
    for bar in bars:
        at = utc(bar["timestamp"])
        if first <= at < stop and not bar.get("is_partial", False):
            if at in by_time and by_time[at] != bar:
                return {"excursion_note": "Historical prices contain conflicting bars."}
            by_time[at] = bar
    cursor = first
    while cursor < stop:
        if futures_session_is_open(cursor, symbol=trade["symbol"]) and cursor not in by_time:
            return {"excursion_note": "Historical price coverage is incomplete; calculation will retry."}
        cursor += MINUTE
    if not by_time:
        return {"excursion_note": "Historical prices are unavailable; calculation will retry."}
    exit_pnl = sum((float(trade["exit_price"]) - price) * qty * direction * point_value for _, price, qty in legs)
    adverse, favorable = min(0., exit_pnl), max(0., exit_pnl)
    for at, bar in sorted(by_time.items()):
        low, high = float(bar["low"]), float(bar["high"])
        if not all(math.isfinite(v) and v > 0 for v in (low, high)) or low > high:
            return {"excursion_note": "Invalid historical prices."}
        active = [(price, qty) for opened, price, qty in legs if opened < at + MINUTE]
        for mark in (low, high):
            pnl = sum((mark - price) * qty * direction * point_value for price, qty in active)
            adverse, favorable = min(adverse, pnl), max(favorable, pnl)
    return dict(mae=round(adverse, 2), mfe=round(favorable, 2), excursion_scope="trade",
                excursion_source="historical_1m_estimate", excursion_note=ESTIMATE_NOTE)


def backfill(owner, account, *, client, session_factory, live=False, stop=None):
    """Bounded recent closed-trade batch; immutable results are reused, failures retry."""
    with session_factory() as db:
        from ..models import Account
        if db.query(Account.id).filter(Account.user_id == owner, Account.provider == "projectx",
                                       Account.external_id == str(account)).first() is None:
            raise ValueError("account_ownership_unverified")
        rows = list_trade_events(db, user_id=owner, account_id=account, limit=1000)
        lives = derive_trade_execution_lifecycles(db, user_id=owner, account_id=account, closed_rows=rows)
        prepared = [inputs(serialize_trade_event(row, lifecycle=lives.get(row.id)), lives.get(row.id),
                           imported=row.entry_timestamp is not None) for row in rows]
        points = build_point_value_lookup(load_instrument_specs(db))
    counts = dict(calculated=0, cached=0, unavailable=0)
    now = datetime.now(timezone.utc)
    for trade in prepared:
        if stop is not None and stop.is_set():
            break
        key = fingerprint(trade)
        with connect() as db:
            saved = db.execute("SELECT fingerprint,retry_at FROM results WHERE owner=? AND account=? AND trade=?",
                               (owner, account, trade["id"])).fetchone()
        if saved and saved[0] == key and (saved[1] is None or utc(saved[1]) > now):
            counts["cached"] += 1
            continue
        point_value = points.get(normalize_symbol_key(trade["contract_id"]))
        if not trade["legs"] or point_value is None:
            result = {"excursion_note": "Complete entry fills and instrument point value are required."}
        else:
            first = min(utc(leg[0]) for leg in trade["legs"]).replace(second=0, microsecond=0)
            end = utc(trade["exit_time"]).replace(second=0, microsecond=0) + MINUTE
            if end - first > timedelta(days=7):
                result = {"excursion_note": "Trade exceeds the seven-day historical calculation window."}
            elif end > now:
                result = {"excursion_note": "Waiting for the final historical candle to close."}
            else:
                try:
                    bars = client.retrieve_bars(contract_id=trade["contract_id"], live=bool(live), start=first,
                                                end=end, unit=2, unit_number=1, limit=20000)
                    result = calculate(trade, bars, point_value=point_value)
                except Exception as exc:
                    logger.warning("trade_excursion_history_failed error_type=%s", type(exc).__name__)
                    result = {"excursion_note": "Historical price request failed; calculation will retry."}
        success = "mae" in result
        counts["calculated" if success else "unavailable"] += 1
        with connect() as db:
            db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?)",
                       (owner, account, trade["id"], key, encoded(result),
                        None if success else (now + timedelta(minutes=5)).isoformat()))
    return counts


def save_observation(lifecycle):
    payload = asdict(lifecycle)
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?)",
                   (lifecycle.user_id, lifecycle.account_id, lifecycle.contract_id,
                    utc(lifecycle.opened_at).isoformat(), utc(lifecycle.closed_at).isoformat(), encoded(payload)))


def attach_cached(owner, account, trades, *, lifecycles, imported_ids=()):
    if not location().exists():
        return trades
    with connect() as db:
        for trade in trades:
            # Existing trustworthy stream values take precedence over estimates.
            if trade.get("mae") is not None or trade.get("position_mae") is not None:
                continue
            prepared = inputs(trade, lifecycles.get(trade["id"]), imported=trade["id"] in imported_ids)
            row = db.execute("SELECT result FROM results WHERE owner=? AND account=? AND trade=? AND fingerprint=?",
                             (owner, account, trade["id"], fingerprint(prepared))).fetchone()
            if row:
                trade.update(json.loads(row[0]))
            else:
                trade["excursion_note"] = "Historical calculation pending. The recorder must be enabled on this backend."
            # A full single-entry/fixed-size observed position can replace an estimate.
            # Partial exits retain row-level estimates rather than duplicating position maxima.
            if not prepared["legs"]:
                continue
            start = min(utc(leg[0]) for leg in prepared["legs"])
            end = utc(trade["exit_time"])
            observed = db.execute("SELECT payload FROM observations WHERE owner=? AND account=? AND contract=? "
                                  "AND closed>=? AND closed<=?", (owner, account, trade["contract_id"],
                                  (end-timedelta(seconds=1)).isoformat(), (end+timedelta(seconds=1)).isoformat())).fetchall()
            matching = []
            for value, in observed:
                life = json.loads(value)
                if (abs((utc(life["opened_at"])-start).total_seconds()) <= 1
                        and all(abs((utc(leg[0])-start).total_seconds()) < 1e-6 for leg in prepared["legs"])
                        and abs(life["max_qty"]-trade["size"]) < 1e-8
                        and (life["mae_usd"] != 0 or life["mfe_usd"] != 0)):
                    matching.append(life)
            if len(matching) == 1:
                life = matching[0]
                trade.update(mae=life["mae_usd"], mfe=life["mfe_usd"], excursion_scope="trade",
                             excursion_source="observed_stream",
                             excursion_note="Observed live position P&L before fees; limited to received market updates.")
    return trades


@contextmanager
def recorder_lock(path=None):
    """One recorder across API processes; OS releases this lock after a crash."""
    path = path or location().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class ExcursionRecorder:
    """Independent of bot arming; only reads provider prices/positions and saved fills."""
    def __init__(self, session_factory, client_factory):
        self.session_factory, self.client_factory = session_factory, client_factory
        self.stop_event = threading.Event()
        self.thread = None
        self.runtimes = {}

    def start(self):
        self.thread = threading.Thread(target=self.run, name="trade-excursions", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=35)
        return self.thread is None or not self.thread.is_alive()

    def client(self, owner):
        with self.session_factory() as db:
            client = self.client_factory(db, user_id=owner)
            client.timeout_seconds = min(client.timeout_seconds, 15)
            return client

    def run(self):
        while not self.stop_event.is_set():
            try:
                if scopes():
                    with recorder_lock() as acquired:
                        if acquired:
                            self.run_owned()
            except Exception as exc:
                logger.error("trade_excursion_recorder_failed error_type=%s", type(exc).__name__)
            self.stop_event.wait(5)

    def run_owned(self):
        from ..models import Account
        from .projectx_streaming_runtime import create_streaming_runtime
        try:
            while not self.stop_event.is_set():
                configured = scopes()
                wanted = {(owner, account) for owner, account, _ in configured}
                for key in tuple(self.runtimes):
                    if key not in wanted and self.runtimes[key].stop():
                        del self.runtimes[key]
                for owner, account, live in configured:
                    if self.stop_event.is_set():
                        break
                    error = None
                    try:
                        with self.session_factory() as db:
                            valid = db.query(Account.id).filter(Account.user_id == owner, Account.provider == "projectx",
                                Account.external_id == str(account), Account.trade_data_source == "projectx").first()
                        if valid is None:
                            runtime = self.runtimes.get((owner, account))
                            if runtime and runtime.stop():
                                del self.runtimes[(owner, account)]
                            raise ValueError("account_ownership_unverified")
                        key = owner, account
                        if key not in self.runtimes:
                            self.runtimes[key] = create_streaming_runtime(user_id=owner, account_id=account,
                                client_factory=lambda owner=owner: self.client(owner),
                                market_hub_url=os.getenv("PROJECTX_MARKET_HUB_URL") or "https://rtc.topstepx.com/hubs/market",
                                lifecycle_sink=save_observation, track_positions=True)
                        self.runtimes[key].start()
                        backfill(owner, account, client=self.client(owner), session_factory=self.session_factory,
                                 live=live, stop=self.stop_event)
                        states = self.runtimes[key].runner.stream_states
                        if any(states.get(kind) != "connected" for kind in ("market", "user")):
                            error = "live_stream_connecting_or_disconnected"
                    except Exception as exc:
                        error = type(exc).__name__
                        logger.warning("trade_excursion_account_failed error_type=%s", error)
                    with connect() as db:
                        db.execute("UPDATE scopes SET heartbeat=?,error=? WHERE owner=? AND account=?",
                                   (datetime.now(timezone.utc).isoformat(), error, owner, account))
                self.stop_event.wait(30)
        finally:
            # Keep ownership until every socket thread is stopped.
            for runtime in self.runtimes.values():
                while not runtime.stop():
                    self.stop_event.wait(1)
            self.runtimes.clear()
