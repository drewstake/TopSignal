"""Bounded filesystem-only forecasts and hypothetical 15-minute outcomes."""
from datetime import datetime, timedelta
from functools import lru_cache
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import threading

import numpy as np

from .probabilistic_observation import enabled, observation_path
from .probabilistic_strategy import BAR, Candle, Costs, contiguous, price_paths, utc

_lock = threading.Lock()


@lru_cache(maxsize=8)
def _logger(path):
    logger = logging.Logger("observation_results")
    handler = RotatingFileHandler(path, maxBytes=10_485_760, backupCount=14, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def collect(candles, *, owner, contract_id, as_of, forecast=None, root=None):
    """Record both sides, including HOLD forecasts. Outcomes are never broker fills."""
    if not enabled() or not candles:
        return
    rows = [Candle.from_row(row) for row in candles]
    if any(row.owner != owner or row.contract_id != contract_id or row.live != rows[-1].live for row in rows):
        raise ValueError("Observation scope mismatch")
    base = observation_path(owner, contract_id, rows[-1].live, root=root)
    directory = base.parent.parent.parent / "observations" / base.parent.name
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / (base.stem + ".state.json")
    configured_log = os.getenv("TOPSIGNAL_OBSERVATION_LOG_PATH", "")
    log_path = Path(configured_log) if configured_log else directory / (base.stem + ".jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    now = utc(as_of)
    with _lock:
        state = json.loads(state_path.read_text()) if state_path.exists() else {"pending": [], "last_forecast": None}
        logger = _logger(str(log_path))

        def emit(event, record):
            logger.info(json.dumps({"event": event, "recorded_at": now.isoformat(),
                "validation_status": "unvalidated", "execution_mode": "dry_run",
                "broker_orders": False, **record}, allow_nan=False, sort_keys=True))

        remaining = []
        by_stamp = {row.timestamp: row for row in rows if not row.partial and row.timestamp + BAR <= now}
        for pending in state["pending"]:
            close = datetime.fromisoformat(pending["decision_close"])
            if now < close + 3 * BAR:
                remaining.append(pending)
                continue
            future = [by_stamp.get(close + i * BAR) for i in range(3)]
            if (pending["contract_id"] == contract_id and all(future) and contiguous(future)):
                reference = future[0].open
                path = np.array([[[v-reference for v in (r.open, r.high, r.low, r.close)] for r in future]])
                outcomes = {}
                for action, side in (("BUY", 1), ("SELL", -1)):
                    net, kind, bars = price_paths(path, stop=pending["stop_points"], side=side, costs=Costs(), scale=1.0)
                    outcomes[action] = {"hypothetical_net_usd": float(net[0]),
                                        "exit_kind": ("stop", "time", "target")[int(kind[0])], "bars_exposed": int(bars[0])}
                emit("observation_outcome", {**pending, "outcome_status": "observed",
                    "outcomes": outcomes, "fill_assumption": "Next observed bar open; registered costs; stop-first OHLC. Not actual fills or portfolio P&L."})
            elif now > close + timedelta(hours=2) or pending["contract_id"] != contract_id:
                emit("observation_outcome", {**pending, "outcome_status": "unknown_missing_bars_or_contract_roll"})
            else:
                remaining.append(pending)
        if forecast and forecast.get("observation_only") is True and forecast.get("forecasts"):
            stamp = forecast["candle_close_timestamp"]
            # Do not double count request retries or process restarts.
            if state["last_forecast"] is None or stamp > state["last_forecast"]:
                record = {"observation_id": contract_id + ":" + stamp, "decision_close": stamp,
                    "contract_id": contract_id, "research_action": forecast["research_action"],
                    "stop_points": forecast["stop_points"], "target_points": forecast["target_points"],
                    "model_age_days": forecast["model_age_days"], "training_stale": forecast["training_stale"],
                    "artifact_sha256": forecast["artifact_sha256"]}
                emit("observation_forecast", {**record, "forecasts": forecast["forecasts"]})
                remaining.append(record)
                state["last_forecast"] = stamp
        if len(remaining) > 64:
            raise ValueError("Observation pending-state limit exceeded")
        state["pending"] = remaining
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, allow_nan=False), encoding="utf-8")
        temporary.replace(state_path)
