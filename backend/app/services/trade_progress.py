"""Deterministic recorder state machine; called only by the recording worker.

Quotes aren't archived here: each price updates observed extrema, while periodic
and lifecycle observations retain their contemporaneous entry basis and size.
"""
from datetime import datetime, timezone
import json
from hashlib import sha256
import math
import uuid

from . import trade_progress_store as store
from .instruments import resolve_point_value
from .streaming_pnl_tracker import parse_position_update, parse_quote_trade

CADENCE = store.SAMPLE_INTERVAL_SECONDS
STALE = 30


def timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (float(value) / 1000 if value > 1e12 else float(value)) if math.isfinite(value) else None
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and not isinstance(value, bool) else None
    except (ValueError, TypeError, OverflowError):
        return None


class ProgressEngine:
    def __init__(self, owner, account, points, *, clock=store.now):
        self.owner, self.account, self.points, self.clock = owner, account, points, clock
        self.active, self.cursors, self.prices = {}, {}, {}
        self.dirty, self.samples, self.fills = {}, [], []
        self.connected = {"market": False, "user": False}
        self.reconciled = False
        with store.connect(write=True) as db:
            for row in db.execute("SELECT state FROM trade_progress_positions WHERE owner=? AND account=? AND ended IS NULL", (owner, account)):
                state = json.loads(row[0])
                self.active[state["contract"]] = state
            for row in db.execute("SELECT contract,payload FROM trade_progress_cursors WHERE owner=? AND account=?", (owner, account)):
                self.cursors[row[0]] = json.loads(row[1])
        self.gap("backend_restart", self.clock())

    def reason(self, state, reason):
        if reason not in state["reasons"]:
            state["reasons"].append(reason)
        self.dirty[state["id"]] = state

    def gap(self, reason, at, *, since=None):
        self.reconciled = False
        self.prices.clear()
        for state in self.active.values():
            self.reason(state, reason)
            self.sample(state, "gap", at, issue=reason, gap_start=since or state["last_recorded"])

    def connection(self, stream, connected, at):
        self.connected[stream] = connected
        if not connected:
            self.gap(stream + "_disconnected", at)

    def quote(self, payload, at):
        update = parse_quote_trade(payload)
        if update is None:
            return
        ts = update.timestamp.timestamp()
        previous = self.prices.get(update.contract_id)
        if ts > at or (previous and (ts < previous["timestamp"] or (ts == previous["timestamp"] and update.mark_price == previous["price"]))):
            return
        basis = "bid_ask_midpoint" if any(payload.get(k) is not None for k in ("bid", "bidPrice", "bestBid", "bestBidPrice")) and any(payload.get(k) is not None for k in ("ask", "askPrice", "bestAsk", "bestAskPrice")) else "last_price"
        self.prices[update.contract_id] = dict(price=update.mark_price, timestamp=ts, basis=basis)
        state = self.active.get(update.contract_id)
        if state and ts >= state["basis_since"]:
            value = self.observation(state, at)
            if value["points"] is not None:
                for key, better in (("best", lambda a,b: a>b), ("worst", lambda a,b: a<b)):
                    old = state[key]
                    if old is None or better(value["points"], old["points"]):
                        state[key] = value | {"ts": ts, "kind": key}
                        self.dirty[state["id"]] = state
                # Dollar extrema can differ from point extrema when size changes.
                if value["unrealized_dollars"] is not None:
                    for key, better in (("best_dollars", lambda a,b: a>b), ("worst_dollars", lambda a,b: a<b)):
                        old = state[key]
                        if old is None or better(value["unrealized_dollars"], old["unrealized_dollars"]):
                            state[key] = value | {"ts": ts, "kind": key}
                            self.dirty[state["id"]] = state

    def observation(self, state, at, issue=None):
        mark = self.prices.get(state["contract"])
        if issue is None:
            if not all(self.connected.values()) or not self.reconciled:
                issue = "position_or_connection_unverified"
            elif mark is None:
                issue = "missing_price"
            elif not 0 <= at-mark["timestamp"] <= STALE:
                issue = "stale_price"
        points = None if issue else ((mark["price"]-state["average_entry"]) * (1 if state["quantity"]>0 else -1))
        dollars = None if points is None or state["point_value"] is None else points*abs(state["quantity"])*state["point_value"]
        return dict(ts=at, owner=self.owner, account=self.account, position_id=state["id"],
                    provider_position_id=state["provider_id"], contract=state["contract"],
                    direction="long" if state["quantity"]>0 else "short", quantity=abs(state["quantity"]),
                    average_entry=state["average_entry"], mark=mark["price"] if mark else None,
                    mark_timestamp=mark["timestamp"] if mark else None, mark_basis=mark["basis"] if mark else None,
                    points=points, unrealized_dollars=dollars, point_value=state["point_value"],
                    issue=issue, dollar_issue="unknown_point_value" if state["point_value"] is None else None,
                    source="live_observation")

    def sample(self, state, kind, at, *, issue=None, **extra):
        value = self.observation(state, at, issue) | dict(kind=kind, **extra)
        self.samples.append(value)
        state["last_recorded"] = at
        if value["issue"]:
            self.reason(state, value["issue"])
        self.dirty[state["id"]] = state

    def position(self, payload, at, *, snapshot=False):
        if number(payload.get("size")) == 0:
            # Flat notifications may omit type/average entry; the outer hub has
            # already identified this as a position event, never an order.
            payload = dict(payload, netQty=0)
        update = parse_position_update(payload)
        if update is None or update.account_id != self.account:
            return False
        contract = update.contract_id
        event_at = next((timestamp(payload[k]) for k in ("timestamp", "updatedAt", "updateTime", "updateTimestamp") if payload.get(k) is not None), None)
        created = timestamp(payload.get("creationTimestamp"))
        provider_id = str(payload.get("id") or payload.get("positionId") or "")
        identity = f"{provider_id}:{created}" if provider_id and created is not None else None
        cursor = self.cursors.get(contract, {})
        if snapshot and cursor.get("received", 0) > payload.get("_request_started", at):
            return True
        if created is not None and created < cursor.get("created", 0):
            return False
        if event_at is not None and (event_at > at+1 or (cursor.get("event_at") is not None and event_at < cursor["event_at"])):
            return False
        # Retired provider identities cannot reopen a position after replay/reconnect.
        if identity and identity in cursor.get("retired", []) and update.net_qty:
            return False
        state = self.active.get(contract)
        replaced = False
        signature = [identity, update.net_qty, update.avg_price]
        if not snapshot and signature == cursor.get("signature"):
            return True
        if event_at is not None and event_at == cursor.get("event_at") and signature != cursor.get("signature") and not snapshot:
            self.gap("conflicting_position_timestamp", at)
            return False
        if snapshot and state and state["last_event_received"] > payload.get("_request_started", at):
            return True  # A response to an older REST request cannot overwrite a newer stream event.
        if state and state["identity"] and created is not None and created < state["provider_created"]:
            return False
        if snapshot and state and not state["identity"] and identity:
            # A receipt-only opening notification cannot establish provider
            # identity. Split at the fresh snapshot; never silently attach its
            # earlier creation time to an uncertain live lifecycle.
            self.close(state, "identity_unverified", at, issue="lifecycle_identity_uncertain")
            state = None
        if not update.net_qty:
            if state:
                if identity and state["identity"] and identity != state["identity"]:
                    return False
                self.close(state, "exit", at, event_at=event_at)
        else:
            reverse = bool(state and state["quantity"]*update.net_qty<0)
            replaced = bool(state and identity and state["identity"] and identity != state["identity"])
            if reverse or replaced:
                self.close(state, "reversal_exit" if reverse else "identity_changed", at, event_at=event_at,
                           issue="missing_position_transition" if replaced else None)
                state = None
            if state is None:
                opened = created if created is not None and created <= at else at
                if reverse:
                    opened = event_at or at
                state = dict(id=str(uuid.uuid4()), owner=self.owner, account=self.account, contract=contract,
                    provider_id=provider_id or None, identity=identity, provider_created=created or at,
                    opened=opened, first_observed=at, ended=None, exit_time=None,
                    quantity=update.net_qty, average_entry=update.avg_price, basis_since=event_at or at,
                    point_value=resolve_point_value(symbol=update.symbol, contract_id=contract, point_value_by_symbol=self.points),
                    best=None, worst=None, best_dollars=None, worst_dollars=None, reasons=[],
                    last_recorded=at, last_periodic=at, last_event_received=at)
                self.active[contract] = state
                if snapshot or at-opened>1:
                    self.reason(state, "recording_began_after_entry")
                if identity is None:
                    self.reason(state, "position_identity_incomplete")
                kind = "reversal_entry" if reverse else "entry"
            elif signature != cursor.get("signature"):
                before = abs(state["quantity"])
                kind = "partial_exit" if abs(update.net_qty)<before else "scale_in" if abs(update.net_qty)>before else "basis_change"
                state.update(quantity=update.net_qty, average_entry=update.avg_price, basis_since=event_at or at)
                if snapshot:
                    self.reason(state, "position_change_found_by_snapshot")
            else:
                kind = None
            state["last_event_received"] = at
            if kind:
                if event_at is None:
                    self.reason(state, "position_update_time_unavailable")
                self.sample(state, kind, at, event_timestamp=event_at, timing="snapshot" if snapshot else "provider" if event_at else "receipt")
        retired = cursor.get("retired", [])
        if not update.net_qty or replaced:
            old_identity = cursor.get("signature", [None])[0]
            if old_identity and old_identity != identity:
                retired = (retired+[old_identity])[-256:]
            elif old_identity and not update.net_qty:
                retired = (retired+[old_identity])[-256:]
        self.cursors[contract] = dict(signature=signature, event_at=event_at or cursor.get("event_at"),
                                     created=created or cursor.get("created", 0), received=at, retired=retired)
        return True

    def close(self, state, kind, at, *, event_at=None, issue=None):
        self.sample(state, kind, at, issue=issue, quantity_after=0, event_timestamp=event_at,
                    timing="provider" if event_at is not None else "receipt")
        state.update(ended=at, exit_time=event_at)
        if event_at is None:
            self.reason(state, "exit_time_unavailable")
        self.active.pop(state["contract"], None)

    def snapshot(self, payloads, started, at):
        # Validate the entire snapshot before treating any absent contract as closed.
        parsed = [parse_position_update(p) for p in payloads]
        if any(p is None or p.account_id != self.account or not p.net_qty for p in parsed):
            self.gap("invalid_position_snapshot", at)
            return
        seen = {p.contract_id for p in parsed}
        was_reconciled = self.reconciled
        self.reconciled = True
        for payload in payloads:
            contract = payload.get("contractId")
            state = self.active.get(contract)
            identity_known = payload.get("id") and timestamp(payload.get("creationTimestamp")) is not None
            if not was_reconciled and state and (not state["identity"] or not identity_known):
                self.reason(state, "lifecycle_identity_uncertain")
                self.close(state, "identity_unverified", at, issue="lifecycle_identity_uncertain")
                self.cursors.pop(contract, None)
            self.position(dict(payload, _request_started=started), at, snapshot=True)
        for contract, state in list(self.active.items()):
            if contract not in seen and state["last_event_received"] <= started:
                self.reason(state, "exit_during_missing_coverage")
                self.close(state, "exit_detected", at, issue="exit_during_missing_coverage")
                old = self.cursors.get(contract, {})
                old["retired"] = (old.get("retired", []) + [state["identity"]])[-256:]
                old["signature"] = [state["identity"], 0, 0]
                self.cursors[contract] = old
        if not was_reconciled:
            for state in self.active.values():
                self.sample(state, "coverage_resumed", at)

    def fill(self, payload, at):
        if payload.get("accountId") != self.account:
            return
        trade, contract = payload.get("id"), payload.get("contractId")
        ts = timestamp(payload.get("creationTimestamp"))
        if trade is None or not contract or ts is None:
            return
        # Corrected and voided legs replace the same scoped ID; never add an
        # unproven cumulative realized number to unrealized P&L.
        self.fills.append(dict(trade_id=str(trade), contract=contract, ts=ts,
            realized_dollars=number(payload.get("profitAndLoss")), fees=number(payload.get("fees")),
            commissions=number(payload.get("commissions")), quantity=number(payload.get("size")),
            price=number(payload.get("price")), voided=bool(payload.get("voided")), source="live_broker_fill",
              clock_issue="provider_timestamp_after_receipt" if ts>at else None,
            order_id=payload.get("orderId"), fill_id=payload.get("fillId"),
            realized_basis="provider_reported_separate_from_unrealized"))

    def tick(self, at):
        for state in self.active.values():
            elapsed = at-state["last_periodic"]
            if elapsed >= CADENCE:
                if elapsed > CADENCE*2:
                    self.reason(state, "sampling_gap")
                    self.sample(state, "gap", at, issue="sampling_gap", gap_start=state["last_periodic"])
                self.sample(state, "periodic", at)
                state["last_periodic"] = at

    def flush(self, *, error=None, diagnostics=None):
        with store.connect(write=True) as db:
            for state in self.dirty.values():
                db.execute("INSERT INTO trade_progress_positions VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ended=excluded.ended,state=excluded.state",
                    (state["id"], self.owner, self.account, state["contract"], state["opened"], state["ended"], store.encode(state)))
            db.executemany("INSERT INTO trade_progress_samples(position,ts,kind,payload) VALUES(?,?,?,?)",
                [(s["position_id"], s["ts"], s["kind"], store.encode(s)) for s in self.samples])
            for contract, cursor in self.cursors.items():
                db.execute("INSERT OR REPLACE INTO trade_progress_cursors VALUES(?,?,?,?)", (self.owner,self.account,contract,store.encode(cursor)))
            for fill in self.fills:
                key = fill["trade_id"]
                encoded = store.encode(fill)
                # Receipt clock uncertainty is observation metadata, not a new
                # broker revision when the same leg replays after reconnect.
                content = {k:v for k,v in fill.items() if k != 'clock_issue'}
                digest = sha256(store.encode(content).encode()).hexdigest()
                # Older stores hashed receipt-clock metadata too. Reuse their
                # immutable receipt when the actual broker content is equal;
                # do not manufacture a correction at the upgrade boundary.
                old = db.execute('SELECT digest,payload FROM trade_progress_fill_revisions '
                    'WHERE owner=? AND account=? AND trade=?', (self.owner,self.account,key)).fetchall()
                contents = {store.encode(content)}
                retained_clock_issue = fill.get('clock_issue')
                for revision in old:
                    prior = json.loads(revision['payload'])
                    normalized = store.encode({k:v for k,v in prior.items() if k != 'clock_issue'})
                    contents.add(normalized)
                    if normalized == store.encode(content):
                        digest = revision['digest']
                        retained_clock_issue = prior.get('clock_issue') or retained_clock_issue
                db.execute("INSERT OR IGNORE INTO trade_progress_fill_revisions VALUES(?,?,?,?,?,?)",
                           (self.owner,self.account,key,digest,self.clock(),encoded))
                first = json.loads(db.execute('SELECT payload FROM trade_progress_fill_revisions '
                    'WHERE owner=? AND account=? AND trade=? AND digest=?', (self.owner,self.account,key,digest)).fetchone()[0])
                fill = fill | dict(clock_issue=first.get('clock_issue') or retained_clock_issue)
                if len(contents) > 1:
                    # GatewayUserTrade has no revision sequence. An old leg
                    # replay must not silently undo a correction/void. Preserve
                    # every distinct payload and withhold an authoritative total.
                    fill = fill | dict(revision_conflict=True, reported_realized_dollars=fill["realized_dollars"], realized_dollars=None)
                db.execute("INSERT OR REPLACE INTO trade_progress_fills VALUES(?,?,?,?,?,?)",
                    (self.owner,self.account,key,fill["contract"],fill["ts"],store.encode(fill)))
            db.execute("UPDATE trade_progress_scopes SET heartbeat=?,error=? WHERE owner=? AND account=?",
                       (self.clock(),error,self.owner,self.account))
            if diagnostics is not None:
                db.execute('INSERT OR REPLACE INTO trade_progress_meta VALUES(?,?)',
                    (store.diagnostics_key(self.owner,self.account),store.encode(diagnostics)))
        self.dirty.clear()
        self.samples.clear()
        self.fills.clear()
