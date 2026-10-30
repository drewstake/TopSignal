"""Append-only local market evidence. No application database or broker mutations.

SQLite WAL/FULL transactions are the atomic filesystem batches. Heads and coverage
are rebuildable indexes; versions, transitions, decisions and events are immutable.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import zlib
from time import monotonic

from .probabilistic_shadow import cache_root
from .trading_day import as_utc, futures_session_is_open, trading_day_bounds_utc, trading_day_date

VERSION = 1
MINUTE = timedelta(minutes=1)
CALENDAR = 'topsignal-cme-equity-' + hashlib.sha256(Path(__file__).with_name('trading_day.py').read_bytes()).hexdigest()[:16]
logger = logging.getLogger(__name__)
_health = {}
_lock = threading.Lock()


def enabled():
    return os.getenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', '').lower() in {'1', 'true', 'yes', 'on'}


def stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return as_utc(value).isoformat()


def json_default(value):
    if isinstance(value, datetime):
        return stamp(value)
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=json_default)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def pack(value):
    return zlib.compress(canonical(value).encode(), 6)


def unpack(value):
    return json.loads(zlib.decompress(value))


def location(owner, root=None):
    return (Path(root) if root else cache_root()) / 'market-archive' / 'v1' / hashlib.sha256(str(owner).encode()).hexdigest() / 'archive.sqlite3'


def failure(owner, operation, exc):
    # Never log provider payloads, URLs, credentials or arbitrary exception text.
    from .market_archive_quotes import diagnostic
    info = diagnostic(exc)
    with _lock:
        health = _health.setdefault(str(owner), {})
        health.update(last_error={'at': stamp(datetime.now(timezone.utc)), 'operation': operation, **info},
                      errors=health.get('errors', 0) + 1)
        if operation.startswith('optional_quote'):
            if monotonic() - health.get('last_quote_error_log', -60) < 30:
                health['suppressed_quote_error_logs'] = health.get('suppressed_quote_error_logs', 0)+1
                return
            health['last_quote_error_log'] = monotonic()
    logger.error('market_archive_failed operation=%s error_type=%s sqlite_code=%s errno=%s category=%s retryable=%s',
                 operation, info['type'], info['sqlite_code'], info['errno'], info['category'], info['recoverable'])


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bars(id TEXT PRIMARY KEY, stream TEXT NOT NULL, at TEXT NOT NULL, received TEXT NOT NULL, payload BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS bars_stream_time ON bars(stream,at);
CREATE TABLE IF NOT EXISTS heads(stream TEXT NOT NULL, at TEXT NOT NULL, id TEXT NOT NULL REFERENCES bars(id), PRIMARY KEY(stream,at));
CREATE TABLE IF NOT EXISTS revisions(seq INTEGER PRIMARY KEY, stream TEXT NOT NULL, at TEXT NOT NULL, id TEXT NOT NULL REFERENCES bars(id), received TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, at TEXT NOT NULL, config INTEGER NOT NULL, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS decision_commits(id TEXT PRIMARY KEY REFERENCES decisions(id), confirmed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS decisions_time ON decisions(at);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, at TEXT NOT NULL, contract TEXT, payload BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS events_kind_time ON events(kind,at);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coverage(stream TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(stream,day));
"""


class Archive:
    def __init__(self, owner, root=None):
        self.owner = str(owner)
        self.path = location(owner, root)

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.path.parent).free < 512 * 1024 * 1024:
            raise OSError('archive_free_space_floor')
        db = sqlite3.connect(self.path, timeout=.25)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, VERSION):
                raise ValueError('Unsupported archive schema')
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            db.execute(f'PRAGMA user_version={VERSION}')
            db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('schema', canonical({'version': VERSION, 'calendar': CALENDAR, 'created_at': stamp(datetime.now(timezone.utc)), 'storage': 'local SQLite WAL FULL', 'retention': 'no automatic deletion'})))
            yield db
            set_state(db, 'last_write', stamp(datetime.now(timezone.utc)))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def reader(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=.25)
        try:
            yield db
        finally:
            db.close()

    def decision(self, prepared):
        with self.transaction() as db:
            identity = str(prepared['decision_id'])
            if db.execute('SELECT 1 FROM decisions WHERE id=?', (identity,)).fetchone():
                return
            refs = []
            receipts = []
            for original in prepared['candles']:
                row = dict(original)
                received = row.pop('fetched_at')
                receipts.append({'fetched_at': received, 'first_fetched_at': row.pop('first_fetched_at'), 'provider_revision_hash': row.pop('provider_revision_hash')})
                refs.append(put_bar(db, row, received or prepared['at']))
            record = {k: v for k, v in prepared.items() if k != 'candles'}
            record.update(input_ids=refs, input_receipts=receipts, input_sha256=digest(refs), schema=VERSION)
            model_id = prepared['forecast'].get('artifact_sha256')
            record['artifact_status'] = 'no_model_used'
            if model_id:
                if not db.execute('SELECT 1 FROM artifacts WHERE id=?', (model_id,)).fetchone():
                    from .probabilistic_observation import observation_path
                    from .probabilistic_shadow import model_path
                    for path in (observation_path(self.owner, prepared['contract'], False), model_path(self.owner, prepared['contract'], False)):
                        if path.is_file():
                            artifact = json.loads(path.read_text(encoding='utf-8'))
                            if digest(artifact) == model_id:
                                db.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?)', (model_id, pack(artifact)))
                                break
                record['artifact_status'] = 'preserved' if db.execute('SELECT 1 FROM artifacts WHERE id=?', (model_id,)).fetchone() else 'unavailable_or_changed'
            db.execute('INSERT INTO decisions VALUES (?,?,?,?)', (identity, record['at'], record['config'], pack(record)))

    def reconstruct(self, decision_id):
        with self.reader() as db:
            row = db.execute('SELECT payload FROM decisions WHERE id=?', (str(decision_id),)).fetchone()
            if row is None:
                raise KeyError(decision_id)
            record = unpack(row[0])
            assert digest(record['input_ids']) == record['input_sha256']
            candles = []
            for ident, receipt in zip(record['input_ids'], record['input_receipts'], strict=True):
                candle = unpack(db.execute('SELECT payload FROM bars WHERE id=?', (ident,)).fetchone()[0])
                if digest(candle) != ident:
                    raise ValueError('Corrupt input revision')
                candles.append({**candle, **receipt})
            confirmed = db.execute('SELECT confirmed_at FROM decision_commits WHERE id=?', (str(decision_id),)).fetchone()
            return {**record, 'execution_commit_confirmed_at': confirmed[0] if confirmed else None, 'candles': candles}

    def events(self, events):
        inserted = 0
        with self.transaction() as db:
            for event in events:
                # Provider ID/timestamp/content identity; receipt does not defeat retry dedup.
                from .market_archive_spool import event_identity
                identity = event_identity(event)
                inserted += db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                           (identity, event['kind'], event['received_at'], event.get('contract'), pack(event))).rowcount
        return inserted


def set_state(db, key, value):
    db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, canonical(value)))


def get_state(db, key, default=None):
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def stream_key(contract, timeframe=1, origin='provider_native', live=False):
    if not str(contract).startswith('CON.F.US.MNQ.'):
        raise ValueError('Explicit MNQ delivery contract required')
    return canonical(['projectx', contract, timeframe, origin, live])


def put_bar(db, row, received):
    ident = digest(row)
    stream = stream_key(row['contract'], row['timeframe_minutes'], row['origin'], row['live_requested'])
    at = row['timestamp']
    db.execute('INSERT OR IGNORE INTO bars VALUES (?,?,?,?,?)', (ident, stream, at, received, pack(row)))
    old = db.execute('SELECT id FROM heads WHERE stream=? AND at=?', (stream, at)).fetchone()
    if old is None or old[0] != ident:
        db.execute('INSERT INTO revisions(stream,at,id,received) VALUES (?,?,?,?)', (stream, at, ident, received))
        db.execute('INSERT INTO heads VALUES (?,?,?) ON CONFLICT(stream,at) DO UPDATE SET id=excluded.id', (stream, at, ident))
    return ident


def native_bar(bar, contract, timeframe=1, live=False):
    at = stamp(bar['timestamp'])
    when = datetime.fromisoformat(at)
    if when.second or when.microsecond or when.minute % min(timeframe, 60):
        raise ValueError('Unaligned candle')
    prices = [float(bar[k]) for k in ('open', 'high', 'low', 'close')]
    volume = float(bar['volume'])
    if (not all(math.isfinite(p) and p > 0 for p in prices) or not math.isfinite(volume) or volume < 0
            or prices[2] > min(prices[0], prices[3]) or prices[1] < max(prices[0], prices[3])):
        raise ValueError('Invalid candle')
    return dict(provider='projectx', contract=contract, timeframe_minutes=timeframe,
                origin='provider_native', timestamp=at, live_requested=live,
                feed_status='unverified', partial=bool(bar.get('is_partial', False)),
                **dict(zip(('open', 'high', 'low', 'close'), prices)), volume=volume)


def expected_minutes(day):
    start, end = trading_day_bounds_utc(day)
    return [start + i * MINUTE for i in range(int((end + timedelta(microseconds=1) - start) / MINUTE))
            if futures_session_is_open(start + i * MINUTE, symbol='MNQ')]


def refresh_day(db, contract, day, now, received):
    """At most 1,440 rows; no synthetic minutes, cross-delivery or partial buckets."""
    stream = stream_key(contract)
    start, end = trading_day_bounds_utc(day)
    rows = {at: (ident, unpack(payload)) for at, ident, payload in db.execute(
        'SELECT h.at,h.id,b.payload FROM heads h JOIN bars b ON b.id=h.id WHERE h.stream=? AND h.at>=? AND h.at<=? ORDER BY h.at',
        (stream, stamp(start), stamp(end)))}
    expected = expected_minutes(day)
    complete = [t for t in expected if t + MINUTE <= now]
    missing = [stamp(t) for t in complete if stamp(t) not in rows]
    ranges = []
    for value in missing:
        if ranges and ranges[-1]['end'] == value:
            ranges[-1]['end'] = stamp(datetime.fromisoformat(value) + MINUTE)
            ranges[-1]['minutes'] += 1
        else:
            ranges.append({'start': value, 'end': stamp(datetime.fromisoformat(value) + MINUTE), 'minutes': 1})
    previous = db.execute('SELECT payload FROM coverage WHERE stream=? AND day=?', (stream, day.isoformat())).fetchone()
    old = json.loads(previous[0]) if previous else {}
    coverage = dict(day=day.isoformat(), contract=contract, calendar=CALENDAR,
                    expected=len(complete), observed=sum(stamp(t) in rows for t in complete),
                    missing=len(missing), gaps=ranges, checked_through=stamp(min(now, end)),
                    scheduled_closed_minutes=int((end + timedelta(microseconds=1)-start)/MINUTE)-len(expected),
                    unexpected_minutes=len(set(rows) - {stamp(t) for t in expected}),
                    status='gaps_unresolved' if missing else 'matches_calendar_to_cutoff',
                    calendar_confidence='repository_calendar; future holiday exceptions require review',
                    attempts=old.get('attempts', 0), retry_at=old.get('retry_at'))
    db.execute('INSERT INTO coverage VALUES (?,?,?) ON CONFLICT(stream,day) DO UPDATE SET payload=excluded.payload', (stream, day.isoformat(), canonical(coverage)))
    for frame in (5, 15, 60, 240, 1440):
        buckets = {}
        for t in expected:
            bucket = int((t-start)/MINUTE)//frame
            buckets.setdefault(bucket, []).append(t)
        for times in buckets.values():
            if times[-1] + MINUTE > now or any(stamp(t) not in rows for t in times):
                continue
            inputs = [rows[stamp(t)] for t in times]
            values = [r for _, r in inputs]
            derived = dict(provider='projectx', contract=contract, timeframe_minutes=frame,
                           origin='derived_1m', live_requested=False, feed_status='unverified', partial=False,
                           timestamp=stamp(times[0]), end=stamp(times[-1] + MINUTE), session=day.isoformat(),
                           calendar=CALENDAR, expected_minutes=len(times), input_ids=[i for i, _ in inputs],
                           open=values[0]['open'], high=max(r['high'] for r in values),
                           low=min(r['low'] for r in values), close=values[-1]['close'], volume=sum(r['volume'] for r in values))
            put_bar(db, derived, received)
    return coverage


def prepare_decision(result):
    if not enabled() or str(result.config.strategy_type) != 'topbot_adaptive':
        return None
    c, d = result.config, result.decision
    rows = []
    for row in result.candles:
        if str(row.user_id) != str(c.user_id):
            raise ValueError('Archive scope mismatch')
        if row.unit != 'minute' or row.unit_number != 5:
            raise ValueError('Unexpected decision timeframe')
        item = native_bar(dict(timestamp=row.candle_timestamp, open=row.open_price, high=row.high_price,
                               low=row.low_price, close=row.close_price, volume=row.volume, is_partial=row.is_partial),
                          str(row.contract_id), 5, bool(row.live))
        item.update(fetched_at=stamp(row.fetched_at) if row.fetched_at else None,
                    first_fetched_at=stamp(row.first_fetched_at) if row.first_fetched_at else None,
                    provider_revision_hash=row.revision_hash)
        rows.append(item)
    forecast = (result.analysis.get('bot_decision') or {}).get('probabilistic_research') or {}
    from .forward_results import location as forward_location
    close = stamp(as_utc(result.candles[-1].candle_timestamp) + timedelta(minutes=5)) if rows else None
    attempt = result.order_attempt
    return dict(owner=str(c.user_id), decision_id=int(d.id), config=int(c.id), account=int(c.account_id),
                run=int(result.run.id) if result.run else None, contract=str(d.contract_id),
                at=stamp(d.created_at or datetime.now(timezone.utc)), correlation_id=result.correlation_id,
                action=str(d.action), reason=str(d.reason), status=result.status,
                execution_mode='dry_run' if result.run and result.run.dry_run else str(c.execution_mode),
                risk_codes=[str(r.code) for r in result.risk_events], forecast=forecast,
                simulation_eligibility=(result.analysis.get('bot_decision') or {}).get('simulation_eligibility'),
                broker_readiness=(result.analysis.get('bot_decision') or {}).get('broker_readiness'),
                config_hash=result.analysis.get('evaluation_config_hash'), strategy_params=c.strategy_params,
                config_snapshot={k: getattr(c, k, None) for k in ('strategy_type', 'strategy_params', 'symbol', 'contract_id',
                    'timeframe_unit', 'timeframe_unit_number', 'lookback_bars', 'order_size', 'max_contracts',
                    'max_open_position', 'max_daily_loss', 'max_trades_per_day', 'cooldown_seconds',
                    'max_data_staleness_seconds', 'trading_start_time', 'trading_end_time', 'execution_mode')},
                timing=result.analysis.get('evaluation_timing'), candles=rows,
                order_attempt=None if attempt is None else {k: getattr(attempt, k) for k in (
                    'id', 'provider_order_id', 'status', 'execution_mode', 'side', 'size', 'limit_price', 'stop_price', 'created_at', 'updated_at')},
                outcomes_reference={'ledger': str(forward_location(c.user_id, c.account_id, c.id).relative_to(cache_root())),
                                    'id': str(c.contract_id)+':'+close if close else None,
                                    'meaning': 'hypothetical forecasts and simulated paper accounts; never actual broker fills'})


def record_decision(prepared):
    if prepared is None:
        return
    owner = prepared['owner']
    try:
        archive = Archive(owner)
        archive.decision(prepared)
        with archive.transaction() as db:
            db.execute('INSERT OR IGNORE INTO decision_commits VALUES (?,?)', (str(prepared['decision_id']), stamp(datetime.now(timezone.utc))))
    except Exception as exc:
        failure(owner, 'decision_write', exc)


def recover_decision_commits(db, owner):
    """Resolve the crash window using exact committed ID AND correlation identity.

    Called from a fresh worker read transaction, before evaluating new decisions.
    Absent rows remain unconfirmed (including rolled-back evaluations); never
    delete their evidence or infer success. No input arrays enter this session.
    """
    if not enabled():
        return
    archive = Archive(owner)
    if not archive.path.exists():
        return
    with archive.reader() as local:
        pending = [(ident, unpack(payload)) for ident, payload in local.execute(
            'SELECT d.id,d.payload FROM decisions d LEFT JOIN decision_commits c ON c.id=d.id WHERE c.id IS NULL ORDER BY d.at DESC LIMIT 100')]
    if not pending:
        return
    from ..models import BotDecision
    committed = {str(row.id): row.correlation_id for row in db.query(BotDecision.id, BotDecision.correlation_id).filter(
        BotDecision.user_id == str(owner), BotDecision.id.in_([int(i) for i, _ in pending])).all()}
    with archive.transaction() as local:
        for ident, record in pending:
            if ident in committed and committed[ident] == record['correlation_id']:
                local.execute('INSERT OR IGNORE INTO decision_commits VALUES (?,?)', (ident, stamp(datetime.now(timezone.utc))))


def status(owner, *, root=None, days=7):
    archive = Archive(owner, root)
    with _lock:
        health = dict(_health.get(str(owner), {}))
    result = dict(enabled=enabled(), schema=VERSION, path=str(archive.path), errors=health,
                  calendar=CALENDAR, coverage=[], last_write=None, bytes=0,
                  retention='Final candles, revisions, decisions and broker events: indefinite. Sampled quotes: indefinite pending approved verified off-device archival; no automatic purge.',
                  limitations=['Provider live=False selects a subscription environment, not verified latency.',
                               'Missing expected minutes are unresolved observations, not proof of a provider outage.',
                               'No full-depth or individual market tick capture; sampled BBO only.',
                               'Pre-activation decisions cannot be reconstructed retroactively.'])
    ancestor = archive.path.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    usage = shutil.disk_usage(ancestor)
    result.update(disk_free_bytes=usage.free, disk_total_bytes=usage.total)
    from .market_archive_scope import health as collection_health
    from .market_archive_events import health as event_health
    result['collector'] = collection_health(owner)
    result['critical_events'] = event_health(owner, root)
    receipt = (Path(root) if root else cache_root()) / 'market-archive' / 'backup-status.json'
    result['backup'] = {'status': 'never_verified', 'stale': True}
    if receipt.is_file():
        try:
            backup = json.loads(receipt.read_text(encoding='utf-8-sig'))
            result['backup'] = {key: backup.get(key) for key in (
                'status', 'last_attempt_at', 'last_verified_at', 'last_restore_at',
                'verified_files', 'verified_bytes', 'destination', 'schedule', 'last_failure', 'publish_failure')}
            age = (datetime.now(timezone.utc)-datetime.fromisoformat(backup['last_verified_at'])).total_seconds() if backup.get('last_verified_at') else None
            result['backup'].update(age_seconds=age, stale=age is None or age > 36*3600,
                                    restore_verified=bool(backup.get('restore', {}).get('verified')))
        except Exception as exc:
            result['backup'] = {'status': 'unreadable', 'error': type(exc).__name__, 'stale': True}
    if not archive.path.exists():
        return result
    result['bytes'] = sum(p.stat().st_size for p in archive.path.parent.iterdir() if p.is_file())
    with archive.reader() as db:
        result['last_write'] = get_state(db, 'last_write')
        result['collection'] = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM state WHERE key LIKE ?', ('collection:%',))}
        result['coverage'] = [json.loads(r[0]) for r in db.execute('SELECT payload FROM coverage ORDER BY day DESC LIMIT ?', (min(30, max(1, days)),))]
        result['counts'] = {table: db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('bars', 'decisions', 'decision_commits', 'events', 'revisions')}
        result['unconfirmed_decisions'] = result['counts']['decisions'] - result['counts']['decision_commits']
        result['broker_recovery'] = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM state WHERE key LIKE ? OR key LIKE ?', ('broker:%', 'broker_error:%'))}
        result['event_counts'] = dict(db.execute('SELECT kind,count(*) FROM events GROUP BY kind'))
        result['latest_event'] = dict(db.execute('SELECT kind,max(at) FROM events GROUP BY kind'))
    return result
