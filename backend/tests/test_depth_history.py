from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from tools.depth_recording import SegmentRecorder
from tools.depth_totals import TotalsRecorder
from tools.read_depth_history import read_history
from app.services import depth_history as service

OWNER = 'a'*64
BASE = datetime(2026, 10, 30, 17, tzinfo=timezone.utc)


def capture(root, owner=OWNER, *, seal=True):
    recorder = TotalsRecorder(SegmentRecorder(root, owner))
    for i, (typ, size, price) in enumerate([(2,30,20000),(1,10,20000.25),(2,20,20000)]):
        at = (BASE+timedelta(milliseconds=i*250)).isoformat()
        recorder.append('CON.F.US.MNQ.Z26','depth',dict(type=typ,volume=size,price=price,timestamp=at),
                        received_at=at,monotonic_ns=i*250000000)
    if seal: recorder.close()
    return recorder


def test_history_scopes_owner_and_reads_committed_segments_only(tmp_path):
    capture(tmp_path)
    capture(tmp_path, 'b'*64)
    partial = capture(tmp_path, seal=False)
    result = read_history(tmp_path/'totals', dict(owner_hash=OWNER))
    assert len(result['points']) == 3
    assert result['points'][1]['imbalance'] == .5
    assert result['points'][-1]['bid_contracts'] == 20
    assert result['contracts'] == ['CON.F.US.MNQ.Z26']
    assert 'owner_hash' not in json.dumps(result)
    partial.close()


def test_scope_range_and_corrupt_file_handling(tmp_path):
    capture(tmp_path)
    result = read_history(tmp_path/'totals', dict(owner_hash=OWNER, scope='10',
        start=(BASE+timedelta(milliseconds=200)).isoformat(), end=(BASE+timedelta(milliseconds=500)).isoformat()))
    assert len(result['points']) == 1 and result['points'][0]['bid_contracts'] == 30
    next((tmp_path/'totals').rglob('*.ndjson.gz')).write_bytes(b'tampered')
    result = read_history(tmp_path/'totals', dict(owner_hash=OWNER))
    assert not result['points'] and result['skipped_segments'] == 1


@pytest.mark.parametrize('overrides', [dict(owner_hash='../escape'), dict(scope='500'),
    dict(start=BASE.isoformat()), dict(start=BASE.isoformat(), end=(BASE+timedelta(hours=2)).isoformat()),
    dict(start='2026-10-30T17:00:00',end='2026-10-30T17:15:00')])
def test_history_rejects_invalid_queries(tmp_path, overrides):
    with pytest.raises(ValueError): read_history(tmp_path, dict(dict(owner_hash=OWNER), **overrides))


def test_history_rejects_manifest_path_traversal(tmp_path):
    capture(tmp_path)
    marker = next((tmp_path/'totals').rglob('*.segment.json'))
    m=json.loads(marker.read_text());m['file']='../outside.ndjson.gz';marker.write_text(json.dumps(m))
    result=read_history(tmp_path/'totals',dict(owner_hash=OWNER))
    assert result['points'] == [] and result['skipped_segments'] == 1


def test_remote_transport_uses_server_configuration_and_never_passes_auth_token(tmp_path, monkeypatch):
    config = tmp_path/'source.json'
    config.write_text(json.dumps(dict(python_path='C:/runtime/python.exe',reader_path='C:/tools/reader.py',
        recording_root='D:/cache/totals',ssh_target='reader@100.1.2.3',identity_file='C:/private/key',label='Host')))
    monkeypatch.setattr(service,'history_source_path',lambda:config)
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs));return SimpleNamespace(returncode=0,stdout='{"points":[]}')
    monkeypatch.setattr(service.subprocess,'run',run)
    assert service.account_history(user_id='owner')['source']=='Host'
    args, kwargs=calls[0]
    assert 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args
    assert kwargs['timeout']==25 and json.loads(kwargs['input'])['owner_hash']==service.scope_hash('owner')
    assert 'owner' not in args and not kwargs.get('shell')
    bad=json.loads(config.read_text());bad['reader_path']='C:/tools/read.py & echo bad';config.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError):service.account_history(user_id='owner')
    assert len(calls)==1


def test_history_api_checks_account_ownership_before_reading(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'get_authenticated_user_id',lambda:'owner')
    def denied(*args,**kwargs):raise HTTPException(403,'Not owned')
    monkeypatch.setattr(main,'_require_owned_projectx_account',denied)
    monkeypatch.setattr(service,'account_history',lambda **kwargs:pytest.fail('Must not read files'))
    with pytest.raises(HTTPException) as failure:
        main.get_order_book_history(123,Response(),db=object())
    assert failure.value.status_code==403


def test_history_api_passes_authenticated_owner_and_disables_response_caching(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'get_authenticated_user_id',lambda:'owner')
    monkeypatch.setattr(main,'_require_owned_projectx_account',lambda *a,**kw:None)
    calls=[]
    monkeypatch.setattr(service,'account_history',lambda **kwargs:calls.append(kwargs) or {'points':[]})
    response=Response()
    main.get_order_book_history(123,response,db=SimpleNamespace(rollback=lambda:None))
    assert calls[0]['user_id']=='owner' and response.headers['cache-control']=='no-store'
