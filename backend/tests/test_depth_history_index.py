from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from time import monotonic

import pytest

from tools import read_depth_history as reader
from tools.depth_history_index import MAX_BYTES
from test_depth_history import BASE, OWNER, capture


def read(tmp_path, **query):
    return reader.read_history(tmp_path/'totals', dict(owner_hash=OWNER, **query),
                               cache_path=tmp_path/'index/history.sqlite3')


def test_warm_index_avoids_old_directory_walks_and_decompression(tmp_path, monkeypatch):
    capture(tmp_path)
    first = read(tmp_path)
    original = reader.verified_rows
    calls = []
    def rows(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(reader, 'verified_rows', rows)
    glob = Path.glob
    def scan(path, pattern):
        if pattern == '*.segment.json':
            pytest.fail('Unchanged run must reuse its incremental inventory')
        return glob(path, pattern)
    monkeypatch.setattr(Path, 'glob', scan)
    assert read(tmp_path)['points'] == first['points']
    assert calls == []
    monkeypatch.setattr(Path, 'glob', glob)
    capture(tmp_path, base=BASE+timedelta(minutes=1))
    assert len(read(tmp_path)['points']) == 6
    assert len(calls) == 1


def test_index_never_shares_owners_or_depth_scopes(tmp_path):
    capture(tmp_path)
    read(tmp_path)
    other = reader.read_history(tmp_path/'totals', dict(owner_hash='b'*64), cache_path=tmp_path/'index/history.sqlite3')
    assert other['points'] == []
    assert read(tmp_path, scope='10')['scope'] == '10'


def test_cached_points_reject_changed_raw_and_manifest_scope(tmp_path):
    capture(tmp_path)
    assert read(tmp_path)['points']
    path = next((tmp_path/'totals').rglob('*.ndjson.gz'))
    data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
    assert read(tmp_path)['skipped_segments'] == 1
    marker = next((tmp_path/'totals').rglob('*.segment.json'))
    m = json.loads(marker.read_text()); m['owner_hash'] = 'b'*64; marker.write_text(json.dumps(m))
    assert read(tmp_path)['points'] == []
    assert read(tmp_path)['points'] == []


def test_corrupt_derived_points_are_rebuilt_from_verified_source(tmp_path, monkeypatch):
    capture(tmp_path)
    expected = read(tmp_path)['points']
    with sqlite3.connect(tmp_path/'index/history.sqlite3') as db:
        db.execute("UPDATE points SET payload=x'0000'")
    assert read(tmp_path)['points'] == expected
    with sqlite3.connect(tmp_path/'index/history.sqlite3') as db:
        assert db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0] <= MAX_BYTES


def test_deadline_preserves_completed_index_work_for_retry(tmp_path, monkeypatch):
    capture(tmp_path)
    expected = read(tmp_path)['points']
    with pytest.raises(reader.HistoryReadTimeout):
        reader.read_history(tmp_path/'totals', dict(owner_hash=OWNER), deadline=monotonic()-1,
                            cache_path=tmp_path/'index/history.sqlite3')
    monkeypatch.setattr(reader, 'verified_rows', lambda *a, **k: pytest.fail('Completed cache must survive retry'))
    assert read(tmp_path)['points'] == expected


def test_overview_preserves_exact_gap_and_extrema_semantics(tmp_path):
    for minute in (0, 1, 65):
        capture(tmp_path, base=BASE+timedelta(minutes=minute))
    query=dict(owner_hash=OWNER, start=BASE.isoformat(), end=(BASE+timedelta(hours=2)).isoformat())
    expected = reader.read_history(tmp_path/'totals', query)
    for _ in range(2):
        actual = reader.read_history(tmp_path/'totals', query, cache_path=tmp_path/'index/history.sqlite3')
        assert actual['points'] == expected['points']
        assert actual['quality_counts'] == expected['quality_counts']
        assert actual['raw_measurements'] == expected['raw_measurements']


def test_partial_cold_inventory_survives_deadline(tmp_path, monkeypatch):
    for minute in range(3):
        capture(tmp_path, base=BASE+timedelta(minutes=minute))
    clock = [0]
    original = reader.read_manifest
    reads = []
    def slow(marker, *args, **kwargs):
        result = original(marker, *args, **kwargs)
        reads.append(marker)
        clock[0] += 1
        return result
    monkeypatch.setattr(reader, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(reader, 'read_manifest', slow)
    with pytest.raises(reader.HistoryReadTimeout):
        reader.read_history(tmp_path/'totals', dict(owner_hash=OWNER), deadline=1,
                            cache_path=tmp_path/'index/history.sqlite3')
    with sqlite3.connect(tmp_path/'index/history.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM markers').fetchone()[0] == 1
    monkeypatch.setattr(reader, 'read_manifest', original)
    assert len(read(tmp_path)['points']) == 9


def test_inventory_fast_path_rejects_reparse_metadata(tmp_path):
    from types import SimpleNamespace
    import stat
    marker = tmp_path/'00000001.segment.json'
    marker.write_text('{}')
    info = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
    with pytest.raises(ValueError, match='reparse'):
        reader.read_manifest(marker, tmp_path, validated_parent=tmp_path, file_info=info)


def test_warm_points_recheck_raw_child_reparse_metadata(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat
    capture(tmp_path)
    assert read(tmp_path)['points']
    original = Path.stat
    def altered(path, *args, **kwargs):
        if path.name.endswith('.ndjson.gz'):
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', altered)
    result = read(tmp_path)
    assert result['points'] == [] and result['skipped_segments'] == 1
