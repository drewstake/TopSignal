"""Bounded background confirmation of archived decisions; never routes orders."""
from threading import Lock, Thread
from time import monotonic

from .market_archive import enabled, failure, recover_decision_commits


class DecisionCommitRecovery:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self._lock = Lock()
        self._busy = False
        self._next_at = 0.0

    def request(self, owners):
        owners = tuple(sorted(set(owners)))
        if not owners or not enabled():
            return False
        with self._lock:
            if self._busy or monotonic() < self._next_at:
                return False
            self._busy = True
            self._next_at = monotonic() + 60
        try:
            Thread(target=self._run, args=(owners,), daemon=True,
                   name="archive-decision-recovery").start()
        except Exception:
            with self._lock:
                self._busy = False
            raise
        return True

    def _run(self, owners):
        try:
            for owner in owners:
                try:
                    recover_decision_commits(self.session_factory, owner)
                except Exception as exc:
                    failure(owner, 'decision_commit_recovery', exc)
        finally:
            with self._lock:
                self._busy = False
