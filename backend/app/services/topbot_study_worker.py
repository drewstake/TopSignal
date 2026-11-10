"""Independent research reader. Never calls an order or changes a trading config."""
from datetime import datetime, timezone
import logging
from threading import Event, Thread

from .topbot_study import EXPERIMENT_ID
from .topbot_depth import feed, utc
from .topbot_runner import statistics_item
from . import topbot_study_store as store

logger = logging.getLogger(__name__)
HEALTH = {"state": "not_started", "last_cycle_at": None, "last_error_type": None}
DEADLINE_SECONDS = 300
# A replay that fails or overruns is retried later instead of immediately, so one
# entry cannot keep a child process running continuously or starve later entries.
RETRY_SECONDS = 900
MAX_RETRY_SECONDS = 6*3600


class StudyRecorder:
    def __init__(self, session_factory, client_factory):
        self.sessions, self.clients = session_factory, client_factory
        self.halt = Event()
        self.thread = None
        self.after = 0
        self.job = None
        self.failures = {}  # entry id -> (consecutive failures, monotonic time of next attempt)

    def start(self):
        import os
        if os.getenv("TOPSIGNAL_BOT_WORKER_ENABLED", "false").lower() != "true": return
        HEALTH.update(state="starting", last_error_type=None)
        self.thread = Thread(target=self.run, daemon=True, name="exit-study-reader")
        self.thread.start()

    def stop(self):
        self.halt.set()
        if self.thread: self.thread.join(2)
        if self.job:
            if self.job["process"].poll() is None: self.job["process"].terminate()
            self.job["process"].wait(timeout=2)
            self.job["output"].close()
            self.job = None
        return not self.thread or not self.thread.is_alive()

    def defer(self, entry_id):
        from time import monotonic
        count = self.failures.get(entry_id, (0, 0))[0]+1
        delay = min(MAX_RETRY_SECONDS, RETRY_SECONDS*2**min(count-1, 16))
        if len(self.failures) >= 1024: self.failures.clear()  # Bounded; worst case is an earlier retry.
        self.failures[entry_id] = (count, monotonic()+delay)
        HEALTH["deferred_replays"] = len(self.failures)
        logger.warning("exit_study_replay_deferred entry_id=%s failures=%s retry_seconds=%s", entry_id, count, delay)

    def client(self, owner):
        # The application factory takes (session, user_id=...). Calling it with the
        # owner alone raised TypeError, so observation was never retained after a
        # stop or restart unless the trading worker happened to keep the stream.
        with self.sessions() as db:
            return self.clients(db, user_id=owner)

    def deferred(self, entry_id):
        from time import monotonic
        return entry_id in self.failures and monotonic() < self.failures[entry_id][1]

    def run(self):
        while not self.halt.is_set():
            try: self.cycle()
            except Exception as exc:
                HEALTH.update(state="error", last_error_type=type(exc).__name__)
                logger.error("exit_study_unavailable error_type=%s", type(exc).__name__)
            else:
                HEALTH.update(state="idle", last_cycle_at=datetime.now(timezone.utc).isoformat(), last_error_type=None)
            self.halt.wait(15)

    def cycle(self):
        from ..models import BotOrderAttempt
        now = datetime.now(timezone.utc)
        HEALTH["state"] = "reading"
        if self.job:
            from time import monotonic
            job = self.job
            code = job["process"].poll()
            overdue = code is None and monotonic()-job["started"] > DEADLINE_SECONDS
            if overdue or code is not None:
                # Finished either way: one failure is reported once, and the
                # next cycle is free to replay a different entry.
                self.job = None
                try:
                    if overdue:
                        import subprocess
                        job["process"].terminate()
                        try: job["process"].wait(timeout=2)
                        except subprocess.TimeoutExpired: job["process"].kill()
                        raise TimeoutError("research_replay_deadline")
                    if code != 0: raise RuntimeError("research_child_failed")
                    import json
                    job["output"].seek(0)
                    result = json.load(job["output"])
                    result.update(job["enrollment"])
                    store.save(job["owner"], job["account"], result)
                    self.failures.pop(job["entry_id"], None)
                    HEALTH["deferred_replays"] = len(self.failures)
                except Exception:
                    self.defer(job["entry_id"])
                    raise
                finally: job["output"].close()
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
        done = {}
        for owner, account, contract, request, entry in entries:
            if self.halt.is_set(): return
            snapshot = entry["strategy_snapshot"]
            cutoff = utc(snapshot["research_cutoff"])
            if not entry.get("entry_at"):
                continue  # Unfilled/blocked attempts do not enroll or extend retention.
            current = snapshot.get("experiment_id") == EXPERIMENT_ID
            enrollment = store.enroll(owner, account, entry["entry_id"]) if current else None
            if now < cutoff:
                # Continues after actual exit and entry pauses. Existing streams
                # keep their current signal parameters; no new evaluation/order.
                feed.retain(owner, contract, lambda: self.client(owner), cutoff,
                            snapshot["rules"]["entry"])
                continue
            if not current:
                continue  # Prior experiments need their recorded model, never today's code.
            HEALTH["state"] = "replaying"
            if (owner, account) not in done:
                done[owner, account] = store.completed_ids(owner, account)
            if entry["entry_id"] in done[owner, account] or self.job or self.deferred(entry["entry_id"]): continue
            if not entry.get("entry_at"):
                continue  # Actual fill still unknown; visible as pending, never zero P&L.
            evidence = request.get("entryEvidence") or {}
            ref = (evidence.get("pre_submit") or evidence.get("signal") or {}).get("evidence") or {}
            capture_id = ref.get("capture_id", "")
            from .databento_cache import default_cache_root
            from hashlib import sha256
            path = default_cache_root()/"topbot-worker-evidence-v1"/sha256(owner.encode()).hexdigest()/(capture_id+".sqlite3")
            if len(capture_id) != 32 or any(c not in "0123456789abcdef" for c in capture_id):
                from .topbot_study import simulate
                result = simulate(entry, [], gap_reason="missing_capture_reference")
                result.update(enrollment or {})
                store.save(owner, account, result)
                continue
            import json, os, subprocess, sys, tempfile
            from pathlib import Path
            from time import monotonic
            output = tempfile.TemporaryFile(mode="w+b")
            env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in
                   ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY", "DATABASE_URL", "SUPABASE"))}
            flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
            try:
                process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).parents[2]/"tools/compare_topbot_exits.py"),
                    "--capture", str(path)], stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL,
                    env=env, creationflags=flags)
                self.job = {"process": process, "output": output, "started": monotonic(), "owner": owner,
                            "account": account, "enrollment": enrollment or {}, "entry_id": entry["entry_id"]}
                process.stdin.write(json.dumps({"entry": entry, "owner": owner, "contract": contract}).encode())
                process.stdin.close()
            except Exception:
                if self.job: self.job["process"].terminate(); self.job = None
                output.close()
                raise
