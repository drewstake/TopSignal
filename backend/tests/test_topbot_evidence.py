from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.services.topbot_depth import DepthState, DepthFeed
from app.services.topbot_evidence import Capture, recorded_batches
from app.services.topbot_replay import replay, trade_timeline
from app.services.topbot_execution_trace import entry_metrics
from app.services import topbot_audit as audit

CONTRACT = "CON.F.US.MNQ.Z26"


def test_default_queue_absorbs_market_open_burst(tmp_path, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    original = Capture._capacity
    def delayed_capacity(self, growth=0):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(self, growth)
    monkeypatch.setattr(Capture, "_capacity", delayed_capacity)
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    try:
        assert entered.wait(2)
        at = datetime.now(timezone.utc)
        for sequence in range(2500):
            cap.append({"event": "update", "data": {"contract_id": CONTRACT,
                "sequence": sequence, "timestamp": at.isoformat(), "side": "bid",
                "price": 20000, "size": 10}}, at, [])
        assert cap.dropped == 0 and cap.queue.qsize() == 2500
        assert cap.queued_bytes < 8*1024**2
    finally:
        release.set()
        cap.close()
    assert not cap.thread.is_alive()
    assert len(list(recorded_batches(cap.path))) == 2501


def book(at, sequence, bid_size=15):
    return {"event": "snapshot", "data": {"contract_id": CONTRACT, "sequence": sequence,
        "timestamp": at.isoformat(), "bids": [{"price": 20000-i*.25, "size": bid_size} for i in range(100)],
        "asks": [{"price": 20000.25+i*.25, "size": 10} for i in range(100)]}}


def test_backlogged_burst_drains_without_excessive_durable_commits(tmp_path, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    original = Capture._capacity
    commits = []
    def delayed_capacity(self, growth=0):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        if growth:
            commits.append(growth)
        return original(self, growth)
    monkeypatch.setattr(Capture, "_capacity", delayed_capacity)
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    try:
        assert entered.wait(2)
        at = datetime.now(timezone.utc)
        for sequence in range(4000):
            cap.append({"event": "update", "data": {"contract_id": CONTRACT,
                "sequence": sequence, "timestamp": at.isoformat(), "side": "bid",
                "price": 20000, "size": 10}}, at, [])
        assert cap.dropped == 0
    finally:
        release.set()
        cap.close()
    assert not cap.thread.is_alive()
    rows = list(recorded_batches(cap.path))
    assert len(rows) == 4001 and cap.durable_index == 4000
    # On a contended disk, one fsync per small slice cannot keep up with bursts.
    # This bounds disk work while still checking every event and the hash chain.
    assert len(commits) <= 4


def recorded(tmp_path, *, missing=False):
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    state = DepthState(CONTRACT)
    state.capture = cap
    at = datetime.now(timezone.utc)
    state.apply({"event": "parameters", "data": {"threshold": .1, "persistence_seconds": 30}}, at)
    for index in range(31):
        if missing and index == 10:
            # Oversize capture is explicitly lost; the trading state still receives its next event.
            cap.append({"event": "update", "data": {"price": "x"*1100000}}, at, [])
        state.apply(book(at+timedelta(seconds=index), index+1), at+timedelta(seconds=index))
    observed_at = at+timedelta(seconds=30, milliseconds=100)
    observation = {**state.snapshot(observed_at), "checked_at": observed_at.isoformat(), "evidence": state.evidence_snapshot()}
    cap.close()
    assert not cap.thread.is_alive()
    return cap, observation


def test_exact_worker_state_replay_and_episode_changes(tmp_path):
    cap, observation = recorded(tmp_path)
    result = replay(cap.path, observation=observation)
    assert result["observation_verification"]["verified"]
    assert result["qualifying_episodes_by_action"] == {"SELL": 1}
    assert result["recording_status"]["state"] == "closed"
    candidate = replay(cap.path, threshold=.25)
    assert candidate["qualifying_episodes_by_action"] == {}
    assert replay(cap.path, persistence_seconds=31)["qualifying_episodes_by_action"] == {}
    assert "broker fills are not simulated" in " ".join(result["limitations"])


def test_missing_capture_event_prevents_reproduction_claim(tmp_path):
    cap, observation = recorded(tmp_path, missing=True)
    result = replay(cap.path, observation=observation)
    assert not result["continuous_prefix"]
    assert not result["observation_verification"]["verified"]
    assert result["recording_status"]["dropped_events"] == 1
    assert result["capture_gap_examples"]


def test_wrong_capture_unknown_tail_and_code_mismatch_fail_closed(tmp_path, monkeypatch):
    cap, observation = recorded(tmp_path)
    from copy import deepcopy
    wrong = deepcopy(observation)
    wrong["evidence"]["capture_id"] = "other"
    with pytest.raises(ValueError, match="capture_mismatch"): replay(cap.path, observation=wrong)
    wrong = deepcopy(observation)
    wrong["evidence"]["event_index"] = 999
    with pytest.raises(ValueError, match="missing_or_not_durable"): replay(cap.path, observation=wrong)
    monkeypatch.setattr("app.services.topbot_replay.binding", lambda: {"code_sha256": "different"})
    with pytest.raises(ValueError, match="implementation_mismatch"): replay(cap.path)
    assert not replay(cap.path, observation=observation, allow_code_mismatch=True)["observation_verification"]["verified"]


def test_tampered_payload_rejected(tmp_path):
    cap, _ = recorded(tmp_path)
    with sqlite3.connect(cap.path) as db:
        db.execute("DROP TRIGGER no_batch_update")
        db.execute("UPDATE batches SET hash='wrong' WHERE id=1")
    with pytest.raises(ValueError, match="invalid_evidence_batch"): list(recorded_batches(cap.path))


def test_storage_budget_stops_capture_without_deletion(tmp_path):
    retained = tmp_path / "keep.txt"
    retained.write_text("retain me")
    cap = Capture("owner", CONTRACT, root=tmp_path, budget=1, free_floor=0)
    cap.close()
    assert cap.state == "storage_blocked"
    assert retained.read_text() == "retain me"
    cap.append(book(datetime.now(timezone.utc), 1), datetime.now(timezone.utc), [])
    assert cap.dropped == 1


def test_full_queue_does_not_block_trading_and_reports_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(Capture, "_run", lambda self: self.stop.wait())
    cap = Capture("owner", CONTRACT, root=tmp_path, queue_size=1)
    state = DepthState(CONTRACT)
    state.capture = cap
    now = datetime.now(timezone.utc)
    state.apply(book(now, 1), now)
    state.apply(book(now+timedelta(seconds=1), 2), now+timedelta(seconds=1))
    assert state.snapshot(now+timedelta(seconds=1))["ready"]
    assert cap.dropped == 1 and cap.index == 2
    cap.close()


def test_capture_strips_nonconsumed_messages(tmp_path):
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    cap.append({"event": "state", "data": {"state": "connected", "message": "token=secret", "credentials": "secret"}},
               datetime.now(timezone.utc), [])
    cap.close()
    assert "secret" not in json.dumps(list(recorded_batches(cap.path)))


def test_unexpected_recorder_exception_does_not_stop_depth(caplog):
    def fail(*a, **kw): raise OSError("private path and token=secret")
    state = DepthState(CONTRACT)
    state.capture = SimpleNamespace(append=fail)
    now = datetime.now(timezone.utc)
    state.apply(book(now, 1), now)
    assert state.snapshot(now)["ready"] and state.capture is None
    assert "topbot_evidence_enqueue_failed" in caplog.text and "secret" not in caplog.text


def test_capture_constructor_failure_still_subscribes_to_market(monkeypatch):
    from app.services import projectx_order_book, topbot_evidence
    def fail(*a, **kw): raise OSError("disk unavailable")
    monkeypatch.setattr(topbot_evidence, "Capture", fail)
    calls = []
    async def subscribe(*a, **kw):
        calls.append(kw["contract_id"])
        return SimpleNamespace(initial_events=[])
    async def close(*a, **kw): pass
    monkeypatch.setattr(projectx_order_book, "ProjectXOrderBookRegistry", lambda: SimpleNamespace(subscribe=subscribe, close=close))
    feed = DepthFeed()
    stream = {"state": DepthState(CONTRACT), "touched": 0}
    feed.streams[("owner", CONTRACT)] = stream
    feed._run(("owner", CONTRACT), stream, object())
    assert calls == [CONTRACT]


def test_existing_cache_backup_preserves_replayable_evidence(tmp_path):
    from tools.backup_market_cache import backup
    cap, observation = recorded(tmp_path / "source" / "topbot-worker-evidence-v1")
    output = tmp_path / "backup"
    backup(tmp_path / "source", output)
    restored = output / cap.path.relative_to(tmp_path / "source")
    assert replay(restored, observation=observation)["observation_verification"]["verified"]


def test_offline_cli_executes_real_replay(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    cap, observation = recorded(tmp_path)
    saved = tmp_path / "observation.json"
    saved.write_text(json.dumps(observation))
    tool = Path(__file__).parents[1] / "tools/replay_topbot_dom.py"
    completed = subprocess.run([sys.executable, "-B", str(tool), "replay", str(cap.path), "--observation", str(saved)],
                               check=True, capture_output=True, text=True, timeout=10)
    assert json.loads(completed.stdout)["observation_verification"]["verified"]


def test_age_review_uses_original_capture_date_not_recent_file_mtime(tmp_path):
    old = tmp_path / "old.sqlite3"
    with sqlite3.connect(old) as db:
        db.execute("CREATE TABLE metadata(value TEXT)")
        db.execute("INSERT INTO metadata VALUES(?)", (json.dumps({"started_at": (datetime.now(timezone.utc)-timedelta(days=31)).isoformat()}),))
    cap = Capture("owner", CONTRACT, root=tmp_path, free_floor=0)
    cap.close()
    assert cap.state == "storage_blocked"
    assert old.exists()


def test_coverage_tracks_missing_intervals_and_never_certifies_complete():
    now = datetime.now(timezone.utc)
    feed = DepthFeed()
    state = DepthState(CONTRACT)
    feed.streams[("owner", CONTRACT)] = {"state": state, "touched": 0}
    seed = {"side": "BUY", "entry_at": (now-timedelta(seconds=5)).isoformat(), "entry_price": 20000,
            "mfe_points": 0, "mae_points": 0, "coverage_complete": False}
    feed.track("owner", CONTRACT, 1, seed)
    state.apply(book(now+timedelta(seconds=1), 1), now+timedelta(seconds=1))
    row = feed.track("owner", CONTRACT, 1, seed)
    assert row["coverage"]["gap_count"] == 1
    assert row["coverage"]["gap_seconds"] >= 6
    state.apply({"event": "state", "data": {"state": "reconnecting"}}, now+timedelta(seconds=2))
    state.apply(book(now+timedelta(seconds=4), 2), now+timedelta(seconds=4))
    row = feed.track("owner", CONTRACT, 1, seed)
    assert row["coverage"]["gap_count"] == 2
    assert row["coverage"]["last_gap"]["reason"] == "disconnected"
    assert row["coverage"]["last_gap"]["seconds"] == 3
    assert row["coverage_complete"] is False


def test_terminal_consumer_error_logged_without_secret(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("TOPSIGNAL_TOPBOT_EVIDENCE_DIR", str(tmp_path))
    from app.services import projectx_order_book
    async def fail(*a, **kw): raise RuntimeError("token=secret")
    async def close(*a, **kw): pass
    monkeypatch.setattr(projectx_order_book, "ProjectXOrderBookRegistry", lambda: SimpleNamespace(subscribe=fail, close=close))
    feed = DepthFeed()
    stream = {"state": DepthState(CONTRACT), "touched": 0}
    feed.streams[("owner", CONTRACT)] = stream
    feed._run(("owner", CONTRACT), stream, object())
    assert "topbot_depth_consumer_failed" in caplog.text
    assert "secret" not in caplog.text
    assert not feed.streams


def test_latency_slippage_and_clock_skew_are_explicit():
    request = {"entryEvidence": {"decision_at": "2026-11-03T12:00:00+00:00", "pre_submit": {
        "ready": True, "bid": 20000, "ask": 20000.25,
        "checked_at": "2026-11-03T12:00:01+00:00", "observed_at": "2026-11-03T12:00:00.9+00:00"}},
        "submissionTiming": {"submitted_at": "2026-11-03T12:00:01+00:00", "request_duration_ms": 250}}
    result = entry_metrics(request, "BUY", 20000.75, "2026-11-03T12:00:01.1+00:00", "2026-11-03T12:00:04+00:00")
    assert result["entry_slippage_ticks"] == 2 and result["submit_to_fill_ms"] == 100
    assert result["decision_to_submit_ms"] == 1000 and result["fill_to_observation_ms"] == 2900
    result = entry_metrics(request, "SELL", 19999.75, "2026-11-03T12:00:00+00:00", "2026-11-03T12:00:04+00:00")
    assert result["entry_slippage_ticks"] == 1 and result["submit_to_fill_ms"] == -1000
    request["entryEvidence"]["pre_submit"]["ready"] = False
    assert entry_metrics(request, "BUY", 20000, None, None)["entry_slippage_ticks"] is None


def test_timeline_includes_linked_evaluation_but_not_other_account():
    audit.record("owner", 1, "evaluation_started", correlation_id="correlation")
    audit.record("owner", 1, "evaluation_result", correlation_id="correlation", order_attempt_id=7)
    client = audit.AuditedClient(SimpleNamespace(place_order=lambda **kw: {"order_id": "1"}),
                                {"owner": "owner", "account_id": 1, "entry_id": 7})
    client.place_order(account_id=1)
    audit.record("owner", 2, "secret_other_account", entry_id=7, correlation_id="correlation")
    result = trade_timeline(audit.location("owner"), account_id=1, entry_id=7)
    assert len(result["events"]) == 4
    assert result["events"][0]["kind"] == "evaluation_started"
    assert result["operations"][0]["record_to_record_ms"] >= 0
    assert result["operations"][0]["request_timing"]["request_duration_ms"] >= 0
    assert "secret_other_account" not in json.dumps(result)
