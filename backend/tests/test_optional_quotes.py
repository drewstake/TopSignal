import errno
import json
from queue import Queue
from threading import Event, Thread
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.services import market_archive as archive, market_archive_events as events, market_archive_quotes as quotes
from app.services.market_archive_spool import Spool
from test_market_archive_durability import event as critical_event

OWNER = 'quote-fixture'
CONTRACT = 'CON.F.US.MNQ.Z26'


def quote(ident=1):
    from datetime import datetime, timezone
    return events.event_record(1, 'GatewayQuote', dict(contractId=CONTRACT, bestBid=100, bestAsk=101,
                               timestamp=f'2026-10-29T14:00:{ident:02d}Z'), datetime.now(timezone.utc))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', 'true')
    monkeypatch.setattr(events, '_queue', Queue(maxsize=2))
    monkeypatch.setattr(events, '_critical', Queue(maxsize=2))
    monkeypatch.setattr(events, '_owners', {OWNER})
    monkeypatch.setattr(events, '_stats', {})
    monkeypatch.setattr(events, '_last_sample', {})
    monkeypatch.setattr(events, '_closing', False)
    monkeypatch.setattr(events, '_stop', Event())
    monkeypatch.setattr(events, '_thread', None)
    monkeypatch.setattr(events, '_quote_thread', None)
    monkeypatch.setattr(events, 'resume', lambda owner: None)


def count():
    with archive.Archive(OWNER).reader() as db:
        return db.execute('SELECT count(*) FROM events').fetchone()[0]


def test_real_sqlite_writer_contention_retries_without_losing_or_duplicating():
    spool = quotes.QuoteSpool(OWNER)
    spool.enqueue(quote(), now=0)
    target = archive.Archive(OWNER)
    with target.transaction():
        with pytest.raises(sqlite3.OperationalError) as caught:
            spool.flush(now=0)
    assert quotes.diagnostic(caught.value)['category'] == 'contention'
    assert spool.status()['backlog'] == 1 and spool.status().get('dropped',0) == 0
    assert spool.flush(now=1) == 0
    assert quotes.QuoteSpool(OWNER).flush(now=2) == 1
    assert count() == 1
    status = spool.status()
    assert status['persisted'] == status['retried'] == status['recovered'] == 1
    assert spool.flush(now=3) == 0


def test_process_exit_after_archive_commit_before_ack_replays_idempotently(tmp_path):
    code = '''import os,sys,json
from app.services.market_archive_quotes import QuoteSpool
from app.services.market_archive import Archive
from types import SimpleNamespace
spool=QuoteSpool(sys.argv[1]);spool.enqueue(json.loads(sys.argv[2]),now=0)
def crash(rows):
 Archive(sys.argv[1]).events(rows)
 os._exit(19)
spool.flush(now=0,archive=SimpleNamespace(events=crash))
'''
    result = subprocess.run([sys.executable,'-B','-c',code,OWNER,json.dumps(quote())], timeout=15)
    assert result.returncode == 19 and count() == 1
    spool = quotes.QuoteSpool(OWNER)
    assert spool.status()['backlog'] == 1
    assert spool.flush(now=2) == 1 and count() == 1
    assert spool.status()['persisted'] == 1 and spool.status()['inserted'] == 0


@pytest.mark.parametrize('last_attempt', [False, True])
def test_expired_or_exhausted_committed_quote_is_recovered_not_lost(last_attempt):
    spool = quotes.QuoteSpool(OWNER); spool.enqueue(quote(), now=0)
    if last_attempt:
        with spool.transaction() as db: db.execute('UPDATE pending SET attempts=4')
    def crash(rows):
        archive.Archive(OWNER).events(rows)
        raise SystemExit('commit succeeded before acknowledgment')
    with pytest.raises(SystemExit): spool.flush(now=0, archive=SimpleNamespace(events=crash))
    assert count() == 1
    restarted = quotes.QuoteSpool(OWNER)
    assert restarted.flush(now=121 if not last_attempt else 31) == 1
    status = restarted.status()
    assert status['persisted'] == status['recovered'] == 1 and status.get('dropped', 0) == 0
    assert status['backlog'] == 0 and restarted.flush(now=122) == 0 and count() == 1


def test_archive_unavailable_is_terminal_uncertainty_not_asserted_loss():
    spool = quotes.QuoteSpool(OWNER); spool.enqueue(quote(), now=0)
    path = archive.Archive(OWNER).path
    path.write_bytes(b'isolated unreadable SQLite fixture')
    before = path.read_bytes()
    assert spool.flush(now=121) == 0
    status = spool.status()
    assert status['uncertain'] == 1 and status['uncertain_retry_exhausted'] == 1
    assert status.get('dropped', 0) == status.get('persisted', 0) == status['backlog'] == 0
    assert status['last_error']['operation'] == 'archive_presence'
    assert path.read_bytes() == before


def test_ambiguous_failed_archive_call_checks_committed_identity():
    spool = quotes.QuoteSpool(OWNER); spool.enqueue(quote(), now=0)
    def ambiguous(rows):
        archive.Archive(OWNER).events(rows)
        raise sqlite3.OperationalError('commit result unavailable')
    with pytest.raises(sqlite3.OperationalError): spool.flush(now=0, archive=SimpleNamespace(events=ambiguous))
    assert count() == 1 and spool.status()['persisted'] == 1
    assert spool.status().get('dropped', 0) == 0 and spool.status()['backlog'] == 0


def test_growth_preserves_cleanup_headroom_and_checkpoint_recovers(monkeypatch):
    monkeypatch.setattr(quotes, 'MAX_BYTES', 128*1024)
    spool = quotes.QuoteSpool(OWNER); spool.enqueue(quote(), now=0)
    reader = sqlite3.connect(spool.path)
    reader.execute('BEGIN'); reader.execute('SELECT count(*) FROM pending').fetchone()
    try:
        for n in range(1, 500):
            try:
                spool.enqueue(quote(n), now=0)
                spool.flush(now=0)
            except OSError as exc:
                assert 'quote_physical_capacity' in str(exc)
                break
        else: pytest.fail('growth must be bounded while a reader pins the WAL')
        assert spool.physical_bytes() < quotes.MAX_BYTES+quotes.WAL_ALLOWANCE_BYTES
        # Draining uses its reserved budget even while new growth is refused.
        while spool.status()['backlog']: spool.flush(now=0)
        assert spool.status()['backlog'] == 0
        reader.commit()  # Connection deliberately remains open.
        spool.enqueue(quote(501), now=1)
        assert spool.flush(now=1) == 1
        assert spool.physical_bytes() < quotes.MAX_BYTES+quotes.WAL_ALLOWANCE_BYTES
    finally:
        reader.close()


def test_legacy_full_wal_recovers_after_snapshot_release_without_manual_checkpoint(monkeypatch):
    monkeypatch.setattr(quotes, 'MAX_BYTES', 128*1024)
    spool = quotes.QuoteSpool(OWNER); spool.enqueue(quote(), now=0)
    reader = sqlite3.connect(spool.path)
    reader.execute('BEGIN'); reader.execute('SELECT count(*) FROM pending').fetchone()
    try:
        # Recreate a pre-fix spool already at its physical ceiling.
        with sqlite3.connect(spool.path) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            for n in range(1000):
                writer.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('fixture_counter', str(n)))
                writer.commit()
                if spool.physical_bytes() >= quotes.MAX_BYTES+quotes.WAL_ALLOWANCE_BYTES: break
        assert spool.physical_bytes() >= quotes.MAX_BYTES+quotes.WAL_ALLOWANCE_BYTES
        with pytest.raises(OSError, match='quote_physical_capacity'): spool.flush(now=0)
        reader.commit()
        assert spool.flush(now=0) == 1
        assert spool.status()['backlog'] == 0 and count() == 1
        assert spool.physical_bytes() < quotes.MAX_BYTES+quotes.WAL_ALLOWANCE_BYTES
    finally:
        reader.close()


def raising(exc):
    def fail(rows): raise exc
    return SimpleNamespace(events=fail)


def test_five_attempt_limit_and_age_limit_are_durable():
    spool = quotes.QuoteSpool(OWNER); spool.enqueue(quote(),now=0)
    busy = raising(OSError(errno.EBUSY,'private payload must not appear'))
    for at in (0,2,6,14,30):
        with pytest.raises(OSError): spool.flush(now=at,archive=busy)
    status = spool.status()
    assert status['backlog'] == 0 and status['dropped_retry_exhausted'] == 1 and status['retried'] == 4
    assert 'private' not in json.dumps(status)
    spool.enqueue(quote(2),now=40)
    assert spool.flush(now=161,archive=busy) == 0
    assert spool.status()['dropped_retry_exhausted'] == 2


@pytest.mark.parametrize('kind', ['readonly','unknown','full'])
def test_permanent_failure_is_not_misreported_as_lock_contention(kind):
    spool = quotes.QuoteSpool(OWNER); spool.enqueue(quote(),now=0)
    if kind == 'readonly':
        with sqlite3.connect(spool.path.as_uri()+'?mode=ro',uri=True) as db:
            with pytest.raises(sqlite3.OperationalError) as caught: db.execute('DELETE FROM pending')
        exc = caught.value
    elif kind == 'full': exc = OSError(errno.ENOSPC,'full')
    else: exc = sqlite3.OperationalError('secret account SQL values')
    with pytest.raises(type(exc)): spool.flush(now=0,archive=raising(exc))
    status = spool.status()
    assert status['dropped_permanent_failure'] == 1 and status['backlog'] == 0
    assert not status['last_error']['recoverable'] and 'secret' not in json.dumps(status)
    # Recovery admits a later event without resetting old loss evidence.
    spool.enqueue(quote(2),now=1); spool.flush(now=1)
    assert spool.status()['persisted'] == 1 and spool.status()['dropped'] == 1


def test_spool_and_archive_budgets_do_not_delete_archived_evidence(monkeypatch):
    monkeypatch.setattr(quotes,'MAX_PENDING',1)
    spool=quotes.QuoteSpool(OWNER); spool.enqueue(quote(),now=0)
    with pytest.raises(OSError): spool.enqueue(quote(2),now=0)
    spool.flush(now=0); assert count() == 1
    monkeypatch.setattr(quotes,'MAX_ARCHIVE_BYTES',1)
    spool.enqueue(quote(2),now=1)
    with pytest.raises(OSError): spool.flush(now=1)
    assert count() == 1 and spool.status()['dropped'] == 1


def test_capture_counters_sampling_size_saturation_and_shutdown(monkeypatch):
    def receive(**extra):
        events.capture(OWNER,1,'GatewayQuote',dict(contractId=CONTRACT,bestBid=100,bestAsk=101,**extra),(CONTRACT,))
    receive(); receive()  # Second is intentionally sampled out.
    for n in range(2):
        monkeypatch.setattr(events,'_last_sample',{}); receive(timestamp=str(n))
    stats = events.health(OWNER)['process']
    assert stats['optional_quotes_received'] == 4 and stats['optional_quotes_queued'] == 2
    assert stats['optional_quotes_sampled_out'] == 1 and stats['optional_quotes_dropped'] == 1
    monkeypatch.setattr(events,'_last_sample',{}); receive(symbol='x'*5000)
    assert events.health(OWNER)['process']['optional_quotes_dropped'] == 2
    assert events.drain(timeout=0)
    stats=events.health(OWNER)['process']
    assert stats['quote_memory_backlog'] == 0 and stats['optional_quotes_shutdown_dropped'] == 2
    assert stats['optional_quotes_received'] == stats['optional_quotes_sampled_out']+stats['optional_quotes_dropped']


def test_memory_enqueue_failure_retries_and_counts_terminal_loss(monkeypatch):
    tick=[0.]
    monkeypatch.setattr(events,'monotonic',lambda:tick[0])
    original=quotes.QuoteSpool.enqueue
    monkeypatch.setattr(quotes.QuoteSpool,'enqueue',lambda *a,**kw: (_ for _ in ()).throw(OSError(errno.EBUSY,'busy')))
    events.capture(OWNER,1,'GatewayQuote',dict(contractId=CONTRACT,bestBid=100,bestAsk=101),(CONTRACT,))
    events._quote_iteration()
    assert events.health(OWNER)['process']['quote_memory_backlog'] == 1
    tick[0]=2
    monkeypatch.setattr(quotes.QuoteSpool,'enqueue',original)
    events._quote_iteration()
    assert events.health(OWNER)['quotes']['persisted'] == 1
    assert events.health(OWNER)['process']['optional_quotes_memory_retried'] == 1
    assert count() == 1


def test_blocked_quote_consumer_does_not_block_critical_consumer(monkeypatch):
    started, release = Event(), Event()
    original=quotes.QuoteSpool.flush
    def slow(self,**kw):
        started.set(); assert release.wait(2)
        return original(self,**kw)
    monkeypatch.setattr(quotes.QuoteSpool,'flush',slow)
    worker=Thread(target=events._quote_iteration)
    worker.start(); assert started.wait(1)
    try:
        Spool(OWNER).enqueue(critical_event())
        events._iteration()
        assert Spool(OWNER).status()['backlog'] == 0 and count() == 1
    finally:
        release.set(); worker.join(2)
    assert not worker.is_alive()


def test_shutdown_preserves_final_critical_attempt_and_bounds_wait(monkeypatch):
    seen=[]
    monkeypatch.setattr(events,'_iteration',lambda:seen.append('critical'))
    events._stop.set();events._run()
    assert seen==['critical']
    started,release=Event(),Event()
    thread=Thread(target=lambda:(started.set(),release.wait(2)))
    thread.start();assert started.wait(1)
    monkeypatch.setattr(events,'_quote_thread',thread)
    try:
        assert events.drain(timeout=0) is False
    finally:
        release.set();thread.join(2)
