"""Recording failures stay visible, recover without duplicates, and never stall the caller."""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import errno
import json
from pathlib import Path
import sqlite3
from threading import Event
import time

import pytest

import app.services.topbot_evidence as evidence
from app.services.topbot_evidence import Capture, recorded_batches
from app.services import market_archive as archive_module
from app.services import market_archive_decisions as decisions

CONTRACT = "CON.F.US.MNQ.Z26"
UTC = timezone.utc


def update(sequence, at=None):
    at = at or datetime.now(UTC)
    return {"event": "update", "data": {"contract_id": CONTRACT, "sequence": sequence,
            "timestamp": at.isoformat(), "side": "bid", "price": 20000, "size": 10}}


def wait_for(condition, seconds=5):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.01)
    return condition()


def events(path):
    rows = list(recorded_batches(path))
    return rows[0], rows[1:]


def legacy_capture(root, days_old=1):
    """An older capture with no sidecar, as written before sidecars existed."""
    path = root / "owner" / "legacy.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(value TEXT)")
        db.execute("INSERT INTO metadata VALUES(?)", (json.dumps(
            {"started_at": (datetime.now(UTC)-timedelta(days=days_old)).isoformat()}),))
    return path


@pytest.fixture(autouse=True)
def isolated_inventories():
    evidence._INVENTORIES.clear()
    yield
    evidence._INVENTORIES.clear()


class Faults:
    """Inject storage faults into batch commits only, after startup has succeeded."""
    def __init__(self, monkeypatch):
        self.pending = []
        self.delay = 0.
        self.commits = 0
        original = sqlite3.connect
        faults = self

        class Connection(sqlite3.Connection):
            def commit(self):
                if not self.in_transaction:
                    return super().commit()
                faults.commits += 1
                if faults.delay:
                    time.sleep(faults.delay)
                if faults.pending:
                    fault = faults.pending.pop(0)
                    if fault.get("after_commit"):
                        super().commit()
                    raise fault["error"]
                return super().commit()

        monkeypatch.setattr(evidence.sqlite3, "connect", lambda *a, **kw: original(*a, **kw, factory=Connection))


def busy():
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


def disk_full():
    error = sqlite3.OperationalError("database or disk is full")
    error.sqlite_errorcode = sqlite3.SQLITE_FULL
    return error


def recording(tmp_path, **options):
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0, **options)
    assert wait_for(lambda: cap.state == "recording")
    return cap


# --- worker evidence: startup -------------------------------------------------

def test_slow_older_captures_do_not_hold_a_new_capture_in_starting(tmp_path, monkeypatch):
    legacy_capture(tmp_path)
    entered, release = Event(), Event()
    original = evidence.Inventory._resolve

    def slow_disk(self, names):
        entered.set()
        assert release.wait(10)
        return original(self, names)

    monkeypatch.setattr(evidence.Inventory, "_resolve", slow_disk)
    began = time.monotonic()
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    try:
        for sequence in range(500):
            cap.append(update(sequence), datetime.now(UTC), [])
        # Recording begins while the older file is still being read elsewhere.
        assert wait_for(lambda: cap.durable_events == 500, 5)
        assert time.monotonic()-began < 5 and entered.is_set()
        status = cap.status()
        assert status["state"] == "recording" and status["dropped_events"] == 0
        assert status["storage"]["estimated_capture_starts"] == 1
    finally:
        release.set()
        cap.close()
    assert wait_for(lambda: (tmp_path / evidence.START_INDEX).exists())


def test_older_capture_dates_are_read_once_then_served_from_the_index(tmp_path, monkeypatch):
    legacy = legacy_capture(tmp_path)
    first = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    first.close()
    assert wait_for(lambda: (tmp_path / evidence.START_INDEX).exists())
    index = json.loads((tmp_path / evidence.START_INDEX).read_text())
    assert list(index["starts"]) == ["owner/legacy.sqlite3"]
    # A new process (fresh inventory) must not reopen the older capture database.
    evidence._INVENTORIES.clear()
    original = sqlite3.connect

    def guarded(target, *args, **kwargs):
        assert "legacy.sqlite3" not in str(target)
        return original(target, *args, **kwargs)

    monkeypatch.setattr(evidence.sqlite3, "connect", guarded)
    second = recording(tmp_path)
    assert second.status()["storage"]["estimated_capture_starts"] == 0
    second.close()
    assert legacy.exists()


def test_locked_older_capture_cannot_end_a_new_capture(tmp_path, monkeypatch):
    legacy_capture(tmp_path)
    original = sqlite3.connect

    def locked(target, *args, **kwargs):
        if "legacy.sqlite3" in str(target):
            raise busy()
        return original(target, *args, **kwargs)

    monkeypatch.setattr(evidence.sqlite3, "connect", locked)
    cap = recording(tmp_path)
    cap.append(update(1), datetime.now(UTC), [])
    cap.close()
    status = cap.status()
    assert status["state"] == "closed" and status["durable_events"] == 1
    assert status["storage"]["unreadable_capture_starts"] == 1 and status["error_type"] is None


def test_startup_fault_is_retried_instead_of_ending_the_capture(tmp_path, monkeypatch):
    original = Capture._open
    attempts = []

    def flaky(self):
        attempts.append(1)
        if len(attempts) < 3:
            raise busy()
        return original(self)

    monkeypatch.setattr(Capture, "_open", flaky)
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    try:
        cap.append(update(1), datetime.now(UTC), [])  # buffered while startup retries
        assert wait_for(lambda: cap.durable_events == 1, 10)
        assert cap.status()["recoveries"] == 1 and cap.dropped == 0
    finally:
        cap.close()


# --- worker evidence: faults while recording ---------------------------------

def test_database_lock_is_retried_without_loss_or_duplicates(tmp_path, monkeypatch):
    faults = Faults(monkeypatch)
    cap = recording(tmp_path)
    try:
        faults.pending = [{"error": busy()}, {"error": busy()}]
        for sequence in range(50):
            cap.append(update(sequence), datetime.now(UTC), [])
        assert wait_for(lambda: cap.failures >= 1)
        assert cap.status()["error_category"] in {"contention", None}
        for sequence in range(50, 100):
            cap.append(update(sequence), datetime.now(UTC), [])
        assert wait_for(lambda: cap.durable_events == 100, 10)
    finally:
        cap.close()
    status = cap.status()
    assert status["dropped_events"] == 0 and status["failures"] == 2 and status["recoveries"] >= 1
    _, rows = events(cap.path)
    assert [row["event_index"] for row in rows] == list(range(1, 101))


def test_ambiguous_commit_failure_never_duplicates_a_durable_batch(tmp_path, monkeypatch):
    faults = Faults(monkeypatch)
    cap = recording(tmp_path)
    try:
        # The commit reaches disk and only then reports an I/O error.
        error = sqlite3.OperationalError("disk I/O error")
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        faults.pending = [{"error": error, "after_commit": True}]
        for sequence in range(20):
            cap.append(update(sequence), datetime.now(UTC), [])
        assert wait_for(lambda: cap.durable_events == 20, 10)
    finally:
        cap.close()
    with sqlite3.connect(cap.path) as db:
        assert db.execute("SELECT count(*), count(DISTINCT first_index) FROM batches").fetchone()[0:2] == \
            tuple([db.execute("SELECT count(*) FROM batches").fetchone()[0]]*2)
    _, rows = events(cap.path)  # also verifies the hash chain and index order
    assert [row["event_index"] for row in rows] == list(range(1, 21))
    assert cap.status()["failures"] == 1


def test_disk_full_is_visible_nonblocking_and_recording_resumes_with_an_explicit_gap(tmp_path, monkeypatch):
    faults = Faults(monkeypatch)
    monkeypatch.setattr(Capture, "_failed", lambda self, exc, original=Capture._failed: min(.05, original(self, exc)))
    cap = recording(tmp_path)
    try:
        cap.append(update(1), datetime.now(UTC), [])
        assert wait_for(lambda: cap.durable_events == 1)
        faults.pending = [{"error": disk_full()} for _ in range(4)]
        cap.append(update(2), datetime.now(UTC), [])
        assert wait_for(lambda: cap.state == "storage_blocked")
        status = cap.status()
        assert status["error_category"] == "disk_full" and status["last_error_at"]
        began = time.monotonic()
        for sequence in range(3, 203):
            cap.append(update(sequence), datetime.now(UTC), [])
        assert time.monotonic()-began < 1  # refused immediately, never waiting on the disk
        refused = cap.dropped
        assert refused >= 1
        faults.pending = []
        assert wait_for(lambda: cap.state == "recording", 10)
        cap.append(update(999), datetime.now(UTC), [])
        assert wait_for(lambda: cap.durable_index == cap.index, 10)
    finally:
        cap.close()
    _, rows = events(cap.path)
    indexes = [row["event_index"] for row in rows]
    # The held batch survives, refused events are index holes, and the resumed
    # event states exactly how many were lost before it.
    assert indexes[:2] == [1, 2] and indexes[-1] == cap.index
    assert len(indexes) == cap.index-refused
    assert rows[-1]["dropped_before"] == refused
    assert cap.status()["dropped_events"] == refused


def test_slow_disk_and_queue_saturation_never_block_the_producer(tmp_path, monkeypatch):
    faults = Faults(monkeypatch)
    cap = recording(tmp_path, queue_size=64)
    try:
        faults.delay = .5
        startup_commits = faults.commits
        cap.append(update(-1), datetime.now(UTC), [])
        assert wait_for(lambda: faults.commits > startup_commits)  # the writer is now inside a slow commit
        slowest = 0.
        for sequence in range(1999):
            began = time.perf_counter()
            cap.append(update(sequence), datetime.now(UTC), [])
            slowest = max(slowest, time.perf_counter()-began)
        status = cap.status()
        assert slowest < .1
        assert status["dropped_events"] > 0 and status["last_drop"]
        assert status["queued_events"] <= 64 and status["oldest_queued_seconds"] is not None
        faults.delay = 0.
    finally:
        cap.close()
    assert wait_for(lambda: not cap.thread.is_alive(), 10)
    _, rows = events(cap.path)
    assert len(rows) == cap.durable_events == 2000-cap.dropped
    assert cap.status()["max_commit_seconds"] >= .5


def test_stop_during_a_storage_fault_counts_the_unwritten_tail_as_lost(tmp_path, monkeypatch):
    faults = Faults(monkeypatch)
    cap = recording(tmp_path)
    faults.pending = [{"error": busy()} for _ in range(1000)]
    for sequence in range(10):
        cap.append(update(sequence), datetime.now(UTC), [])
    assert wait_for(lambda: cap.failures >= 1)
    cap.close()
    assert wait_for(lambda: not cap.thread.is_alive(), 10)
    status = cap.status()
    assert status["state"] != "closed" and status["durable_events"] == 0
    assert status["dropped_events"] == 10
    assert status["last_drop"]["reason"] == "writer_stopped_before_commit"


def test_wal_is_checkpointed_off_the_commit_path(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "CHECKPOINT_BYTES", 1)
    monkeypatch.setattr(evidence, "CHECKPOINT_SECONDS", .05)
    cap = recording(tmp_path)
    try:
        for sequence in range(200):
            cap.append(update(sequence), datetime.now(UTC), [])
        assert wait_for(lambda: cap.durable_events == 200 and cap.checkpoint["runs"] >= 1, 10)
        assert cap.checkpoint["checkpointed_frames"] >= 1
    finally:
        cap.close()
    _, rows = events(cap.path)
    assert len(rows) == 200


def test_health_reports_queue_latency_storage_and_errors(tmp_path):
    cap = recording(tmp_path)
    cap.append(update(1), datetime.now(UTC), [])
    assert wait_for(lambda: cap.durable_events == 1)
    status = cap.status()
    cap.close()
    for key in ("queued_events", "queued_bytes", "oldest_queued_seconds", "last_durable_at", "last_commit_seconds",
                "max_commit_seconds", "dropped_events", "error_category", "blocked_reason", "failures", "checkpoint"):
        assert key in status
    assert status["storage"]["disk_free_bytes"] > 0 and status["storage"]["evidence_bytes"] >= 0
    assert status["storage"]["review_due_at"]


# --- decision archive: off the trading cycle ----------------------------------

@pytest.fixture
def decision_writer(tmp_path, monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_MARKET_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("TOPSIGNAL_DATABENTO_CACHE_DIR", str(tmp_path))
    decisions.reset()
    yield decisions
    decisions.reset()
    archive_module.close_writers()


def prepared(ident):
    at = datetime(2026, 11, 10, 15, tzinfo=UTC)
    return dict(owner="owner", decision_id=ident, config=40, account=1, run=14, contract=CONTRACT,
                at=at.isoformat(), correlation_id=f"correlation-{ident}", action="HOLD", reason="test",
                status="held", forecast={}, candles=[])


def test_slow_archive_disk_never_delays_the_evaluation_thread(decision_writer, monkeypatch):
    entered, release = Event(), Event()

    def slow(prepared_row, confirmed_at):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(decisions, "_write", slow)
    began = time.perf_counter()
    try:
        assert decisions.submit(prepared(1), committed=True)
        assert entered.wait(5)
        for ident in range(2, 2+decisions.MAX_PENDING+10):
            decisions.submit(prepared(ident), committed=True)
        assert time.perf_counter()-began < 2
        health = decisions.health()
        assert health["queued"] == decisions.MAX_PENDING and health["dropped_queue_full"] == 11
        assert health["oldest_queued_seconds"] is not None and health["writing"]
    finally:
        release.set()


def test_contention_is_retried_and_the_copy_is_written_once_with_confirmation(decision_writer, monkeypatch):
    original = decisions._write
    attempts = []

    def contended(prepared_row, confirmed_at):
        attempts.append(1)
        if len(attempts) < 3:
            raise busy()
        return original(prepared_row, confirmed_at)

    monkeypatch.setattr(decisions, "_write", contended)
    monkeypatch.setattr(decisions, "RETRY_BASE_SECONDS", .01)
    assert decisions.submit(prepared(7), committed=True)
    assert wait_for(lambda: decisions.health().get("written") == 1, 10)
    health = decisions.health()
    assert health["retries"] == 2 and health["written_after_retry"] == 1 and health["queued"] == 0
    record = archive_module.Archive("owner").reconstruct(7)
    assert record["execution_commit_confirmed_at"] and record["correlation_id"] == "correlation-7"
    with archive_module.Archive("owner").reader() as db:
        assert db.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1


def test_failed_execution_commit_is_kept_as_explicitly_unconfirmed(decision_writer):
    assert decisions.submit(prepared(8), committed=False)
    assert wait_for(lambda: decisions.health().get("written") == 1, 10)
    assert archive_module.Archive("owner").reconstruct(8)["execution_commit_confirmed_at"] is None


def test_permanent_failure_is_counted_and_does_not_block_later_copies(decision_writer, monkeypatch):
    original = decisions._write

    def broken(prepared_row, confirmed_at):
        if prepared_row["decision_id"] == 1:
            raise ValueError("unsupported")
        return original(prepared_row, confirmed_at)

    monkeypatch.setattr(decisions, "_write", broken)
    decisions.submit(prepared(1), committed=True)
    decisions.submit(prepared(2), committed=True)
    assert wait_for(lambda: decisions.health().get("written") == 1, 10)
    health = decisions.health()
    assert health["failed"] == 1 and health["last_error"]["type"] == "ValueError"
    assert archive_module.status("owner")["decision_writer"]["failed"] == 1


def test_shutdown_drain_is_bounded_and_reports_unwritten_copies(decision_writer, monkeypatch):
    release = Event()
    monkeypatch.setattr(decisions, "_write", lambda *a: release.wait(10))
    decisions.submit(prepared(1), committed=True)
    decisions.submit(prepared(2), committed=True)
    began = time.monotonic()
    assert decisions.drain(.3) is False
    assert time.monotonic()-began < 2
    assert decisions.health()["unwritten_at_shutdown"] >= 1
    release.set()


def test_worker_performs_no_archive_io_inside_the_execution_transaction():
    source = (Path(__file__).parents[1] / "app/bot_worker.py").read_text(encoding="utf-8")
    prepare = source.index("prepared_archive = prepare_decision(result)")
    commit = source.index("db.commit()", prepare)
    between = source[prepare:commit]
    assert "Archive(" not in between and ".decision(" not in between and "record_decision" not in between
    assert "submit_archive_decision(prepared_archive, committed=True)" in source[commit:]
    assert "recover_decision_commits(db" not in source


# --- independent depth recorder: capacity checks ------------------------------

def monitor_config(**extra):
    return {**dict(max_bytes=64*1024**2, reserve_bytes=0, review_after_days=30), **extra}


def test_stalled_or_failed_capacity_scan_is_reported_without_stopping_capture(tmp_path, monkeypatch):
    import tools.record_market_depth as service
    monkeypatch.setattr(service, "inventory", lambda *a, **kw: dict(bytes=0, free_bytes=10**12, blocked_reason=None))
    clock = [1000.]
    monitor = service.CapacityMonitor(tmp_path, monitor_config(), clock=lambda: clock[0])
    try:
        pending = Future()
        monitor.pending, monitor.started = pending, clock[0]
        clock[0] += 600  # a walk that has been running for ten minutes
        value = monitor.check(1024)
        assert value["scan_stalled"] and value["blocked_reason"] is None
        # The budget is still enforced from bytes written since that walk began.
        assert monitor.check(64*1024**2)["blocked_reason"] == "recording_budget_reached"
        pending.set_exception(OSError("private path"))
        value = monitor.check(1024)
        assert value["scan_failures"] == 1 and value["scan_error"] == "OSError" and value["blocked_reason"] is None
        assert "private" not in json.dumps(value)
        # Limits are never waived indefinitely: no completed walk within the stale limit stops capture.
        monitor.pending = Future()
        clock[0] += monitor.stale_after
        assert monitor.check(1024)["blocked_reason"] == "capacity_check_stale"
    finally:
        monitor.close()


def test_capacity_walks_are_infrequent_and_a_restart_reuses_the_last_result(tmp_path, monkeypatch):
    import tools.record_market_depth as service
    calls = []
    release = Event()

    def scan(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            assert release.wait(10)
        return dict(bytes=1000, free_bytes=10**12, blocked_reason=None, oldest_mtime=time.time())

    monkeypatch.setattr(service, "inventory", scan)
    root, cache = tmp_path / "recordings", tmp_path / "runtime" / "capacity-cache.json"
    root.mkdir()
    first = service.CapacityMonitor(root, monitor_config(), cache=cache)
    for _ in range(50):
        assert first.check(10)["blocked_reason"] is None
    assert len(calls) == 1 and first.interval >= 600  # not one walk per 30 seconds
    first.close()
    began = time.monotonic()
    second = service.CapacityMonitor(root, monitor_config(), cache=cache)  # walk blocked: slow disk
    try:
        value = second.check(10)
        assert time.monotonic()-began < 1
        assert value["bytes"] == 1000 and value["scan_pending"]
        assert value["uncompressed_growth_allowance"] == 10+service.CapacityMonitor.RESTART_ALLOWANCE
    finally:
        release.set()
        second.close()


def test_review_age_and_free_space_are_enforced_between_walks(tmp_path, monkeypatch):
    import tools.record_market_depth as service
    monkeypatch.setattr(service, "inventory", lambda *a, **kw: dict(
        bytes=0, free_bytes=10**12, blocked_reason=None, oldest_mtime=time.time()-31*86400))
    monitor = service.CapacityMonitor(tmp_path, monitor_config())
    assert monitor.check()["blocked_reason"] == "retention_review_due"
    monitor.close()
    monkeypatch.setattr(service, "inventory", lambda *a, **kw: dict(bytes=0, free_bytes=10**12, blocked_reason=None))
    monitor = service.CapacityMonitor(tmp_path, monitor_config(reserve_bytes=10**9))
    monkeypatch.setattr(service.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 10**8})())
    assert monitor.check()["blocked_reason"] == "disk_free_reserve"
    monitor.close()


# --- recording health is separate from trading health --------------------------

def health_environment(tmp_path, monkeypatch):
    from app.services import topbot_depth
    monkeypatch.setenv("TOPSIGNAL_MARKET_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("TOPSIGNAL_DATABENTO_CACHE_DIR", str(tmp_path))
    decisions.reset()
    feed = topbot_depth.DepthFeed()
    monkeypatch.setattr(topbot_depth, "feed", feed)
    state = topbot_depth.DepthState(CONTRACT)
    feed.streams[("owner", CONTRACT)] = {"state": state, "touched": 0}
    return feed, state


def publish_recorder_health(tmp_path, at, state="recording"):
    from app.services.probabilistic_shadow import scope_hash
    path = tmp_path / "depth-recordings-v1" / "recording-health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(owner_hash=scope_hash("owner"), at=at.isoformat(), state=state)))


def publish_backup(tmp_path, verified_at):
    path = tmp_path / "market-archive" / "backup-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(status="verified", last_verified_at=verified_at.isoformat())))


def test_trading_can_be_healthy_while_recording_is_reported_unhealthy(tmp_path, monkeypatch):
    from app.services.recording_health import report
    feed, state = health_environment(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    state.capture = Capture("owner", CONTRACT, root=tmp_path / "evidence", budget=1, free_floor=0)
    assert wait_for(lambda: state.capture.state == "storage_blocked")
    snapshot = {"event": "snapshot", "data": {"contract_id": CONTRACT, "sequence": 1, "timestamp": now.isoformat(),
                "bids": [{"price": 20000, "size": 15}], "asks": [{"price": 20000.25, "size": 10}]}}
    state.apply(snapshot, now)
    publish_recorder_health(tmp_path, now-timedelta(minutes=10))
    publish_backup(tmp_path, now-timedelta(days=6))
    try:
        result = report("owner", now=now)
    finally:
        state.capture.close()
    # The strategy input is intact and ready; only the evidence is impaired.
    assert state.snapshot(now)["ready"]
    codes = {problem["code"] for problem in result["problems"]}
    assert result["recording_healthy"] is False
    assert {"worker_evidence_storage_blocked", "worker_evidence_events_dropped",
            "depth_recorder_not_recording", "off_device_backup_stale"} <= codes
    blocked = next(p for p in result["problems"] if p["code"] == "worker_evidence_storage_blocked")
    assert blocked["reason"] == "evidence_budget_reached" and blocked["action"]
    assert "trading health is reported by readiness" in result["scope"]
    assert report("someone-else", now=now)["depth_recorder"] == {"state": "not_reported"}


def test_recording_health_is_clean_when_every_recorder_is_current(tmp_path, monkeypatch):
    from app.services.recording_health import report
    feed, state = health_environment(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    state.capture = recording(tmp_path / "evidence")
    state.capture.append(update(1), now, [])
    assert wait_for(lambda: state.capture.durable_events == 1)
    publish_recorder_health(tmp_path, now)
    publish_backup(tmp_path, now-timedelta(hours=2))
    try:
        result = report("owner", now=now)
    finally:
        state.capture.close()
    assert result["problems"] == [] and result["recording_healthy"] is True
    assert result["worker_evidence"][0]["last_durable_at"] and result["disk"]["free_bytes"] > 0
    assert any("not that any recording is complete" in line for line in result["limitations"])


def test_recording_diagnostics_keep_their_fields_in_the_log():
    from app.bot_diagnostics import safe_fields
    kept = safe_fields(dict(error_category="disk_full", blocked_reason="disk_free_floor", dropped_events=3,
                            problems=["worker_evidence_storage_blocked"], recording_healthy=False,
                            oldest_queued_seconds=1.5, secret="token"))
    assert kept["error_category"] == "disk_full" and kept["problems"] and "secret" not in kept
