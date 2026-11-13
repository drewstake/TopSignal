from array import array
import base64
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///:memory:')
from tools import depth_heatmap as heatmap
from tools.depth_heatmap import Deriver, read_heatmap, session_start_ms, StorageReview
from tools.depth_recording import SegmentRecorder
from app.services import liquidity_heatmap_history as service

OWNER = 'a' * 64
OTHER = 'b' * 64
CONTRACT = 'CON.F.US.MNQ.Z26'
# Wednesday 09:30 New York, inside the session that opened Tuesday 18:00.
BASE = datetime(2026, 11, 4, 14, 30, tzinfo=timezone.utc)
BASE_MS = int(BASE.timestamp() * 1000)


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def depth(side, price, volume, lag_ms=100, **extra):
    return ('depth', dict(type=2 if side == 'bid' else 1, price=price, volume=volume, lag_ms=lag_ms, **extra))


def write(root, events, *, owner=OWNER, contract=CONTRACT, live=False):
    recorder = SegmentRecorder(root / 'depth-recordings-v1', owner, data_live=live)
    for seconds, kind, payload in events:
        if kind == 'SEAL':
            recorder.seal()
            continue
        payload = dict(payload)
        lag = payload.pop('lag_ms', 100)
        if kind in {'depth', 'trade', 'quote'}:
            payload.setdefault('timestamp', (at(seconds) - timedelta(milliseconds=lag)).isoformat())
        recorder.append(payload.pop('contract', contract), kind, payload, received_at=at(seconds).isoformat(),
                        monotonic_ns=int((seconds + 10_000) * 1e9))
    recorder.close()
    return recorder


def derive(root, *, now=BASE + timedelta(hours=1), **kwargs):
    deriver = Deriver(root / 'depth-recordings-v1', root / 'depth-heatmap-v1', free_reserve=0,
                      clock=lambda: int(now.timestamp() * 1000), **kwargs)
    try:
        return deriver.run_once()
    finally:
        deriver.close()


def read(root, start=0, end=60, *, tier=0, owner=OWNER, contract=CONTRACT, live=False, now=BASE + timedelta(hours=1)):
    return read_heatmap(root / 'depth-heatmap-v1', dict(owner_hash=owner, contract_id=contract, data_live=live,
                        start_ms=BASE_MS + int(start * 1000), end_ms=BASE_MS + int(end * 1000), tier=tier),
                        now_ms=int(now.timestamp() * 1000))


def column(result, group, name, typecode):
    values = array(typecode)
    values.frombytes(base64.b64decode(result[group][name]))
    return list(values)


def frames(result):
    times = column(result, 'frames', 'time', 'I')
    counts = column(result, 'frames', 'level_count', 'H')
    ticks, qty = column(result, 'frames', 'tick', 'i'), column(result, 'frames', 'qty', 'f')
    out, offset = {}, 0
    for t, n in zip(times, counts):
        out[(result['time_origin_ms'] + t - BASE_MS) / 1000] = dict(zip(ticks[offset:offset + n], qty[offset:offset + n]))
        offset += n
    return out


BOOK = [(0, 'connect', {}), (0.1, *depth('bid', 100.0, 5)), (0.1, *depth('bid', 99.75, 7)),
        (0.1, *depth('ask', 100.25, 3)), (0.1, *depth('ask', 100.5, 9))]


def test_frames_match_live_sampler_semantics_and_never_fill_silence(tmp_path):
    # Provider trade times 1.26/1.35/1.37 s share one 250 ms group per price and side.
    write(tmp_path, BOOK + [(1.2, *depth('bid', 100.0, 6)), (1.36, 'trade', dict(type=0, price=100.25, volume=2)),
                            (1.45, 'trade', dict(type=0, price=100.25, volume=1)), (1.47, 'trade', dict(type=1, price=100.0, volume=4)),
                            (6.0, *depth('ask', 100.25, 4))])
    assert derive(tmp_path)['processed'] == 1
    result = read(tmp_path, 0, 7)
    got = frames(result)
    # Ticks are price / 0.25; the 0.5 s slot shows the book after every event received by then.
    assert got[0.5] == {400: 5, 399: 7, 401: 3, 402: 9}
    assert got[1.5][400] == 6
    # Depth older than two seconds of provider time is a gap, not a frozen book.
    assert 4.0 not in got and 5.5 not in got
    stale = [g for g in result['gaps'] if g[2] == 'stale']
    assert stale and stale[0][0] <= BASE_MS + 4000 and stale[0][1] >= BASE_MS + 6000
    assert column(result, 'trades', 'volume', 'f') == [3.0, 4.0]
    assert column(result, 'trades', 'side', 'B') == [1, 2]
    assert column(result, 'trades', 'prints', 'I') == [2, 1]
    assert column(result, 'trades', 'tick', 'i') == [401, 400]
    assert result['tick_size'] == 0.25 and result['level_limit'] == 80
    assert result['coverage'] and result['history_through_ms'] >= BASE_MS + 6000
    assert 'owner_hash' not in json.dumps(result)


def test_disconnect_crossed_and_reset_are_explicit_gaps(tmp_path):
    write(tmp_path, BOOK + [(1.1, 'disconnect', {}), (3.0, 'connect', {}), (3.2, *depth('bid', 100.0, 5)),
                            (3.2, *depth('ask', 100.0, 5)), (5.2, *depth('ask', 100.25, 3)), (5.3, *depth('bid', 100.0, 0)),
                            (5.3, *depth('bid', 99.75, 2)), (6.6, 'quote', dict(bestBid=99.75, bestAsk=100.0))])
    derive(tmp_path)
    result = read(tmp_path, 0, 7)
    reasons = {g[2] for g in result['gaps']}
    assert {'disconnected', 'reset', 'crossed'} <= reasons
    got = frames(result)
    assert 2.0 not in got and 3.5 not in got
    # After reconnect only post-reconnect observations exist; nothing carries across.
    assert got[6.0] == {399: 2, 400: 5, 401: 3}  # The pre-disconnect 100.50 ask is gone.


def test_owner_contract_subscription_and_unsealed_isolation(tmp_path):
    end = [(2.0, 'quote', dict(bestBid=100.0, bestAsk=100.25))]
    write(tmp_path, BOOK + [(1.0, *depth('bid', 100.0, 8))] + end)
    write(tmp_path, BOOK + [(1.0, *depth('bid', 100.0, 50))] + end, owner=OTHER)
    write(tmp_path, BOOK + [(1.0, *depth('bid', 100.0, 60))] + end, live=True)
    write(tmp_path, [(seconds, kind, dict(payload, contract='CON.F.US.MNQ.H27')) for seconds, kind, payload in BOOK + end])
    open_recorder = SegmentRecorder(tmp_path / 'depth-recordings-v1', OWNER)
    open_recorder.append(CONTRACT, 'depth', dict(type=2, price=100.0, volume=999, timestamp=at(0.5).isoformat()),
                         received_at=at(0.5).isoformat())
    open_recorder.flush()  # Active .partial file: must never be read.
    derive(tmp_path)
    assert frames(read(tmp_path, 0, 2))[1.5][400] == 8
    assert frames(read(tmp_path, 0, 2, owner=OTHER))[1.5][400] == 50
    assert frames(read(tmp_path, 0, 2, live=True))[1.5][400] == 60
    assert frames(read(tmp_path, 0, 2, contract='CON.F.US.MNQ.H27'))[0.5][400] == 5
    assert read(tmp_path, 0, 2, owner='c' * 64)['frames'] is None
    open_recorder.close()


def test_checkpoint_resume_is_identical_to_continuous_processing(tmp_path):
    events = BOOK + [(1.0 + i * 0.3, *depth('bid' if i % 2 else 'ask', 100.0 - 0.25 * (i % 5) if i % 2 else 100.25 + 0.25 * (i % 5), 1 + i))
                     for i in range(40)]
    split = events[:20] + [(0, 'SEAL', {})] + events[20:]
    write(tmp_path / 'one', events)
    write(tmp_path / 'two', split)
    derive(tmp_path / 'one')
    root = tmp_path / 'two'
    deriver = Deriver(root / 'depth-recordings-v1', root / 'depth-heatmap-v1', free_reserve=0,
                      clock=lambda: int((BASE + timedelta(hours=1)).timestamp() * 1000))
    entries = deriver.inventory.scan()
    first = min(entries, key=lambda e: e['first_index'])
    deriver.lease(OWNER)
    deriver.engines[(OWNER, first['capture'])] = heatmap.HeatmapEngine(first['capture'])
    deriver._process(OWNER, first['capture'], first, write=True)
    deriver.close()  # Simulated backend restart: only the SQLite checkpoint survives.
    derive(root)
    assert frames(read(tmp_path / 'one', 0, 15)) == frames(read(root, 0, 15))
    assert not read(root, 0, 15)['notes']


def _mark_derived(root, count):
    deriver = Deriver(root / 'depth-recordings-v1', root / 'depth-heatmap-v1', free_reserve=0,
                      clock=lambda: int((BASE + timedelta(hours=2)).timestamp() * 1000))
    entries = sorted(deriver.inventory.scan(), key=lambda e: e['first_index'])
    deriver.lease(OWNER)
    state = deriver._state(OWNER)
    # Earlier segments are already derived but their checkpoint was lost.
    for entry in entries[:count]:
        state.execute('INSERT INTO segments VALUES (?,?,?,?,?,?)', (entry['key'], entry['capture'], entry['first_index'],
                                                                    entry['last_ms'], 'derived', 'x'))
    state.commit()
    deriver.run_once()
    deriver.close()


def test_lost_checkpoint_near_capture_start_replays_exactly(tmp_path):
    write(tmp_path, BOOK + [(0, 'SEAL', {}), (1.0, *depth('bid', 100.0, 4)), (0, 'SEAL', {}), (2.0, *depth('ask', 100.25, 6)),
                            (3.0, 'quote', {})])
    _mark_derived(tmp_path, 2)
    result = read(tmp_path, 0, 5, now=BASE + timedelta(hours=2))
    assert result['notes'] == []
    assert frames(result)[2.5] == {400: 4, 399: 7, 401: 6, 402: 9}


def test_lost_checkpoint_far_from_capture_start_discloses_rebuilt_book(tmp_path):
    events = []
    for i in range(8):
        events += [(i * 400.0, *depth('bid', 100.0, 5 + i)), (i * 400.0 + 0.1, *depth('ask', 100.25, 3)),
                   (i * 400.0 + 1, 'quote', {}), (0, 'SEAL', {})]
    write(tmp_path, BOOK + [(0, 'SEAL', {})] + events)
    _mark_derived(tmp_path, 8)
    result = read(tmp_path, 7 * 400.0 - 5, 7 * 400.0 + 5, now=BASE + timedelta(hours=2))
    assert [kind for _, kind in result['notes']] == ['book_rebuilt_mid_capture']
    # Only levels observed after the rebuild are shown; nothing older is invented.
    assert frames(result)[7 * 400.0 + 0.5] == {400: 12, 401: 3}


def test_session_rollover_uses_new_session_only(tmp_path):
    close = datetime(2026, 11, 4, 21, 59, 58, tzinfo=timezone.utc)  # 16:59:58 New York
    reopen = datetime(2026, 11, 4, 23, 0, 1, tzinfo=timezone.utc)   # 18:00:01 New York
    recorder = SegmentRecorder(tmp_path / 'depth-recordings-v1', OWNER)
    stamp = lambda when: (when - timedelta(milliseconds=50)).isoformat()
    for when, price in ((close, 100.0), (close + timedelta(seconds=1), 100.0), (reopen, 101.0), (reopen + timedelta(seconds=1), 101.0)):
        if when == reopen:
            recorder.append(CONTRACT, 'depth', dict(type=6, price=0, volume=0, timestamp=stamp(when)), received_at=when.isoformat())
        recorder.append(CONTRACT, 'depth', dict(type=2, price=price, volume=5, timestamp=(when - timedelta(milliseconds=50)).isoformat()),
                        received_at=when.isoformat())
        recorder.append(CONTRACT, 'depth', dict(type=1, price=price + 0.25, volume=5, timestamp=(when - timedelta(milliseconds=50)).isoformat()),
                        received_at=when.isoformat())
    recorder.append(CONTRACT, 'quote', dict(timestamp=stamp(reopen + timedelta(seconds=3))),
                    received_at=(reopen + timedelta(seconds=3)).isoformat())
    recorder.close()
    later = reopen + timedelta(minutes=5)
    derive(tmp_path, now=later)
    sessions = sorted(p.name for p in (tmp_path / 'depth-heatmap-v1' / OWNER / 'sessions').iterdir() if p.suffix == '.sqlite3')
    assert '2026-11-04.sqlite3' in sessions
    query = dict(owner_hash=OWNER, contract_id=CONTRACT, data_live=False, tier=0,
                 start_ms=int((reopen - timedelta(minutes=2)).timestamp() * 1000), end_ms=int(later.timestamp() * 1000))
    result = read_heatmap(tmp_path / 'depth-heatmap-v1', query, now_ms=int(later.timestamp() * 1000))
    assert result['session']['start_ms'] == int(datetime(2026, 11, 4, 23, tzinfo=timezone.utc).timestamp() * 1000)
    assert result['range']['start_ms'] == result['session']['start_ms']
    ticks = column(result, 'frames', 'tick', 'i')
    assert ticks and set(ticks) <= {404, 405}  # Only the reopened market's prices.
    # No frames/gaps are emitted for the 17:00-18:00 halt.
    assert all(g[0] >= result['session']['start_ms'] for g in result['gaps'])


def test_coarse_tiers_average_observed_levels_and_bound_trades(tmp_path, monkeypatch):
    write(tmp_path, BOOK + [(2.0, *depth('bid', 100.0, 15))] + [(3 + i * 0.01, 'trade', dict(type=i % 2, price=100.0 + 0.25 * (i % 7), volume=1 + i % 3))
                                                              for i in range(60)] + [(4.6, 'quote', {})])
    derive(tmp_path)
    coarse = read(tmp_path, 0, 60, tier=1)
    times = column(coarse, 'frames', 'time', 'I')
    assert coarse['bucket_ms'] == 5000 and times[0] + coarse['time_origin_ms'] == BASE_MS
    level = dict(zip(column(coarse, 'frames', 'tick', 'i'), column(coarse, 'frames', 'qty', 'f')))
    valid = column(coarse, 'frames', 'valid', 'H')[0]
    assert valid == 7  # 0.5-3.5 s; 4.0 s onward is stale (no depth for over two seconds).
    assert level[400] == pytest.approx((3 * 5 + 4 * 15) / 7)  # Mean of observed samples, not the max.
    seen = dict(zip(column(coarse, 'frames', 'tick', 'i'), column(coarse, 'frames', 'seen', 'H')))
    assert seen[400] == 7 and read(tmp_path, 0, 60)['frames']['seen'] is None
    summary = read_heatmap(tmp_path / 'depth-heatmap-v1', dict(owner_hash=OWNER, contract_id=CONTRACT, data_live=False, tier=2,
                           start_ms=BASE_MS - 60_000, end_ms=BASE_MS + 600_000, summary=True), now_ms=BASE_MS + 3600_000)
    assert summary['frames']['levels'] == 0 and summary['trades']['count'] == 0
    assert column(summary, 'frames', 'best_bid', 'i') == [400]
    monkeypatch.setattr(heatmap, 'MAX_TRADES', 5)
    bounded = read(tmp_path, 0, 60, tier=0)
    assert bounded['trades']['count'] == 5 and bounded['trades_truncated'] and bounded['trades_min_volume'] >= 1


@pytest.mark.parametrize('tier,span', [(0, 11 * 60), (1, 4 * 3600), (2, 27 * 3600)])
def test_reader_rejects_unbounded_windows(tmp_path, tier, span):
    with pytest.raises(ValueError):
        read(tmp_path, 0, span, tier=tier)


@pytest.mark.parametrize('scope', [dict(owner_hash='x'), dict(contract_id='CON.F.US.ES.Z26'), dict(data_live='false'), dict(tier=3)])
def test_reader_rejects_invalid_scope(tmp_path, scope):
    query = {**dict(owner_hash=OWNER, contract_id=CONTRACT, data_live=False, start_ms=BASE_MS, end_ms=BASE_MS + 1000, tier=0), **scope}
    with pytest.raises(ValueError):
        read_heatmap(tmp_path, query)


def test_corrupt_segment_is_an_explicit_gap(tmp_path):
    write(tmp_path, BOOK + [(0, 'SEAL', {}), (1.0, *depth('bid', 100.0, 4)), (0, 'SEAL', {}), (2.0, *depth('ask', 100.25, 6)),
                            (2.1, *depth('bid', 100.0, 3)), (3.0, 'quote', {})])
    segment = sorted((tmp_path / 'depth-recordings-v1').rglob('00000001.ndjson.gz'))[0]
    segment.write_bytes(segment.read_bytes()[:-8] + b'\0' * 8)
    derive(tmp_path)
    result = read(tmp_path, 0, 5)
    assert 'unreadable_segment' in {g[2] for g in result['gaps']}
    assert [kind for _, kind in result['notes']] == ['book_rebuilt_mid_capture']
    assert frames(result)[2.5] == {400: 3, 401: 6}  # No pre-corruption levels survive.


def test_storage_cap_stops_without_deleting(tmp_path):
    write(tmp_path, BOOK)
    with pytest.raises(StorageReview):
        derive(tmp_path, max_owner_bytes=1)
    status = heatmap.deriver_status(tmp_path / 'depth-heatmap-v1', OWNER)
    assert status['state'] == 'storage_review_required'
    assert (tmp_path / 'depth-recordings-v1').exists()


def test_single_active_deriver_lease(tmp_path):
    write(tmp_path, BOOK)
    first = Deriver(tmp_path / 'depth-recordings-v1', tmp_path / 'depth-heatmap-v1', free_reserve=0, pid=1)
    second = Deriver(tmp_path / 'depth-recordings-v1', tmp_path / 'depth-heatmap-v1', free_reserve=0, pid=2)
    try:
        assert first.lease(OWNER) and not second.lease(OWNER)
    finally:
        first.close()
        second.close()


def test_session_helper_matches_existing_rule():
    from tools.read_depth_history import session_bounds
    for hours in range(0, 24 * 9, 5):
        when = datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(hours=hours)
        assert session_start_ms(int(when.timestamp() * 1000)) == int(session_bounds(when)[0].timestamp() * 1000)


def test_api_checks_ownership_before_reading_and_compresses(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'get_authenticated_user_id', lambda: 'owner')

    def deny(*a, **kw):
        raise HTTPException(403, 'Not owned')
    monkeypatch.setattr(main, '_require_owned_projectx_account', deny)
    monkeypatch.setattr(service, 'account_heatmap', lambda **kw: pytest.fail('Unauthorized read'))
    request = SimpleNamespace(headers={'accept-encoding': 'gzip, br'})
    with pytest.raises(HTTPException):
        main.get_liquidity_heatmap_history(1, request, CONTRACT, 0, 1000, 0, False, False, db=object())
    calls = []
    monkeypatch.setattr(main, '_require_owned_projectx_account', lambda *a, **kw: calls.append('owned'))
    monkeypatch.setattr(service, 'account_heatmap', lambda **kw: calls.append(kw) or json.dumps(dict(pad='x' * 9000)).encode())
    response = main.get_liquidity_heatmap_history(1, request, CONTRACT, 0, 1000, 0, False, False,
                                                  db=SimpleNamespace(rollback=lambda: calls.append('rollback')))
    assert calls[:2] == ['owned', 'rollback']
    assert calls[2] == dict(user_id='owner', contract_id=CONTRACT, data_live=False, start_ms=0, end_ms=1000, tier=0, summary=False)
    assert response.headers['Cache-Control'] == 'no-store' and response.headers['Content-Encoding'] == 'gzip'
    assert json.loads(gzip.decompress(response.body))['pad'] == 'x' * 9000
    with pytest.raises(HTTPException) as invalid:
        main.get_liquidity_heatmap_history(1, request, 'CON.F.US.ES.Z26', 0, 1000, 0, False, False, db=SimpleNamespace(rollback=lambda: None))
    assert invalid.value.status_code == 422 and invalid.value.detail['server_time_ms'] > 0
    assert 'MNQ' in invalid.value.detail['message']


def test_remote_source_requires_explicit_installation(tmp_path, monkeypatch):
    config = tmp_path / 'source.json'
    monkeypatch.setattr(service, 'history_source_path', lambda: config)
    config.write_text('{}')
    with pytest.raises(RuntimeError, match='not installed'):
        service.account_heatmap(user_id='owner', contract_id=CONTRACT, data_live=False, start_ms=0, end_ms=1000, tier=0)
    config.write_text(json.dumps(dict(heatmap_reader_path='C:/tools/depth_heatmap.py', heatmap_cache_root='D:/data/depth-heatmap-v1',
                                      python_path='C:/python/python.exe', ssh_target='reader@host', identity_file='key')))
    calls = []

    def remote(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(schema='liquidity-heatmap-history-v1', contract_id='CON.F.US.MNQ.H27',
                                                                   data_live=False, tier=0)))
    monkeypatch.setattr(service.subprocess, 'run', remote)
    with pytest.raises(RuntimeError, match='different market scope'):
        service.account_heatmap(user_id='owner2', contract_id=CONTRACT, data_live=False, start_ms=0, end_ms=1000, tier=0)
    assert json.loads(calls[0][1]['input'])['owner_hash'] == service.scope_hash('owner2')
    assert 'StrictHostKeyChecking=yes' in calls[0][0] and not calls[0][1].get('shell')


def test_supervisor_only_runs_for_local_recordings(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'history_source_path', lambda: tmp_path / 'absent.json')
    assert not service.HeatmapDeriverSupervisor(root=tmp_path / 'missing', mode='auto').enabled()
    (tmp_path / 'rec').mkdir()
    assert service.HeatmapDeriverSupervisor(root=tmp_path / 'rec', mode='auto').enabled()
    assert not service.HeatmapDeriverSupervisor(root=tmp_path / 'rec', mode='off').enabled()
    assert not service.HeatmapDeriverSupervisor(root=tmp_path / 'rec', mode='bogus').enabled()
    (tmp_path / 'absent.json').write_text('{}')
    assert not service.HeatmapDeriverSupervisor(root=tmp_path / 'rec', mode='auto').enabled()
    assert service.HeatmapDeriverSupervisor(mode='off').stop() is True


def test_heatmap_supervisor_yields_to_replay_and_recorder_pressure(monkeypatch):
    from io import BytesIO
    from app.services import topbot_study_worker as worker
    monkeypatch.setattr(worker, 'ACTIVE', {})
    monkeypatch.setattr(worker, 'recording_pressure', lambda: None)
    supervisor = service.HeatmapDeriverSupervisor(mode='off')
    pipe = BytesIO()
    supervisor.process = SimpleNamespace(stdin=pipe)
    supervisor._signal_pressure()
    worker.ACTIVE['entry_id'] = 12
    supervisor._signal_pressure()
    supervisor._signal_pressure()  # Unchanged status does not fill the pipe.
    worker.ACTIVE.clear()
    monkeypatch.setattr(worker, 'recording_pressure', lambda: {'reason': 'backlog'})
    supervisor._signal_pressure()
    monkeypatch.setattr(worker, 'recording_pressure', lambda: None)
    supervisor._signal_pressure()
    assert pipe.getvalue() == b'resume\npause\nresume\n'


def test_paused_heatmap_follower_does_not_read_archive(tmp_path, monkeypatch):
    import threading
    stop, paused = threading.Event(), threading.Event()
    paused.set()
    reads = []
    monkeypatch.setattr(heatmap.Deriver, 'run_once', lambda *a, **k: reads.append(True))
    monkeypatch.setattr(stop, 'wait', lambda seconds: stop.set())
    heatmap.follow(tmp_path/'raw', tmp_path/'derived', stop=stop, paused=paused)
    assert reads == []


def test_supervised_child_exits_when_backend_closes_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'history_source_path', lambda: tmp_path / 'absent.json')
    (tmp_path / 'rec').mkdir()
    supervisor = service.HeatmapDeriverSupervisor(root=tmp_path / 'rec', cache=tmp_path / 'cache', mode='on')
    assert supervisor.start()
    for _ in range(100):
        if supervisor.process is not None:
            break
        import time
        time.sleep(0.05)
    process = supervisor.process
    assert process is not None and process.poll() is None
    supervisor.request_stop()  # Signals only; returns immediately.
    assert process.wait(timeout=20) is not None
    assert supervisor.stop(timeout=20)
    assert process.poll() is not None


def test_out_of_range_values_become_gaps_instead_of_crash_loops(tmp_path):
    write(tmp_path, BOOK + [(0, 'SEAL', {}), (1.0, 'trade', dict(type=0, price=1e12, volume=1)), (1.1, *depth('bid', 1e15, 3)),
                            (2.0, 'trade', dict(type=0, price=100.0, volume=5e9)), (0, 'SEAL', {}), (3.0, *depth('bid', 100.0, 4)),
                            (3.1, *depth('ask', 100.25, 2)), (4.0, 'quote', {})])
    assert derive(tmp_path)['processed'] == 3
    result = read(tmp_path, 0, 5)
    assert column(result, 'trades', 'volume', 'f') == []  # Absurd prints are omitted, not stored.
    # The absurd bid stays in the observed book, so later samples are explicit gaps, never drawn.
    assert {'crossed', 'off_grid'} & {g[2] for g in result['gaps'] if g[0] >= BASE_MS + 1500}
    assert heatmap.deriver_status(tmp_path / 'depth-heatmap-v1', OWNER)['state'] == 'current'


def test_rederiving_a_segment_replaces_its_rows_without_orphans(tmp_path):
    import sqlite3
    write(tmp_path, BOOK + [(1.0, 'quote', {})])
    derive(tmp_path)
    deriver = Deriver(tmp_path / 'depth-recordings-v1', tmp_path / 'depth-heatmap-v1', free_reserve=0,
                      clock=lambda: int((BASE + timedelta(hours=1)).timestamp() * 1000))
    entry = deriver.inventory.scan()[0]
    deriver.lease(OWNER)
    deriver.engines[(OWNER, entry['capture'])] = heatmap.HeatmapEngine(entry['capture'])
    deriver._process(OWNER, entry['capture'], entry, write=True)  # Retry after a crash.
    deriver.close()
    session = next((tmp_path / 'depth-heatmap-v1' / OWNER / 'sessions').glob('*.sqlite3'))
    with sqlite3.connect(session) as db:
        assert db.execute('SELECT count(*) FROM chunks').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM tier_data WHERE chunk_id NOT IN (SELECT id FROM chunks)').fetchone()[0] == 0


def test_inventory_reads_only_new_manifests(tmp_path, monkeypatch):
    recorder = SegmentRecorder(tmp_path / 'depth-recordings-v1', OWNER)
    for i in range(5):
        recorder.append(CONTRACT, 'quote', dict(timestamp=at(i).isoformat()), received_at=at(i).isoformat())
        recorder.seal()
    inventory = heatmap.Inventory(tmp_path / 'depth-recordings-v1')
    reads = []
    original = heatmap.read_manifest
    monkeypatch.setattr(heatmap, 'read_manifest', lambda marker, root, **kwargs: reads.append(marker.name) or original(marker, root, **kwargs))
    assert len(inventory.scan(since_ms=BASE_MS - 1)) == 5
    recorder.append(CONTRACT, 'quote', dict(timestamp=at(9).isoformat()), received_at=at(9).isoformat())
    recorder.close()
    import os as _os
    _os.utime(recorder.path, ns=(recorder.path.stat().st_atime_ns, recorder.path.stat().st_mtime_ns + 10**9))
    assert len(inventory.scan(since_ms=BASE_MS - 1)) == 6
    assert len(reads) == 6  # The five earlier manifests were not reread.
    assert len(inventory.scan(since_ms=BASE_MS + 4_500)) == 1  # Out-of-window entries leave memory.


def test_cached_responses_carry_a_fresh_server_time(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'history_source_path', lambda: tmp_path / 'absent.json')
    monkeypatch.setattr(service, 'read_heatmap', lambda cache, query, **kw: dict(schema='x', server_time_ms=1))
    clock = iter([1000.0, 2000.0])
    monkeypatch.setattr(service, 'time', lambda: next(clock))
    args = dict(user_id='owner3', contract_id=CONTRACT, data_live=False, start_ms=0, end_ms=1000, tier=0)
    first, second = json.loads(service.account_heatmap(**args)), json.loads(service.account_heatmap(**args))
    assert first['server_time_ms'] == 1_000_000 and second['server_time_ms'] == 2_000_000


def test_retention_keeps_the_newest_30_sessions_and_never_touches_recordings(tmp_path):
    import sqlite3
    write(tmp_path, BOOK + [(1.0, 'quote', {})])
    derive(tmp_path)  # Current session file (2026-11-03) plus state.
    sessions = tmp_path / 'depth-heatmap-v1' / OWNER / 'sessions'
    old = [(datetime(2026, 9, 1) + timedelta(days=i)).date().isoformat() for i in range(33)]
    for key in old:
        (sessions / f'{key}.sqlite3').write_bytes(b'x')
    (sessions / f'{old[0]}.sqlite3-wal').write_bytes(b'x')
    (sessions / 'notes.txt').write_text('not a session file')
    raw_before = sorted(p.name for p in (tmp_path / 'depth-recordings-v1').rglob('*'))
    deriver = Deriver(tmp_path / 'depth-recordings-v1', tmp_path / 'depth-heatmap-v1', free_reserve=0,
                      clock=lambda: int((BASE + timedelta(hours=1)).timestamp() * 1000))
    state = deriver._state(OWNER)
    state.execute("INSERT INTO segments VALUES ('ancient', 'c', 1, 1, 'derived', 'x')")
    state.commit()
    removed = deriver.apply_retention(OWNER)
    deriver.close()
    kept = sorted(p.name for p in sessions.glob('*.sqlite3'))
    assert len(kept) == 30 and '2026-11-03.sqlite3' in kept  # The current session is always kept.
    assert removed == sorted(old)[:4][::-1]  # 34 sessions -> the 4 oldest go.
    assert not (sessions / f'{old[0]}.sqlite3-wal').exists()
    assert (sessions / 'notes.txt').exists()
    assert sorted(p.name for p in (tmp_path / 'depth-recordings-v1').rglob('*')) == raw_before
    with sqlite3.connect(tmp_path / 'depth-heatmap-v1' / OWNER / 'state.sqlite3') as db:
        assert db.execute("SELECT count(*) FROM segments WHERE segment_key='ancient'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM segments").fetchone()[0] == 1  # The current session's segment stays.
        report = json.loads(db.execute("SELECT value FROM status WHERE key='retention'").fetchone()[0])
    assert report['keep_sessions'] == 30 and len(report['removed']) == 4
    assert read(tmp_path, 0, 2)['frames']['count'] > 0  # Current history still readable.


def test_retention_runs_from_the_deriver_loop(tmp_path):
    write(tmp_path, BOOK + [(1.0, 'quote', {})])
    sessions = tmp_path / 'depth-heatmap-v1' / OWNER / 'sessions'
    sessions.mkdir(parents=True)
    for i in range(3):
        (sessions / f'2026-10-0{i + 1}.sqlite3').write_bytes(b'x')
    summary = derive(tmp_path, keep_sessions=2)
    assert summary['removed_sessions'] == ['2026-10-02', '2026-10-01']
    assert sorted(p.name for p in sessions.glob('*.sqlite3')) == ['2026-10-03.sqlite3', '2026-11-03.sqlite3']


def test_inventory_pause_preserves_verified_progress_and_resumes(tmp_path, monkeypatch):
    recorder = SegmentRecorder(tmp_path / 'depth-recordings-v1', OWNER)
    for i in range(8):
        recorder.append(CONTRACT, 'quote', dict(timestamp=at(i).isoformat()), received_at=at(i).isoformat())
        recorder.seal()
    inventory = heatmap.Inventory(tmp_path / 'depth-recordings-v1')
    reads = []
    original = heatmap.read_manifest
    def manifest(*args, **kwargs):
        reads.append(args[0].name)
        return original(*args, **kwargs)
    monkeypatch.setattr(heatmap, 'read_manifest', manifest)
    with pytest.raises(heatmap.InventoryPaused):
        inventory.scan(should_stop=lambda: len(reads) >= 2)
    assert len(reads) == 2
    assert len(inventory.scan()) == 8
    assert len(reads) == len(set(reads)) == 8
    recorder.close()


def test_heatmap_pressure_during_first_scan_does_not_derive(tmp_path, monkeypatch):
    write(tmp_path, BOOK + [(1.0, 'quote', {})])
    deriver = Deriver(tmp_path / 'depth-recordings-v1', tmp_path / 'depth-heatmap-v1', free_reserve=0,
                      clock=lambda: BASE_MS + 3600000)
    monkeypatch.setattr(deriver, '_process', lambda *a, **kw: pytest.fail('derived while paused'))
    assert deriver.run_once(should_stop=lambda: True)['paused'] is True
    assert not deriver.states
    deriver.close()


@pytest.mark.parametrize('status', [None, 'invalid-json', {'state': 'recording'},
    {'state': 'recording', 'at': '2020-01-01T00:00:00+00:00'},
    {'state': 'reconnecting'}, {'state': 'recording', 'storage': {'scan_stalled': True}},
    {'state': 'recording', 'storage': {'blocked_reason': 'disk_free_reserve'}}])
def test_heatmap_yields_to_independent_recorder_status(tmp_path, monkeypatch, status):
    from io import BytesIO
    from app.services import topbot_study_worker as worker
    monkeypatch.setattr(worker, 'ACTIVE', {})
    monkeypatch.setattr(worker, 'recording_pressure', lambda: None)
    path = tmp_path / 'status.json'
    if isinstance(status, dict):
        status = dict(status)
        status.setdefault('at', datetime.now(timezone.utc).isoformat())
        path.write_text(json.dumps(status))
    elif status:
        path.write_text(status)
    pipe = BytesIO()
    supervisor = service.HeatmapDeriverSupervisor(recorder_status=path)
    supervisor.process = SimpleNamespace(stdin=pipe)
    supervisor._signal_pressure()
    assert pipe.getvalue() == b'pause\n'
    path.write_text(json.dumps({'state': 'recording', 'at': datetime.now(timezone.utc).isoformat(), 'storage': {}}))
    supervisor._signal_pressure()
    assert pipe.getvalue() == b'pause\nresume\n'
