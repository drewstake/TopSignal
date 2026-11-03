from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from tools.depth_recording import SegmentRecorder
from tools.depth_totals import TotalsRecorder
from tools.read_depth_history import read_history, session_bounds, add_overview_point, overview_points
from app.services import depth_history as service

OWNER = 'a'*64
BASE = datetime(2026, 10, 30, 17, tzinfo=timezone.utc)


def capture(root, owner=OWNER, *, seal=True, base=BASE):
    recorder = TotalsRecorder(SegmentRecorder(root, owner))
    for i, (typ, size, price) in enumerate([(2,30,20000),(1,10,20000.25),(2,20,20000)]):
        at = (base+timedelta(milliseconds=i*250)).isoformat()
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
    dict(start=BASE.isoformat()), dict(start=BASE.isoformat(), end=(BASE+timedelta(hours=25)).isoformat()),
    dict(window='session', start=BASE.isoformat(), end=(BASE+timedelta(hours=1)).isoformat()),
    dict(start='2026-10-30T17:00:00',end='2026-10-30T17:15:00')])
def test_history_rejects_invalid_queries(tmp_path, overrides):
    with pytest.raises(ValueError): read_history(tmp_path, dict(dict(owner_hash=OWNER), **overrides))


def test_history_rejects_manifest_path_traversal(tmp_path):
    capture(tmp_path)
    marker = next((tmp_path/'totals').rglob('*.segment.json'))
    m=json.loads(marker.read_text());m['file']='../outside.ndjson.gz';marker.write_text(json.dumps(m))
    result=read_history(tmp_path/'totals',dict(owner_hash=OWNER))
    assert result['points'] == [] and result['skipped_segments'] == 1


@pytest.mark.parametrize('at,start,end', [
    ('2026-11-02T02:00:00+00:00','2026-11-01T23:00:00+00:00','2026-11-02T02:00:00+00:00'),
    ('2026-03-09T02:00:00+00:00','2026-03-08T22:00:00+00:00','2026-03-09T02:00:00+00:00'),
    ('2026-11-02T22:30:00+00:00','2026-11-01T23:00:00+00:00','2026-11-02T22:00:00+00:00'),
    ('2026-11-07T14:00:00+00:00','2026-11-05T23:00:00+00:00','2026-11-06T22:00:00+00:00'),
])
def test_session_bounds_follow_eastern_time_dst_and_weekend(at,start,end):
    result=session_bounds(datetime.fromisoformat(at))
    assert tuple(d.isoformat() for d in result)==(start,end)


def test_full_session_range_keeps_early_and_late_recordings_in_bounded_overview(tmp_path):
    capture(tmp_path)
    capture(tmp_path,base=BASE+timedelta(hours=22))
    result=read_history(tmp_path/'totals',dict(owner_hash=OWNER,start=BASE.isoformat(),end=(BASE+timedelta(hours=23)).isoformat()))
    assert result['interval_ms']==10000
    assert len(result['points'])==6
    assert result['points'][-1]['time']-result['points'][0]['time']==22*3600+.5
    assert result['points'][0]['quality_flags']  # flag in a discarded raw sample is retained


def test_overview_preserves_hidden_gaps_and_caps_a_full_day():
    buckets, previous={},{}
    point=dict(contract_id='CON.F.US.MNQ.Z26',capture_id='run',generation=1,quality_flags=[],mid_price=100,bid_contracts=10,ask_contracts=20,bid_levels=1,ask_levels=1,imbalance=-1/3)
    for i in range(24*3600*4):
        if 100 <= i < 112: continue
        add_overview_point(buckets,previous,dict(point,time=i/4,index=i))
    assert len(buckets)==8640
    assert any(p['gap_before']['mid_price'] and 'missing_observations' in p['gap_before']['mid_price']['reasons'] for p in overview_points(buckets))
    assert max(p['time'] for p in overview_points(buckets))==86399.75


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


def test_duplicate_tabs_share_bounded_owner_scoped_reads(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    monkeypatch.setattr(service, 'history_source_path', lambda: tmp_path/'source.json')
    started, release = Event(), Event()
    calls=[]
    def read(**kwargs):
        calls.append(kwargs['user_id'])
        started.set()
        assert release.wait(3)
        return {'points': [], 'source': kwargs['user_id']}
    monkeypatch.setattr(service, '_read_account_history', read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(service.account_history,user_id='a',window='session')
        assert started.wait(3)
        second=pool.submit(service.account_history,user_id='a',window='session')
        release.set()
        a,b=first.result(),second.result()
    assert calls==['a']
    a['points'].append('local mutation')
    assert not b['points']
    assert not service.account_history(user_id='a',window='session')['points']
    assert service.account_history(user_id='b',window='session')['source']=='b'
    assert calls==['a','b']
    monkeypatch.setattr(service,'monotonic',lambda:float('inf'))
    service.account_history(user_id='a',window='session')
    assert calls==['a','b','a']
