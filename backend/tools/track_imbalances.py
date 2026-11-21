"""Run the read-only TopBot study beside the application and its independent recorder."""

import argparse
import json
import os
import shutil
import sys
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
from uuid import uuid4

from maintain_session_flow import LocalAPI, publish, read_json, recorder_ready

sys.path.append(str(Path(__file__).resolve().parents[1] / "app" / "services"))


def iso(stamp):
    return datetime.fromtimestamp(stamp, UTC).isoformat()


def bootstrap(config):
    release = Path(config["release"])
    sys.path.insert(0, str(release / "backend/.venv/Lib/site-packages"))
    sys.path.insert(0, str(release / "backend"))
    from dotenv import dotenv_values

    env = dotenv_values(release / "backend/.env")
    dsn = env["DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:")
    if urlsplit(dsn).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local PostgreSQL required")
    os.environ["TOPSIGNAL_DATABENTO_CACHE_DIR"] = env["TOPSIGNAL_DATABENTO_CACHE_DIR"]
    return Path(env["TOPSIGNAL_DATABENTO_CACHE_DIR"]), dsn


def pg(dsn):
    import psycopg

    return psycopg.connect(
        dsn, connect_timeout=3, options="-c default_transaction_read_only=on -c statement_timeout=5000"
    )


def scope(dsn, cfg):
    with pg(dsn) as db:
        row = db.execute(
            """SELECT user_id,contract_id,strategy_params FROM bot_configs
            WHERE id=%s AND account_id=%s AND strategy_type='topbot_adaptive'""",
            (cfg["config"], cfg["account"]),
        ).fetchone()
    if not row or (row[2] or {}).get("revision") != "dom_contrarian_v1":
        raise ValueError("Configured imbalance strategy unavailable")
    return str(row[0]), row[1], float(row[2]["imbalance_threshold"]), int(row[2]["persistence_seconds"])


def start_anchor(cap, start):
    """Seek near the prospective boundary, seeding the book from a later full snapshot."""
    from imbalance_study import batch_before

    return batch_before(cap, start - 70)


def worker_chunk(path, conn, owner, contract, threshold, persistence, start):
    from app.services.topbot_depth import DepthState, utc
    from app.services.topbot_replay_index import Capture, dump_state, load_state
    from imbalance_study import Observations

    with closing(Capture(path)) as cap:
        cap.check_scope(owner, contract)
        capture = cap.meta["capture_id"]
        saved = conn.execute("SELECT state FROM cursors WHERE capture=?", (capture,)).fetchone()
        if saved:
            data = json.loads(saved[0])
            batch, digest, last = data["anchor"]
            if not cap.anchor_matches(batch, digest):
                raise ValueError("Capture cursor mismatch")
            state = load_state(data["book"])
            obs = Observations(conn, "Worker", capture, contract, start, data["study"])
            seeded, through = data["seeded"], data["through"]
        else:
            batch, digest, last = start_anchor(cap, start)
            state = DepthState(contract, threshold)
            state.persistence_required = persistence
            obs = Observations(conn, "Worker", capture, contract, start)
            seeded, through = False, 0
        deadline = time.monotonic() + 1.0
        count = 0
        verified_batches = cap.batches(batch, digest, last)
        for batch, digest, rows in verified_batches:
            for row in rows:
                timestamp = utc(row["received_at"])
                at = timestamp.timestamp()
                evt = row["event"]
                if row["event_index"] != last + 1:
                    obs.gap(at, "missing worker events")
                    state.reset("missing worker events", timestamp)
                    seeded = False
                last = row["event_index"]
                state.apply(evt, timestamp)
                if evt["event"] == "snapshot" and not evt["data"].get("reset"):
                    seeded = state.snapshot(timestamp)["ready"]
                if evt["event"] == "observation_gap":
                    obs.gap(at, "worker stream silent")
                snap = state.snapshot(timestamp) if seeded else None
                obs.observe(at, snap, row["transitions"], state.threshold, state.persistence_required)
                through = at
            count += 1
            if time.monotonic() >= deadline or count >= 32:
                break
        if not count and obs.due:
            status_row = cap.db.execute("SELECT value FROM status WHERE id=1").fetchone()
            if status_row and json.loads(status_row[0]).get("state") == "closed":
                obs.gap(through or start, "worker capture ended")
                saved = None  # Commit the final censored horizons and cursor together.
        if count or not saved:
            data = {
                "anchor": [batch, digest, last],
                "book": dump_state(state),
                "study": obs.checkpoint(),
                "seeded": seeded,
                "through": through,
            }
            conn.execute(
                "INSERT OR REPLACE INTO cursors VALUES(?,?)",
                (capture, json.dumps(data, separators=(",", ":"))),
            )
        return count, through


def captures(root, owner, contract, start):
    folder = root / "topbot-worker-evidence-v1" / sha256(owner.encode()).hexdigest()
    rows = []
    for path in folder.glob("*.metadata.json"):
        meta = read_json(path)
        if meta.get("contract_id") == contract and meta.get("owner_hash") == folder.name:
            rows.append(
                (
                    datetime.fromisoformat(meta["started_at"]).timestamp(),
                    path.with_name(meta["capture_id"] + ".sqlite3"),
                )
            )
    rows.sort()
    # Include the capture active at registration and every subsequent capture.
    prior = [row for row in rows if row[0] <= start]
    return [p for _, p in prior[-1:] + [row for row in rows if row[0] > start]]


def capacity(folder):
    if (
        shutil.disk_usage(folder).free < 10 * 1024**3
        or sum(p.stat().st_size for p in folder.iterdir() if p.is_file()) > 512 * 1024**2
    ):
        raise ValueError("Research storage limit reached")


def independent_boundary(db, obs):
    # A busy worker cursor can keep the second WAL writer out even during cleanup.
    # Retry on the next connection. Pending marks never cross the subscription boundary.
    from imbalance_study import HORIZONS
    with db:
        db.execute("INSERT OR IGNORE INTO gaps VALUES(?,?,?,?)",
                   (obs.source, obs.capture, time.time(), "independent stream boundary"))
        for horizon in HORIZONS:
            db.execute("""INSERT OR IGNORE INTO marks SELECT id,?,NULL,NULL,'independent stream boundary'
                       FROM anchors WHERE source=? AND capture=?""", (horizon, obs.source, obs.capture))


def continuous(cfg, dsn, folder, start):
    """Independent normalized market-only subscription, even while the bot is disarmed."""
    from app.services.topbot_depth import DepthState
    from imbalance_study import Observations, open_store

    api = LocalAPI(cfg["account"], cfg["config"])
    with closing(open_store(folder / "study.sqlite3")) as db:
        status_path = folder / "independent.json"
        previous = read_json(status_path) if status_path.exists() else {}
        contract, through = previous.get("contract"), previous.get("through")
        pending_boundary = None
        while True:
            obs = None
            try:
                capacity(folder)
                if pending_boundary:
                    independent_boundary(db, pending_boundary)
                    pending_boundary = None
                owner, current_contract, threshold, persist = scope(dsn, cfg)
                if current_contract != contract:
                    contract, through = current_contract, None
                if sha256(owner.encode()).hexdigest() != folder.parent.name:
                    raise ValueError("Owner changed")
                api.authenticate()
                req = Request(
                    api.base + "/api/projectx/market-depth/stream?" + urlencode({"contract_id": contract}),
                    headers={"Authorization": "Bearer " + api.token, "Accept": "text/event-stream"},
                )
                state = DepthState(contract, threshold)
                state.persistence_required = persist
                capture = uuid4().hex
                obs = Observations(db, "Independent", capture, contract, start)
                check = 0
                candidate = through
                with api.opener.open(req, timeout=24) as stream:
                    kind = None
                    while True:
                        line = stream.readline(1024 * 1024 + 1)
                        if not line or len(line) > 1024 * 1024:
                            raise ValueError("Market stream ended")
                        now = datetime.now(UTC)
                        if line.startswith(b"event:"):
                            kind = line[6:].strip().decode()
                        elif line.startswith(b"data:") and kind:
                            event = {"event": kind, "data": json.loads(line[5:])}
                            state.apply(event, now)
                            obs.observe(
                                now.timestamp(), state.snapshot(now), state.transitions, threshold, persist
                            )
                            if state.last_at and event["data"].get("contract_id") == contract:
                                candidate = max(candidate or 0, state.last_at.timestamp())
                            kind = None
                        if db.in_transaction:
                            db.commit()
                        through = candidate  # All corresponding observations have committed.
                        if time.monotonic() >= check:
                            db.commit()
                            capacity(folder)
                            if (
                                scope(dsn, cfg) != (owner, contract, threshold, persist)
                                or api.expires < time.time() + 70
                            ):
                                break
                            check = time.monotonic() + 30
                            publish(
                                folder / "independent.json",
                                {"at": iso(time.time()), "contract": contract, "through": through, "state": "listening"},
                            )
            except Exception as exc:  # noqa: BLE001 - isolate and report feed failures before reconnecting
                db.rollback()
                publish(
                    folder / "independent.json",
                    {"at": iso(time.time()), "state": "waiting", "contract": contract, "through": through,
                     "error": getattr(exc, "sqlite_errorname", type(exc).__name__)},
                )
            finally:
                if obs:
                    pending_boundary = obs
                api.close()
            time.sleep(3)


def run(cfg):
    cache, dsn = bootstrap(cfg)
    from imbalance_report import build_report
    from imbalance_study import directory, open_store

    owner, contract, threshold, persist = scope(dsn, cfg)
    folder = directory(cache, owner, cfg["account"])
    folder.mkdir(parents=True, exist_ok=True)
    capacity(folder)
    db = open_store(folder / "study.sqlite3")
    requested = datetime.fromisoformat(cfg["started_at"]).timestamp()
    with db:
        db.execute("INSERT OR IGNORE INTO meta VALUES('start',?)", (str(requested),))
    start = float(db.execute("SELECT value FROM meta WHERE key='start'").fetchone()[0])
    # Unfinished independent horizons from a previous process have no continuous stream.
    with db:
        for horizon in (30, 120, 300):
            db.execute(
                """INSERT OR IGNORE INTO marks SELECT id,?,NULL,NULL,'collector restart'
                       FROM anchors WHERE source='Independent'""",
                (horizon,),
            )
    independent = None
    registered = None
    refresh = report_at = 0
    paths = []
    while True:
        stage = "worker evidence"
        try:
            if independent is None or not independent.is_alive():
                independent = threading.Thread(target=continuous, args=(cfg, dsn, folder, start), daemon=True)
                independent.start()
            now = time.monotonic()
            capacity(folder)
            if now >= refresh:
                current_owner, contract, threshold, persist = scope(dsn, cfg)
                if owner != current_owner:
                    raise ValueError("Owner changed")
                if registered != contract:
                    publish(folder / "scope.json", {"account": cfg["account"], "config": cfg["config"], "contract": contract})
                    registered = contract
                paths = captures(cache, owner, contract, start)
                refresh = now + 12
            active = 0
            through = 0
            if recorder_ready(cfg["recorder_status"]):
                for path in paths:
                    with db:
                        count, observed = worker_chunk(path, db, owner, contract, threshold, persist, start)
                    active += count
                    through = max(through, observed)
            if now >= report_at:
                stage = "report"
                with db:
                    report = build_report(cfg, dsn, cache, owner, contract, db, start, through)
                publish(folder / "report.json", report)
                report_at = now + 15
            publish(
                folder / "worker.json",
                {
                    "at": iso(time.time()),
                    "through": through,
                    "state": "following" if through else "waiting",
                },
            )
            time.sleep(0.25 if active else 1.5)
        except Exception as exc:  # noqa: BLE001 - keep the optional collector alive through storage/provider outages
            db.rollback()
            publish(
                folder / "worker.json",
                {"at": iso(time.time()), "state": "waiting", "phase": stage, "error": type(exc).__name__},
            )
            import traceback

            traceback.print_tb(exc.__traceback__, limit=4)
            time.sleep(4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    run(read_json(parser.parse_args().config))
