import json
import pytest
from tools import depth_recording as storage
from tools.record_market_depth import StatusPublisher


def test_atomic_status_retries_short_reader_lock_and_preserves_prior_until_success(tmp_path, monkeypatch):
    path=tmp_path/'health.json';path.write_text('{"old":true}')
    original=storage.os.replace; calls=[]; waits=[]
    def replace(source, target):
        calls.append(1)
        if len(calls)<3:
            assert json.loads(path.read_text()) == {'old':True}
            raise PermissionError(13, 'fixture')
        original(source,target)
    monkeypatch.setattr(storage.os,'replace',replace)
    monkeypatch.setattr(storage.time,'sleep',waits.append)
    storage.atomic_json(path,{'new':True})
    assert json.loads(path.read_text()) == {'new':True}
    assert waits == [.01,.03] and not list(tmp_path.glob('.*.tmp'))


def test_persistent_permission_failure_keeps_old_status_and_diagnostics_then_recovers(tmp_path, monkeypatch):
    path=tmp_path/'health.json';path.write_text('{"at":"old"}')
    original=storage.os.replace
    monkeypatch.setattr(storage.os,'replace',lambda *a: (_ for _ in ()).throw(PermissionError(13,'private exception')))
    monkeypatch.setattr(storage.time,'sleep',lambda *a:None)
    publisher=StatusPublisher()
    assert not publisher.write(path,{'at':'new'})
    assert json.loads(path.read_text()) == {'at':'old'}
    assert publisher.failures==1 and not list(tmp_path.glob('.*.tmp'))
    # Critical manifests cannot claim success under the same fault.
    with pytest.raises(PermissionError):storage.atomic_json(tmp_path/'segment.json',{'committed':True})
    monkeypatch.setattr(storage.os,'replace',original)
    assert publisher.write(path,{'at':'recovered'})
    assert json.loads(path.read_text())['status_publication']['failures']==1
    assert 'private exception' not in path.read_text()


def test_disk_full_is_still_fatal_for_status(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.os,'replace',lambda *a: (_ for _ in ()).throw(OSError(28,'full')))
    with pytest.raises(OSError):StatusPublisher().write(tmp_path/'health.json',{})
