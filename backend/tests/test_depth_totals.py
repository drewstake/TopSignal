from datetime import datetime, timedelta, timezone
import gzip
import json

import pytest

from tools.depth_recording import SegmentRecorder, StorageLimit, verify_segments
from tools.depth_totals import BookTotals, TotalsRecorder
from tools.backup_market_cache import backup

CONTRACT = 'CON.F.US.MNQ.Z26'
BASE = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)


def event(ms, kind='depth', *, typ=2, price=20000, size=10, **extra):
    at = (BASE + timedelta(milliseconds=ms)).isoformat()
    payload = dict(timestamp=at, type=typ, price=price, volume=size, currentVolume=999, **extra)
    return dict(contract_id=CONTRACT, kind=kind, payload=payload, monotonic_ns=ms*1_000_000,
                received_at=at, provider_at=at, capture_id='raw-run', index=ms+1)


def test_totals_use_aggregate_volume_and_update_replacements_deletes_and_sampling():
    book = BookTotals()
    book.apply(event(0, 'connect'))
    assert book.apply(event(1, typ=2, size=30)) is None
    assert book.apply(event(2, typ=1, price=20000.25, size=10)) is None
    row = book.apply(event(250, 'trade', price=20000.25))
    assert row['bid_contracts'] == 30 and row['ask_contracts'] == 10
    assert row['imbalance'] == .5 and row['mid_price'] == 20000.125
    assert row['last_trade_price'] == 20000.25 and not row['quality_flags']
    assert row['source_index'] == 251 and row['snapshot_completion_verified'] is False
    book.apply(event(300, size=5))
    row = book.apply(event(500, 'quote'))
    assert row['bid_contracts'] == 5 and row['imbalance'] == pytest.approx(-1/3)
    row = book.apply(event(750, size=0))
    assert row['bid_contracts'] == 0 and row['bid_levels'] == 0 and row['mid_price'] is None
    assert 'missing_side' in row['quality_flags']


@pytest.mark.parametrize('kind', ['disconnect', 'gap', 'roll', 'end', 'connect'])
def test_boundaries_emit_immediately_and_clear_old_book_and_trade(kind):
    book = BookTotals()
    book.apply(event(0, typ=2))
    book.apply(event(1, typ=1, price=20000.25))
    book.apply(event(2, 'trade', price=20000))
    row = book.apply(event(3, kind))
    assert row['bid_contracts'] == row['ask_contracts'] == 0
    assert row['imbalance'] is None and row['last_trade_price'] is None
    assert kind in row['quality_flags']


def test_nearest_levels_are_sorted_and_best_quote_is_not_double_counted():
    book = BookTotals()
    book.apply(event(0, 'connect'))
    for i in range(105):
        book.apply(event(i+1, typ=2, price=20000-i*.25, size=i+1))
        book.apply(event(i+1, typ=1, price=20000.25+i*.25, size=2))
    row = book.apply(event(250, typ=9, price=20000, size=1))
    assert row['bid_contracts'] == sum(range(1,106))
    assert row['ask_contracts'] == 210
    assert row['nearest']['10']['bid_contracts'] == 55
    assert row['nearest']['100']['bid_contracts'] == 5050
    assert row['nearest']['100']['ask_contracts'] == 200


def test_reset_epoch_staleness_and_crossed_book_are_explicit():
    book = BookTotals()
    book.apply(event(0, epoch=1))
    row = book.apply(event(250, typ=1, price=19999, epoch=1))
    assert 'locked_or_crossed' in row['quality_flags']
    row = book.apply(event(3000, 'quote'))
    assert 'bid_stale' in row['quality_flags'] and 'ask_stale' in row['quality_flags']
    row = book.apply(event(3010, typ=2, price=19998, epoch=2))
    assert 'epoch_changed' in row['quality_flags'] and row['ask_levels'] == 0
    row = book.apply(event(3020, typ=6, epoch=2))
    assert 'reset' in row['quality_flags'] and row['bid_levels'] == 0


def test_old_provider_times_remain_flagged_despite_fresh_receipt():
    book = BookTotals()
    e = event(0)
    e['provider_at'] = e['payload']['timestamp'] = '0001-01-01T00:00:00+00:00'
    row = book.apply(e)
    assert 'bid_provider_time_unreliable' in row['quality_flags']


def test_totals_segments_share_budget_and_existing_verified_backup_format(tmp_path):
    root = tmp_path/'cache'/'depth-recordings-v1'
    checked = []
    recorder = TotalsRecorder(SegmentRecorder(root, 'a'*64), before_segment=lambda: checked.append(True))
    for ms, typ, price in [(0, 2, 20000), (250, 1, 20000.25), (500, 2, 20000)]:
        e = event(ms, typ=typ, price=price)
        recorder.append(CONTRACT, 'depth', e['payload'], received_at=e['received_at'], monotonic_ns=e['monotonic_ns'])
    recorder.close()
    assert checked
    assert verify_segments(root/'totals')['verified_events'] == 3
    marker = next((root/'totals').rglob('*.segment.json'))
    with gzip.open(marker.with_name(json.loads(marker.read_text())['file']), 'rt') as f:
        rows = [json.loads(line) for line in f]
    assert rows[-1]['payload']['bid_contracts'] == 10
    assert rows[-1]['payload']['source_capture_id'] == recorder.raw.run_id
    assert rows[-1]['payload']['source_index'] == 3
    result = backup(tmp_path/'cache', tmp_path/'backup')
    assert result['depth_snapshot']['sealed_segments'] == 2
    assert verify_segments(tmp_path/'backup'/'depth-recordings-v1'/'totals')['verified_events'] == 3
    def full():
        raise StorageLimit('recording_budget_reached')
    blocked = TotalsRecorder(SegmentRecorder(tmp_path/'blocked', 'b'*64), before_segment=full)
    with pytest.raises(StorageLimit):
        e = event(0)
        blocked.append(CONTRACT, 'depth', e['payload'], received_at=e['received_at'], monotonic_ns=e['monotonic_ns'])
    blocked.close()
    assert not list((tmp_path/'blocked'/'totals').rglob('*.ndjson.gz'))
