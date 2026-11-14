from concurrent.futures import Future
import json
import os
import time
from types import SimpleNamespace

import pytest

from tools import depth_recording, record_market_depth


GIB = 1024**3
CONFIG = dict(max_bytes=250*GIB, reserve_bytes=10*GIB,
              review_after_days=None, segment_seconds=60)


def test_explicit_no_age_limit_preserves_old_evidence_and_storage_guards(tmp_path, monkeypatch):
    evidence = tmp_path / 'old-recording.partial'
    evidence.write_bytes(b'preserved evidence')
    old = time.time() - 90*86400
    os.utime(evidence, (old, old))
    free = [100*GIB]
    monkeypatch.setattr(depth_recording.shutil, 'disk_usage', lambda _: SimpleNamespace(free=free[0]))
    bounds = dict(max_bytes=250*GIB, reserve_bytes=10*GIB, max_age_days=None)
    result = depth_recording.inventory(tmp_path, **bounds)
    assert result['blocked_reason'] is None and result['review_after_days'] is None
    assert not result['automatic_deletion']
    assert depth_recording.inventory(tmp_path, max_bytes=250*GIB, reserve_bytes=10*GIB)['blocked_reason'] == 'retention_review_due'
    assert depth_recording.inventory(tmp_path, **(bounds | dict(max_bytes=32*1024**2)))['blocked_reason'] == 'recording_budget_reached'
    free[0] = 10*GIB
    assert depth_recording.inventory(tmp_path, **bounds)['blocked_reason'] == 'disk_free_reserve'
    assert evidence.read_bytes() == b'preserved evidence'


def test_restart_applies_new_policy_to_cached_inventory_without_waiving_guards(tmp_path, monkeypatch):
    root = tmp_path / 'recordings'
    root.mkdir()
    cache = tmp_path / 'capacity-cache.json'
    cache.write_text(json.dumps(dict(at=time.time(), root=str(root), value=dict(
        bytes=9*GIB, budget_bytes=20*GIB, reserve_bytes=10*GIB, review_after_days=30,
        oldest_mtime=time.time()-90*86400, blocked_reason='retention_review_due'))))
    free, clock = [100*GIB], [1000.]
    monkeypatch.setattr(record_market_depth.shutil, 'disk_usage', lambda _: SimpleNamespace(free=free[0]))
    monitor = record_market_depth.CapacityMonitor(root, CONFIG, cache=cache, clock=lambda: clock[0])
    # The next directory walk can be slow; the cached values must use the new
    # policy immediately while continuing to charge all new writes.
    monitor.pending = Future()
    try:
        result = monitor.check()
        assert result['blocked_reason'] is None
        assert result['budget_bytes'] == 250*GIB and result['review_after_days'] is None
        assert result['reserve_bytes'] == 10*GIB
        assert monitor.check(241*GIB)['blocked_reason'] == 'recording_budget_reached'
        free[0] = 10*GIB
        assert monitor.check()['blocked_reason'] == 'disk_free_reserve'
        free[0] = 100*GIB
        clock[0] += monitor.stale_after + 1
        assert monitor.check()['blocked_reason'] == 'capacity_check_stale'
    finally:
        monitor.close()


@pytest.mark.parametrize('review', [None, 1, 30])
def test_config_accepts_explicit_age_policy_and_larger_finite_budget(review):
    record_market_depth.validate_bounds(CONFIG | dict(review_after_days=review))


@pytest.mark.parametrize('overrides', [
    dict(review_after_days=0), dict(review_after_days=-1), dict(review_after_days=31),
    dict(review_after_days=False), dict(review_after_days='none'),
    dict(max_bytes=None), dict(max_bytes=251*GIB), dict(max_bytes=0),
    dict(reserve_bytes=0), dict(reserve_bytes=5*GIB-1),
])
def test_invalid_policy_never_disables_a_guard_implicitly(overrides):
    with pytest.raises(ValueError, match='Invalid bounds'):
        record_market_depth.validate_bounds(CONFIG | overrides)
