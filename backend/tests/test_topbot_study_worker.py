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
    def forbidden(owner): raise AssertionError("No broker call is needed")
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


def test_overdue_replay_is_reported_once_deferred_and_cleared_by_success(db_session, dom_entry, tmp_path, monkeypatch):
    import io
    import pytest
    from contextlib import contextmanager
    from copy import deepcopy
    from app.services import topbot_study_store as store
    from app.services import topbot_study_worker as worker
    monkeypatch.setattr(store, "default_cache_root", lambda: tmp_path)
    result, _, _ = dom_entry
    row = result.order_attempt
    request = deepcopy(row.raw_request)
    now = datetime.now(timezone.utc)
    request["strategySnapshot"]["research_cutoff"] = (now-timedelta(minutes=1)).isoformat()
    request["timeExit"]["runner"].update(entry_at=(now-timedelta(minutes=30)).isoformat(), entry_price=20000, stats_status="open")
    request["entryEvidence"] = {"pre_submit": {"evidence": {"capture_id": "ab"*16}}}
    row.raw_request = request
    db_session.commit()
    @contextmanager
    def sessions(): yield db_session
    children = []
    class Input(io.BytesIO):
        def close(self): self.sent = self.getvalue(); super().close()
    class Child:
        def __init__(self, *args, **kwargs):
            self.stdin, self.code, self.terminated = Input(), None, False
            children.append(self)
        def poll(self): return self.code
        def terminate(self): self.terminated, self.code = True, 1
        def wait(self, timeout=None): return self.code
        def kill(self): self.code = 1
    monkeypatch.setattr(subprocess, "Popen", Child)
    def forbidden(owner): raise AssertionError("No broker call is needed")
    recorder = worker.StudyRecorder(sessions, forbidden)
    recorder.cycle()
    assert len(children) == 1 and recorder.job["entry_id"] == row.id
    recorder.job["started"] -= worker.DEADLINE_SECONDS+1
    with pytest.raises(TimeoutError):
        recorder.cycle()
    assert children[0].terminated and recorder.job is None
    assert recorder.failures[row.id][0] == 1 and worker.HEALTH["deferred_replays"] == 1
    # The terminated child is not reported a second time and is not restarted at once.
    recorder.cycle()
    recorder.cycle()
    assert len(children) == 1
    recorder.failures[row.id] = (1, 0)  # Retry time reached.
    recorder.cycle()
    assert len(children) == 2
    children[1].code = 3
    with pytest.raises(RuntimeError):
        recorder.cycle()
    assert recorder.failures[row.id][0] == 2 and recorder.job is None
    recorder.failures[row.id] = (2, 0)
    recorder.cycle()
    assert len(children) == 3
    entry = json.loads(children[2].stdin.sent)["entry"]
    recorder.job["output"].write(json.dumps(simulate(entry, [], gap_reason="test_gap")).encode())
    children[2].code = 0
    recorder.cycle()
    assert recorder.failures == {} and recorder.job is None and len(children) == 3
    assert store.completed_ids(str(row.user_id), int(row.account_id)) == {row.id}
