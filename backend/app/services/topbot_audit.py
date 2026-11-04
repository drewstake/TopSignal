"""Append-only, synchronous local action journal; independent of SQL rollbacks.

An intent is durable before a broker call. Acknowledgement is not verification.
No deletion/rotation or raw provider payloads; reads are owner/account scoped.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4
from time import perf_counter, monotonic_ns


def location(owner):
    from .databento_cache import default_cache_root
    root = Path(os.environ['TOPSIGNAL_TOPBOT_AUDIT_DIR']) if os.getenv('TOPSIGNAL_TOPBOT_AUDIT_DIR') else default_cache_root() / 'topbot-audit'
    return root / sha256(str(owner).encode()).hexdigest() / 'actions.sqlite3'


@contextmanager
def connection(owner):
    path = location(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('''
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, account INTEGER NOT NULL,
            at TEXT NOT NULL, kind TEXT NOT NULL, operation TEXT, payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS account_seq ON events(account,seq);
          CREATE TABLE IF NOT EXISTS fill_identity(account INTEGER, kind TEXT, operation TEXT, event_seq INTEGER,
            PRIMARY KEY(account,kind,operation));
          CREATE TRIGGER IF NOT EXISTS no_event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append_only'); END;
          CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append_only'); END;
        ''')
        yield db
        db.commit()
    finally:
        db.close()


def scope(subject):
    return dict(owner=str(subject.user_id), account_id=int(subject.account_id),
                config_id=getattr(subject, 'bot_config_id', None), run_id=getattr(subject, 'bot_run_id', None),
                correlation_id=getattr(subject, 'correlation_id', None), decision_id=getattr(subject, 'bot_decision_id', None),
                entry_id=getattr(subject, 'id', None), contract_id=getattr(subject, 'contract_id', None))


def record(owner, account_id, kind, *, operation=None, **details):
    from .topbot_evidence import binding
    from ..bot_diagnostics import PROCESS_SESSION_ID
    details = {**details, "implementation": binding(), "process_session_id": PROCESS_SESSION_ID,
               "monotonic_ns": monotonic_ns()}
    payload = json.dumps(details, allow_nan=False, separators=(',', ':'), default=str)
    if len(payload.encode()) > 32768:
        raise ValueError('topbot_audit_event_too_large')
    with connection(owner) as db:
        deduplicate = kind in {'entry_fill_verified', 'exit_fill_verified'} and operation is not None
        if deduplicate:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT event_seq FROM fill_identity WHERE account=? AND kind=? AND operation=?',
                                  (int(account_id), kind, operation)).fetchone()
            if existing: return existing[0]
        cursor = db.execute('INSERT INTO events(account,at,kind,operation,payload) VALUES(?,?,?,?,?)',
                            (int(account_id), datetime.now(timezone.utc).isoformat(), kind, operation, payload))
        if deduplicate:
            db.execute('INSERT INTO fill_identity VALUES(?,?,?,?)', (int(account_id), kind, operation, cursor.lastrowid))
        return cursor.lastrowid


def page(owner, account_id, *, before=None, limit=100):
    if not 1 <= limit <= 200 or (before is not None and before <= 0):
        raise ValueError('invalid_audit_cursor')
    with connection(owner) as db:
        rows = db.execute('SELECT seq,at,kind,operation,payload FROM events WHERE account=? AND seq<? ORDER BY seq DESC LIMIT ?',
                          (int(account_id), before or 9223372036854775807, limit+1)).fetchall()
    return dict(items=[dict(seq=r[0], at=r[1], kind=r[2], operation=r[3], details=json.loads(r[4])) for r in rows[:limit]],
                next_before=rows[limit-1][0] if len(rows)>limit else None,
                storage='Worker persistent audit; retained until explicitly removed')


class AuditedClient:
    MUTATIONS = {'place_order', 'modify_stop_order', 'cancel_order', 'close_position'}
    FIELDS = {'account_id','contract_id','order_id','order_type','side','size','stop_price','limit_price',
              'custom_tag','stop_loss_bracket','take_profit_bracket'}

    def __init__(self, client, context):
        self.client = client.client if isinstance(client, AuditedClient) else client
        self.context = context
        self.last_operation = None
        self.last_call = None
        self.request_started = None

    def __getattr__(self, name):
        target = getattr(self.client, name)
        if name not in self.MUTATIONS:
            return target
        def call(**kwargs):
            if kwargs.get('account_id') != self.context['account_id']:
                raise ValueError('audit_account_scope_mismatch')
            operation = self.last_operation = str(uuid4())
            request = {k:v for k,v in kwargs.items() if k in self.FIELDS}
            intent_started = perf_counter()
            record(**self.context, kind='broker_request', operation=operation, action=name, request=request)
            audit_ms = (perf_counter()-intent_started)*1000
            started = perf_counter()
            self.request_started = started
            submitted_at = datetime.now(timezone.utc).isoformat()
            self.last_call = {"operation": operation, "submitted_at": submitted_at,
                              "intent_audit_ms": round(audit_ms, 3)}
            try:
                response = target(**kwargs)
            except Exception as exc:
                self.last_call.update(completed_at=datetime.now(timezone.utc).isoformat(),
                                      request_duration_ms=round((perf_counter()-started)*1000, 3))
                record(**self.context, kind='broker_outcome_unconfirmed', operation=operation,
                       action=name, error_type=type(exc).__name__, status_code=getattr(exc,'status_code',None),
                       timing={k:v for k,v in self.last_call.items() if k != "operation"})
                raise
            self.last_call.update(completed_at=datetime.now(timezone.utc).isoformat(),
                                  request_duration_ms=round((perf_counter()-started)*1000, 3))
            record(**self.context, kind='broker_acknowledged', operation=operation, action=name,
                   order_id=response.get('order_id') if isinstance(response,dict) else None,
                   timing={k:v for k,v in self.last_call.items() if k != "operation"},
                   outcome_note='Acknowledgement received; broker readback/fills determine completion')
            return response
        return call


def client_for(client, subject, **details):
    return AuditedClient(client, {**scope(subject), **details})
