"""Independent research reader. Never calls an order or changes a trading config.

One replay child runs at a time, at below-normal CPU and background I/O
priority, outside the trading process. Work is scheduled from durable per-entry
job state (``topbot_study_jobs``) so failures, retries and progress survive a
restart and stay visible until the entry is actually processed.

Why replays used to fail: every child re-read and re-verified its capture from
event 1. Late entries in a full-day capture needed longer than the fixed
300-second deadline, were killed, and retried from the beginning, keeping the
archive disk busy without ever finishing. Replays now start from verified
checkpoints (``topbot_replay_index``); a child that is still progressing is not
killed for being slow, one that stops progressing is, and checkpoint building
yields after a fixed budget with its progress saved.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from .topbot_study import EXPERIMENT_ID
from .topbot_depth import feed, utc
from .topbot_runner import statistics_item
from . import topbot_study_store as store
from . import topbot_study_jobs as jobs

logger = logging.getLogger(__name__)
HEALTH = {"state": "not_started", "last_cycle_at": None, "last_error_type": None}
ACTIVE = {}          # The running replay, if any: owner/account/entry/phase/progress.
RETENTION = {}       # (owner, account, entry) -> latest retention outcome for open windows.
STATE_LOCK = Lock()
CYCLE_SECONDS = 15
# A child that keeps reporting progress is allowed to finish; one that stops is ended.
NO_PROGRESS_SECONDS = 180
# Absolute safety cap per child. Checkpoint building yields after INDEX_BUDGET_SECONDS,
# so this only bounds the non-resumable replay of a single entry's window.
HARD_CAP_SECONDS = 1800
INDEX_BUDGET_SECONDS = 90
RETRY_SECONDS = 900
MAX_RETRY_SECONDS = 6*3600
PRESSURE_RETRY_SECONDS = 60
DEADLINE_SECONDS = HARD_CAP_SECONDS  # Backward-compatible name.


def _now():
    return datetime.now(timezone.utc)


def _capture_id(request):
    evidence = request.get("entryEvidence") or {}
    ref = (evidence.get("pre_submit") or evidence.get("signal") or {}).get("evidence") or {}
    value = ref.get("capture_id", "")
    return value if isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value) else None


def backoff(consecutive_failures):
    return min(MAX_RETRY_SECONDS, RETRY_SECONDS*2**min(max(consecutive_failures, 1)-1, 16))


def recording_pressure():
    """Storage backpressure in this process's worker captures (memory status only)."""
    with feed.lock:
        captures = [(key[1], stream["state"].capture) for key, stream in feed.streams.items()]
    for contract, capture in captures:
        if capture is None:
            continue
        status = capture.status()
        if (status["state"] in {"stalled", "retrying"} or status["queued_events"] > 1024
                or (status["oldest_queued_seconds"] or 0) > 2
                or (status["last_commit_seconds"] or 0) > 2):
            return {"reason": "worker_capture_backlog", "contract_id": contract, "state": status["state"],
                    "queued_events": status["queued_events"], "oldest_queued_seconds": status["oldest_queued_seconds"],
                    "last_commit_seconds": status["last_commit_seconds"]}
    return None


def _tail(path, limit=4096):
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size-limit))
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _last_progress(path):
    for line in reversed(_tail(path).splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and "phase" in value:
            return value
    return None


class StudyRecorder:
    def __init__(self, session_factory, client_factory):
        self.sessions, self.clients = session_factory, client_factory
        self.halt = Event()
        self.thread = None
        self.after = 0
        self.job = None
        self.pressure_seen = 0
        self.holds = {}  # (owner, entry) -> monotonic time; only if the job store cannot be written.
        self.enrolled = {}
        self.scope_turns = {}
        self.turn = 0

    def start(self):
        if os.getenv("TOPSIGNAL_BOT_WORKER_ENABLED", "false").lower() != "true": return
        HEALTH.update(state="starting", last_error_type=None)
        self.thread = Thread(target=self.run, daemon=True, name="exit-study-reader")
        self.thread.start()

    def stop(self):
        self.halt.set()
        if self.thread: self.thread.join(2)
        if self.job:
            self._end_child(self.job)
            self._cleanup(self.job)
            self.job = None
            ACTIVE.clear()
        return not self.thread or not self.thread.is_alive()

    def client(self, owner):
        # The application factory takes (session, user_id=...). Calling it with the
        # owner alone raised TypeError, so observation was never retained after a
        # stop or restart unless the trading worker happened to keep the stream.
        with self.sessions() as db:
            return self.clients(db, user_id=owner)

    def run(self):
        while not self.halt.is_set():
            try: self.cycle()
            except Exception as exc:
                HEALTH.update(state="error", last_error_type=type(exc).__name__,
                              last_error_at=_now().isoformat())
                logger.error("exit_study_unavailable error_type=%s", type(exc).__name__)
            else:
                HEALTH.update(last_cycle_at=_now().isoformat())
            self.halt.wait(CYCLE_SECONDS)

    # -- child lifecycle -------------------------------------------------

    def _end_child(self, job):
        import subprocess
        process = job["process"]
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill()

    def _cleanup(self, job):
        for name in ("output", "progress_handle"):
            try: job[name].close()
            except Exception: pass
        try: Path(job["progress_path"]).unlink(missing_ok=True)
        except OSError: pass

    def _finish(self, job, *, outcome, failure, detail, retry_seconds=None):
        now = _now()
        attempt = jobs.jobs(job["owner"], job["account"]).get((job["account"], job["entry_id"], EXPERIMENT_ID), {})
        consecutive = attempt.get("consecutive_failures", 0)+1 if failure else 0
        delay = backoff(consecutive) if failure else retry_seconds
        progress = job.get("progress") or {}
        detail = {**detail, "seconds": round(monotonic()-job["started"], 3),
                  "progress": {k: progress.get(k) for k in ("phase", "events", "batch_id", "received_at") if k in progress}}
        try:
            jobs.record(job["owner"], job["account"], job["entry_id"], EXPERIMENT_ID, outcome=outcome,
                        started_at=job["started_at"], detail=detail, failure=failure, progress=progress or None,
                        next_attempt_at=(now+timedelta(seconds=delay)).isoformat() if delay else None)
        except Exception as exc:
            # The attempt still ends; an in-memory hold prevents an immediate relaunch.
            logger.error("exit_study_job_record_failed entry_id=%s error_type=%s", job["entry_id"], type(exc).__name__)
            HEALTH.update(last_error_type="job_store_unavailable", last_error_at=now.isoformat())
            self.holds[(job["owner"], job["entry_id"])] = monotonic()+(delay or PRESSURE_RETRY_SECONDS)
        if failure:
            logger.warning("exit_study_replay_deferred entry_id=%s outcome=%s failures=%s retry_seconds=%s",
                           job["entry_id"], outcome, consecutive, delay)
            HEALTH.update(last_error_type=outcome, last_error_at=now.isoformat())

    def poll_job(self):
        job = self.job
        if not job:
            return
        code = job["process"].poll()
        progress = _last_progress(job["progress_path"])
        if progress and progress != job.get("progress"):
            job["progress"], job["progressed"] = progress, monotonic()
        with STATE_LOCK:
            ACTIVE.update(phase=(job.get("progress") or {}).get("phase", "starting"),
                          events=(job.get("progress") or {}).get("events"),
                          received_at=(job.get("progress") or {}).get("received_at"))
        if code is None:
            pressure = recording_pressure()
            self.pressure_seen = self.pressure_seen+1 if pressure else 0
            if pressure and self.pressure_seen >= 2:
                # Recording first: stop research and resume later from saved checkpoints.
                self._end_child(job)
                self._finish(job, outcome="paused_recording_pressure", failure=False, detail=pressure,
                             retry_seconds=PRESSURE_RETRY_SECONDS)
                HEALTH["pressure"] = {**pressure, "at": _now().isoformat()}
            elif monotonic()-job["progressed"] > NO_PROGRESS_SECONDS:
                self._end_child(job)
                self._finish(job, outcome="no_progress", failure=True,
                             detail={"category": "no_progress", "limit_seconds": NO_PROGRESS_SECONDS})
            elif monotonic()-job["started"] > HARD_CAP_SECONDS:
                self._end_child(job)
                self._finish(job, outcome="hard_cap", failure=True,
                             detail={"category": "hard_cap", "limit_seconds": HARD_CAP_SECONDS})
            else:
                return
        else:
            payload = None
            try:
                job["output"].seek(0)
                payload = json.loads(job["output"].read(1024*1024) or b"null")
            except ValueError:
                payload = None
            if code == 0 and isinstance(payload, dict) and "index_progress" in payload:
                self._finish(job, outcome="index_progress", failure=False,
                             detail={"category": "index_progress", "index": payload["index_progress"]})
            elif code == 0 and isinstance(payload, dict) and "variants" in payload:
                try:
                    payload.update(job["enrollment"])
                    store.save(job["owner"], job["account"], payload)
                except Exception as exc:
                    self._finish(job, outcome="result_store_unavailable", failure=True,
                                 detail={"category": "result_store", "type": type(exc).__name__,
                                         "code": str(exc) if str(exc).replace("_", "").isalnum() else None})
                else:
                    self._finish(job, outcome="completed", failure=False,
                                 detail={"category": "completed", "replay": payload.get("replay"),
                                         "paired_valid": payload.get("paired_valid")})
            elif isinstance(payload, dict) and "failure" in payload:
                self._finish(job, outcome="child_failed", failure=True, detail=payload["failure"])
            else:
                types = sorted({word.rstrip(":") for line in _tail(job["progress_path"]).splitlines()[-20:]
                                for word in line.split()[:1] if word.rstrip(":").endswith(("Error", "Exception"))})
                self._finish(job, outcome="child_exit", failure=True,
                             detail={"category": "child_exit", "exit_code": code, "exception_types": types[:4]})
        self._cleanup(job)
        self.job = None
        with STATE_LOCK:
            ACTIVE.clear()

    def launch(self, owner, account, contract, entry, enrollment, path):
        import subprocess, sys, tempfile
        output = tempfile.TemporaryFile(mode="w+b")
        handle = tempfile.NamedTemporaryFile(mode="wb", prefix="exit-study-", suffix=".progress", delete=False)
        env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in
               ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY", "DATABASE_URL", "SUPABASE"))}
        flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
        try:
            process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).parents[2]/"tools/compare_topbot_exits.py"),
                "--capture", str(path), "--checkpoints", "--index-budget", str(INDEX_BUDGET_SECONDS)],
                stdin=subprocess.PIPE, stdout=output, stderr=handle, env=env, creationflags=flags)
        except Exception:
            output.close(); handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise
        self.job = {"process": process, "output": output, "progress_handle": handle, "progress_path": handle.name,
                    "started": monotonic(), "progressed": monotonic(), "started_at": _now().isoformat(),
                    "owner": owner, "account": account, "enrollment": enrollment or {}, "entry_id": entry["entry_id"]}
        with STATE_LOCK:
            ACTIVE.clear()
            ACTIVE.update(owner=owner, account=account, entry_id=entry["entry_id"], started_at=self.job["started_at"],
                          phase="starting")
        try:
            process.stdin.write(json.dumps({"entry": entry, "owner": owner, "contract": contract}).encode())
            process.stdin.close()
        except Exception:
            self._end_child(self.job); self._cleanup(self.job); self.job = None
            with STATE_LOCK: ACTIVE.clear()
            raise

    # -- scheduling --------------------------------------------------------

    def cycle(self):
        from ..models import BotOrderAttempt
        now = _now()
        self.poll_job()
        with self.sessions() as db:
            registered = db.query(BotOrderAttempt).filter(
                BotOrderAttempt.raw_request["strategySnapshot"]["experiment_id"].as_string().isnot(None)
            )
            query = registered.filter(
                BotOrderAttempt.raw_request["strategySnapshot"]["experiment_id"].as_string() == EXPERIMENT_ID
            )
            active = registered.filter(BotOrderAttempt.raw_request["strategySnapshot"]["research_cutoff"].as_string() > now.isoformat()).order_by(BotOrderAttempt.id).limit(500).all()
            rows = query.filter(BotOrderAttempt.id > self.after).order_by(BotOrderAttempt.id).limit(500).all()
            self.after = rows[-1].id if len(rows) == 500 else 0
            unique = {r.id: r for r in active+rows}
            entries = [(str(r.user_id), int(r.account_id), r.contract_id, dict(r.raw_request), statistics_item(r)) for r in unique.values()]
            db.rollback()
        done, retention, candidates = {}, {}, []
        for owner, account, contract, request, entry in entries:
            if self.halt.is_set(): return
            snapshot = entry["strategy_snapshot"]
            cutoff = utc(snapshot["research_cutoff"])
            if not entry.get("entry_at"):
                continue  # Unfilled/blocked attempts do not enroll or extend retention.
            current = snapshot.get("experiment_id") == EXPERIMENT_ID
            enrollment = None
            if current:
                # Enrollments are append-only; read them once instead of reopening the
                # results store for write on every entry every cycle.
                key = (owner, account, entry["entry_id"])
                enrollment = self.enrolled.get(key)
                if enrollment is None:
                    enrollment = self.enrolled[key] = store.enroll(owner, account, entry["entry_id"])
                    if len(self.enrolled) > 20000:
                        self.enrolled.clear()
            if now < cutoff:
                # Continues after actual exit and entry pauses. Existing streams
                # keep their current signal parameters; no new evaluation/order.
                key = (owner, account, entry["entry_id"])
                try:
                    feed.retain(owner, contract, lambda: self.client(owner), cutoff, snapshot["rules"]["entry"])
                    retention[key] = {"state": "retained", "at": now.isoformat(), "until": cutoff.isoformat(),
                                      "contract_id": contract}
                except Exception as exc:
                    retention[key] = {"state": "failed", "at": now.isoformat(), "until": cutoff.isoformat(),
                                      "contract_id": contract, "error_type": type(exc).__name__}
                    logger.error("exit_study_retention_failed entry_id=%s error_type=%s", entry["entry_id"], type(exc).__name__)
                continue
            if not current:
                continue  # Prior experiments need their recorded model, never today's code.
            if (owner, account) not in done:
                done[owner, account] = store.completed_ids(owner, account)
            if entry["entry_id"] in done[owner, account]:
                continue
            candidates.append((cutoff, owner, account, contract, request, entry, enrollment))
        with STATE_LOCK:
            RETENTION.clear()
            RETENTION.update(retention)
        pending = self._schedule(now, candidates)
        HEALTH.update(state="replaying" if self.job else pending["state"], overdue=pending["overdue"],
                      unresolved_failures=pending["failing"], pressure=HEALTH.get("pressure"))

    def _schedule(self, now, candidates):
        failing = 0
        eligible = []
        states = {}
        for owner in {c[1] for c in candidates}:
            states.update({(owner,)+k: v for k, v in jobs.jobs(owner).items()})
        # Entries share physical recordings. Retrying a different entry from a
        # stalled capture immediately repeats the same IO and can keep every
        # other account waiting. Reuse durable failure cooldowns across that
        # owner/capture, including after a worker restart. Successful index
        # slices have no failure count and must remain immediately resumable.
        cooling_captures = set()
        for _, owner, account, _, request, entry, _ in candidates:
            state = states.get((owner, account, entry["entry_id"], EXPERIMENT_ID), {})
            capture = _capture_id(request)
            due = state.get("next_attempt_at")
            if capture and due and state.get("consecutive_failures") and utc(due) > now:
                cooling_captures.add((owner, capture))
        for cutoff, owner, account, contract, request, entry, enrollment in sorted(candidates, key=lambda c: (c[0], c[5]["entry_id"])):
            state = states.get((owner, account, entry["entry_id"], EXPERIMENT_ID), {})
            if state.get("consecutive_failures"):
                failing += 1
            due = state.get("next_attempt_at")
            if self.job and self.job["owner"] == owner and self.job["entry_id"] == entry["entry_id"]:
                continue
            if due and utc(due) > now:
                continue
            if (owner, _capture_id(request)) in cooling_captures:
                continue
            if self.holds.get((owner, entry["entry_id"]), 0) > monotonic():
                continue
            eligible.append((owner, account, contract, request, entry, enrollment))
        result = {"overdue": len(candidates), "failing": failing,
                  "state": "deferred" if candidates and not eligible else "idle"}
        if self.job or not eligible or self.halt.is_set():
            return result
        pressure = recording_pressure()
        if pressure:
            HEALTH["pressure"] = {**pressure, "at": now.isoformat()}
            result["state"] = "paused_recording_pressure"
            return result
        # One resumable slice per account turn. A healthy but large historical
        # capture must not consume every slice ahead of a newer active account.
        scopes = {(c[0], c[1]) for c in eligible}
        self.scope_turns = {k: v for k, v in self.scope_turns.items() if k in scopes}
        owner, account, contract, request, entry, enrollment = min(
            eligible, key=lambda c: self.scope_turns.get((c[0], c[1]), 0))
        self.turn += 1
        self.scope_turns[(owner, account)] = self.turn
        capture_id = _capture_id(request)
        if capture_id is None:
            from .topbot_study import simulate
            result_row = simulate(entry, [], gap_reason="missing_capture_reference")
            result_row.update(enrollment or {})
            store.save(owner, account, result_row)
            return result
        from .databento_cache import default_cache_root
        from hashlib import sha256
        path = default_cache_root()/"topbot-worker-evidence-v1"/sha256(owner.encode()).hexdigest()/(capture_id+".sqlite3")
        self.launch(owner, account, contract, entry, enrollment, path)
        result["state"] = "replaying"
        return result


def account_activity(owner, account):
    """The running replay and retention outcomes for one account only."""
    with STATE_LOCK:
        active = dict(ACTIVE) if ACTIVE.get("owner") == str(owner) and ACTIVE.get("account") == int(account) else None
        retention = {k[2]: dict(v) for k, v in RETENTION.items() if k[0] == str(owner) and k[1] == int(account)}
    if active:
        active.pop("owner", None)
    return {"active": active, "retention": retention,
            "reader": {k: HEALTH.get(k) for k in ("state", "last_cycle_at", "last_error_type", "last_error_at", "pressure")}}
