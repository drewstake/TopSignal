"""Independent, resumable read-only provider history collection.

Policies, coverage and original provider revisions live only in the filesystem.
Compact current fills use the existing execution store. No orders or study
registrations are made here. Enable an owner policy explicitly using the CLI.
"""
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
import logging
import shutil
import sqlite3
from threading import Event, Thread
import time as clock

from .probabilistic_shadow import cache_root

log = logging.getLogger(__name__)
UTC = timezone.utc
SCHEMA = '''
CREATE TABLE IF NOT EXISTS policies(owner TEXT PRIMARY KEY, start_date TEXT NOT NULL,
 enabled INTEGER NOT NULL, record_active INTEGER NOT NULL, max_accounts INTEGER NOT NULL,
 checked_at REAL, error TEXT);
CREATE TABLE IF NOT EXISTS accounts(owner TEXT, account INTEGER, state TEXT NOT NULL,
 checked_at REAL NOT NULL, PRIMARY KEY(owner,account));
CREATE TABLE IF NOT EXISTS windows(owner TEXT, account INTEGER, day TEXT, state TEXT NOT NULL,
 fetched_start TEXT, fetched_end TEXT, checked_at REAL, next_retry REAL, attempts INTEGER NOT NULL,
 row_count INTEGER, error TEXT, failures INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,account,day));
CREATE TABLE IF NOT EXISTS revisions(owner TEXT, account INTEGER, trade TEXT, digest TEXT,
 first_seen REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,account,trade,digest));
CREATE TABLE IF NOT EXISTS prior_heads(owner TEXT, account INTEGER, trade TEXT, digest TEXT,
 first_seen REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,account,trade,digest));
'''


def path():
    return cache_root() / 'trade-history-sync-v1/state.sqlite3'


@contextmanager
def state(*, write=False):
    location = path().resolve()
    if not location.is_relative_to(cache_root().resolve()):
        raise ValueError('History state escaped cache root')
    if write:
        location.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(location.parent).free < 10_000_000_000:
            raise OSError('History synchronization free-space floor')
        if sum(p.stat().st_size for p in location.parent.glob('state.sqlite3*')) > 256*1024**2:
            raise OSError('History synchronization capacity review required')
        db = sqlite3.connect(location, timeout=2)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(SCHEMA)
    else:
        db = sqlite3.connect(location.as_uri()+'?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        yield db
        if write:
            db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def configure(owner, start_date, *, record_active=False, max_accounts=8, enabled=True):
    if not owner or not 1 <= max_accounts <= 32 or date.fromisoformat(start_date) > datetime.now(UTC).date():
        raise ValueError('Invalid explicit coverage policy')
    if record_active:
        from .trade_progress_store import verify_destination
        verify_destination()
    with state(write=True) as db:
        db.execute('INSERT INTO policies VALUES(?,?,?,?,?,NULL,NULL) ON CONFLICT(owner) DO UPDATE SET '
                   'start_date=excluded.start_date,enabled=excluded.enabled,record_active=excluded.record_active,'
                   'max_accounts=excluded.max_accounts', (owner, start_date, int(enabled), int(record_active), max_accounts))


class PartialHistory(ValueError):
    pass


def fetch_window(client, account, start, end, *, wait=clock.sleep, max_requests=32, threshold=1000):
    """Use documented timestamp bounds, not undocumented offset pagination.

Trade/search publishes no row cap, pagination or maximum date-window contract.
Use <=1 day requests; conservatively subdivide dense responses at 1000 legs.
An indivisible/over-budget window remains partial, never silently complete.
"""
    pending, found, calls = [(start, end)], {}, 0
    while pending:
        left, right = pending.pop()
        if calls >= max_requests:
            raise PartialHistory('request_budget')
        wait(.65)  # <=93 calls/min for this worker, below the general 200/60 limit.
        rows = client.fetch_trade_history(account, left, right, require_valid_collection=True)
        calls += 1
        if any(int(r['account_id']) != account or not r.get('source_trade_id') for r in rows):
            raise PartialHistory('invalid_scope_or_identity')
        if len(rows) >= threshold:
            if right-left <= timedelta(microseconds=2):
                raise PartialHistory('indivisible_dense_timestamp')
            middle = left+(right-left)/2
            pending.extend(((middle, right), (left, middle)))
            continue
        for row in rows:
            ts = row['timestamp']
            if ts.tzinfo is None or not left <= ts <= right:
                raise PartialHistory('out_of_range')
            key = str(row['source_trade_id'])
            if key in found and found[key] != row:
                raise PartialHistory('conflicting_pages')
            found[key] = row
    return sorted(found.values(), key=lambda r: (r['timestamp'], str(r['source_trade_id'])))


def due_days(owner, account, start_date, now, *, maximum=2):
    with state() as db:
        saved = {r['day']: dict(r) for r in db.execute('SELECT * FROM windows WHERE owner=? AND account=?', (owner, account))}
    first, today = date.fromisoformat(start_date), now.date()
    days = [today-timedelta(days=i) for i in range(min(10, (today-first).days+1))]
    days += [first+timedelta(days=i) for i in range(max(0, (today-first).days-9))]
    due = []
    for day in days:
        row = saved.get(day.isoformat())
        refresh = 300 if day == today else 10800 if (today-day).days < 10 else 30*86400
        if row and (row['next_retry'] or 0) > now.timestamp():
            continue
        if not row or row['state'] != 'response_covered' or now.timestamp()-(row['checked_at'] or 0) >= refresh:
            due.append(day)
        if len(due) >= maximum:
            break
    return due


def sync_day(sessions, client, owner, account, day, *, now=None, wait=clock.sleep):
    from .projectx_trades import store_trade_events
    from ..models import Account, ProjectXTradeEvent
    now = now or datetime.now(UTC)
    start = datetime.combine(day, time(), UTC)
    end = min(start+timedelta(days=1), now)
    with sessions() as db:
        owned = db.query(Account.id).filter(Account.user_id == owner, Account.external_id == str(account),
            Account.provider == 'projectx', Account.trade_data_source == 'projectx', Account.archived_at.is_(None)).first()
        if not owned:
            raise ValueError('Account not owned or not provider-readable')
    with state() as db:
        old = db.execute('SELECT attempts,failures FROM windows WHERE owner=? AND account=? AND day=?', (owner, account, day.isoformat())).fetchone()
    attempts = (old[0] if old else 0)+1
    status, error, count, retry = 'failed', None, None, None
    failures = 0
    try:
        events = fetch_window(client, account, start, end, wait=wait)
        # Append evidence BEFORE merging current heads. A crash can retry safely;
        # coverage advances only after both stores have committed.
        with state(write=True) as local:
            for event in events:
                encoded = json.dumps(event, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)
                local.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?,?)',
                    (owner, account, str(event['source_trade_id']), sha256(encoded.encode()).hexdigest(), now.timestamp(), encoded))
        with sessions() as db:
            # Preserve pre-existing execution heads before an authenticated
            # correction changes the display row. No historical version is lost.
            identities=[str(e['source_trade_id']) for e in events]
            previous=db.query(ProjectXTradeEvent).filter(ProjectXTradeEvent.user_id==owner,
                ProjectXTradeEvent.account_id==account,ProjectXTradeEvent.source_trade_id.in_(identities)).all() if identities else []
            with state(write=True) as local:
                for row in previous:
                    encoded=json.dumps({c.name:getattr(row,c.name) for c in row.__table__.columns},
                        sort_keys=True,separators=(',',':'),default=str,allow_nan=False)
                    local.execute('INSERT OR IGNORE INTO prior_heads VALUES(?,?,?,?,?,?)',
                        (owner,account,str(row.source_trade_id),sha256(encoded.encode()).hexdigest(),now.timestamp(),encoded))
            # A fresh authenticated REST snapshot can correct null P&L too.
            # Raw provider content stays untouched in the revision journal.
            store_trade_events(db, [dict(e, correction=True) for e in events], user_id=owner)
            db.commit()
        status, count = ('provisional' if end < start+timedelta(days=1) else 'response_covered'), len(events)
        retry = now.timestamp()+300 if status == 'provisional' else None
    except Exception as exc:
        failures = (old['failures'] if old else 0)+1
        error = type(exc).__name__  # Never serialize provider messages or credentials.
        status = 'partial' if isinstance(exc, PartialHistory) else 'unavailable' if getattr(exc, 'status_code', None) in (401,403,404) else 'temporarily_inaccessible'
        retry = now.timestamp()+min(21600, 60*2**min(failures-1, 9))
        log.warning('history_sync_failed category=%s error_type=%s', status, error)
    with state(write=True) as local:
        local.execute('INSERT INTO windows VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,account,day) DO UPDATE SET '
                      'state=excluded.state,fetched_start=excluded.fetched_start,fetched_end=excluded.fetched_end,'
                      'checked_at=excluded.checked_at,next_retry=excluded.next_retry,attempts=excluded.attempts,'
                      'row_count=excluded.row_count,error=excluded.error,failures=excluded.failures',
                      (owner, account, day.isoformat(), status, start.isoformat(), end.isoformat(), now.timestamp(), retry, attempts, count, error, failures))
    return {'account': account, 'day': day.isoformat(), 'state': status, 'rows': count, 'error': error}


class HistorySynchronizer:
    def __init__(self, sessions, clients):
        self.sessions, self.clients = sessions, clients
        self.halt = Event()
        self.thread = None

    def start(self):
        self.thread = Thread(target=self.run, name='trade-history-sync', daemon=True)
        self.thread.start()

    def stop(self):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=35)
        return not self.thread or not self.thread.is_alive()

    def cycle(self):
        from ..models import Account
        from .projectx_accounts import sync_projectx_accounts
        from . import trade_progress_store as progress
        with state() as db:
            policies = [dict(r) for r in db.execute('SELECT * FROM policies WHERE enabled=1')]
        for policy in policies:
            owner = policy['owner']
            try:
                with self.sessions() as db:
                    client = self.clients(db, user_id=owner)
                    client.timeout_seconds = min(client.timeout_seconds, 15)
                provider = client.list_accounts(only_active_accounts=False)
                with self.sessions() as db:
                    sync_projectx_accounts(db, provider, user_id=owner)
                    db.commit()
                    permitted = {int(a.external_id) for a in db.query(Account).filter(Account.user_id == owner,
                        Account.trade_data_source == 'projectx', Account.archived_at.is_(None)).all() if a.external_id.isdigit()}
                # Fresh owned visibility determines passive recording eligibility.
                # A broker's trading permission is independent of observation.
                eligible = [a for a in provider if a['id'] in permitted and a['is_visible']]
                capacity = len(eligible) <= policy['max_accounts']
                if capacity and policy['record_active']:
                    capacity = progress.reconcile_policy(owner, [a['id'] for a in eligible], policy['max_accounts'])
                with state(write=True) as db:
                    for a in provider:
                        classification = ('active_supported' if a in eligible else 'not_active_or_visible') if capacity else 'capacity_review_required'
                        db.execute('INSERT OR REPLACE INTO accounts VALUES(?,?,?,?)', (owner, a['id'], classification, clock.time()))
                    db.execute('UPDATE policies SET checked_at=?,error=? WHERE owner=?',
                               (clock.time(), None if capacity else 'capacity_review_required', owner))
                if not capacity:
                    continue
                for a in eligible:
                    if self.halt.is_set():
                        return
                    for day in due_days(owner,a['id'],policy['start_date'],datetime.now(UTC)):
                        if self.halt.is_set():
                            return
                        sync_day(self.sessions,client,owner,a['id'],day,wait=self.wait)
            except Exception as exc:
                with state(write=True) as db:
                    db.execute('UPDATE policies SET checked_at=?,error=? WHERE owner=?', (clock.time(),type(exc).__name__,owner))

    def wait(self, seconds):
        if self.halt.wait(seconds):
            raise InterruptedError('History synchronization stopped')

    def run(self):
        from .historical_trade_excursions import recorder_lock
        while not self.halt.is_set():
            try:
                if path().exists():
                    with recorder_lock(path().with_suffix('.lock')) as acquired:
                        if acquired:
                            self.cycle()
            except Exception as exc:
                log.warning('history_sync_cycle_failed error_type=%s', type(exc).__name__)
            self.halt.wait(30)
