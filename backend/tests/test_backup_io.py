from datetime import datetime, timezone
import json
import pytest
from tools.backup_io import BackupIO


def test_transport_writer_refuses_short_write():
    from tools.backup_io import HashingWriter
    class ShortWriter:
        def write(self,data):return len(data)-1
    writer=HashingWriter(ShortWriter())
    with pytest.raises(OSError,match='Incomplete archive write'):
        writer.write(b'payload')
    assert writer.bytes_written==0


def test_pacing_does_not_accumulate_burst_credit_and_bounds_each_sleep():
    clock = [100.0]
    delays = []
    def sleep(delay):
        delays.append(delay)
        clock[0] += delay
    pacing = BackupIO(2, clock=lambda: clock[0], sleep=sleep)
    pacing.check(4*1024**2)
    assert clock[0] == 102 and delays == [1,1]
    clock[0] = 200
    pacing.check(1024**2)
    assert clock[0] == 200.5


def test_pacing_clock_crossing_deadline_never_sleeps_negative_duration():
    ticks = iter((0, 0, .999, 1.001))
    delays = []
    def sleep(delay):
        assert 0 < delay <= 1
        delays.append(delay)
    pacing = BackupIO(1, clock=lambda: next(ticks), sleep=sleep)
    pacing.check(1024**2)
    assert delays == [pytest.approx(.001)]


def test_recorder_unknown_stale_or_unhealthy_defers_backup(tmp_path):
    path = tmp_path/'status.json'
    clock = [100]
    guard = BackupIO(status_path=path, clock=lambda: clock[0])
    with pytest.raises(RuntimeError,match='deferred'):
        guard.check()
    good = dict(state='recording',at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(good))
    guard.check()
    for bad in ({'state':'failed'}, {'at':'2020-01-01T00:00:00+00:00'}, {'storage':{'scan_stalled':True}}):
        clock[0] += 3
        path.write_text(json.dumps(good|bad))
        with pytest.raises(RuntimeError,match='deferred'):
            guard.check()
