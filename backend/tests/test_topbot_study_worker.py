import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

from app.services.topbot_evidence import Capture
from app.services.topbot_study import simulate
from app.services.topbot_study_store import quotes
from test_topbot_study import entry, NOW, CONTRACT
from test_bot_execution_safety import db_session, open_exchange_session
from test_topbot_dom import dom_entry


def test_real_offline_child_reads_verified_path_past_actual_exit(tmp_path):
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    path = [(.1, 0), (.5, 25.25), (.8, 80), (2, 80), (3, 80), (4, 80), (5, 80), (5.3, 80), (5.7, 54), (6, 53)]
    for index, (second, points) in enumerate(path, 1):
        at = NOW+timedelta(seconds=second)
        bid = 20000+points
        cap.append({"event": "snapshot", "data": {"contract_id": CONTRACT, "sequence": index, "timestamp": at.isoformat(),
                   "bids": [{"price": bid, "size": 2}], "asks": [{"price": bid+.25, "size": 2}]}}, at, [])
    cap.close()
    e = entry(); e["exit_at"] = (NOW+timedelta(seconds=.5)).isoformat()
    e["strategy_snapshot"]["research_cutoff"] = (NOW+timedelta(seconds=7)).isoformat()
    completed = subprocess.run([sys.executable, "-B", str(Path(__file__).parents[1]/"tools/compare_topbot_exits.py"), "--capture", str(cap.path)],
        input=json.dumps({"owner": "owner", "contract": CONTRACT, "entry": e}), text=True, capture_output=True, timeout=10, check=True)
    result = json.loads(completed.stdout)
    assert result["paired_valid"]
    assert result["variants"][-1]["exit_at"] > e["exit_at"]
    assert result["verified_through_event_index"] == 10
    assert result["capture_id"] == cap.capture_id


def test_reader_marks_account_mismatch_as_missing_and_never_reuses_path(tmp_path):
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    cap.close()
    completed = subprocess.run([sys.executable, "-B", str(Path(__file__).parents[1]/"tools/compare_topbot_exits.py"), "--capture", str(cap.path)],
        input=json.dumps({"owner": "wrong", "contract": CONTRACT, "entry": entry()}), text=True, capture_output=True, timeout=10, check=True)
    result = json.loads(completed.stdout)
    assert not result["paired_valid"]
    assert all(v["status"] == "missing" for v in result["variants"])


def test_absent_capture_is_missing_not_an_endlessly_retrying_child(tmp_path):
    completed = subprocess.run([sys.executable, "-B", str(Path(__file__).parents[1]/"tools/compare_topbot_exits.py"), "--capture", str(tmp_path/"absent.sqlite3")],
        input=json.dumps({"owner": "owner", "contract": CONTRACT, "entry": entry()}), text=True, capture_output=True, timeout=10, check=True)
    result = json.loads(completed.stdout)
    assert all(v["status"] == "missing" and v["reason"] == "evidence_unavailable_FileNotFoundError" for v in result["variants"])
    assert not (tmp_path/"absent.sqlite3").exists()


def test_reader_restart_restores_filled_retention_without_reenrollment(db_session, dom_entry, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from copy import deepcopy
    from app.services import topbot_study_store as store
    from app.services.topbot_study_worker import StudyRecorder, feed
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    result, _, _ = dom_entry
    row = result.order_attempt
    request = deepcopy(row.raw_request)
    now = datetime.now(timezone.utc)
    request["strategySnapshot"]["research_cutoff"] = (now+timedelta(minutes=1)).isoformat()
    row.raw_request = request
    db_session.commit()
    @contextmanager
    def sessions(): yield db_session
    calls = []
    monkeypatch.setattr(feed, "retain", lambda *args: calls.append(args))
    def forbidden(db, *, user_id): raise AssertionError("No broker call is needed")
    StudyRecorder(sessions, forbidden).cycle()
    assert not calls  # An order attempt without a verified fill is not an observation.
    request = deepcopy(row.raw_request)
    request["timeExit"]["runner"].update(entry_at=now.isoformat(), entry_price=20000, stats_status="open")
    row.raw_request = request
    db_session.commit()
    StudyRecorder(sessions, forbidden).cycle()
    StudyRecorder(sessions, forbidden).cycle()
    assert len(calls) == 2 and calls[0][3] == calls[1][3]
    with store.connection(str(row.user_id)) as db:
        assert db.execute("select count(*) from enrollments").fetchone()[0] == 1
    request = deepcopy(row.raw_request)
    request["strategySnapshot"]["experiment_id"] = "prior-prespecified-model"
    row.raw_request = request
    db_session.commit()
    StudyRecorder(sessions, forbidden).cycle()
    assert len(calls) == 3  # A model deployment must not cancel outstanding path retention.
    with store.connection(str(row.user_id)) as db:
        assert db.execute("select count(*) from enrollments").fetchone()[0] == 1


def _overdue_entry(db_session, dom_entry, minutes_ago=1):
    from copy import deepcopy
    result, _, _ = dom_entry
    row = result.order_attempt
    request = deepcopy(row.raw_request)
    now = datetime.now(timezone.utc)
    request["strategySnapshot"]["research_cutoff"] = (now-timedelta(minutes=minutes_ago)).isoformat()
    request["timeExit"]["runner"].update(entry_at=(now-timedelta(minutes=30)).isoformat(), entry_price=20000,
                                         stats_status="complete", exit_reason="take_profit",
                                         exit_at=(now-timedelta(minutes=29)).isoformat())
    request["entryEvidence"] = {"pre_submit": {"evidence": {"capture_id": "ab"*16}}}
    row.raw_request = request
    db_session.commit()
    return row


class FakeChild:
    """Popen stand-in that writes progress lines to the real stderr file the parent reads."""
    made = []

    def __init__(self, args, stdin=None, stdout=None, stderr=None, **kwargs):
        import io
        class Input(io.BytesIO):
            def close(inner): inner.sent = inner.getvalue(); super().close()
        self.args, self.stdin, self.stdout, self.stderr = args, Input(), stdout, stderr
        self.code, self.terminated = None, False
        FakeChild.made.append(self)

    def progress(self, events):
        line = {"phase": "replay", "events": events, "received_at": "2026-11-11T15:00:00+00:00"}
        self.stderr.write((json.dumps(line)+"\n").encode())
        self.stderr.flush()

    def finish(self, code, payload):
        self.stdout.write(json.dumps(payload).encode())
        self.code = code

    def poll(self): return self.code
    def terminate(self): self.terminated, self.code = True, 1
    def wait(self, timeout=None): return self.code
    def kill(self): self.code = 1


def _worker_env(monkeypatch, tmp_path):
    from app.services import databento_cache, topbot_study_store as store
    from app.services import topbot_study_worker as worker
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    monkeypatch.setattr(databento_cache, "default_cache_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "recording_pressure", lambda: None)
    clock = [1000.]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    FakeChild.made = []
    monkeypatch.setattr(subprocess, "Popen", FakeChild)
    return worker, clock


def test_progressing_replay_is_not_killed_stalled_one_is_and_failures_are_durable(db_session, dom_entry, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.services import topbot_study_store as store, topbot_study_jobs as jobs
    from app.services.topbot_study import EXPERIMENT_ID
    worker, clock = _worker_env(monkeypatch, tmp_path)
    row = _overdue_entry(db_session, dom_entry)
    owner, account = str(row.user_id), int(row.account_id)
    @contextmanager
    def sessions(): yield db_session
    def forbidden(db, *, user_id): raise AssertionError("No broker call is needed")
    recorder = worker.StudyRecorder(sessions, forbidden)
    recorder.cycle()
    child = FakeChild.made[-1]
    assert "--checkpoints" in child.args and recorder.job["entry_id"] == row.id
    assert worker.account_activity(owner, account)["active"]["entry_id"] == row.id
    assert worker.account_activity(owner, account+1)["active"] is None  # Another account never sees it.
    # Slow work that keeps progressing outlives the old fixed 300 s deadline.
    for step in range(1, 6):
        child.progress(step*100_000)
        clock[0] += 120
        recorder.cycle()
        assert not child.terminated
    # No new progress for longer than the stall limit ends the child as a recorded failure.
    clock[0] += worker.NO_PROGRESS_SECONDS+1
    recorder.cycle()
    assert child.terminated and recorder.job is None
    state = jobs.jobs(owner, account)[(account, row.id, EXPERIMENT_ID)]
    assert state["last_outcome"] == "no_progress" and state["consecutive_failures"] == 1
    assert state["last_failure"]["category"] == "no_progress" and state["last_failure"]["progress"]["events"] == 500_000
    # A restarted reader keeps the retry schedule instead of starting over immediately.
    restarted = worker.StudyRecorder(sessions, forbidden)
    restarted.cycle()
    assert len(FakeChild.made) == 1 and restarted.job is None
    with jobs.connection(owner, write=True) as db:
        db.execute("update jobs set next_attempt_at=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    restarted.cycle()
    child = FakeChild.made[-1]
    child.finish(3, {"failure": {"category": "disk_io", "type": "OperationalError", "phase": "replay",
                                 "code": "C:\\Users\\secret\\path", "frames": ["topbot_replay_index.py:12:batches"]}})
    restarted.cycle()
    state = jobs.jobs(owner, account)[(account, row.id, EXPERIMENT_ID)]
    assert state["last_outcome"] == "child_failed" and state["consecutive_failures"] == 2
    assert state["last_failure"]["code"] == "redacted" and state["last_failure"]["category"] == "disk_io"
    assert state["first_failure"]["category"] == "no_progress"
    history = jobs.history(owner, account, row.id, EXPERIMENT_ID)
    assert [h["outcome"] for h in history] == ["child_failed", "no_progress"]
    # Checkpoint progress is not a failure and is retried at once.
    with jobs.connection(owner, write=True) as db:
        db.execute("update jobs set next_attempt_at=null")
    restarted.cycle()
    FakeChild.made[-1].finish(0, {"index_progress": {"state": "budget_exhausted", "events": 10}})
    restarted.cycle()
    assert jobs.jobs(owner, account)[(account, row.id, EXPERIMENT_ID)]["consecutive_failures"] == 0
    assert len(FakeChild.made) == 4  # Relaunched in the same cycle.
    entry = json.loads(FakeChild.made[-1].stdin.sent)["entry"]
    FakeChild.made[-1].finish(0, simulate(entry, [], gap_reason="test_gap"))
    restarted.cycle()
    assert store.completed_ids(owner, account) == {row.id}
    assert jobs.jobs(owner, account)[(account, row.id, EXPERIMENT_ID)]["last_outcome"] == "completed"


def test_recording_pressure_pauses_research_without_counting_a_failure(db_session, dom_entry, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.services import topbot_study_jobs as jobs
    from app.services.topbot_study import EXPERIMENT_ID
    worker, clock = _worker_env(monkeypatch, tmp_path)
    row = _overdue_entry(db_session, dom_entry)
    owner, account = str(row.user_id), int(row.account_id)
    @contextmanager
    def sessions(): yield db_session
    recorder = worker.StudyRecorder(sessions, lambda db, *, user_id: None)
    pressure = {"reason": "worker_capture_backlog", "queued_events": 3000}
    monkeypatch.setattr(worker, "recording_pressure", lambda: pressure)
    recorder.cycle()
    assert not FakeChild.made and worker.HEALTH["state"] == "paused_recording_pressure"
    monkeypatch.setattr(worker, "recording_pressure", lambda: None)
    recorder.cycle()
    child = FakeChild.made[-1]
    monkeypatch.setattr(worker, "recording_pressure", lambda: pressure)
    recorder.cycle()
    assert not child.terminated  # One sample is not enough; avoid flapping.
    recorder.cycle()
    assert child.terminated
    state = jobs.jobs(owner, account)[(account, row.id, EXPERIMENT_ID)]
    assert state["last_outcome"] == "paused_recording_pressure" and state["consecutive_failures"] == 0
    assert state["next_attempt_at"] is not None


def test_post_exit_retention_survives_take_profit_and_paused_entries(db_session, dom_entry, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from copy import deepcopy
    from app.models import BotConfig
    worker, clock = _worker_env(monkeypatch, tmp_path)
    result, _, _ = dom_entry
    row = result.order_attempt
    now = datetime.now(timezone.utc)
    request = deepcopy(row.raw_request)
    request["strategySnapshot"]["research_cutoff"] = (now+timedelta(minutes=90)).isoformat()
    request["timeExit"]["runner"].update(entry_at=(now-timedelta(minutes=30)).isoformat(), entry_price=20000,
        stats_status="complete", exit_reason="take_profit", exit_at=(now-timedelta(minutes=25)).isoformat())
    row.raw_request = request
    for config in db_session.query(BotConfig).all():
        config.enabled = False  # New entries paused; the trading worker no longer touches the feed.
    db_session.commit()
    @contextmanager
    def sessions(): yield db_session
    calls = []
    monkeypatch.setattr(worker.feed, "retain", lambda owner, contract, factory, until, params: calls.append((owner, contract, until)))
    worker.StudyRecorder(sessions, lambda db, *, user_id: "client").cycle()
    assert calls and calls[0][2] == datetime.fromisoformat(request["strategySnapshot"]["research_cutoff"])
    activity = worker.account_activity(str(row.user_id), int(row.account_id))
    assert activity["retention"][row.id]["state"] == "retained"
    def broken(*args): raise ConnectionError("provider down")
    monkeypatch.setattr(worker.feed, "retain", broken)
    worker.StudyRecorder(sessions, lambda db, *, user_id: "client").cycle()  # Must not raise or hide the failure.
    failed = worker.account_activity(str(row.user_id), int(row.account_id))["retention"][row.id]
    assert failed["state"] == "failed" and failed["error_type"] == "ConnectionError"


def test_retention_resolves_a_client_through_the_application_factory(db_session, dom_entry, tmp_path, monkeypatch):
    """After a stop or restart no stream exists, so retention must be able to open one."""
    from contextlib import contextmanager
    from copy import deepcopy
    from app.services import topbot_depth, topbot_study_store as store
    from app.services.topbot_study_worker import StudyRecorder
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    result, _, _ = dom_entry
    row = result.order_attempt
    request = deepcopy(row.raw_request)
    now = datetime.now(timezone.utc)
    request["strategySnapshot"]["research_cutoff"] = (now+timedelta(minutes=1)).isoformat()
    request["timeExit"]["runner"].update(entry_at=now.isoformat(), entry_price=20000, stats_status="open")
    row.raw_request = request
    db_session.commit()

    @contextmanager
    def sessions():
        yield db_session

    fresh = topbot_depth.DepthFeed()
    monkeypatch.setattr("app.services.topbot_study_worker.feed", fresh)
    opened, resolved = [], []
    monkeypatch.setattr(fresh, "ensure", lambda owner, contract, client, *args: opened.append(client))

    def factory(db, *, user_id):  # the production signature
        resolved.append(user_id)
        return "client"

    StudyRecorder(sessions, factory).cycle()
    assert resolved == [str(row.user_id)] and opened == ["client"]
