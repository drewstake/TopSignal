from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///:memory:')
from tools.depth_recording import SegmentRecorder
from tools.read_session_flow import read_session_flow, summarize
from app.services import session_order_flow as service

OWNER = 'a'*64
CONTRACT = 'CON.F.US.MNQ.Z26'
BASE = datetime(2026, 11, 3, 23, tzinfo=timezone.utc)


def write(root, rows, *, owner=OWNER, contract=CONTRACT, live=False, close=True):
    recorder = SegmentRecorder(root, owner, data_live=live)
    for minute, kind, payload in rows:
        at = (BASE+timedelta(minutes=minute)).isoformat()
        recorder.append(contract, kind, dict(timestamp=at, **payload), received_at=at)
    if close:
        recorder.close()
    return recorder


def read(root, **kwargs):
    return read_session_flow(root, dict(owner_hash=OWNER, contract_id=CONTRACT, data_live=False),
                             now=BASE+timedelta(minutes=31), **kwargs)


def test_session_and_rolling_volume_use_market_trades_only(tmp_path):
    write(tmp_path, [(-1, 'trade', dict(type=1, price=100, volume=999)),
        (0, 'quote', dict(bestBid=99, bestAsk=100)),
        (1, 'trade', dict(type=1, price=99, volume=60)),
        (20, 'trade', dict(type=0, price=100, volume=20)),
        (26, 'trade', dict(type=0, price=100, volume=10)),
        (27, 'trade', dict(type=7, price=100, volume=5)),
        (28, 'depth', dict(type=1, price=100, volume=99999)),
        (29, 'quote', dict(bestBid=99, bestAsk=100, volume=99999)),
        (30.1, 'quote', dict(bestBid=99, bestAsk=100))])
    result = read(tmp_path)
    assert result['session']['buy'] == 30
    assert result['session']['sell'] == 60
    assert result['session']['unknown'] == 5
    assert result['session']['delta'] == -30
    assert result['windows']['15']['buy'] == 30
    assert result['windows']['15']['sell'] == 0
    assert result['windows']['5']['buy'] == 10
    assert result['session']['buy_percent'] == pytest.approx(100/3)
    assert result['session']['classified_percent'] == pytest.approx(9000/95)
    assert result['through'] == (BASE+timedelta(minutes=30)).isoformat()
    assert result['classification']['aggressor_verified'] is False
    assert result['bias'] == result['absorption'] == 'unavailable'
    assert 'owner_hash' not in json.dumps(result)


def test_owner_delivery_subscription_and_unsealed_isolation(tmp_path):
    rows = [(1, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', dict())]
    write(tmp_path, rows, owner='b'*64)
    write(tmp_path, rows, contract='CON.F.US.MNQ.H27')
    write(tmp_path, rows, live=True)
    partial = write(tmp_path, rows, close=False)
    result = read(tmp_path)
    assert result['session']['buy'] == 0
    assert result['session']['buy_percent'] is None
    assert result['coverage']['stale'] is True
    partial.close()


def test_bad_types_times_and_quantities_remain_unknown():
    def event(payload, provider=BASE.isoformat()):
        return dict(kind='trade', payload=payload, contract_id=CONTRACT, data_live=False, source='projectx',
                    received_at=BASE.isoformat(), provider_at=provider)
    rows = [event(dict(type=False, price=100, volume=3)), event(dict(type='0', price=100, volume=4)),
            event(dict(type=0, price=100, volume=7), None),
            event(dict(type=1, price=100, volume=8), (BASE-timedelta(seconds=3)).isoformat()),
            event(dict(type=0, price=100, volume=9), (BASE+timedelta(seconds=1)).isoformat()),
            event(dict(type=1, price=100, volume=-2)), event(dict(type=0, price=float('nan'), volume=1))]
    rows.append(dict(event(dict(type=0, price=100, volume=999)), kind='GatewayUserTrade'))
    result = summarize(rows, CONTRACT, False)['buckets'][BASE.isoformat()]
    assert result['buy'] == result['sell'] == 0
    assert result['unknown'] == 31
    assert result['invalid'] == 2 and result['bad_time'] == 3


def test_incremental_build_and_cache_reuse_without_recounting(tmp_path, monkeypatch):
    write(tmp_path, [(1, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', dict())])
    first = read(tmp_path, max_read_bytes=0)
    assert first['coverage']['status'] == 'building'
    assert first['session']['buy_percent'] is None
    second = read(tmp_path)
    assert second['session']['buy'] == 10
    import tools.read_session_flow as reader
    monkeypatch.setattr(reader, 'verified_rows', lambda *a, **kw: pytest.fail('Cached immutable segment reread'))
    third = read(tmp_path)
    assert third['session'] == second['session']
    assert third['coverage']['pending_segments'] == 0


def test_cached_data_not_used_after_source_tampering(tmp_path):
    write(tmp_path, [(1, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', dict())])
    assert read(tmp_path)['session']['buy'] == 10
    next(tmp_path.glob('*/*/*.ndjson.gz')).write_bytes(b'tampered')
    result = read(tmp_path)
    assert result['session']['buy'] == 0
    assert result['coverage']['reasons']['unreadable_segment'] == 1


def test_overlapping_captures_excluded_and_coverage_disclosed(tmp_path):
    rows = [(1, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', dict())]
    write(tmp_path, rows)
    write(tmp_path, rows)
    result = read(tmp_path)
    assert result['session']['buy'] == 10
    assert result['coverage']['reasons']['overlapping_recordings'] == 1


def test_other_delivery_cannot_hide_overlapping_requested_contract(tmp_path):
    write(tmp_path, [(0, 'trade', dict(type=0, price=100, volume=999)), (30.2, 'quote', dict())],
          contract='CON.F.US.MNQ.H27')
    write(tmp_path, [(1, 'trade', dict(type=1, price=99, volume=10)), (30.1, 'quote', dict())])
    result = read(tmp_path)
    assert result['session']['sell'] == 10
    assert result['session']['buy'] == 0
    assert 'overlapping_recordings' not in result['coverage']['reasons']


def test_new_session_does_not_reuse_previous_session_totals(tmp_path):
    write(tmp_path, [(1, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', dict())])
    read(tmp_path)
    result = read_session_flow(tmp_path, dict(owner_hash=OWNER, contract_id=CONTRACT), now=BASE+timedelta(days=1, minutes=1))
    assert result['session']['buy_percent'] is None
    assert result['coverage']['reasons']['session_start_missing'] == 1


@pytest.mark.parametrize('contract', ['../escape', 'CON.F.US.MNQ.Z26;whoami', 'CON.F.US.NQ.Z26'])
def test_reader_rejects_invalid_scope(tmp_path, contract):
    with pytest.raises(ValueError):
        read_session_flow(tmp_path, dict(owner_hash=OWNER, contract_id=contract))


def test_api_checks_ownership_and_releases_transaction(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'get_authenticated_user_id', lambda: 'owner')
    def deny(*a, **kw):
        raise HTTPException(403, 'Not owned')
    monkeypatch.setattr(main, '_require_owned_projectx_account', deny)
    monkeypatch.setattr(service, 'account_session_flow', lambda **kw: pytest.fail('Unauthorized read'))
    with pytest.raises(HTTPException):
        main.get_session_order_flow(1, Response(), CONTRACT, False, db=object())
    calls = []
    monkeypatch.setattr(main, '_require_owned_projectx_account', lambda *a, **kw: calls.append('owned'))
    monkeypatch.setattr(service, 'account_session_flow', lambda **kw: calls.append(kw) or {})
    response = Response()
    main.get_session_order_flow(1, response, CONTRACT, False, db=SimpleNamespace(rollback=lambda: calls.append('rollback')))
    assert calls == ['owned', 'rollback', dict(user_id='owner', contract_id=CONTRACT, data_live=False)]
    assert response.headers['Cache-Control'] == 'no-store'


def test_remote_source_requires_explicit_installation_and_checks_scope(tmp_path, monkeypatch):
    config = tmp_path/'source.json'
    monkeypatch.setattr(service, 'history_source_path', lambda: config)
    config.write_text('{}')
    with pytest.raises(RuntimeError, match='not installed'):
        service.account_session_flow(user_id='owner', contract_id=CONTRACT)
    config.write_text(json.dumps(dict(session_flow_reader_path='C:/tools/flow.py', research_recording_root='D:/data',
        python_path='C:/python/python.exe', ssh_target='reader@host', identity_file='key')))
    calls = []
    def remote(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(schema='session-provider-flow-v1', contract_id='CON.F.US.MNQ.H27', data_live=False)))
    monkeypatch.setattr(service.subprocess, 'run', remote)
    with pytest.raises(RuntimeError, match='different market scope'):
        service.account_session_flow(user_id='owner', contract_id=CONTRACT)
    assert json.loads(calls[0][1]['input'])['owner_hash'] == service.scope_hash('owner')
    assert 'StrictHostKeyChecking=yes' in calls[0][0]
    assert not calls[0][1].get('shell')


def test_cold_inventory_deadline_preserves_progress_and_warm_refresh_skips_old_scan(tmp_path, monkeypatch):
    import tools.read_session_flow as reader
    import tools.depth_heatmap as heatmap
    for minute in (1, 2, 3):
        write(tmp_path, [(minute, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', {})])
    reader._inventory = None
    clock, opened = [0], []
    original = heatmap.read_manifest
    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        opened.append(args[0])
        clock[0] += 1
        return result
    monkeypatch.setattr(reader.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(heatmap, 'read_manifest', slow)
    with pytest.raises(RuntimeError, match='preparing'):
        read(tmp_path, max_seconds=1)
    assert len(opened) == 1
    read(tmp_path, max_seconds=100)
    assert len(opened) == 3
    monkeypatch.setattr(heatmap, 'read_manifest', lambda *a, **k: pytest.fail('Old inventory reread'))
    assert read(tmp_path)['coverage']['pending_segments'] == 0


def test_cached_inventory_rechecks_selected_owner_and_child_integrity(tmp_path):
    write(tmp_path, [(1, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', {})])
    assert read(tmp_path)['session']['buy'] == 10
    marker = next(tmp_path.glob('*/*/*.segment.json'))
    manifest = json.loads(marker.read_text())
    manifest['owner_hash'] = 'b'*64
    marker.write_text(json.dumps(manifest))
    assert read(tmp_path)['session']['buy'] == 0
    assert read(tmp_path)['session']['buy'] == 0


def test_read_deadline_stops_optional_processing_and_exposes_pending(tmp_path, monkeypatch):
    import tools.read_session_flow as reader
    for minute in (1, 2, 3):
        write(tmp_path, [(minute, 'trade', dict(type=0, price=100, volume=10)), (30.1, 'quote', {})])
    clock = [0]
    original = reader.verified_rows
    def slow(*args, **kwargs):
        clock[0] += 2
        return original(*args, **kwargs)
    monkeypatch.setattr(reader.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(reader, 'verified_rows', slow)
    result = read(tmp_path, max_seconds=1)
    assert result['coverage']['status'] == 'building'
    assert result['coverage']['processed_segments'] == 1
    assert result['coverage']['pending_segments'] == 2
