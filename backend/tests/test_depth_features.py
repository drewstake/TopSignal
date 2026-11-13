from copy import deepcopy
from datetime import datetime, timedelta, timezone
import ast
import json
from pathlib import Path
import pytest
from tools.depth_recording import SegmentRecorder
from tools.depth_totals import BookTotals
from tools.read_depth_research import read_research

BASE = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)
OWNER = 'a'*64
CONTRACT = 'CON.F.US.MNQ.Z26'

def event(ms, typ=2, price=20000, volume=10, kind='depth', **fields):
    at = (BASE+timedelta(milliseconds=ms)).isoformat()
    return dict(contract_id=CONTRACT, kind=kind, payload=dict(type=typ, price=price, volume=volume, timestamp=at, **fields),
                received_at=at, provider_at=at, monotonic_ns=ms*1000000, capture_id='a'*32, index=ms+1)



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
    with pytest.raises(ValueError,match='Derived feature budget reached'):backfill.backfill(tmp_path,OWNER,BASE.isoformat(),(BASE+timedelta(seconds=48)).isoformat())
