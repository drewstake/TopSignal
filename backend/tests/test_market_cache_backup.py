import importlib.util
import hashlib
import json
from pathlib import Path
import sqlite3
import io
import tarfile
from contextlib import closing

import pytest


def test_transport_hash_covers_headers_padding_and_payload_without_readback(tmp_path, monkeypatch):
    from tools import backup_market_cache as tool
    source=tmp_path/'source';source.mkdir()
    (source/'evidence.bin').write_bytes(b'important evidence' * 257)
    snapshot=tmp_path/'snapshot'
    tool.backup(source,snapshot)
    def no_second_pass(_):
        raise AssertionError('Packing must not reread its completed archive')
    monkeypatch.setattr(tool,'checksum',no_second_pass)
    output=tmp_path/'transport.tar'
    receipt=tool.pack(snapshot,output)
    actual=output.read_bytes()
    assert receipt['sha256']==hashlib.sha256(actual).hexdigest()
    assert receipt['bytes']==len(actual)
    with tarfile.open(output) as archive:
        assert archive.extractfile('evidence.bin').read()==(source/'evidence.bin').read_bytes()
    assert len(actual)%tarfile.RECORDSIZE==0
    with pytest.raises(FileExistsError):
        tool.pack(snapshot,output)


def test_transport_pacing_remains_useful_with_coarse_windows_timers(tmp_path, monkeypatch):
    import math
    from tools import backup_market_cache as tool
    from tools.backup_io import BackupIO
    source = tmp_path/'source'; source.mkdir()
    payload = b'evidence' * (4 * 1024 * 1024 // 8)
    (source/'evidence.bin').write_bytes(payload)
    snapshot = tmp_path/'snapshot'
    tool.backup(source, snapshot)
    elapsed = [0.0]
    # Observed on the host: a requested 2 ms sleep takes one 15.625 ms tick.
    # A 16 KiB tar read consequently turns an 8 MiB/s cap into 1 MiB/s.
    def sleep(seconds):
        elapsed[0] += math.ceil(seconds / .015625) * .015625
    monkeypatch.setattr(tool, '_IO_GUARD', BackupIO(8, clock=lambda: elapsed[0], sleep=sleep))
    output = tmp_path/'transport.tar'
    receipt = tool.pack(snapshot, output)
    assert .5 <= elapsed[0] < 1.5  # Preserve the cap without a per-small-read timer penalty.
    assert hashlib.sha256(output.read_bytes()).hexdigest() == receipt['sha256']
    with tarfile.open(output) as archive:
        assert archive.extractfile('evidence.bin').read() == payload


def test_wal_snapshot_finishes_under_continuous_external_writes(tmp_path):
    from tools.backup_market_cache import backup_sqlite
    path = tmp_path / 'busy.sqlite3'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,payload BLOB)')
        writer.executemany('INSERT INTO events VALUES(?,zeroblob(4096))', [(i,) for i in range(1000)])
        writer.commit()
        writes = []
        def concurrent_write(status, remaining, total):
            # A separate connection commits after every backup step. Without
            # the pinned snapshot this workload continually restarts the copy.
            writer.execute('INSERT INTO events(payload) VALUES(zeroblob(4096))')
            writer.commit()
            writes.append(remaining)
            assert len(writes) < 20, 'Backup keeps restarting'
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(tmp_path / 'copy.sqlite3')) as destination:
                backup_sqlite(source, destination, progress=concurrent_write)
                assert len(writes) > 1
                assert destination.execute('SELECT count(*) FROM events').fetchone()[0] == 1000
                assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                assert not source.in_transaction
            assert writer.execute('SELECT count(*) FROM events').fetchone()[0] == 1000 + len(writes)


def test_snapshot_deadline_releases_wal_reader(tmp_path):
    from tools.backup_market_cache import backup_sqlite
    path = tmp_path / 'deadline.sqlite3'
    with closing(sqlite3.connect(path)) as source:
        source.execute('PRAGMA journal_mode=WAL')
        source.execute('CREATE TABLE events(id INTEGER PRIMARY KEY)')
        source.commit()
        with closing(sqlite3.connect(tmp_path / 'incomplete.sqlite3')) as destination:
            with pytest.raises(TimeoutError, match='deadline'):
                backup_sqlite(source, destination, timeout_seconds=0)
        assert not source.in_transaction


def test_sqlite_scratch_preserves_wal_data_and_unrelated_files(tmp_path):
    from tools.backup_market_cache import snapshot_sqlite
    source, target, scratch = tmp_path/'source.sqlite3', tmp_path/'copy.sqlite3', tmp_path/'scratch'
    scratch.mkdir()
    original = scratch/'existing-audit.txt'; original.write_text('keep')
    with closing(sqlite3.connect(source)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY)')
        writer.execute('INSERT INTO observations VALUES(42)'); writer.commit()
        snapshot_sqlite(source, target, scratch=scratch)
        assert writer.execute('SELECT id FROM observations').fetchall() == [(42,)]
        with closing(sqlite3.connect(target)) as restored:
            assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert restored.execute('SELECT id FROM observations').fetchall() == [(42,)]
    assert list(scratch.iterdir()) == [original]
    assert original.read_text() == 'keep'


def test_sqlite_scratch_capacity_failure_preserves_sources(tmp_path, monkeypatch):
    from tools import backup_market_cache as tool
    from types import SimpleNamespace
    source, target = tmp_path/'source.sqlite3', tmp_path/'copy.sqlite3'
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE evidence(id INTEGER)'); db.commit()
    original = source.read_bytes()
    monkeypatch.setattr(tool.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(OSError, match='free-space floor'):
        tool.snapshot_sqlite(source, target, scratch=tmp_path)
    assert source.read_bytes() == original and not target.exists()


def test_integrity_uses_identical_scratch_bytes_and_preserves_original(tmp_path):
    from tools import backup_market_cache as tool
    source = tmp_path/'snapshot.sqlite3'
    scratch = tmp_path/'scratch'; scratch.mkdir()
    sentinel = scratch/'original-audit'; sentinel.write_bytes(b'keep')
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO entries VALUES(123)'); db.commit()
    digest = tool.checksum(source)
    tool.verify_sqlite(source, digest, scratch=scratch)
    assert tool.checksum(source) == digest
    assert list(scratch.iterdir()) == [sentinel]


def test_integrity_refuses_corrupted_scratch_copy(tmp_path, monkeypatch):
    from tools import backup_market_cache as tool
    source = tmp_path/'snapshot.sqlite3'
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY)'); db.commit()
    digest = tool.checksum(source)
    monkeypatch.setattr(tool, 'paced_copy', lambda src, dst: Path(dst).write_bytes(b'corrupt copy'))
    with pytest.raises(ValueError, match='scratch checksum mismatch'):
        tool.verify_sqlite(source, digest, scratch=tmp_path)
    assert tool.checksum(source) == digest


def test_optional_quote_spool_is_copied_before_archive_handoff(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('cache_backup_quote_tool', Path(__file__).parents[1]/'tools/backup_market_cache.py')
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    from app.services.market_archive_quotes import QuoteSpool
    from app.services.market_archive import Archive
    from test_optional_quotes import quote
    source, output = tmp_path/'source', tmp_path/'backup'
    spool = QuoteSpool('fixture',source); spool.enqueue(quote(),now=0)
    with Archive('fixture',source).transaction(): pass
    original=tool.checksum
    def handoff(path):
        if Path(path).name == 'optional-quotes.sqlite3':
            spool.flush(now=0)
        return original(path)
    monkeypatch.setattr(tool,'checksum',handoff)
    tool.backup(source,output)
    restored=QuoteSpool('fixture',output)
    assert restored.status()['backlog'] == 1
    restored.flush(now=1)
    with Archive('fixture',output).reader() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1


def test_restored_cache_can_be_backed_up_again_without_losing_original_manifest(tmp_path):
    spec = importlib.util.spec_from_file_location('cache_backup_tool', Path(__file__).parents[1]/'tools/backup_market_cache.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    source = tmp_path/'original'
    source.mkdir()
    (source/'model.json').write_text('{"frozen":"original"}')
    with sqlite3.connect(source/'ledger.sqlite3') as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO decisions VALUES (123)')
        db.commit()
        first = tmp_path/'first-backup'
        tool.backup(source, first)
    second = tmp_path/'second-backup'
    tool.backup(first, second)
    manifest = tool.verify(second)
    preserved = second/'source-manifests'/(tool.checksum(first/'backup-manifest.json')+'.json')
    assert preserved.read_bytes() == (first/'backup-manifest.json').read_bytes()
    assert (source/'model.json').read_bytes() == (second/'model.json').read_bytes()
    assert len(manifest['files']) == 3
    with sqlite3.connect(second/'ledger.sqlite3') as db:
        assert db.execute('SELECT id FROM decisions').fetchall() == [(123,)]


def test_transport_round_trip_preserves_long_windows_paths(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'tools'))
    from backup_market_cache import backup,pack,checksum,filesystem_path
    from run_market_archive_backup import unpack_backup
    source=filesystem_path(tmp_path/'source')
    relative=Path('audits')/('a'*64)/('b'*64)/('c'*64)/('d'*64+'.json')
    original=source/relative;original.parent.mkdir(parents=True)
    original.write_bytes(b'original evidence')
    snapshot=tmp_path/'snapshot';backup(source,snapshot)
    transport=tmp_path/'transport.tar';receipt=pack(snapshot,transport)
    assert receipt['sha256']==checksum(transport)
    restored=tmp_path/'restored';unpack_backup(transport,restored,max_bytes=100000)
    assert (filesystem_path(restored)/relative).read_bytes()==original.read_bytes()


@pytest.mark.parametrize('name,link,limit',[
    ('../escape',False,100),('C:/escape',False,100),('directory\\escape',False,100),
    ('link',True,100),('large',False,0),
])
def test_transport_rejects_unsafe_members_before_writing(tmp_path,monkeypatch,name,link,limit):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'tools'))
    from run_market_archive_backup import unpack_backup
    archive=tmp_path/'bad.tar'
    with tarfile.open(archive,'w') as f:
        item=tarfile.TarInfo(name)
        if link:item.type=tarfile.SYMTYPE;item.linkname='outside'
        else:item.size=1
        f.addfile(item,None if link else io.BytesIO(b'x'))
    destination=tmp_path/'destination'
    with pytest.raises(ValueError):unpack_backup(archive,destination,max_bytes=limit)
    assert not destination.exists()


# --- incremental snapshots -----------------------------------------------------

CONTRACT = 'CON.F.US.MNQ.Z26'


def incremental_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'tools'))
    import backup_market_cache as tool
    import run_market_archive_backup as runner
    return tool, runner


def seal_segment(source, payload):
    from depth_recording import SegmentRecorder
    recorder = SegmentRecorder(source/'depth-recordings-v1', 'a'*64)
    recorder.append(CONTRACT, 'connect', payload)
    recorder.close()
    return next(recorder.path.glob('*.ndjson.gz'))


def test_snapshot_does_not_reopen_storage_roots_for_each_segment(tmp_path, monkeypatch):
    tool, _ = incremental_tools(monkeypatch)
    original = Path.resolve
    counts = []
    for count in (1, 4):
        source = tmp_path/f'source-{count}'
        for index in range(count):seal_segment(source, {'connection': index})
        root_reads = []
        def observed(path, *args, **kwargs):
            if path.name in (source.name, 'depth-recordings-v1'):
                root_reads.append(path)
            return original(path, *args, **kwargs)
        with monkeypatch.context() as context:
            context.setattr(Path, 'resolve', observed)
            tool.backup(source, tmp_path/f'snapshot-{count}', deep_verify=False)
        counts.append(len(root_reads))
    # Windows resolution opens filesystem metadata; more segments must not
    # multiply I/O for invariant roots. Each member remains resolved/checked.
    assert counts[0] == counts[1]


def cache(tmp_path):
    source = tmp_path/'source'
    segment = seal_segment(source, {'connection': 1})
    (source/'versions').mkdir()
    (source/'versions'/'model.json').write_text('{"frozen":true}')
    capture = source/'topbot-worker-evidence-v1'/'owner'/'closed.sqlite3'
    capture.parent.mkdir(parents=True)
    for path in (capture, source/'live.sqlite3'):
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE rows(id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO rows VALUES(1)'); db.commit()
    return source, segment, capture


def test_incremental_snapshot_ships_only_changes_and_restores_a_fully_verified_copy(tmp_path, monkeypatch):
    tool, runner = incremental_tools(monkeypatch)
    source, old_segment, capture = cache(tmp_path)
    first = tmp_path/'staging'/'first'
    tool.backup(source, first)
    tool.pack(first, tmp_path/'first.tar')
    offsite_first = tmp_path/'offsite'/'first'
    runner.unpack_backup(tmp_path/'first.tar', offsite_first, max_bytes=10**7)

    new_segment = seal_segment(source, {'connection': 2})
    with closing(sqlite3.connect(source/'live.sqlite3')) as db:
        db.execute('INSERT INTO rows VALUES(2)'); db.commit()
    original = tool.checksum
    reread = []

    def recording_checksum(path):
        reread.append(str(path).replace(chr(92), '/'))
        return original(path)

    monkeypatch.setattr(tool, 'checksum', recording_checksum)
    second = tmp_path/'staging'/'second'
    tool.backup(source, second, previous=first)
    monkeypatch.setattr(tool, 'checksum', original)
    # Unchanged evidence is not read again from the recording disk.
    from_source = [name for name in reread if '/source/' in name]
    assert any(new_segment.parent.name in name for name in from_source)  # the check is not vacuous
    assert not any((old_segment.parent.name in name and name.endswith('.ndjson.gz'))
                   or name.endswith(('model.json', 'closed.sqlite3')) for name in from_source)
    manifest = json.loads((second/'backup-manifest.json').read_text())
    relative = lambda path: path.relative_to(source).as_posix()
    files = manifest['files']
    assert files[relative(old_segment)]['reused'] and files['versions/model.json']['reused']
    assert files['topbot-worker-evidence-v1/owner/closed.sqlite3']['reused']
    assert not files['live.sqlite3'].get('reused') and not files[relative(new_segment)].get('reused')
    assert manifest['incremental']['previous'] == 'first' and manifest['incremental']['reused_files'] == 4
    assert not (second/relative(old_segment)).exists() and (second/relative(new_segment)).exists()

    receipt = tool.pack(second, tmp_path/'second.tar')
    assert receipt['reused'] == 4
    with tarfile.open(tmp_path/'second.tar') as archive:
        assert relative(old_segment) not in archive.getnames() and relative(new_segment) in archive.getnames()
    offsite_second = tmp_path/'offsite'/'second'
    verified = runner.unpack_backup(tmp_path/'second.tar', offsite_second, max_bytes=10**7, previous=offsite_first)
    assert set(verified['files']) == set(files)
    assert all((offsite_second/name).is_file() for name in files)
    assert (offsite_second/relative(old_segment)).read_bytes() == old_segment.read_bytes()
    tool.verify(offsite_second)  # strict: every member present and matching
    with closing(sqlite3.connect(offsite_second/'live.sqlite3')) as db:
        assert db.execute('SELECT count(*) FROM rows').fetchone()[0] == 2
    # A third, chained snapshot still restores completely from the latest verified copy.
    third = tmp_path/'staging'/'third'
    tool.backup(source, third, previous=second)
    tool.pack(third, tmp_path/'third.tar')
    runner.unpack_backup(tmp_path/'third.tar', tmp_path/'offsite'/'third', max_bytes=10**7, previous=offsite_second)
    assert (tmp_path/'offsite'/'third'/relative(old_segment)).read_bytes() == old_segment.read_bytes()
    assert old_segment.exists()  # nothing removed from the source


def test_changed_capture_is_snapshotted_again(tmp_path, monkeypatch):
    tool, _ = incremental_tools(monkeypatch)
    source, _, capture = cache(tmp_path)
    first = tmp_path/'first'
    tool.backup(source, first)
    with closing(sqlite3.connect(capture)) as db:
        db.execute('INSERT INTO rows VALUES(2)'); db.commit()
    second = tmp_path/'second'
    tool.backup(source, second, previous=first)
    entry = json.loads((second/'backup-manifest.json').read_text())['files']['topbot-worker-evidence-v1/owner/closed.sqlite3']
    assert not entry.get('reused')
    with closing(sqlite3.connect(second/'topbot-worker-evidence-v1/owner/closed.sqlite3')) as db:
        assert db.execute('SELECT count(*) FROM rows').fetchone()[0] == 2


def test_incremental_restore_refuses_a_missing_or_altered_previous_backup(tmp_path, monkeypatch):
    import shutil
    tool, runner = incremental_tools(monkeypatch)
    source, old_segment, _ = cache(tmp_path)
    first, second = tmp_path/'first', tmp_path/'second'
    tool.backup(source, first)
    tool.backup(source, second, previous=first)
    tool.pack(second, tmp_path/'second.tar')
    with pytest.raises(ValueError, match='previous verified backup'):
        runner.unpack_backup(tmp_path/'second.tar', tmp_path/'no-previous', max_bytes=10**7)
    altered = tmp_path/'altered'
    shutil.copytree(first, altered)
    (altered/old_segment.relative_to(source)).write_bytes(b'not the recorded segment')
    with pytest.raises(ValueError, match='checksum mismatch'):
        runner.unpack_backup(tmp_path/'second.tar', tmp_path/'from-altered', max_bytes=10**7, previous=altered)


def test_snapshot_without_a_previous_manifest_is_a_full_copy(tmp_path, monkeypatch):
    tool, _ = incremental_tools(monkeypatch)
    source, _, _ = cache(tmp_path)
    output = tmp_path/'full'
    tool.backup(source, output, previous=tmp_path/'absent')
    manifest = json.loads((output/'backup-manifest.json').read_text())
    assert manifest['incremental']['reused_files'] == 0 and manifest['incremental']['previous'] is None
    tool.verify(output)


@pytest.mark.parametrize('cache_name', ['depth-heatmap-v1', 'depth-history-cache-v1'])
def test_derived_heatmap_cache_is_rebuilt_not_archived(tmp_path, monkeypatch, cache_name):
    tool, _ = incremental_tools(monkeypatch)
    source, segment, _ = cache(tmp_path)
    derived = source/cache_name/('a'*64)/'sessions'/'2026-11-03.sqlite3'
    derived.parent.mkdir(parents=True)
    with closing(sqlite3.connect(derived)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE chunks(id INTEGER PRIMARY KEY)'); db.commit()
        # Open writer with a live WAL, as the deriver keeps it during a backup.
        output = tmp_path/'full'
        tool.backup(source, output, previous=tmp_path/'absent')
    files = json.loads((output/'backup-manifest.json').read_text())['files']
    assert not any(name.startswith(cache_name+'/') for name in files)
    # Raw segments (the source of the derived cache) and other stores are still archived.
    assert any(name.endswith(segment.name) for name in files)
    assert 'live.sqlite3' in files
    tool.verify(output)


def test_snapshot_deadline_grows_with_database_size(monkeypatch):
    tool, _ = incremental_tools(monkeypatch)
    assert tool.snapshot_deadline(0) == 900
    assert tool.snapshot_deadline(2_792_503_072) > 3500  # the capture that stopped every attempt


def test_failed_snapshot_leaves_its_reason_beside_the_staging_directory(tmp_path):
    import subprocess, sys
    source = tmp_path/'source'; source.mkdir()
    (source/'file.txt').write_text('x')
    output = tmp_path/'staging'/'attempt'
    output.mkdir(parents=True)  # already exists: the snapshot must refuse it
    completed = subprocess.run([sys.executable, '-B', str(Path(__file__).parents[1]/'tools/backup_market_cache.py'),
                                '--source', str(source), '--output', str(output)], capture_output=True, text=True, timeout=30)
    assert completed.returncode != 0
    receipt = json.loads((tmp_path/'staging'/'attempt.failure.json').read_text())
    assert receipt['type'] == 'FileExistsError' and receipt['message'] is None


def test_sqlite_failure_receipt_names_member_and_fixed_error_code(tmp_path):
    import subprocess, sys
    source = tmp_path/'source'; source.mkdir()
    (source/'damaged.sqlite3').write_bytes(b'not a sqlite database' * 100)
    output = tmp_path/'staging'/'attempt'
    completed = subprocess.run([sys.executable, '-B', str(Path(__file__).parents[1]/'tools/backup_market_cache.py'),
                                '--source', str(source), '--output', str(output)], capture_output=True, text=True, timeout=30)
    assert completed.returncode != 0
    receipt = json.loads((tmp_path/'staging'/'attempt.failure.json').read_text())
    assert receipt['member'] == 'damaged.sqlite3'
    assert receipt['sqlite_errorcode'] == sqlite3.SQLITE_NOTADB
    assert receipt['sqlite_errorname'] == 'SQLITE_NOTADB'
    assert receipt['message'] is None


def test_capacity_counts_hard_linked_members_once(tmp_path, monkeypatch):
    import os
    _, runner = incremental_tools(monkeypatch)
    (tmp_path/'a').mkdir(); (tmp_path/'b').mkdir()
    (tmp_path/'a'/'segment').write_bytes(b'x'*1000)
    try:
        os.link(tmp_path/'a'/'segment', tmp_path/'b'/'segment')
    except OSError:
        pytest.skip('hard links unavailable')
    assert runner.unique_bytes(tmp_path) == 1000


def runner_config(tmp_path, destination):
    return dict(destination=str(destination), state_file=str(tmp_path/'state.json'), remote_source='D:/s',
                remote_staging='D:/stage', remote_python='py', remote_helper='helper.py', ssh_host='h', ssh_key='k',
                remote_receipt='D:/r.json')


def test_runner_refuses_to_overlap_a_running_snapshot_and_records_the_stage(tmp_path, monkeypatch):
    _, runner = incremental_tools(monkeypatch)
    destination = tmp_path/'offsite'; destination.mkdir()
    calls = []

    def remote(config, script, *, timeout=None):
        calls.append((script, timeout))
        if 'ConvertTo-Json' in script: return '1000'
        if 'Get-CimInstance' in script: return '2'
        return ''

    monkeypatch.setattr(runner, 'remote', remote)
    with pytest.raises(RuntimeError):
        runner.execute(runner_config(tmp_path, destination), sleep=lambda seconds: None)
    status = json.loads((tmp_path/'state.json').read_text())
    assert status['last_failure']['stage'] == 'overlap_check' and status['last_failure']['type'] == 'RuntimeError'
    assert calls[0][1] == 120  # refuse overlap before a potentially expensive capacity walk
    assert not any('ConvertTo-Json' in script for script, _ in calls)
    assert not any('--source' in script for script, _ in calls)


def test_runner_requests_reuse_only_against_its_own_verified_backup(tmp_path, monkeypatch):
    _, runner = incremental_tools(monkeypatch)
    destination = tmp_path/'offsite'
    verified = destination/'backups'/'20261104T180605Z-a89f27f7'
    verified.mkdir(parents=True)
    (verified/'backup-manifest.json').write_text(json.dumps(dict(files={})))
    scripts = []

    def remote(config, script, *, timeout=None):
        scripts.append(script)
        if 'ConvertTo-Json' in script: return '1000'
        if 'Get-CimInstance' in script: return '0'
        if '--source' in script: raise RuntimeError('stop after the snapshot command')
        return ''

    monkeypatch.setattr(runner, 'remote', remote)
    config = runner_config(tmp_path, destination)
    previous = dict(status='failed', last_verified_at='2026-11-04T20:52:13+00:00', destination=str(verified))
    with pytest.raises(RuntimeError, match='stop after'):
        runner.attempt(config, previous=previous)
    assert "--previous 'D:/stage/20261104T180605Z-a89f27f7'" in scripts[-1]
    scripts.clear()
    with pytest.raises(RuntimeError, match='stop after'):
        runner.attempt(config, previous=dict(previous, destination=str(tmp_path/'elsewhere')))
    assert '--previous' not in scripts[-1]
