"""The checkpointed reader must reproduce the recorded full-scan reader exactly."""
from datetime import datetime, timedelta, timezone
import random
import sqlite3
import time
import zlib

import pytest

from app.services import topbot_replay_index as index
from app.services.topbot_evidence import Capture
from app.services.topbot_study import simulate
from app.services.topbot_study_store import quotes

OWNER = "owner-a"
CONTRACT = "CON.F.US.MNQ.Z26"
START = datetime(2026, 11, 11, 14, tzinfo=timezone.utc)


def _flush(cap, timeout=10):
    deadline = time.monotonic()+timeout
    while cap.durable_index < cap.index and time.monotonic() < deadline:
        time.sleep(.005)


def market_capture(root, *, seed=7, minutes=12, holes=True, owner=OWNER):
    """A recorded stream with every condition the reader treats specially."""
    rng = random.Random(seed)
    cap = Capture(owner, CONTRACT, root=root, free_floor=0)
    at, seq, mid = START, 0, 20000.
    times = []
    end = START+timedelta(minutes=minutes)
    group = 0
    while at < end:
        roll = rng.random()
        at += timedelta(milliseconds=rng.choice((40, 90, 150, 300, 700)))
        if roll < .004:
            at += timedelta(seconds=3.5)  # silence: observed book becomes stale
        provider = at-timedelta(milliseconds=30)
        if roll > .997:
            provider = at-timedelta(seconds=4)  # stale provider timestamp resets the book
        seq += 1
        if .004 <= roll < .007:
            seq += 1  # provider sequence gap
        mid += rng.choice((-.25, 0, .25))
        if roll < .03 or seq == 1:
            event = {"event": "snapshot", "data": {"contract_id": CONTRACT, "sequence": seq, "timestamp": provider.isoformat(),
                     "bids": [{"price": mid-.25*i, "size": rng.randint(1, 9)} for i in range(1, 40)],
                     "asks": [{"price": mid+.25*i, "size": rng.randint(1, 9)} for i in range(0, 40)]}}
        elif roll < .035:
            event = {"event": "observation_gap", "data": {"reason": "stream_silent"}}
        else:
            side = rng.choice(("bid", "ask"))
            price = mid-.25*rng.randint(1, 20) if side == "bid" else mid+.25*rng.randint(0, 20)
            event = {"event": "update", "data": {"contract_id": CONTRACT, "sequence": seq, "timestamp": provider.isoformat(),
                     "side": side, "price": price, "size": rng.choice((0, 1, 2, 5))}}
        if holes and rng.random() < .002:
            cap.index += rng.randint(1, 30)  # events lost before durable capture
            cap.dropped += 1
        cap.append(event, at, [])
        times.append(at)
        group += 1
        if group % 97 == 0:
            _flush(cap)
    _flush(cap)
    cap.close()
    return cap, times


def entry(start, cutoff, side="BUY", price=20000.):
    return {"entry_id": 9, "entry_at": start.isoformat(), "entry_price": price, "side": side,
            "strategy_version_id": "v", "strategy_snapshot": {"initial_risk_usd": 50, "quantity": 1,
            "initial_risk_points": 25, "contract_id": CONTRACT, "session": "2026-11-11",
            "research_cutoff": cutoff.isoformat()}}


def test_mirror_fingerprints_match_recorded_reader():
    status = index.mirror_status()
    assert status["available"], status


@pytest.mark.parametrize("seed", [3, 11, 29])
def test_checkpointed_reader_equals_full_scan_for_every_start(tmp_path, seed):
    cap, times = market_capture(tmp_path/"evidence", seed=seed)
    root = tmp_path/"checkpoints"
    summary = index.advance(cap.path, OWNER, CONTRACT, root=root, interval=5)
    assert summary["state"] == "end_of_capture" and summary["checkpoints_written"] > 20
    rng = random.Random(seed)
    starts = sorted(rng.sample(times[len(times)//10:], 25)) + [times[-1], times[len(times)//2]]
    checked = 0
    for start in starts:
        for offset in (0, .001, -.001):
            s = start+timedelta(seconds=offset)
            cutoff = s+timedelta(seconds=rng.choice((2, 30, 120, 900)))
            expected = list(quotes(cap.path, OWNER, CONTRACT, s, cutoff))
            report = {}
            try:
                actual = list(index.resumed_quotes(cap.path, OWNER, CONTRACT, s, cutoff, root=root, report=report))
            except LookupError:
                continue  # Before the first checkpoint the original full scan is used.
            assert actual == expected
            assert report["reader"] == "checkpoint-v1"
            for side in ("BUY", "SELL"):
                e = entry(s, cutoff, side, 20000.)
                assert simulate(e, iter(actual)) == simulate(e, iter(expected))
            checked += 1
    assert checked > 40


def test_interrupted_builder_resumes_without_rework_and_matches_single_pass(tmp_path):
    cap, times = market_capture(tmp_path/"evidence", seed=5)
    one, many = tmp_path/"one", tmp_path/"many"
    index.advance(cap.path, OWNER, CONTRACT, root=one, interval=5)
    rounds = 0
    while True:
        rounds += 1
        result = index.advance(cap.path, OWNER, CONTRACT, root=many, interval=5, budget_seconds=0)
        if result["state"] == "end_of_capture" and result["events"] == 0:
            break
        assert rounds < 10_000
    assert rounds > 3
    single = sqlite3.connect(one/cap.path.parent.name/(cap.capture_id+".sqlite3"))
    chunked = sqlite3.connect(many/cap.path.parent.name/(cap.capture_id+".sqlite3"))
    last_single = single.execute("select batch_id,state_sha256,last_index,damaged from checkpoints order by batch_id desc limit 1").fetchone()
    last_chunked = chunked.execute("select batch_id,state_sha256,last_index,damaged from checkpoints order by batch_id desc limit 1").fetchone()
    assert last_single == last_chunked
    s = times[len(times)*3//4]
    c = s+timedelta(minutes=5)
    assert list(index.resumed_quotes(cap.path, OWNER, CONTRACT, s, c, root=many)) == list(quotes(cap.path, OWNER, CONTRACT, s, c))


def test_tampered_checkpoint_or_capture_anchor_is_rejected(tmp_path):
    cap, times = market_capture(tmp_path/"evidence", seed=8, holes=False)
    root = tmp_path/"checkpoints"
    index.advance(cap.path, OWNER, CONTRACT, root=root, interval=5)
    path = root/cap.path.parent.name/(cap.capture_id+".sqlite3")
    db = sqlite3.connect(path)
    for batch, blob in db.execute("select batch_id,state from checkpoints").fetchall():
        db.execute("update checkpoints set state=? where batch_id=?", (zlib.compress(zlib.decompress(blob)+b" "), batch))
    db.commit(); db.close()
    with pytest.raises(ValueError, match="checkpoint_hash_mismatch"):
        list(index.resumed_quotes(cap.path, OWNER, CONTRACT, times[-1], times[-1]+timedelta(seconds=5), root=root))
    db = sqlite3.connect(path)
    db.execute("update checkpoints set batch_hash='0'||substr(batch_hash,2)")
    db.commit(); db.close()
    s = times[len(times)//2]
    with pytest.raises(ValueError, match="checkpoint_(anchor|hash)_mismatch"):
        list(index.resumed_quotes(cap.path, OWNER, CONTRACT, s, s+timedelta(seconds=5), root=root))


def test_changed_recorded_reader_disables_mirror(tmp_path, monkeypatch):
    cap, times = market_capture(tmp_path/"evidence", seed=9, minutes=2)
    monkeypatch.setitem(index.MIRRORED_SOURCES, "topbot_study_store.quotes", "0"*64)
    assert index.advance(cap.path, OWNER, CONTRACT, root=tmp_path/"c")["state"] == "mirror_unavailable"
    with pytest.raises(LookupError, match="recorded_reader_changed"):
        list(index.resumed_quotes(cap.path, OWNER, CONTRACT, times[-1], times[-1], root=tmp_path/"c"))


def test_other_owner_cannot_use_capture_or_checkpoints(tmp_path):
    cap, times = market_capture(tmp_path/"evidence", seed=4, minutes=2)
    with pytest.raises(ValueError, match="capture_scope_mismatch"):
        index.advance(cap.path, "owner-b", CONTRACT, root=tmp_path/"c")
    index.advance(cap.path, OWNER, CONTRACT, root=tmp_path/"c", interval=5)
    with pytest.raises(ValueError, match="capture_scope_mismatch"):
        list(index.resumed_quotes(cap.path, "owner-b", CONTRACT, times[-1], times[-1], root=tmp_path/"c"))


def test_store_budget_stops_writing_and_never_deletes(tmp_path, monkeypatch):
    cap, _ = market_capture(tmp_path/"evidence", seed=2, minutes=2)
    root = tmp_path/"c"
    owner_dir = root/cap.path.parent.name
    owner_dir.mkdir(parents=True)
    keep = owner_dir/("f"*32+".sqlite3")
    keep.write_bytes(b"x"*1024)
    store = index.Store(cap.path.parent.name, cap.capture_id, root=root, budget=4096)
    assert not store.writable() and store.blocked == "checkpoint_budget_reached"
    monkeypatch.setattr(index, "MAX_OWNER_BYTES", 4096)
    assert index.advance(cap.path, OWNER, CONTRACT, root=root)["state"] == "store_blocked"
    assert keep.exists() and not (owner_dir/(cap.capture_id+".sqlite3")).exists()


def test_segment_coverage_reports_holes_gaps_and_unready_book(tmp_path):
    cap, times = market_capture(tmp_path/"evidence", seed=13, minutes=6)
    root = tmp_path/"c"
    index.advance(cap.path, OWNER, CONTRACT, root=root, interval=5)
    report = index.coverage(cap.path, times[0], times[-1], root=root)
    assert report["state"] == "complete"
    assert report["events"] == len(times)
    assert report["max_receipt_gap_seconds"] >= 3.5
    assert report["not_ready_events"] > 0
    assert index.coverage(cap.path, times[-1]+timedelta(hours=1), times[-1]+timedelta(hours=2), root=root)["state"] == "not_indexed"


def _child(capture, payload, root, *extra):
    import json, os, subprocess, sys
    from pathlib import Path
    env = {**os.environ, "TOPSIGNAL_REPLAY_CHECKPOINT_DIR": str(root), "TOPSIGNAL_RESEARCH_IO_MODE": "normal"}
    done = subprocess.run([sys.executable, "-B", str(Path(__file__).parents[1]/"tools/compare_topbot_exits.py"),
                           "--capture", str(capture), *extra], input=json.dumps(payload), text=True,
                          capture_output=True, timeout=120, env=env)
    return done.returncode, json.loads(done.stdout), done.stderr


def test_child_checkpoint_replay_matches_full_scan_and_reports_provenance(tmp_path):
    from app.services.topbot_study import MODEL_CODE, EXPERIMENT_ID
    cap, times = market_capture(tmp_path/"evidence", seed=21, minutes=10)
    s = times[len(times)*4//5]
    e = entry(s, s+timedelta(minutes=3))
    e["strategy_snapshot"].update(study_model_code=MODEL_CODE, experiment_id=EXPERIMENT_ID)
    payload = {"entry": e, "owner": OWNER, "contract": CONTRACT}
    code, full, _ = _child(cap.path, payload, tmp_path/"none")
    assert code == 0 and full["replay"]["reader"] == "full-scan"
    code, partial, _ = _child(cap.path, payload, tmp_path/"cp", "--checkpoints", "--index-budget", "0")
    assert code == 0 and partial["index_progress"]["state"] == "budget_exhausted"
    rounds = 0
    while "index_progress" in partial:
        rounds += 1
        assert rounds < 500
        code, partial, _ = _child(cap.path, payload, tmp_path/"cp", "--checkpoints", "--index-budget", "0.5")
    assert code == 0 and partial["replay"]["reader"] == "checkpoint-v1"
    strip = lambda r: {k: v for k, v in r.items() if k != "replay"}
    assert strip(partial) == strip(full)
    # A second replay of the same entry reuses the saved checkpoints without indexing.
    code, again, _ = _child(cap.path, payload, tmp_path/"cp", "--checkpoints")
    assert "index" not in again["replay"] and strip(again) == strip(full)


def test_child_refuses_other_recorded_model_and_falls_back_on_bad_checkpoint(tmp_path):
    from app.services.topbot_study import MODEL_CODE, EXPERIMENT_ID
    cap, times = market_capture(tmp_path/"evidence", seed=22, minutes=8)
    s = times[len(times)*3//4]
    e = entry(s, s+timedelta(minutes=2))
    e["strategy_snapshot"].update(study_model_code={**MODEL_CODE, "topbot_study.py": "0"*64}, experiment_id=EXPERIMENT_ID)
    code, out, _ = _child(cap.path, {"entry": e, "owner": OWNER, "contract": CONTRACT}, tmp_path/"cp", "--checkpoints")
    assert code == 3 and out["failure"]["code"] == "recorded_model_unavailable" and "variants" not in out
    e["strategy_snapshot"]["study_model_code"] = MODEL_CODE
    payload = {"entry": e, "owner": OWNER, "contract": CONTRACT}
    index.advance(cap.path, OWNER, CONTRACT, root=tmp_path/"cp", interval=5)
    path = tmp_path/"cp"/cap.path.parent.name/(cap.capture_id+".sqlite3")
    db = sqlite3.connect(path)
    db.execute("update checkpoints set state_sha256='0'||substr(state_sha256,2)")
    db.commit(); db.close()
    code, out, _ = _child(cap.path, payload, tmp_path/"cp", "--checkpoints")
    _, full, _ = _child(cap.path, payload, tmp_path/"none")
    assert code == 0 and out["replay"]["reader"] == "full-scan" and out["replay"]["checkpoint_error"] == "checkpoint_hash_mismatch"
    assert {k: v for k, v in out.items() if k != "replay"} == {k: v for k, v in full.items() if k != "replay"}


def test_later_entry_extends_existing_index_instead_of_reusing_old_prefix(tmp_path):
    cap, times = market_capture(tmp_path/"evidence", seed=23, minutes=18)
    root = tmp_path/"cp"
    early, later = times[len(times)//3], times[len(times)*5//6]
    index.advance(cap.path, OWNER, CONTRACT, until=early, root=root)
    old = index.locate(cap.path, OWNER, CONTRACT, later, root=root)
    payload = {"entry": entry(later, later+timedelta(minutes=2)), "owner": OWNER, "contract": CONTRACT}
    code, result, _ = _child(cap.path, payload, root, "--checkpoints")
    assert code == 0 and result["replay"]["index"]["state"] == "reached"
    assert result["replay"]["checkpoint_batch"] > old["batch_id"]
    assert index.locate(cap.path, OWNER, CONTRACT, later, root=root, require_indexed_through=True)
    _, full, _ = _child(cap.path, payload, tmp_path/"none")
    assert {k: v for k, v in result.items() if k != "replay"} == {k: v for k, v in full.items() if k != "replay"}
