import sys
from datetime import UTC, datetime
from pathlib import Path

from app.services.imbalance_study import Observations, open_store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import track_imbalances
from imbalance_report import observed, reconcile, version_label
from imbalance_study import EpisodeMatches


def test_version_labels_use_saved_rules_and_limits():
    trade = {"mode": "fixed", "contract": "CON.F.US.MNQ.Z26"}
    entry = {"revision": "dom_contrarian_v1", "imbalance_direction": "following"}
    plan = {"initial_stop_points": 25, "take_profit_points": 25}
    limits = {"daily_profit_target": 800, "max_daily_loss": "800.000000"}
    assert version_label(trade, entry, plan, limits, 2) == "Follow imbalance · 2 MNQ · 25-pt fixed bracket · ±$800/day"
    assert version_label(trade, {"revision": "dom_contrarian_v1"}, plan, {}, 1).startswith("Contrarian · 1 MNQ")
    assert version_label(trade, {}, {}, {}, None) == "Fixed bracket"


def event(at):
    return {"kind": "episode_qualified", "direction": -1, "episode_at": str(at - 30)}


def previous_rules(db, cap, at):
    attempt = dict(id=1, user_id="o", account_id=2, bot_config_id=3, provider_order_id=None,
                   runner={}, capture=cap, episode="prior", at=at, version="v1", direction="contrarian",
                   contract_id="MNQ", status="blocked")
    reconcile(db, EpisodeMatches([attempt], [], "o", 2, 3), [])


def test_pairs_are_prior_same_hour_nonoverlapping_and_side_correct(tmp_path):
    db = open_store(tmp_path / "study.sqlite3")
    start = datetime(2026, 11, 17, 15, tzinfo=UTC).timestamp()
    obs = Observations(db, "Worker", "cap", "MNQ", start)
    for n in range(1100):
        is_signal = n in (600, 601)
        price = 101 if 630 <= n < 700 else 100
        events = [event(start + 600)] if is_signal else []
        obs.observe(
            start + n,
            {"ready": True, "bid": price, "ask": price + 0.25, "imbalance": 0.18 if is_signal else 0},
            events,
            0.1,
            30,
        )
    row = db.execute("SELECT * FROM anchors WHERE side=1").fetchone()
    other = db.execute("SELECT * FROM anchors WHERE id=?", (row["control"],)).fetchone()
    assert row["at"] - other["at"] >= 300
    previous_rules(db, "cap", start)
    report = observed(db, "Worker", "MNQ", start, start + 1100)
    assert report["total"] == 1
    assert report["horizons"][0]["pairs"] == 1
    assert report["horizons"][0]["signal"] == 0.75
    assert report["horizons"][0]["baseline"] == -0.25
    assert report["horizons"][0]["difference"] == 1
    assert report["horizons"][2]["signal"] == -0.25


def test_gap_keeps_episode_blocks_horizons_and_short_pays_spread(tmp_path):
    db = open_store(tmp_path / "study.sqlite3")
    book = {"ready": True, "bid": 100, "ask": 100.25, "imbalance": 0.2}
    obs = Observations(db, "Worker", "first", "MNQ", 1000)
    obs.observe(
        1000, book, [{"kind": "episode_qualified", "direction": 1, "episode_at": "episode"}], 0.1, 30
    )
    for n in range(1, 32):
        obs.observe(1000 + n, book, [], 0.1, 30)
    next_obs = Observations(db, "Worker", "first", "MNQ", 1000, obs.checkpoint())
    next_obs.observe(1140, book, [], 0.1, 30)
    previous_rules(db, "first", 1000)
    report = observed(db, "Worker", "MNQ", 1000, 1140)
    assert report["matched"] == 0 and report["total"] == 1
    assert report["recent"][0]["moves"]["30"]["points"] == -0.25
    assert report["recent"][0]["moves"]["120"]["reason"] == "quote gap"
    assert report["horizons"][2]["valid"] == 0
    assert observed(db, "Independent", "MNQ", 1000, 1140)["total"] == 0


def test_qualification_without_executable_quote_is_counted(tmp_path):
    db = open_store(tmp_path / "study.sqlite3")
    obs = Observations(db, "Worker", "scope", "MNQ", 0)
    obs.observe(100, None, [event(100)], 0.1, 30)
    assert db.execute("SELECT count(*) FROM anchors WHERE side!=0").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM marks WHERE bid IS NULL").fetchone()[0] == 3


def test_worker_tail_resumes_idempotently_and_censors_closed_capture(tmp_path):
    from datetime import timedelta

    from app.services.topbot_depth import DepthState
    from app.services.topbot_evidence import Capture

    contract = "CON.F.US.MNQ.Z26"
    original = Capture("owner", contract, root=tmp_path / "input", free_floor=0)
    live = DepthState(contract)
    start = datetime.now(UTC)
    try:
        for n in range(40):
            now = start + timedelta(seconds=n)
            evt = {
                "event": "snapshot",
                "data": {
                    "contract_id": contract,
                    "timestamp": now.isoformat(),
                    "sequence": n + 1,
                    "bids": [{"price": 100, "size": 9 if n < 35 else 5}],
                    "asks": [{"price": 100.25, "size": 5}],
                },
            }
            live.apply(evt, now)
            original.append(evt, now, live.transitions)
    finally:
        original.close()
    db = open_store(tmp_path / "study.sqlite3")
    args = (original.path, db, "owner", contract, 0.1, 30, start.timestamp())
    for _ in range(4):
        with db:
            track_imbalances.worker_chunk(*args)
    assert db.execute("SELECT count(*) FROM anchors WHERE side=-1").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM episode_ends").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM marks WHERE reason='worker capture ended'").fetchone()[0] == 3
