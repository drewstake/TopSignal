"""Refreshes reuse metadata without hiding new data or integrity failures."""
from datetime import timedelta
import json
from pathlib import Path

import pytest

from tools import read_depth_history as reader
from test_depth_history import BASE, OWNER, capture


@pytest.fixture(autouse=True)
def empty_manifest_cache():
    reader._manifest_cache.clear()
    reader._manifest_cache_bytes = 0
    yield
    reader._manifest_cache.clear()
    reader._manifest_cache_bytes = 0


def test_refresh_reuses_manifests_but_reads_new_segments(tmp_path, monkeypatch):
    capture(tmp_path)
    root = tmp_path / 'totals'
    opened = []
    original = Path.open

    def track(path, *args, **kwargs):
        if path.name.endswith('.segment.json'):
            opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', track)
    first = reader.read_history(root, dict(owner_hash=OWNER))
    second = reader.read_history(root, dict(owner_hash=OWNER))
    assert first['points'] == second['points']
    assert len(opened) == 1
    capture(tmp_path, base=BASE + timedelta(minutes=1))
    opened.clear()
    refreshed = reader.read_history(root, dict(owner_hash=OWNER))
    assert len(opened) == 1
    assert len(refreshed['points']) == 6
    assert refreshed['latest'] > first['latest']
    assert not reader.read_history(root, dict(owner_hash='b' * 64))['points']


def test_changed_manifest_invalidates_cache(tmp_path):
    capture(tmp_path)
    root = tmp_path / 'totals'
    assert reader.read_history(root, dict(owner_hash=OWNER))['points']
    marker = next(root.rglob('*.segment.json'))
    manifest = json.loads(marker.read_text())
    manifest['file'] = '../outside.ndjson.gz'
    marker.write_text(json.dumps(manifest))
    result = reader.read_history(root, dict(owner_hash=OWNER))
    assert result['points'] == []
    assert result['skipped_segments'] == 1


def test_cached_manifest_still_checks_recording_integrity(tmp_path):
    capture(tmp_path)
    root = tmp_path / 'totals'
    assert reader.read_history(root, dict(owner_hash=OWNER))['points']
    data = next(root.rglob('*.ndjson.gz'))
    raw = bytearray(data.read_bytes())
    raw[-1] ^= 1
    data.write_bytes(raw)  # Same size; the compressed-content checksum must fail.
    result = reader.read_history(root, dict(owner_hash=OWNER))
    assert result['points'] == []
    assert result['skipped_segments'] == 1


@pytest.mark.parametrize('limit', ['entries', 'bytes'])
def test_metadata_cache_is_bounded(tmp_path, monkeypatch, limit):
    capture(tmp_path)
    capture(tmp_path, base=BASE + timedelta(minutes=1))
    root = tmp_path / 'totals'
    markers = list(root.rglob('*.segment.json'))
    if limit == 'entries':
        monkeypatch.setattr(reader, '_MANIFEST_CACHE_ENTRIES', 1)
    else:
        monkeypatch.setattr(reader, '_MANIFEST_CACHE_BYTES', max(p.stat().st_size for p in markers))
    result = reader.read_history(root, dict(owner_hash=OWNER))
    assert len(result['points']) == 6
    assert len(reader._manifest_cache) == 1
    assert reader._manifest_cache_bytes <= reader._MANIFEST_CACHE_BYTES


def test_cached_manifest_still_checks_path(tmp_path, monkeypatch):
    capture(tmp_path)
    root = tmp_path / 'totals'
    reader.read_history(root, dict(owner_hash=OWNER))
    original = reader.safe_path

    def reject_marker(path, storage_root):
        if path.name.endswith('.segment.json'):
            raise ValueError('Recording reparse points are unsupported.')
        return original(path, storage_root)

    monkeypatch.setattr(reader, 'safe_path', reject_marker)
    result = reader.read_history(root, dict(owner_hash=OWNER))
    assert result['points'] == []
    assert result['skipped_segments'] == 1
