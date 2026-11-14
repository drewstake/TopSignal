import os
from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

from tools.depth_recording import inventory, StorageLimit


def test_nested_inventory_counts_sealed_partial_and_hidden_evidence(tmp_path):
    run = tmp_path / 'day' / 'capture'
    run.mkdir(parents=True)
    files = {run/'0001.segment.json': b'123', run/'0002.partial': b'45678',
             tmp_path/'.receipt': b'90'}
    for path, data in files.items():
        path.write_bytes(data)
        os.utime(path, (1000, 1000))
    result = inventory(tmp_path, max_bytes=100*1024**2, reserve_bytes=0, now=1001)
    assert result['bytes'] == 10
    assert result['sealed_segments'] == result['incomplete_segments'] == 1
    assert result['oldest_mtime'] == 1000
    assert result['blocked_reason'] is None
    assert not result['automatic_deletion']
    # A fresh scan sees newly sealed files; no persistent metadata cache.
    (run/'0002.partial').rename(run/'0002.segment.json')
    result = inventory(tmp_path, max_bytes=100*1024**2, reserve_bytes=0, now=1001)
    assert result['sealed_segments'] == 2 and result['incomplete_segments'] == 0


@pytest.mark.parametrize('mode,attributes', [
    (stat.S_IFLNK, 0),
    (stat.S_IFDIR, stat.FILE_ATTRIBUTE_REPARSE_POINT),
    (stat.S_IFREG, stat.FILE_ATTRIBUTE_REPARSE_POINT),
])
def test_inventory_refuses_links_and_windows_reparse_entries(tmp_path, monkeypatch, mode, attributes):
    class Entries:
        def __enter__(self):
            return iter([SimpleNamespace(stat=lambda **kw: SimpleNamespace(
                st_mode=mode, st_file_attributes=attributes), name='escape', path='outside')])
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(os, 'scandir', lambda path: Entries())
    with pytest.raises(StorageLimit, match='recording_reparse_point'):
        inventory(tmp_path, max_bytes=100*1024**2, reserve_bytes=0)


def test_inventory_does_not_hide_enumeration_failure(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError('inventory unavailable')
    monkeypatch.setattr(os, 'scandir', denied)
    with pytest.raises(PermissionError):
        inventory(tmp_path, max_bytes=100*1024**2, reserve_bytes=0)
