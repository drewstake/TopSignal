"""Synthetic fixtures for invariant checks; never presented as market findings."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import ast
import asyncio
import csv
import io
import json
from pathlib import Path
from threading import Event
import time

import pytest
from fastapi import FastAPI, HTTPException

from tools.depth_recording import SegmentRecorder
from tools.depth_totals import BookTotals
from tools.read_depth_research import read_research
from app.services.microstructure_models import ResearchRequest, Study, Execution, BookmarkInput, ExperimentInput
from app.services.microstructure_study import episodes, horizon, run_study, simulate, cluster_interval, summarize_outcomes
from app.services import microstructure as store
from app.microstructure_api import install

BASE = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)
OWNER = 'a'*64
CONTRACT = 'CON.F.US.MNQ.Z26'


def test_small_sample_return_percentiles_preserve_the_distribution():
    rows = [dict(valid=True, return_points=v, favorable=max(0, v), adverse=min(0, v), nothing=False) for v in (7, 16.625)]
    result = summarize_outcomes(rows)
    assert result['p10'] == pytest.approx(7.9625)
    assert result['p90'] == pytest.approx(15.6625)
    assert summarize_outcomes(rows[:1])['p90'] == 7
    assert summarize_outcomes([])['p10'] is None


def event(ms, typ=2, price=20000, volume=10, kind='depth', **fields):
    at = (BASE+timedelta(milliseconds=ms)).isoformat()
    return dict(contract_id=CONTRACT, kind=kind, payload=dict(type=typ, price=price, volume=volume, timestamp=at, **fields),
                received_at=at, provider_at=at, monotonic_ns=ms*1000000, capture_id='a'*32, index=ms+1)


def points(count=240, seconds=.25):
    rows = []
    for i in range(count):
        at = BASE+timedelta(seconds=i*seconds)
        rows.append(dict(time=at.timestamp(), timestamp=at.isoformat(), contract_id=CONTRACT, capture_id='a'*32,
            index=i+1, generation=1, reconstruction_id=1, mid_price=100, best_bid=99.875, best_ask=100.125,
            best_bid_quantity=10, best_ask_quantity=10, spread=.25, quality_flags=[], imbalance=0,
            session='2026-10-30', time_of_day='rth', minute_et=600, volatility=1, bid_contracts=50, ask_contracts=50,
            activity={'5':dict(volume_rate=20)}))
    return rows


def request(**kwargs):
    return ResearchRequest(windows=[dict(start=BASE, end=BASE+timedelta(seconds=60))], **kwargs)


def record(root):
    recorder = SegmentRecorder(root, OWNER)
    for i in range(200):
        for typ, price in ((2,20000),(1,20000.25)):
            e = event(i*250,typ=typ,price=price)
            recorder.append(CONTRACT,e['kind'],e['payload'],received_at=e['received_at'],monotonic_ns=e['monotonic_ns'])
    recorder.close()


def test_book_kernel_has_exact_ast_parity_with_display_implementation():
    backend=Path(__file__).parents[1]
    left=ast.parse((backend/'tools/depth_book.py').read_text())
    right=ast.parse((backend/'app/services/projectx_order_book.py').read_text())
    source={n.name:ast.dump(n) for n in right.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    for n in left.body:
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)):
            assert ast.dump(n)==source[n.name]


def test_event_activity_is_accumulated_before_sampling_and_no_future_leak():
    book=BookTotals()
    book.apply(event(0));book.apply(event(1,typ=1,price=20000.25))
    book.apply(event(2,volume=4))
    book.apply(event(3,volume=10))
    for ms in (20,40,80):
        book.apply(event(ms,kind='trade',volume=2))
    a=book.apply(event(1000))
    assert a['features']['activity']['1']['trade_count']==3
    assert a['features']['activity']['1']['traded_volume']==6
    assert a['features']['activity']['1']['bid_depletion_rate']==6
    assert a['features']['activity']['1']['bid_refill_rate']==6
    frozen=deepcopy(a)
    book.apply(event(1100,kind='trade',volume=5000))
    assert a==frozen
    assert a['features']['activity']['5']['traded_volume'] is None
    assert a['features']['activity']['1']['buy_volume'] is None


def test_trailing_baselines_exclude_current_observation_and_reset_warmup():
    book=BookTotals()
    for i in range(140):
        book.apply(event(i*250,volume=10));row=book.apply(event(i*250+1,typ=1,price=20000.25,volume=10))
    row=book.apply(event(35000,volume=2))
    assert row['features']['scopes']['1']['bid_depth_ratio']==pytest.approx(.2)
    assert row['features']['best_bid_quantity']==2
    assert row['features']['scopes']['ticks:1']['bid_contracts']==2
    row=book.apply(event(35001,typ=6))
    assert row['features']['baseline_warmup']
    assert row['features']['activity']['1']['trade_count'] is None


def test_reconnect_roll_epoch_and_invalid_trade_times_do_not_carry_activity():
    book=BookTotals()
    book.apply(event(0,epoch=1));book.apply(event(1,typ=1,price=20000.25,epoch=1))
    e=event(500,kind='trade');e['provider_at']=None
    book.apply(e)
    row=book.apply(event(1000,epoch=1))
    assert row['features']['activity']['1']['traded_volume'] is None
    row=book.apply(event(1250,epoch=2))
    assert row['ask_levels']==0 and 'epoch_changed' in row['quality_flags']
    row=book.apply(dict(event(1251),contract_id='CON.F.US.MNQ.H27'))
    assert row['features']['baseline_warmup'] and 'contract_changed' in row['quality_flags']


def test_raw_reader_bounds_owner_corruption_provenance_and_reproducibility(tmp_path,monkeypatch):
    record(tmp_path)
    q=dict(owner_hash=OWNER,start=BASE.isoformat(),end=(BASE+timedelta(seconds=49)).isoformat(),scope='5',replay=True)
    a=read_research(tmp_path,q);b=read_research(tmp_path,q)
    assert a['data_id']==b['data_id'] and a['points']==b['points']
    assert a['frames'] and a['sources'] and a['points'][1]['provider_at']
    assert not a['snapshot_completion_verified']
    assert not read_research(tmp_path,dict(q,owner_hash='b'*64))['points']
    with pytest.raises(ValueError):read_research(tmp_path,dict(q,end=(BASE+timedelta(hours=1)).isoformat()))
    with pytest.raises(ValueError):read_research(tmp_path,dict(q,scope='ticks:999'))
    with pytest.raises(InterruptedError):read_research(tmp_path,q,cancelled=lambda:True)
    marker=next(tmp_path.rglob('*.segment.json'));m=json.loads(marker.read_text());m['file']='../../escape.gz';marker.write_text(json.dumps(m))
    corrupt=read_research(tmp_path,q)
    assert not corrupt['points'] and corrupt['skipped_segments']==1 and corrupt['excluded_intervals'][0]['reason']=='corrupt_segment'


def test_reader_refuses_budget_before_decompression(tmp_path,monkeypatch):
    from tools import read_depth_research as reader
    record(tmp_path);monkeypatch.setattr(reader,'MAX_BYTES',1)
    with pytest.raises(ValueError,match='256 MiB'):
        reader.read_research(tmp_path,dict(owner_hash=OWNER,start=BASE.isoformat(),end=(BASE+timedelta(seconds=40)).isoformat()))


def test_episode_persistence_cooldown_and_no_adjacent_tick_pseudoreplication():
    p=points(300)
    for i in range(40,200):p[i]['imbalance']=-.6
    for i in range(220,250):p[i]['imbalance']=-.6
    config=Study(persistence_seconds=1,cooldown_seconds=60)
    indices,_,occurrences,_=episodes(p,config)
    assert occurrences==190 and indices==[44]
    p[100]['quality_flags']=['reset']
    assert episodes(p,config)[0]==[44]


def test_forward_outcomes_do_not_change_features_or_signals_and_reject_gaps_splits():
    p=points();config=Study(persistence_seconds=0)
    p[10]['imbalance']=-.6
    original=episodes(p,config)[0]
    p[40]['mid_price']=90
    assert episodes(p,config)[0]==original
    times=[v['time'] for v in p]
    assert horizon(p,times,10,5,config)['valid']
    p[20]['quality_flags']=['bid_stale']
    assert horizon(p,times,10,5,config)['reason']=='invalid_gap_or_generation'
    p[20]['quality_flags']=[];p[20]['session']='2026-10-31'
    split=Study(validation_session='2026-10-31',test_session='2026-11-01')
    assert horizon(p,times,10,5,split)['reason']=='split_or_session_boundary'


def test_controls_are_earlier_matched_nontriggers_without_outcome_selection():
    p=points(1200)
    for i in (200,600,900):p[i]['imbalance']=-.6
    config=Study(persistence_seconds=0,cooldown_seconds=10,horizons=[5])
    result=run_study(p,config)
    controls=[r['control_timestamp'] for r in result['episodes']]
    assert len(controls)==len(set(controls))
    for r in result['episodes']:
        assert datetime.fromisoformat(r['control_timestamp'])+timedelta(seconds=15)<datetime.fromisoformat(r['timestamp'])
    changed=deepcopy(p)
    for q in changed:q['mid_price']+=q['index']%17
    assert [r['control_timestamp'] for r in run_study(changed,config)['episodes']]==controls


def test_costs_use_bid_ask_once_fees_size_and_harsh_execution():
    p=points(400);times=[v['time'] for v in p]
    for i,q in enumerate(p):
        q['best_bid']-=i*.1;q['best_ask']-=i*.1
    config=Study(execution=Execution(direction='long',stop_ticks=2,target_ticks=2,slippage_ticks_per_side=0,entry_latency_ms=0,exit_latency_ms=0,commission_fees_round_trip=1,size=2))
    result=simulate(p,times,[0],config)
    t=result['trades'][0]
    assert t['entry_price']==p[1]['best_ask']
    assert t['gross']==pytest.approx((t['exit_price']-t['entry_price'])/.25*.5*2)
    assert t['net']==t['gross']-2 and not t['ambiguous']
    assert simulate(p,times,[0],config,harsh=True)['metrics']['net_expectancy']<result['metrics']['net_expectancy']


def test_sampled_targets_are_ambiguous_and_limit_touches_never_assume_fill():
    p=points(100)
    for i,q in enumerate(p):q['best_bid']+=i*.25;q['best_ask']+=i*.25
    times=[v['time'] for v in p]
    config=Study(execution=Execution(direction='long',stop_ticks=1,target_ticks=1,entry_latency_ms=0,exit_latency_ms=0,slippage_ticks_per_side=0))
    t=simulate(p,times,[0],config)['trades'][0]
    assert t['ambiguous'] and t['gross']<=-.5
    assert t['observed_sampled_gross']>0
    config.execution.ambiguity='exclude'
    assert simulate(p,times,[0],config)['trades'][0]['status']=='unresolved'
    config.execution.order='limit_unresolved'
    assert simulate(p,times,[0],config)['trades'][0]['reason']=='limit_queue_unavailable'


def test_chronological_splits_and_session_uncertainty():
    with pytest.raises(ValueError):Study(validation_session='2026-11-02',test_session='2026-11-01')
    assert cluster_interval([dict(session='one',net=1)]*1000,'net')['status']=='insufficient_sessions'
    data=[dict(session=str(s),net=v) for s in range(5) for v in (-1,1,2)]
    assert cluster_interval(data,'net')==cluster_interval(data,'net')


def test_raw_extrema_resolve_single_target_but_both_barriers_remain_ambiguous():
    p=points(100)
    for i,q in enumerate(p):
        q['best_bid']+=i*.25;q['best_ask']+=i*.25
        q['execution_interval']=dict(valid=True,after_source_index=i,bid_low=q['best_bid'],bid_high=q['best_bid'],ask_low=q['best_ask'],ask_high=q['best_ask'])
    times=[v['time'] for v in p]
    config=Study(execution=Execution(direction='long',stop_ticks=1,target_ticks=1,entry_latency_ms=0,exit_latency_ms=0,slippage_ticks_per_side=0,commission_fees_round_trip=0))
    t=simulate(p,times,[0],config)['trades'][0]
    assert not t['ambiguous'] and t['gross']>0
    p[3]['execution_interval']['bid_low']=99
    t=simulate(p,times,[0],config)['trades'][0]
    assert t['ambiguous'] and t['gross']<=-.5
    p[2]['execution_interval']['valid']=False
    assert simulate(p,times,[0],config)['trades'][0]['reason']=='invalid_intra_sample_execution_path'


def test_extrema_capture_a_brief_move_missed_by_the_sampled_final_state():
    book=BookTotals();book.apply(event(0));book.apply(event(1,typ=1,price=20001))
    book.apply(event(250));book.apply(event(300,typ=9,price=20000.5))
    book.apply(event(400,typ=9,price=20000))
    row=book.apply(event(500))
    assert row['best_bid']==20000
    assert row['execution_interval']['bid_high']==20000.5
    assert row['execution_interval']['after_source_index']==251


def test_backfill_is_idempotent_and_cached_features_are_owner_scoped(tmp_path,monkeypatch):
    from tools import backfill_depth_features as backfill
    monkeypatch.setattr(backfill,'RESERVE',0)
    record(tmp_path/'depth-recordings-v1')
    first=backfill.backfill(tmp_path,OWNER,BASE.isoformat(),(BASE+timedelta(seconds=49)).isoformat())
    second=backfill.backfill(tmp_path,OWNER,BASE.isoformat(),(BASE+timedelta(seconds=49)).isoformat())
    assert first['created'] and not second['created'] and second['incremental_bytes']==0
    result=read_research(tmp_path/'depth-recordings-v1',dict(owner_hash=OWNER,start=BASE.isoformat(),end=(BASE+timedelta(seconds=49)).isoformat(),scope='1'))
    assert result['derived_cache_id']==first['data_id'] and 'scopes' not in result['points'][0]
    monkeypatch.setattr(backfill,'DERIVED_BUDGET',1)
    with pytest.raises(ValueError,match='storage limit'):backfill.backfill(tmp_path,OWNER,BASE.isoformat(),(BASE+timedelta(seconds=48)).isoformat())


@pytest.fixture
def local_store(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'cache_root',lambda:tmp_path)
    monkeypatch.setattr(store,'RESERVE',0)
    return tmp_path


def await_job(identity,account=1):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        result=store.get_job('owner',account,identity,include_result=True)
        if result['state'] not in {'queued','running','cancelling'}:return result
        time.sleep(.01)
    pytest.fail('Job did not finish')


def fixture_batch():
    return dict(points=points(240),frames=[],sources=[],data_id='a'*64,start=BASE.isoformat(),end=(BASE+timedelta(seconds=60)).isoformat(),
                feature_version='fixture-only',scope='10')


def test_local_job_storage_isolation_csv_and_immutable_experiment(local_store,monkeypatch):
    monkeypatch.setattr(store,'source_read',lambda *a,**k:fixture_batch())
    created=store.submit('owner',1,request(kind='study'))
    job=await_job(created['id']);assert job['state']=='complete'
    with pytest.raises(FileNotFoundError):store.get_job('other',1,job['id'])
    with pytest.raises(FileNotFoundError):store.get_job('owner',2,job['id'])
    with pytest.raises(ValueError):store.get_job('owner',1,'../outside')
    exp=store.save_experiment('owner',1,ExperimentInput(job_id=job['id'],hypothesis='Synthetic test hypothesis',freeze=True))
    assert exp['version']==1 and exp['data_versions']==['a'*64]
    with pytest.raises(ValueError,match='immutable'):
        store.write(store.path_for('owner',1,'experiments',exp['id']),exp,immutable=True)
    with pytest.raises(ValueError,match='after'):
        store.submit('owner',1,request(kind='study',experiment_id=exp['id'],forward=True))
    exported=list(csv.reader(io.StringIO(store.export_result('owner',1,job['id']))))
    assert exported[0][0]=='research_only'
    result_path=store.path_for('owner',1,'results',job['id'],'.json.gz');result_path.write_bytes(b'broken')
    with pytest.raises(ValueError,match='checksum'):store.get_job('owner',1,job['id'],include_result=True)


def test_storage_refusal_and_restart_status(local_store,monkeypatch):
    monkeypatch.setattr(store,'BUDGET',1)
    with pytest.raises(ValueError,match='storage refused'):
        store.submit('owner',1,request())
    monkeypatch.setattr(store,'BUDGET',512*1024**2)
    path=store.path_for('owner',1,'jobs','a'*32)
    store.write(path,dict(id='a'*32,state='running',instance='previous_process'))
    assert store.get_job('owner',1,'a'*32)['state']=='interrupted'


def test_cancellation_and_one_job_per_account(local_store,monkeypatch):
    entered=Event()
    def blocked(user,query,cancel):
        entered.set()
        if cancel.wait(3):raise InterruptedError()
        return fixture_batch()
    monkeypatch.setattr(store,'source_read',blocked)
    j=store.submit('owner',1,request());assert entered.wait(2)
    with pytest.raises(RuntimeError):store.submit('owner',1,request())
    store.cancel_job('owner',1,j['id'])
    assert await_job(j['id'])['state']=='cancelled'
    assert not store.path_for('owner',1,'results',j['id'],'.json.gz').exists()


def test_http_auth_account_ownership_and_untrusted_source_rejection(local_store,monkeypatch):
    app=FastAPI();calls=[]
    class Db:
        def rollback(self):calls.append('release')
    def own(db,*,user_id,account_id):
        calls.append(('owner',user_id,account_id))
        if account_id!=1:raise HTTPException(403,'Not owned')
    install(app,get_db=lambda:Db(),authenticated_user=lambda:'owner',require_account=own)
    monkeypatch.setattr(store,'source_read',lambda user,query:calls.append('source') or {})
    async def http(path,body=None):
        messages=[]
        async def receive():return {'type':'http.request','body':json.dumps(body).encode() if body else b'', 'more_body':False}
        async def send(message):messages.append(message)
        await app(dict(type='http',asgi={'version':'3.0'},http_version='1.1',method='POST' if body else 'GET',scheme='http',
            path=path,raw_path=path.encode(),query_string=b'',root_path='',headers=[(b'content-type',b'application/json')],
            client=('127.0.0.1',123),server=('test',80)),receive,send)
        return messages[0]
    for path in ('coverage','notebook','jobs/'+'a'*32,'jobs/'+'a'*32+'/export'):
        assert asyncio.run(http('/api/accounts/2/microstructure/'+path))['status']==403
    assert 'source' not in calls
    response=asyncio.run(http('/api/accounts/1/microstructure/coverage'))
    assert response['status']==200 and (b'cache-control',b'no-store') in response['headers']
    assert calls[-2:]==['release','source']
    payload=request().model_dump(mode='json');payload['owner_id']='other';payload['ssh_target']='attacker'
    assert asyncio.run(http('/api/accounts/1/microstructure/jobs',payload))['status']==422


def test_research_backup_restore_preserves_versions_and_results(local_store,monkeypatch,tmp_path):
    from tools.backup_market_cache import backup,verify
    monkeypatch.setattr(store,'source_read',lambda *a,**k:fixture_batch())
    j=await_job(store.submit('owner',1,request(kind='study'))['id'])
    store.save_experiment('owner',1,ExperimentInput(job_id=j['id'],hypothesis='Synthetic backup check'))
    target=tmp_path.parent/(tmp_path.name+'-backup')
    receipt=backup(local_store,target)
    assert any('experiments/' in name for name in receipt['files'])
    assert verify(target)['files']==receipt['files']
