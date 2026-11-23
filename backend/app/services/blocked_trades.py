"""Prospective, simulation-only receipts. No provider client or execution-table writes."""
from contextlib import closing
from datetime import timedelta
import json
import re
import shutil
import sqlite3

from .databento_cache import default_cache_root
from .topbot_depth import utc
from .topbot_runner import runner_plan
from .topbot_strategy import normalize_params
from .trading_day import trading_day_key

DAILY = {"daily_profit_target_reached", "daily_loss_limit_reached"}
BUDGET = "proposed_stop_risk_exceeds_daily_loss_budget"
FREE_FLOOR = 10 * 1024**3


def path():
    return default_cache_root() / "blocked-trades-v1" / "sessions.sqlite3"


def connect(*, readonly=False):
    file = path()
    if readonly:
        db = sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    else:
        file.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(file, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with closing(connect()) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY, owner TEXT NOT NULL, account INTEGER NOT NULL, config INTEGER NOT NULL,
                contract TEXT NOT NULL, episode TEXT NOT NULL, at TEXT NOT NULL, session TEXT NOT NULL,
                reason TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, snapshot TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', entry REAL, tp REAL, sl REAL, exit_at TEXT, exit_price REAL,
                detail TEXT, net REAL, occupied_until TEXT,
                UNIQUE(owner,account,config,contract,episode));
            CREATE INDEX IF NOT EXISTS work ON signals(status,at,id);
            CREATE INDEX IF NOT EXISTS history ON signals(owner,account,config,session,at);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open ON signals(owner,account,config) WHERE status='open';
            CREATE TABLE IF NOT EXISTS reader(id INTEGER PRIMARY KEY CHECK(id=1), at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS halts(owner TEXT,account INTEGER,config INTEGER,contract TEXT,at TEXT,
                PRIMARY KEY(owner,account,config,contract,at));
        """)


def candidate(config, decision, blocks, *, run_id):
    codes = {b.code for b in blocks}
    daily = DAILY & codes
    if len(daily) == 1 and codes <= daily | {BUDGET, "max_daily_loss"}:
        reason = daily.pop()
    elif codes == {BUDGET}:
        reason = BUDGET
    else:
        return None
    data = decision.raw_payload or {}
    check = data.get("depth_recheck") or {}
    ref = check.get("evidence") or {}
    if (not re.fullmatch(r"CON\.F\.US\.MNQ\.[A-Z]\d{2}", config.contract_id)
            or not re.fullmatch(r"[a-f0-9]{32}", ref.get("capture_id", ""))
            or type(ref.get("event_index")) is not int or ref["event_index"] < 1
            or not ref.get("received_at") or not check.get("ready") or check.get("failed_checks") != []
            or not check.get("episode_at") or check["episode_at"] != check.get("signal_episode_at")
            or data.get("entry_checks", {}).get("phase") != "broker"
            or decision.action not in ("BUY", "SELL")):
        return None
    at = utc(check["checked_at"])
    if not timedelta(0) <= at - utc(ref["received_at"]) <= timedelta(seconds=2):
        return None
    params = normalize_params(config.strategy_params)
    plan = runner_plan(at, exit_mode=data["exit_mode"], params=params)
    limits = {key: getattr(config, key) for key in ("max_daily_loss", "max_trades_per_day", "cooldown_seconds",
              "max_open_position", "max_contracts", "order_size", "trading_start_time", "trading_end_time",
              "allowed_contracts", "max_data_staleness_seconds")}
    snapshot = dict(params=params, limits=limits, run_id=run_id, plan=plan, capture=ref["capture_id"],
                    index=ref["event_index"], receipt=ref["received_at"], bid=check["bid"], ask=check["ask"],
                    actual_entries=check.get("account_entries_today", 0),
                    block_messages=[b.message for b in blocks])
    return (str(config.user_id), int(config.account_id), int(config.id), config.contract_id,
            check["episode_at"], at.isoformat(), trading_day_key(at), reason, decision.action,
            params.get("entry_contracts", 1), json.dumps(snapshot, default=str, allow_nan=False))


def enqueue(receipt):
    if receipt is None:
        return
    initialize()
    file = path()
    if shutil.disk_usage(file.parent).free < FREE_FLOOR or file.stat().st_size > 512 * 1024**2:
        raise OSError("Blocked trade research storage limit reached")
    with closing(connect()) as db, db:
        db.execute("""INSERT INTO signals(owner,account,config,contract,episode,at,session,reason,side,quantity,snapshot)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,account,config,contract,episode) DO NOTHING""", receipt)


def record_halt(owner, account, config, pause):
    if path().exists():
        with closing(connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO halts VALUES(?,?,?,?,?)",
                       (owner, account, config, pause["contract_id"], pause["rejected_at"]))


def report(owner, account, config, *, before=None, limit=50):
    counts = dict(TP=0, SL=0, other=0, open=0, unresolved=0)
    result = dict(account_id=account, config_id=config, counts=counts, wins=0, denominator=0,
                  trades=[], next_before=None, reader_at=None)
    if not path().exists():
        return result
    with closing(connect(readonly=True)) as db:
        db.execute("BEGIN")
        reader = db.execute("SELECT at FROM reader WHERE id=1").fetchone()
        result["reader_at"] = reader[0] if reader else None
        scope = (str(owner), account, config)
        for row in db.execute("""SELECT status,count(*) AS n,sum(net>0) AS wins FROM signals
                                 WHERE owner=? AND account=? AND config=? GROUP BY status""", scope):
            if row["status"] in counts:
                counts[row["status"]] = row["n"]
            if row["status"] in ("TP", "SL", "other"):
                result["denominator"] += row["n"]
                result["wins"] += row["wins"]
        rows = db.execute("""SELECT id,at,reason,side,quantity,status,entry,tp,sl,exit_at,exit_price,detail,net
                    FROM signals WHERE owner=? AND account=? AND config=? AND (? IS NULL OR id<?)
                    AND status NOT IN ('queued','skipped') ORDER BY id DESC LIMIT ?""",
                          (*scope, before, before, limit + 1)).fetchall()
        result["trades"] = [dict(r) for r in rows[:limit]]
        if len(rows) > limit:
            result["next_before"] = rows[limit-1]["id"]
    return result
