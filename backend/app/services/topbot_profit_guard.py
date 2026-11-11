"""Account/session profit stops, durable locally across runs and SQL rollbacks.

One bounded state row per account in the backed-up action journal. Only state
transitions add audit events. No broker mutation is performed by this module.
"""
from datetime import datetime, timezone
import json
import math
import sqlite3
from time import monotonic

from .topbot_audit import connection
from .trading_day import as_utc, trading_day_key

TARGET = 500.0
LOSS_LIMIT = 500.0
MESSAGES = {
    "daily_profit_target_reached": "Daily P&L reached +$500. Close the TopBot position and pause entries until the next trading session.",
    "daily_loss_limit_reached": "Daily P&L reached -$500. Close the TopBot position and pause entries until the next trading session.",
}


def _table(db):
    db.execute("CREATE TABLE IF NOT EXISTS daily_profit_guard(account INTEGER PRIMARY KEY, state TEXT NOT NULL)")


def _empty(now):
    return dict(trading_day=trading_day_key(now), target=TARGET, loss_limit=LOSS_LIMIT, arm_at=None, floor=None,
                armed=False, trigger=None, triggered_at=None, observed_at=None, pnl=None, peak_pnl=None, error=None)


def _current_policy(state):
    """Retire saved break-even latches without deleting historical audit events."""
    state.update(target=TARGET, loss_limit=LOSS_LIMIT, armed=False, arm_at=None, floor=None)
    if state.get("trigger") == "daily_profit_breakeven_stop":
        state.update(trigger=None, triggered_at=None)
    return state


def read(owner, account_id, *, now=None):
    now = as_utc(now or datetime.now(timezone.utc))
    with connection(owner) as db:
        _table(db)
        row = db.execute("SELECT state FROM daily_profit_guard WHERE account=?", (int(account_id),)).fetchone()
    state = json.loads(row[0]) if row else _empty(now)
    if state["trading_day"] > trading_day_key(now):
        raise ValueError("Profit guard cannot move backwards to an older session")
    return _current_policy(state) if state["trading_day"] == trading_day_key(now) else _empty(now)


def observe(owner, account_id, pnl, *, now=None):
    if isinstance(pnl, bool) or not isinstance(pnl, (float, int)) or not math.isfinite(pnl):
        raise ValueError("Daily profit protection requires finite complete account P&L")
    now = as_utc(now or datetime.now(timezone.utc))
    with connection(owner) as db:
        _table(db)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM daily_profit_guard WHERE account=?", (int(account_id),)).fetchone()
        state = json.loads(row[0]) if row else _empty(now)
        day = trading_day_key(now)
        if state["trading_day"] > day or (state.get("observed_at") and as_utc(datetime.fromisoformat(state["observed_at"])) > now):
            raise ValueError("Out-of-order daily profit observation")
        if state["trading_day"] != day:
            state = _empty(now)
        state = _current_policy(state)
        previous_trigger = state["trigger"]
        state.update(pnl=float(pnl), observed_at=now.isoformat(), error=None,
                     peak_pnl=max(state["peak_pnl"] if state["peak_pnl"] is not None else pnl, pnl))
        if not state["trigger"]:
            if pnl >= TARGET:
                state["trigger"] = "daily_profit_target_reached"
            elif pnl <= -LOSS_LIMIT:
                state["trigger"] = "daily_loss_limit_reached"
            if state["trigger"]:
                state["triggered_at"] = now.isoformat()
        db.execute("INSERT INTO daily_profit_guard(account,state) VALUES(?,?) ON CONFLICT(account) DO UPDATE SET state=excluded.state",
                   (int(account_id), json.dumps(state, allow_nan=False, separators=(",", ":"))))
        if state["trigger"] != previous_trigger:
            # State and its explanation commit together, before any close order.
            db.execute("INSERT INTO events(account,at,kind,payload) VALUES(?,?,?,?)",
                       (int(account_id), now.isoformat(), "daily_profit_stop",
                        json.dumps(state, allow_nan=False, separators=(",", ":"))))
    return state


def risk_block(state):
    from .bot_risk import RiskBlock
    code = state.get("trigger")
    return RiskBlock(code=code, message=MESSAGES[code]) if code in MESSAGES else None


def mark_unavailable(owner, account_id, *, now=None):
    """Keep any latch intact while showing a failed monitor read to the operator."""
    now = as_utc(now or datetime.now(timezone.utc))
    with connection(owner) as db:
        _table(db)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM daily_profit_guard WHERE account=?", (int(account_id),)).fetchone()
        state = _current_policy(json.loads(row[0])) if row else _empty(now)
        state["error"] = "Daily P&L could not be verified. New entries are paused; existing position protection continues."
        db.execute("INSERT INTO daily_profit_guard(account,state) VALUES(?,?) ON CONFLICT(account) DO UPDATE SET state=excluded.state",
                   (int(account_id), json.dumps(state, allow_nan=False)))


def poll(owner, account_id, contract_id, client, *, now=None):
    """Read complete realized P&L, then fresh positions and executable quotes."""
    from . import bot_service as service
    from .topbot_depth import feed
    injected_clock = now is not None
    now = as_utc(now or datetime.now(timezone.utc))
    state = read(owner, account_id, now=now)
    if state["trigger"]:
        return state  # A latched close must not depend on another P&L request.
    started = monotonic()
    def position_signature(rows):
        if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("account_id") != account_id for row in rows):
            raise ValueError("Daily P&L position account mismatch or unavailable positions")
        return sorted((str(row.get("contract_id")), str(row.get("signed_size")), str(row.get("average_price")),
                       str(row.get("id", row.get("position_id")))) for row in rows)
    before = position_signature(client.search_open_positions(account_id=account_id))
    realized_at = now if injected_clock else datetime.now(timezone.utc)
    realized, _, _ = service._fetch_account_daily_pnl(client, account_id=account_id, observed_at=realized_at)
    positions = client.search_open_positions(account_id=account_id)
    if before != position_signature(positions):
        # Do not mistake a just-closed position's not-yet-read realized profit
        # for a return to zero, or double count a position changing into a fill.
        raise ValueError("Account positions changed during the daily P&L observation")
    quote = feed.snapshot(owner, contract_id, now=now if injected_clock else None)
    checked_at = now if injected_clock else datetime.now(timezone.utc)
    if monotonic() - started > 15 or trading_day_key(checked_at) != trading_day_key(now):
        raise ValueError("Daily P&L observation expired during provider reads")
    quote_at = quote.get("observed_at")
    quote_fresh = bool(quote.get("ready") and quote_at and
                      0 <= (checked_at - as_utc(datetime.fromisoformat(quote_at))).total_seconds() <= 2)
    total = realized
    if not isinstance(positions, list):
        raise ValueError("Open positions are unavailable")
    for position in positions:
        if not isinstance(position, dict) or position.get("account_id") != account_id:
            raise ValueError("Daily P&L position account mismatch")
        size = service._finite_optional_float(position.get("signed_size"))
        if size is None or not position.get("contract_id"):
            raise ValueError("Daily P&L position metadata is incomplete")
        if size == 0:
            continue
        unrealized = service._finite_optional_float(position.get("unrealized_pnl"))
        if unrealized is None and position["contract_id"] == contract_id and contract_id.startswith("CON.F.US.MNQ.") and quote_fresh:
            average = service._finite_optional_float(position.get("average_price"))
            mark = service._finite_optional_float(quote.get("bid" if size > 0 else "ask"))
            if average is not None and mark is not None and average > 0 and mark > 0:
                unrealized = (mark - average) * size * 2
        if unrealized is None:
            raise ValueError("Complete account unrealized P&L is unavailable")
        total += unrealized
    return observe(owner, account_id, total, now=checked_at)


def public_status(owner, account_id, *, now=None):
    try:
        return {**read(owner, account_id, now=now), "available": True}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {"available": False, "message": "Daily profit protection state is unavailable; new entries are blocked."}
