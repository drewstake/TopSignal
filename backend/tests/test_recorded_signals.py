from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import os

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
from app import main
from app.services import recorded_signals as replay
from app.services.imbalance_study import directory, open_store
from app.services.topbot_evidence import Capture
from test_imbalance_study import track_imbalances
from test_trade_progress_routes import OWNER, db


CONTRACT = "CON.F.US.MNQ.Z26"
START = datetime(2026, 11, 17, 15, tzinfo=timezone.utc)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_DATABENTO_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("TOPSIGNAL_TOPBOT_EVIDENCE_DIR", raising=False)
    # Exercise the production routes and query constraints without starting the live API workers.
    app = FastAPI()
    for route in main.app.routes:
        if "/recorded-signals" in getattr(route, "path", ""):
            app.add_api_route(route.path, route.endpoint, methods=route.methods)
    app.dependency_overrides[main._recorded_signal_owner] = lambda: "owner"
    return app


def record(root):
    from app.services.topbot_depth import DepthState
    capture = Capture("owner", CONTRACT, root=root / "topbot-worker-evidence-v1", free_floor=0)
    state = DepthState(CONTRACT)
    with closing(capture):
        for second in range(36):
            now = START + timedelta(seconds=second)
            # The original bid-heavy qualification is on the 30th second.
            bid = 100 if second <= 30 else 102 if second <= 32 else 98
            event = dict(event="snapshot", data=dict(contract_id=CONTRACT, timestamp=now.isoformat(),
                sequence=second+1, bids=[dict(price=bid, size=9)], asks=[dict(price=bid+.25, size=5)]))
            state.apply(event, now)
            capture.append(event, now, state.transitions)
    folder = directory(root, "owner", 7)
    folder.mkdir(parents=True)
    with closing(open_store(folder / "study.sqlite3")) as db, db:
        track_imbalances.worker_chunk(capture.path, db, "owner", CONTRACT, .1, 30, START.timestamp())
    return folder


def test_account_ownership_and_cache_policy(db):
    response = Response()
    assert main._recorded_signal_owner(1, response, db) == OWNER
    assert response.headers["Cache-Control"] == "no-store"
    with pytest.raises(HTTPException) as denied:
        main._recorded_signal_owner(2, response, db)
    assert denied.value.status_code == 404


def test_prior_day_paging_keeps_qualifications_separate_from_controls_and_other_sources(api, tmp_path):
    folder = directory(tmp_path, "owner", 7)
    folder.mkdir(parents=True)
    start = START.timestamp()
    rows = [("Worker", CONTRACT, start+i, 1) for i in range(34)]
    rows.extend([("Worker", CONTRACT, start+86400, -1), ("Worker", CONTRACT, start+36, 0),
                 ("Independent", CONTRACT, start+37, 1), ("Worker", "CON.F.US.NQ.Z26", start+38, 1)])
    with closing(open_store(folder / "study.sqlite3")) as study, study:
        study.executemany("INSERT INTO anchors(source,contract,at,side) VALUES(?,?,?,?)", rows)
    path = "/api/accounts/7/recorded-signals"
    with TestClient(api) as client:
        latest = client.get(path).json()
        assert latest["day"] == "2026-11-18" and latest["previous_day"] == "2026-11-17"
        first = client.get(path, params={"day": latest["previous_day"]}).json()
        rest = client.get(path, params={"day": first["day"], "before": first["next_before"]}).json()
    assert [r["id"] for r in first["signals"]+rest["signals"]] == list(range(34, 0, -1))
    assert rest["next_day"] == latest["day"] and rest["next_before"] is None


def test_capture_without_any_web_client_and_replay_prior_day(api, tmp_path):
    url = "/api/accounts/7/recorded-signals"
    with TestClient(api) as client:
        assert client.get(url).json()["signals"] == []
    folder = record(tmp_path)  # Both pages and the API client are closed throughout capture and indexing.
    with TestClient(api) as client:
        empty = client.get(url, params={"day": "2026-11-19"}).json()
        assert empty["signals"] == [] and empty["previous_day"] == "2026-11-17"
        page = client.get(url, params={"day": empty["previous_day"]}).json()
        assert len(page["signals"]) == 1
        signal = page["signals"][0]
        assert signal["imbalance"] == "bid-heavy"
        path = f"{url}/{signal['id']}/replay"
        bracket = dict(stop_a=1, target_a=1, stop_b=1, target_b=4, direction="following", quantity=2)
        row = client.get(path, params=bracket).json()
        assert (row["at"], row["id"], row["entry"], row["side"]) == (signal["at"], signal["id"], 100.25, "Long")
        assert row["exits"]["a"]["net"] == 1.56  # $4.00 gross less $2.44.
        assert row["exits"]["b"]["net"] == -12.44
        assert row["exits"]["b"]["reason"] == "Stop"
        switched = client.get(path, params={**bracket, "target_a": 4, "target_b": 1}).json()
        assert switched["exits"] == {"a": row["exits"]["b"], "b": row["exits"]["a"]}
        assert client.get(path, params={**bracket, "stop_a": 0.1}).status_code == 422
        assert client.get(path, params={**bracket, "target_a": 0}).status_code == 422
        assert client.get(path.replace("accounts/7", "accounts/8"), params=bracket).status_code == 404
        assert replay.signals("other-owner", 7, date(2026, 11, 17), None)["signals"] == []
        with closing(open_store(folder / "study.sqlite3")) as db, db:
            db.execute("INSERT INTO gaps SELECT 'Worker', capture, at+2, 'disconnect' FROM anchors WHERE id=?", (signal["id"],))
        partial = client.get(path, params=bracket).json()
        assert partial["exits"] == {"a": row["exits"]["a"]}
        assert partial["coverage"] == "Recorded quote gap"
