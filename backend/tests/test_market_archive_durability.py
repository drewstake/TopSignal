import json
from pathlib import Path
from queue import Queue
import subprocess
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from app.services import market_archive as a
from app.services import market_archive_events as events
from app.services import market_archive_spool as spool_module
from app.services.market_archive_spool import Spool
from test_market_archive import OWNER, CONTRACT, prepared, Provider
from test_bot_worker import session_factory, _runtime, _seed_run


def event(ident=1):
    return events.event_record(1, 'GatewayUserOrder', dict(id=ident, accountId=1, contractId=CONTRACT,
                              status=2, updateTimestamp='2026-10-28T14:00:00Z'), datetime.now(timezone.utc))


def test_abrupt_process_exit_recovers_durable_spool(tmp_path):
    code = '''import os,sys,json
from app.services.market_archive_spool import Spool
Spool(sys.argv[1],sys.argv[2]).enqueue(json.loads(sys.argv[3]))
os._exit(17)
'''
    result = subprocess.run([sys.executable, '-c', code, OWNER, str(tmp_path), json.dumps(event())])
    assert result.returncode == 17
    spool = Spool(OWNER, tmp_path)
    assert spool.status()['backlog'] == 1
    assert spool.flush() == 1
    with a.Archive(OWNER, tmp_path).reader() as db:
        stored = a.unpack(db.execute('SELECT payload FROM events').fetchone()[0])
    assert stored['payload']['updateTimestamp'] == '2026-10-28T14:00:00Z'
    assert stored['capture']['durable_at'] and stored['capture']['spool_sequence'] == 1


def test_commit_before_ack_crash_and_retry_dedup(tmp_path):
    archive = a.Archive(OWNER, tmp_path)
    spool = Spool(OWNER, tmp_path)
    spool.enqueue(event())
    spool.enqueue(event())
    def committed_then_failed(rows):
        archive.events(rows)
        raise OSError('crash before acknowledgement')
    with pytest.raises(OSError):
        spool.flush(now=100, archive=SimpleNamespace(events=committed_then_failed))
    assert spool.status()['backlog'] == 1
    assert spool.flush(now=101) == 0
    assert Spool(OWNER, tmp_path).flush(now=1000) == 1
    assert spool.status()['backlog'] == 0
    with archive.reader() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
    corrected = event(); corrected['payload']['status'] = 3
    spool.enqueue(corrected); spool.flush()
    with archive.reader() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 2


def test_spool_capacity_reuses_acknowledged_transport_space(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_module, 'MAX_PENDING', 1)
    spool = Spool(OWNER, tmp_path)
    spool.enqueue(event())
    with pytest.raises(OSError):
        spool.enqueue(event(2))
    assert spool.status()['backlog'] == 1
    spool.flush()
    spool.enqueue(event(2))
    assert spool.status()['backlog'] == 1


def test_disk_fault_memory_saturation_visible_and_recoverable(tmp_path, monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', 'true')
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(events, '_critical', Queue(maxsize=2))
    monkeypatch.setattr(events, '_queue', Queue(maxsize=1))
    monkeypatch.setattr(events, '_owners', {OWNER})
    monkeypatch.setattr(events, '_stats', {})
    monkeypatch.setattr(events, 'resume', lambda owner: None)
    original = Spool.enqueue
    monkeypatch.setattr(Spool, 'enqueue', lambda *args: (_ for _ in ()).throw(OSError('disk unavailable')))
    for ident in range(3):
        events.capture(OWNER, 1, 'GatewayUserOrder', dict(id=ident, accountId=1, contractId=CONTRACT), (CONTRACT,))
    health = events.health(OWNER)
    assert health['process']['memory_backlog'] == 2
    assert health['process']['critical_unrecoverable'] == 1
    assert health['process']['critical_received'] == 3
    monkeypatch.setattr(Spool, 'enqueue', original)
    events._iteration()
    assert events.health(OWNER)['process']['memory_backlog'] == 0
    with a.Archive(OWNER).reader() as db:
        rows = [a.unpack(row[0]) for row in db.execute('SELECT payload FROM events ORDER BY at')]
    assert len(rows) == 2
    assert rows[0]['capture']['receipt_sequence'] < rows[1]['capture']['receipt_sequence']
    assert events.health(OWNER)['process']['critical_memory_recovered'] == 2


def test_optional_quote_queue_cannot_evict_critical_event(tmp_path, monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', 'true')
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(events, 'resume', lambda owner: None)
    monkeypatch.setattr(events, '_queue', Queue(maxsize=1))
    monkeypatch.setattr(events, '_stats', {})
    for _ in range(2):
        monkeypatch.setattr(events, '_last_sample', {})
        events.capture(OWNER, 1, 'GatewayQuote', dict(contractId=CONTRACT, bestBid=100, bestAsk=101), (CONTRACT,))
    events.capture(OWNER, 1, 'GatewayUserTrade', dict(id=9, accountId=1, contractId=CONTRACT), (CONTRACT,))
    assert Spool(OWNER).status()['backlog'] == 1
    assert events.health(OWNER)['process']['optional_quotes_dropped'] == 1


def test_collection_with_stopped_disabled_bot_and_restart(session_factory, tmp_path, monkeypatch):
    from app.models import BotConfig, BotRun
    from app.services import market_archive_collector as collector
    from app.services.market_archive_scope import health
    import app.bot_worker as worker
    _seed_run(session_factory)
    with session_factory() as db:
        db.query(BotConfig).first().enabled = False
        db.query(BotRun).first().status = 'stopped'
        db.commit()
    monkeypatch.setenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', 'true')
    monkeypatch.setenv('TOPSIGNAL_MARKET_COLLECTION_ENABLED', 'true')
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('TOPSIGNAL_MARKET_COLLECTION_SCOPES', json.dumps([dict(id='mnq', owner='user-a', account=101, contracts=[CONTRACT])]))
    monkeypatch.setattr(events, 'resume', lambda owner: None)
    provider = Provider()
    calls = []
    def request(owner, account, scope, contract, factory, allowed):
        calls.append((owner, account, scope, contract))
        collector.collect(owner, account, scope, contract, provider, allowed=allowed, root=tmp_path)
    monkeypatch.setattr(collector, 'request_collection', request)
    streams = []
    def create(**kwargs):
        streams.append(kwargs)
        return SimpleNamespace(archive_contracts=kwargs.get('archive_contracts'), start=lambda: None, stop=lambda: True)
    monkeypatch.setattr(worker, 'create_streaming_runtime', create)
    for _ in range(2):  # Recreate process coordination; durable cursor resumes.
        runtime = _runtime(session_factory)
        runtime._replace_snapshot(owns_lease=True)
        result = runtime._run_cycle(False)
        assert result['active_runs'] == 0 and result['evaluated_runs'] == 0
        runtime._sync_account_streams([])
        assert len(runtime._account_streams) == 1
    assert len(calls) == 2 and len(streams) == 2  # One per process; no duplicate per cycle.
    assert health('user-a')['running']
    assert a.status('user-a')['counts']['bars'] > 0
    monkeypatch.setenv('TOPSIGNAL_MARKET_COLLECTION_ENABLED', 'false')
    runtime._run_cycle(False)
    assert not runtime._account_streams and len(calls) == 2


def test_invalid_owner_and_lease_cannot_collect(session_factory, monkeypatch):
    from app.services import market_archive_collector as collector
    monkeypatch.setenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', 'true')
    monkeypatch.setenv('TOPSIGNAL_MARKET_COLLECTION_ENABLED', 'true')
    monkeypatch.setenv('TOPSIGNAL_MARKET_COLLECTION_SCOPES', json.dumps([dict(id='mnq', owner='not-owned', account=101, contracts=[CONTRACT])]))
    monkeypatch.setattr(collector, 'request_collection', lambda *args: pytest.fail('Unauthorized collection'))
    runtime = _runtime(session_factory)
    runtime._sync_archive_collection()
    runtime._replace_snapshot(owns_lease=True)
    runtime._sync_archive_collection()
    assert runtime._archive_scopes == []


@pytest.fixture
def backup_tool(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'tools'))
    import run_market_archive_backup
    return run_market_archive_backup


def test_unavailable_backup_destination_bounded_retries_and_visible_failure(tmp_path, monkeypatch, backup_tool):
    published, sleeps = [], []
    monkeypatch.setattr(backup_tool, 'publish', lambda config, status: published.append(dict(status)))
    config = dict(destination=str(tmp_path/'missing'), state_file=str(tmp_path/'control/status.json'))
    with pytest.raises(RuntimeError):
        backup_tool.execute(config, sleep=sleeps.append)
    state = json.loads(Path(config['state_file']).read_text())
    assert state['status'] == 'failed' and state['attempt'] == 3
    assert len(published) == 3 and sleeps == [30, 60]
    assert len(list((tmp_path/'control/backup-receipts').glob('*.json'))) == 3


def test_restore_checks_original_inputs_and_model(tmp_path, backup_tool):
    from check_market_archive_restore import restore_check
    from backup_market_cache import backup
    source = tmp_path/'cache'
    archive = a.Archive(OWNER, source)
    model = {'weights': [1, 2, 3], 'version': 'frozen-fixture'}
    identity = a.digest(model)
    with archive.transaction() as db:
        db.execute('INSERT INTO artifacts VALUES (?,?)', (identity, a.pack(model)))
    decision = prepared(datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0))
    decision['forecast']['artifact_sha256'] = identity
    archive.decision(decision)
    with archive.transaction() as db:
        db.execute('INSERT INTO decision_commits VALUES (?,?)', ('1', a.stamp(datetime.now(timezone.utc))))
    snapshot = tmp_path/'backup'
    backup(source, snapshot)
    result = restore_check(snapshot, tmp_path/'restore')
    assert result['verified'] and result['samples'][0]['inputs'] == 25
    assert result['samples'][0]['model_sha256'] == identity
    # Corrupt only an isolated copy; destination verification must reject it.
    path = next(snapshot.glob('market-archive/v1/*/archive.sqlite3'))
    with path.open('ab') as stream:
        stream.write(b'corruption')
    with pytest.raises(ValueError):
        restore_check(snapshot, tmp_path/'never-restored')


@pytest.mark.parametrize('latest', ['hold_without_model', 'missing_identity', 'missing_artifact'])
def test_restore_after_model_free_hold_still_checks_model_integrity(tmp_path, backup_tool, latest):
    from check_market_archive_restore import restore_check
    from backup_market_cache import backup
    source = tmp_path/'cache'
    archive = a.Archive(OWNER, source)
    model = {'weights': [1, 2, 3], 'version': 'frozen-fixture'}
    identity = a.digest(model)
    at = datetime(2026, 10, 29, 14, tzinfo=timezone.utc)
    with archive.transaction() as db:
        db.execute('INSERT INTO artifacts VALUES (?,?)', (identity, a.pack(model)))
    previous = prepared(at)
    previous['forecast']['artifact_sha256'] = identity
    archive.decision(previous)
    current = prepared(at+timedelta(minutes=5), ident=2)
    if latest == 'missing_identity':
        current['forecast']['forecasts'] = {'BUY': {'net': 1}}
    elif latest == 'missing_artifact':
        current['forecast']['artifact_sha256'] = 'missing'
    archive.decision(current)
    with archive.transaction() as db:
        db.executemany('INSERT INTO decision_commits VALUES (?,?)', [('1', a.stamp(at)), ('2', a.stamp(at))])
    snapshot = tmp_path/'backup'
    backup(source, snapshot)
    if latest != 'hold_without_model':
        with pytest.raises(ValueError, match='Original model missing or corrupt'):
            restore_check(snapshot, tmp_path/'restore')
    else:
        result = restore_check(snapshot, tmp_path/'restore')
        assert result['verified']
        assert [s['decision'] for s in result['samples']] == ['2', '1']
        assert [s['model_sha256'] for s in result['samples']] == [None, identity]
        assert all(s['inputs'] == 25 for s in result['samples'])


def test_backup_publish_outage_keeps_verified_copy_and_fails_visibly(tmp_path, monkeypatch, backup_tool):
    sleeps = []
    monkeypatch.setattr(backup_tool, 'attempt', lambda *args, **kwargs: dict(status='verified', last_verified_at='2026-10-28T15:00:00+00:00'))
    monkeypatch.setattr(backup_tool, 'publish', lambda *args: (_ for _ in ()).throw(OSError('offline')))
    config = dict(destination=str(tmp_path), state_file=str(tmp_path/'control/status.json'))
    with pytest.raises(RuntimeError, match='publishing'):
        backup_tool.execute(config, sleep=sleeps.append)
    state = json.loads(Path(config['state_file']).read_text())
    assert state['status'] == 'verified' and state['last_verified_at']
    assert state['publish_failure']['type'] == 'OSError' and sleeps == [30, 60]


def test_online_backup_retains_event_moving_between_spool_and_archive(tmp_path, monkeypatch, backup_tool):
    import sqlite3
    import backup_market_cache as tool
    source = tmp_path/'source'
    archive = a.Archive(OWNER, source)
    with archive.transaction():
        pass
    spool = Spool(OWNER, source)
    spool.enqueue(event())
    original_connect = sqlite3.connect
    copied = []
    class SourceConnection:
        def __init__(self, db, name):
            self.db, self.name = db, name
        def __getattr__(self, name):
            return getattr(self.db, name)
        def backup(self, target, **kwargs):
            self.db.backup(target, **kwargs)
            copied.append(self.name)
            if len(copied) == 1:
                spool.flush()  # Live commit/ack between the two SQLite snapshots.
    def connect(path, *args, **kwargs):
        db = original_connect(path, *args, **kwargs)
        if str(path).startswith(source.as_uri()) and str(path).endswith('?mode=ro'):
            return SourceConnection(db, str(path))
        return db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    output = tmp_path/'backup'
    tool.backup(source, output)
    assert 'critical-spool.sqlite3' in copied[0]
    with a.Archive(OWNER, output).reader() as db:
        archived = db.execute('SELECT count(*) FROM events').fetchone()[0]
    assert archived == 1
    assert Spool(OWNER, output).status()['backlog'] == 1
    # Replaying the backed-up transport copy still deduplicates.
    Spool(OWNER, output).flush()
    with a.Archive(OWNER, output).reader() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1


def test_spool_reader_waits_for_first_schema_commit(tmp_path):
    import sqlite3
    spool = Spool(OWNER, tmp_path)
    spool.path.parent.mkdir(parents=True)
    # The first enqueue creates the file before committing its schema version.
    sqlite3.connect(spool.path).close()
    assert spool.flush() == 0
    spool.enqueue(event())
    assert spool.flush() == 1
