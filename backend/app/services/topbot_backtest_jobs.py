"""Bounded, owner-scoped in-memory jobs; input candles never enter SQL or responses."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import topbot_backtest
from .probabilistic_strategy import utc


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: datetime
    end: datetime
    starting_balance: float = Field(default=50000, gt=0, le=1e9)
    commission_per_contract: float = Field(default=.61, ge=0, le=100)
    slippage_ticks: int = Field(default=1, ge=0, le=100, strict=True)
    model_source: Literal["saved_running_model", "historical_refit"] = "saved_running_model"
    model_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_window(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Use timezone-aware start and end dates.")
        self.start, self.end = utc(self.start), utc(self.end)
        if not timedelta(0) < self.end-self.start <= timedelta(days=366):
            raise ValueError("Choose a test period from one day to 366 days.")
        if self.start.year < 2019 or self.end.year > 2100:
            raise ValueError("Dates are outside the supported history range.")
        return self


@dataclass
class Job:
    owner: str
    request: BacktestRequest
    id: str = field(default_factory=lambda: uuid4().hex)
    cancel: Event = field(default_factory=Event)
    touched: float = field(default_factory=monotonic)
    status: str = "running"
    progress: dict = field(default_factory=lambda: {"phase": "loading", "completed": 0, "total": 1})
    result: dict | None = None
    error: str | None = None


class Jobs:
    def __init__(self):
        self.lock = Lock()
        self.items: dict[str, Job] = {}

    def start(self, owner, payload):
        with self.lock:
            self.items = {k: v for k, v in self.items.items() if v.status == "running" or monotonic()-v.touched < 3600}
            if any(j.status == "running" for j in self.items.values()):
                raise RuntimeError("A historical backtest is already running. Wait for it to finish or cancel your active run.")
            # A result is bounded to 366 days and 30 entries/day. At most eight
            # completed results are kept in memory across all owners.
            while len(self.items) >= 8:
                self.items.pop(min(self.items, key=lambda k: self.items[k].touched))
            job = Job(owner, payload)
            self.items[job.id] = job
        try:
            Thread(target=self._run, args=(job,), daemon=True, name="topbot-historical-replay").start()
        except Exception:
            with self.lock:
                self.items.pop(job.id, None)
            raise
        return self.get(owner, job.id)

    def _run(self, job):
        def report(phase, completed, total):
            with self.lock:
                job.progress = {"phase": phase, "completed": completed, "total": total}
        try:
            result = topbot_backtest.run(job.request, owner=job.owner, cancel=job.cancel, progress=report)
            with self.lock:
                job.status = "cancelled" if job.cancel.is_set() else "complete"
                if job.status == "complete":
                    job.result = result
        except topbot_backtest.Cancelled:
            with self.lock:
                job.status = "cancelled"
        except ValueError as exc:
            with self.lock:
                job.status, job.error = "failed", str(exc)
        except Exception:
            logging.getLogger(__name__).exception("topbot_historical_backtest_failed")
            with self.lock:
                job.status, job.error = "failed", "Backtest failed while reading or replaying local data. Check the backend log and cache."
        finally:
            with self.lock:
                job.touched = monotonic()

    def completed(self, owner):
        """Bounded metadata for selecting a user-owned analytical scenario."""
        with self.lock:
            return [{"id": job.id, "start": job.request.start.isoformat(), "end": job.request.end.isoformat()}
                    for job in sorted(self.items.values(), key=lambda item: item.touched, reverse=True)
                    if job.owner == owner and job.status == "complete" and job.result is not None
                    and monotonic()-job.touched < 3600]

    def get(self, owner, identifier, *, cancel=False):
        with self.lock:
            job = self.items.get(identifier)
            if job is None or job.owner != owner or (job.status != "running" and monotonic()-job.touched >= 3600):
                raise LookupError("Backtest expired or was not found. Run it again; results are temporary.")
            if cancel and job.status == "running":
                job.cancel.set()
            return {"id": job.id, "status": job.status, "progress": dict(job.progress),
                    "cancel_requested": job.cancel.is_set(), "error": job.error, "result": job.result}


jobs = Jobs()
