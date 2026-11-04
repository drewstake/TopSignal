"""Local, append-only research results. Raw paths stay in worker captures."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3

from .databento_cache import default_cache_root
from .topbot_evidence import canonical, recorded_batches
from .topbot_depth import DepthState, utc
from .topbot_study import EXPERIMENT_ID, PROTOCOL, metrics


def location(owner):
    return default_cache_root()/"topbot-exit-study-v1"/sha256(str(owner).encode()).hexdigest()/"results.sqlite3"


@contextmanager
def connection(owner, *, write=False):
    path = location(owner)
    if write:
        import shutil
        path.parent.mkdir(parents=True, exist_ok=True)
        used = sum(p.stat().st_size for p in path.parent.glob("results.sqlite3*") if p.is_file())
        if shutil.disk_usage(path.parent).free < 10*1024**3 or used+1024**2 > 256*1024**2:
            raise ValueError("study_storage_review_required")
        db = sqlite3.connect(path, timeout=1)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.executescript("""CREATE TABLE IF NOT EXISTS results(seq INTEGER PRIMARY KEY, account INTEGER NOT NULL,
          entry INTEGER NOT NULL, experiment TEXT NOT NULL, hash TEXT NOT NULL, previous_hash TEXT,
          recorded_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(account,entry,experiment,hash));
          CREATE INDEX IF NOT EXISTS entry_scope ON results(account,entry,experiment,seq);
          CREATE TABLE IF NOT EXISTS enrollments(account INTEGER, entry INTEGER, experiment TEXT, ordinal INTEGER,
            phase TEXT NOT NULL, PRIMARY KEY(account,entry,experiment), UNIQUE(account,experiment,ordinal));
          CREATE TRIGGER IF NOT EXISTS enrollment_no_update BEFORE UPDATE ON enrollments BEGIN SELECT RAISE(ABORT,'append_only'); END;
          CREATE TRIGGER IF NOT EXISTS enrollment_no_delete BEFORE DELETE ON enrollments BEGIN SELECT RAISE(ABORT,'append_only'); END;
          CREATE TABLE IF NOT EXISTS selections(account INTEGER, experiment TEXT, ratio REAL NOT NULL,
            registered_at TEXT NOT NULL, PRIMARY KEY(account,experiment));
          CREATE TRIGGER IF NOT EXISTS selection_no_update BEFORE UPDATE ON selections BEGIN SELECT RAISE(ABORT,'append_only'); END;
          CREATE TRIGGER IF NOT EXISTS selection_no_delete BEFORE DELETE ON selections BEGIN SELECT RAISE(ABORT,'append_only'); END;
          CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON results BEGIN SELECT RAISE(ABORT,'append_only'); END;
          CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON results BEGIN SELECT RAISE(ABORT,'append_only'); END;""")
    else:
        if not path.exists():
            yield None
            return
        db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=1)
    try:
        yield db
        if write: db.commit()
    finally: db.close()


def save(owner, account, result):
    if result.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("study_experiment_mismatch")
    raw = canonical(result).decode()
    if len(raw) > 32768: raise ValueError("study_result_too_large")
    digest = sha256(raw.encode()).hexdigest()
    with connection(owner, write=True) as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT hash FROM results WHERE account=? AND entry=? AND experiment=? ORDER BY seq DESC LIMIT 1",
                              (account, result["entry_id"], EXPERIMENT_ID)).fetchone()
        db.execute("INSERT OR IGNORE INTO results(account,entry,experiment,hash,previous_hash,recorded_at,payload) VALUES(?,?,?,?,?,?,?)",
            (account, result["entry_id"], EXPERIMENT_ID, digest, previous[0] if previous else None,
             datetime.now(timezone.utc).isoformat(), raw))


def enroll(owner, account, entry):
    with connection(owner, write=True) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT ordinal,phase FROM enrollments WHERE account=? AND entry=? AND experiment=?",
                         (account, entry, EXPERIMENT_ID)).fetchone()
        if row: return {"ordinal": row[0], "phase": row[1]}
        ordinal = db.execute("SELECT coalesce(max(ordinal),0)+1 FROM enrollments WHERE account=? AND experiment=?",
                             (account, EXPERIMENT_ID)).fetchone()[0]
        phase = "exploratory" if ordinal <= 100 else "holdout"
        db.execute("INSERT INTO enrollments VALUES(?,?,?,?,?)", (account, entry, EXPERIMENT_ID, ordinal, phase))
        return {"ordinal": ordinal, "phase": phase}


def read(owner, account):
    with connection(owner) as db:
        if db is None: return []
        rows = db.execute("""SELECT payload,hash,previous_hash,seq FROM results WHERE seq IN
          (SELECT max(seq) FROM results WHERE account=? AND experiment=? GROUP BY entry) ORDER BY entry LIMIT 5000""",
          (account, EXPERIMENT_ID)).fetchall()
    if any(sha256(r[0].encode()).hexdigest() != r[1] for r in rows):
        raise ValueError("study_result_hash_mismatch")
    return [{**json.loads(r[0]), "result_hash": r[1], "previous_hash": r[2], "revision": r[3]} for r in rows]


def completed_ids(owner, account):
    with connection(owner) as db:
        if db is None: return set()
        return {r[0] for r in db.execute("SELECT DISTINCT entry FROM results WHERE account=? AND experiment=?", (account, EXPERIMENT_ID))}


def selection(owner, account, *, ratio=None):
    if ratio is not None and (isinstance(ratio, bool) or ratio not in PROTOCOL["ratios"]):
        raise ValueError("unsupported_candidate_ratio")
    with connection(owner, write=ratio is not None) as db:
        if db is None: return None
        if ratio is not None:
            db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT ratio,registered_at FROM selections WHERE account=? AND experiment=?", (account, EXPERIMENT_ID)).fetchone()
        if row:
            if ratio is not None and ratio != row[0]: raise ValueError("candidate_already_prespecified")
            return {"ratio": row[0], "registered_at": row[1]}
        if ratio is None: return None
        stamp = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO selections VALUES(?,?,?,?)", (account, EXPERIMENT_ID, ratio, stamp))
        return {"ratio": ratio, "registered_at": stamp}


def quotes(path, owner, contract, start, cutoff):
    state = DepthState(contract)
    last = 0
    damaged = False
    for row in recorded_batches(path):
        if "metadata" in row:
            meta = row["metadata"]
            if meta["owner_hash"] != sha256(str(owner).encode()).hexdigest() or meta["contract_id"] != contract:
                raise ValueError("capture_scope_mismatch")
            continue
        at = utc(row["received_at"])
        if at > cutoff: break
        if row["event_index"] != last+1: damaged = True
        last = row["event_index"]
        if last % 4096 == 0:
            import time
            time.sleep(.001)  # Yield CPU while replaying long captures beside the worker.
        prior_sequence = state.sequence
        state.apply(row["event"], at)
        # A full saved snapshot can re-establish the observed book before the
        # entry. An older gap must not poison every later entry in the capture.
        # Gaps during an entry's path still invalidate its unresolved outcomes.
        sequence = (row["event"].get("data") or {}).get("sequence")
        if (at <= start and row["event"].get("event") == "snapshot"
                and sequence is not None and state.sequence == sequence
                and (prior_sequence is None or sequence > prior_sequence)
                and state.snapshot(at).get("ready")):
            damaged = False
        if at < start: continue
        snap = state.snapshot(at)
        if damaged or not snap.get("ready"):
            yield {"at": at.isoformat(), "gap": "capture_index_gap" if damaged else "feed_unavailable"}
            continue
        yield {"at": at.isoformat(), "capture_event_index": last, "bid": snap["bid"], "ask": snap["ask"],
               "bid_size": state.bids[snap["bid"]], "ask_size": state.asks[snap["ask"]]}


def comparison(owner, account, entries, *, version=None, strategy=None, start=None, end=None, phase="exploratory"):
    def selected(r):
        return ((not version or r.get("strategy_version_id") == version)
                and (not strategy or r.get("strategy_class") == strategy)
                and (not start or (r.get("entry_at") or "")[:10] >= start)
                and (not end or (r.get("entry_at") or "")[:10] <= end))
    scoped = [r for r in entries if selected(r)]
    actual = [r for r in scoped if r.get("entry_at") or
        (r.get("attempt_status") in {"pending", "submission_unknown", "submitted"}
         and r.get("stats_status") != "not_filled")]
    all_results = read(owner, account)
    # Split before display filters; changing a filter cannot move holdout entries into training.
    ordered = sorted(all_results, key=lambda r:(r["entry_at"], r["entry_id"]))
    chosen = selection(owner, account)
    cohort = [r for r in ordered if selected(r) and r.get("phase") == phase
        and (phase != "holdout" or (chosen and r["entry_at"] > chosen["registered_at"]))]
    valid = [r for r in cohort if r["paired_valid"]]
    series = []
    for key in [str(r) for r in PROTOCOL["ratios"]]+["trailing"]:
        if phase == "holdout" and key != "trailing" and (not chosen or float(key) != chosen["ratio"]): continue
        points = [{**next(v for v in r["variants"] if v["key"] == key), "session": r["session"]} for r in valid]
        series.append({"key": key, "ratio": None if key == "trailing" else float(key),
                       "dollars": metrics(points), "r": metrics(points, "realized_r")})
    groups = []
    for mode in ("trailing", "fixed", "unknown"):
        rows = [r for r in actual if r.get("strategy_class") == mode and r.get("stats_status") == "complete"]
        groups.append({"strategy_class": mode, "dollars": metrics(rows), "r": metrics(rows, "realized_r"),
                       "incomplete": sum(r.get("strategy_class") == mode and r.get("stats_status") != "complete" for r in actual)})
    pending = [r for r in actual if r.get("entry_at") and r.get("experiment_id") == EXPERIMENT_ID and r["entry_id"] not in {r["entry_id"] for r in all_results}]
    with connection(owner) as db:
        registered = db.execute("SELECT count(*) FROM enrollments WHERE account=? AND experiment=? AND phase='exploratory'",
                               (account, EXPERIMENT_ID)).fetchone()[0] if db is not None else 0
    differences = []
    if chosen:
        for row in valid:
            candidate = next(v for v in row["variants"] if v.get("ratio") == chosen["ratio"])
            reference = next(v for v in row["variants"] if v["key"] == "trailing")
            differences.append({"session": row["session"], "net_pnl_usd": candidate["net_pnl_usd"]-reference["net_pnl_usd"],
                                "realized_r": candidate["realized_r"]-reference["realized_r"]})
    return {"experiment_id": EXPERIMENT_ID, "protocol": PROTOCOL, "actual": groups, "series": series,
        "selection": chosen, "candidate_minus_trailing": {"dollars": metrics(differences), "r": metrics(differences, "realized_r")},
        "phase": phase, "valid_entries": len(valid), "total_entries": len(cohort), "excluded_entries": len(cohort)-len(valid),
        "pending_entries": len(pending), "coverage_rate": len(valid)/len(cohort) if cohort else None,
        "sessions": len({r["session"] for r in valid}), "progress_to_100": min(100, registered),
        "nonfilled_attempts": len(scoped)-len(actual),
        "versions": sorted({r.get("strategy_version_id", "unknown") for r in entries}),
        "excluded": [{"entry_id": r["entry_id"], "reasons": sorted({v.get("reason", v["status"]) for v in r["variants"] if v["status"] != "resolved"})} for r in cohort if not r["paired_valid"]],
        "historical_unregistered": sum(r.get("experiment_id") != EXPERIMENT_ID for r in actual)}
