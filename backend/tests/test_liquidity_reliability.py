from concurrent.futures import Future
from datetime import datetime, timezone
import json
import time

import pytest

from tools.read_depth_history import add_overview_point, overview_points, metric_reasons, recording_health
from tools.record_market_depth import CapacityMonitor, StorageLimit


def point(t, **extra):
    return dict(time=t,index=int(t*1000),contract_id='CON.F.US.MNQ.Z26',capture_id='run',generation=1,
                bid_contracts=30,ask_contracts=10,bid_levels=10,ask_levels=10,mid_price=100,
                imbalance=.5,quality_flags=[],**extra)


def test_downsampling_keeps_spikes_and_does_not_spread_an_unrelated_flag():
    buckets, prior = {}, {}
    rows = [point(0), point(1), point(2),point(3),point(4)]
    rows[1].update(bid_contracts=900)
    rows[2].update(mid_price=None, quality_flags=['ask_provider_time_unreliable'])
    for p in rows: add_overview_point(buckets,prior,p)
    reduced = overview_points(buckets)
    assert any(p['bid_contracts']==900 for p in reduced)
    assert reduced[-1]['quality_flags']==[]
    assert not metric_reasons(rows[2],'bid_contracts')
    assert metric_reasons(rows[2],'mid_price')
    assert rows[0]['continuity']['bid_contracts']==rows[4]['continuity']['bid_contracts']
    assert rows[0]['continuity']['mid_price']!=rows[4]['continuity']['mid_price']


def test_omitted_stale_side_and_missing_interval_remain_boundaries():
    buckets, prior = {}, {}
    rows=[point(t) for t in [0,1,2,3,4,5,10,11]]
    rows[2]['quality_flags']=['bid_stale']
    for p in rows:add_overview_point(buckets,prior,p)
    assert rows[0]['continuity']['bid_contracts']!=rows[3]['continuity']['bid_contracts']
    assert rows[0]['continuity']['ask_contracts']==rows[3]['continuity']['ask_contracts']
    assert 'missing_observations' in rows[-1]['gap_before']['bid_contracts']['reasons']
    assert rows[-1]['gap_before']['bid_contracts']['seconds']==5


def test_slow_capacity_scan_never_waits_in_feed_loop_and_growth_is_charged(tmp_path,monkeypatch):
    import tools.record_market_depth as service
    monkeypatch.setattr(service,'inventory',lambda *a,**kw:dict(bytes=0,free_bytes=10**12,blocked_reason=None))
    config=dict(max_bytes=64*1024**2,reserve_bytes=0,review_after_days=30)
    monitor=CapacityMonitor(tmp_path,config)
    pending=Future();monitor.pending=pending
    began=time.monotonic()
    assert monitor.check(1024)['uncompressed_growth_allowance']==1024
    assert time.monotonic()-began < .1
    assert monitor.check(64*1024**2)['blocked_reason']=='recording_budget_reached'
    pending.set_exception(OSError('no private error text in health'))
    # A failed walk is reported and retried; it no longer ends the recorder.
    value=monitor.check()
    assert value['scan_failures']==1 and value['scan_error']=='OSError' and value['blocked_reason'] is None
    assert 'private' not in json.dumps(value)
    monitor.close()


def test_health_is_owner_scoped_and_stale_status_is_visible(tmp_path):
    root=tmp_path/'totals';root.mkdir()
    (tmp_path/'recording-health.json').write_text(json.dumps(dict(owner_hash='a'*64,at='2026-01-01T00:00:00Z',state='recording')))
    assert recording_health(root,'b'*64) is None
    value=recording_health(root,'a'*64)
    assert value['stale'] and 'owner_hash' not in value


def test_inventory_tolerates_sealing_race_but_bounds_retries(tmp_path,monkeypatch):
    import tools.record_market_depth as service
    calls=[]
    def scan(*args,**kwargs):
        calls.append(1)
        if len(calls)<3:raise FileNotFoundError('partial segment was sealed')
        return dict(bytes=0,free_bytes=10**12,blocked_reason=None)
    monkeypatch.setattr(service,'inventory',scan)
    monitor=CapacityMonitor(tmp_path,dict(max_bytes=64*1024**2,reserve_bytes=0,review_after_days=30))
    assert len(calls)==3 and monitor.check()['blocked_reason'] is None
    monitor.close()
