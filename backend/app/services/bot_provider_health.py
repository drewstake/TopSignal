"""Bounded, read-only connection checks independent of the trading worker loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isfinite
from threading import BoundedSemaphore, Event, Lock, Thread
from time import monotonic
from typing import Any, Callable


@dataclass
class _Observation:
    status: str = "unknown"
    checked_at: datetime | None = None
    success_at: datetime | None = None
    retry_at: datetime | None = None
    deadline: float = 0
    in_flight: bool = False
    done: Event = field(default_factory=Event)


class BotProviderHealth:
    def __init__(self, client_factory: Callable[[str], Any], *, interval: float, timeout: float = 8):
        self._client_factory = client_factory
        self.interval = interval
        self.timeout = timeout
        self._lock = Lock()
        self._slots = BoundedSemaphore(4)
        self._users: dict[str, _Observation] = {}

    def inspect(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            observation = self._users.get(user_id, _Observation())
            self._expire_probe(observation)
            status = observation.status
            now = datetime.now(timezone.utc)
            if status == "ok" and (
                observation.checked_at is None
                or now - observation.checked_at > timedelta(seconds=self.interval * 2)
            ):
                status = "stale"
            if observation.in_flight and status in {"unknown", "stale"}:
                status = "checking"
            return {
                "status": status,
                "last_checked_at": _iso(observation.checked_at),
                "last_success_at": _iso(observation.success_at),
                "retry_at": _iso(observation.retry_at),
            }

    def refresh(self, user_id: str, *, wait: bool = False) -> dict[str, Any]:
        with self._lock:
            now = datetime.now(timezone.utc)
            observation = self._users.get(user_id)
            if observation is None:
                # Bound retained users as well as concurrent network requests.
                if len(self._users) >= 256:
                    oldest = next((key for key, value in self._users.items() if not value.in_flight
                                   and (value.retry_at is None or value.retry_at <= now)), None)
                    if oldest is None:
                        return {"status": "unknown", "last_checked_at": None, "last_success_at": None, "retry_at": None}
                    del self._users[oldest]
                observation = self._users.setdefault(user_id, _Observation())
            self._expire_probe(observation)
            if not observation.in_flight and (observation.retry_at is None or now >= observation.retry_at):
                if self._slots.acquire(blocking=False):
                    observation.in_flight = True
                    observation.done.clear()
                    observation.deadline = monotonic() + self.timeout
                    try:
                        Thread(target=self._probe, args=(user_id, observation), daemon=True).start()
                    except RuntimeError:
                        observation.in_flight = False
                        observation.status = "error"
                        observation.retry_at = now + timedelta(seconds=self.interval)
                        observation.done.set()
                        self._slots.release()
            done = observation.done
            remaining = max(0, observation.deadline - monotonic()) if observation.in_flight else 0
        if wait and remaining:
            done.wait(remaining)
        return self.inspect(user_id)

    def _expire_probe(self, observation: _Observation) -> None:
        if observation.in_flight and monotonic() >= observation.deadline and observation.status != "timeout":
            observation.status = "timeout"
            observation.checked_at = datetime.now(timezone.utc)
            observation.retry_at = observation.checked_at + timedelta(seconds=self.interval)

    def _probe(self, user_id: str, observation: _Observation) -> None:
        status = "error"
        retry_seconds = self.interval
        try:
            client = self._client_factory(user_id)
            # The factory returns a dedicated client; never alter the worker's client.
            client.timeout_seconds = min(float(client.timeout_seconds), self.timeout)
            client.list_accounts(only_active_accounts=False)
            status = "ok"
        except Exception as exc:
            status = "throttled" if getattr(exc, "status_code", None) == 429 else "error"
            retry_after = getattr(exc, "retry_after_seconds", None)
            if isinstance(retry_after, (int, float)) and isfinite(retry_after):
                retry_seconds = max(retry_seconds, retry_after)
        finally:
            with self._lock:
                now = datetime.now(timezone.utc)
                # A late result must never authorize a start after its deadline.
                if monotonic() >= observation.deadline:
                    status = "timeout"
                observation.status = status
                observation.checked_at = now
                try:
                    observation.retry_at = now + timedelta(seconds=retry_seconds)
                except OverflowError:
                    observation.retry_at = datetime.max.replace(tzinfo=timezone.utc)
                if status == "ok":
                    observation.success_at = now
                observation.in_flight = False
                observation.done.set()
            self._slots.release()


def provider_health_message(health: dict[str, Any]) -> str:
    status = health["status"]
    message = {
        "unknown": "ProjectX connection has not been verified yet. A connection check will retry automatically.",
        "checking": "Checking your ProjectX connection. Runs will unlock after verification succeeds.",
        "stale": "Your ProjectX connection check has expired. Verification will retry automatically.",
        "timeout": "Your ProjectX connection check timed out. Verification will retry automatically.",
        "throttled": "ProjectX is rate-limiting your connection. Waiting before retrying.",
        "error": "Your ProjectX connection could not be verified. Check your provider credentials and connection; verification will retry automatically.",
    }.get(status, "ProjectX connection has not been verified yet.")
    if health.get("last_success_at"):
        message += f" Last successful check: {health['last_success_at']}."
    if health.get("retry_at") and status != "checking":
        message += f" Next check no earlier than {health['retry_at']}."
    return message


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
