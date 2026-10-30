import asyncio
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import time

import pytest

from tools.depth_recording import SegmentRecorder, StorageLimit, inventory, verify_segments
from tools.record_market_depth import ProcessLock, record

OWNER='a'*64
CONTRACT='CON.F.US.MNQ.Z26'


def payload(side, price=20000):
    return dict(type=side,price=price,volume=10,currentVolume=10,timestamp=datetime.now(timezone.utc).isoformat(),seq=123,epoch=456)


def test_rotated_segments_preserve_raw_fields_and_contiguous_receipt_indices(tmp_path):
    r=SegmentRecorder(tmp_path,OWNER,segment_bytes=1024)
    for i in range(12):r.append(CONTRACT,'depth',payload(1+i%2,20000+i*.25))
    r.close();result=verify_segments(tmp_path)
    assert result['verified_events']==12 and result['verified_segments']>1 and result['incomplete_segments']==0
    rows=[]
    for p in sorted(tmp_path.rglob('*.ndjson.gz')):
        with gzip.open(p,'rt') as f:rows.extend(json.loads(line) for line in f)
    assert [v['index'] for v in rows]==list(range(1,13))
    assert all(v['payload']['seq']==123 and v['payload']['epoch']==456 and v['synthetic'] is False for v in rows)
    assert r.status()['model_inputs_enabled'] is False
    assert r.status()['snapshot_completion_verified'] is False


def test_level2_evidence_requires_both_explicit_sides_not_best_quotes(tmp_path):
    r=SegmentRecorder(tmp_path,OWNER)
    for i in range(5):r.append(CONTRACT,'depth',payload(3+i%2,20000+i))
    assert not r.level2_observed
    for side in (1,2):
        for i in range(2):r.append(CONTRACT,'depth',payload(side,20000+i*.25))
    assert r.level2_observed
    assert not r.status()['provider_sequence_guarantee']
    r.close()


def test_stale_and_invalid_timestamps_retained_without_verifying_level2(tmp_path):
    r=SegmentRecorder(tmp_path,OWNER)
    for side in (1,2):
        for i in range(2):r.append(CONTRACT,'depth',dict(payload(side,20000+i),timestamp='0001-01-01T00:00:00Z'))
    r.append(CONTRACT,'depth',dict(payload(1),timestamp='invalid'))
    r.close()
    assert verify_segments(tmp_path)['verified_events']==5
    assert not r.level2_observed
    assert r.provider_time_counts['older_than_2_seconds']==4 and r.provider_time_counts['unparseable']==1


def test_capacity_guard_stops_before_creating_a_new_segment(tmp_path):
    def reject():raise StorageLimit('recording_budget_reached')
    r=SegmentRecorder(tmp_path,OWNER,before_segment=reject)
    with pytest.raises(StorageLimit):r.append(CONTRACT,'depth',payload(1))
    assert r.index==0 and not list(tmp_path.rglob('*.partial'))
    r.close()


def test_storage_review_and_budget_do_not_delete_evidence(tmp_path):
    file=tmp_path/'evidence.partial';file.write_bytes(b'keep')
    limited=inventory(tmp_path,max_bytes=1024,reserve_bytes=0)
    assert limited['blocked_reason']=='recording_budget_reached' and file.read_bytes()==b'keep'
    old=inventory(tmp_path,max_bytes=100*1024**2,reserve_bytes=0,now=time.time()+31*86400)
    assert old['blocked_reason']=='retention_review_due' and file.exists()


def test_tampering_and_path_traversal_fail_verification(tmp_path):
    r=SegmentRecorder(tmp_path,OWNER);r.append(CONTRACT,'depth',payload(1));r.close()
    marker=next(tmp_path.rglob('*.segment.json'));original=marker.read_text();m=json.loads(original)
    m['file']='../escape.ndjson.gz';marker.write_text(json.dumps(m))
    with pytest.raises(ValueError):verify_segments(tmp_path)
    marker.write_text(original)
    next(tmp_path.rglob('*.ndjson.gz')).write_bytes(b'bad')
    with pytest.raises(ValueError):verify_segments(tmp_path)


def test_backup_snapshots_only_committed_depth_segments(tmp_path):
    from tools.backup_market_cache import backup
    source=tmp_path/'cache';depth=source/'depth-recordings-v1'
    r=SegmentRecorder(depth,OWNER);r.append(CONTRACT,'depth',payload(1));r.seal()
    r.append(CONTRACT,'depth',payload(2));r.flush()
    output=tmp_path/'backup';manifest=backup(source,output)
    assert manifest['depth_snapshot']['sealed_segments']==1
    assert not list(output.rglob('*.partial'))
    assert verify_segments(output/'depth-recordings-v1')['verified_events']==1
    assert list(source.rglob('*.partial'))
    r.close()
    assert verify_segments(source/'depth-recordings-v1')['verified_events']==2


def test_os_lock_releases_without_deleting_lock_file(tmp_path):
    path=tmp_path/'lock'
    with ProcessLock(path):
        with pytest.raises(RuntimeError):
            with ProcessLock(path):pass
    with ProcessLock(path):pass
    assert path.exists()


@pytest.mark.parametrize('record_totals', [False, True])
def test_reconnect_resubscribes_and_preserves_disconnect_boundary(tmp_path,monkeypatch,record_totals):
    import app.services.projectx_hubs as hubs
    import tools.record_market_depth as service
    runtime=tmp_path/'runtime';runtime.mkdir()
    config=dict(runtime_root=str(runtime),max_bytes=100*1024**2,reserve_bytes=0,review_after_days=30,segment_seconds=60,
                record_contract_totals=record_totals)
    sockets=[]
    class Client:
        def search_contracts(self,**kwargs):
            assert kwargs==dict(search_text='MNQ',live=False)
            return [dict(id=CONTRACT,active_contract=True,tick_size=.25,tick_value=.5)]
        def get_access_token(self):return 'fixture-token'
    class Socket:
        def __init__(self):self.sent=[];self.receives=0;sockets.append(self)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,text):self.sent.append(json.loads(text.rstrip('\x1e')))
        async def recv(self):
            self.receives+=1
            if len(sockets)==1 and self.receives>1:raise ConnectionError('test disconnect')
            if len(sockets)==2:(runtime/'STOP').write_text('stop')
            return json.dumps(dict(type=1,target='GatewayDepth',arguments=[CONTRACT,payload(1)]))+'\x1e'
    async def handshake(socket):return []
    async def sleep(seconds):pass
    monkeypatch.setattr(hubs,'_open_hub',lambda *args,**kwargs:Socket())
    monkeypatch.setattr(hubs,'_signalr_handshake',handshake)
    monkeypatch.setattr(service.asyncio,'sleep',sleep)
    assert asyncio.run(record(config,Client(),OWNER,tmp_path/'data'))=='stopped'
    assert len(sockets)==2
    for s in sockets:
        assert [r['target'] for r in s.sent[:3]]==['SubscribeContractQuotes','SubscribeContractTrades','SubscribeContractMarketDepth']
    result=verify_segments(tmp_path/'data');assert result['verified_events']>=7
    status=json.loads((runtime/'status.json').read_text())
    assert status['reconnects']==1 and status['broker_mutation_calls']==0
    assert status['recording']['counts']['depth']==2
    if record_totals:
        assert status['recording']['contract_totals']['enabled'] is True
        assert verify_segments(tmp_path/'data'/'totals')['verified_events'] >= 5
    assert 'fixture-token' not in (runtime/'status.json').read_text()


def test_market_client_refuses_order_submission_before_network():
    from app.services.depth_capture import ReadOnlyMarketClient
    client=ReadOnlyMarketClient(base_url='https://api.topstepx.com',username='fixture',api_key='fixture')
    with pytest.raises(RuntimeError):client._request_once('POST','/api/Order/place',json={})


@pytest.mark.parametrize('failure',['missing_acknowledgements','stalled_transport'])
def test_silent_subscription_or_transport_is_reconnected(tmp_path,monkeypatch,failure):
    import app.services.projectx_hubs as hubs
    import tools.record_market_depth as service
    from types import SimpleNamespace
    runtime=tmp_path/'runtime';runtime.mkdir();clock=[0.]
    config=dict(runtime_root=str(runtime),max_bytes=100*1024**2,reserve_bytes=0,review_after_days=30,segment_seconds=60)
    class Client:
        def search_contracts(self,**kwargs):return [dict(id=CONTRACT,active_contract=True,tick_size=.25,tick_value=.5)]
        def get_access_token(self):return 'fixture-token'
    class Socket:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):(runtime/'STOP').write_text('test complete')
        async def send(self,text):pass
        async def recv(self):
            clock[0]+=50
            if failure=='stalled_transport':raise asyncio.TimeoutError()
            return '{"type":6}\x1e'
    async def handshake(socket):
        return ([dict(type=3,invocationId=str(i)) for i in range(3)]
                if failure=='stalled_transport' else [])
    monkeypatch.setattr(service,'time',SimpleNamespace(monotonic=lambda:clock[0],monotonic_ns=time.monotonic_ns))
    monkeypatch.setattr(hubs,'_open_hub',lambda *args,**kwargs:Socket())
    monkeypatch.setattr(hubs,'_signalr_handshake',handshake)
    assert asyncio.run(record(config,Client(),OWNER,tmp_path/'data'))=='stopped'
    status=json.loads((runtime/'status.json').read_text())
    assert status['errors']==1 and status['last_error']['type']=='TimeoutError'
    assert status['recording']['counts']['disconnect']==1
    assert verify_segments(tmp_path/'data')['verified_events']==3


def test_live_environment_is_rejected_before_credential_database_access(tmp_path,monkeypatch):
    from tools.record_market_depth import load_context
    monkeypatch.setenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED','false')
    monkeypatch.setenv('TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION','false')
    monkeypatch.delenv('PYTHON_DOTENV_DISABLED',raising=False)
    (tmp_path/'.env').write_text('TOPSIGNAL_LIVE_EXECUTION_ENABLED=true\n')
    with pytest.raises(RuntimeError,match='Paper-only environment'):
        load_context(dict(backend_root=str(tmp_path)))
