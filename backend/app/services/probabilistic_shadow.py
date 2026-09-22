"""Read-only, tenant-scoped research explanation for completed Dry Run evaluations.

No artifact can authorize a trade. Files are local, bounded and schema-checked;
sample arrays never leave this module in an API response or database result.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Sequence

from .probabilistic_strategy import (
    BAR, DEFAULT_RESEARCH_MODEL, INTERFACE_VERSION, Candle, Costs, PathModel, features, utc,
)


def cache_root() -> Path:
    configured = os.getenv("TOPSIGNAL_DATABENTO_CACHE_DIR", "").strip()
    root = Path(__file__).resolve().parents[3]
    path = Path(configured).expanduser() if configured else root / "backend/storage/databento"
    return (path if path.is_absolute() else root / path).resolve()


def scope_hash(owner: str) -> str:
    return sha256(owner.encode()).hexdigest()


def model_path(owner: str, contract_id: str, live: bool, *, root: Path | None = None) -> Path:
    stream = sha256(f"{contract_id}|{int(live)}".encode()).hexdigest()
    return (root or cache_root()) / "probabilistic-v1/models" / scope_hash(owner) / f"{stream}.json"


def unavailable(*, as_of: datetime, reason: str, data_status: str = "missing") -> dict:
    return {"interface_version": INTERFACE_VERSION, "model_version": DEFAULT_RESEARCH_MODEL,
            "validation_status": "unvalidated", "action": "NO_TRADE", "research_action": "NO_TRADE",
            "routing_allowed": False, "probability_basis": "unavailable", "horizon_minutes": 15,
            "evaluated_at": utc(as_of).isoformat(), "candle_close_timestamp": None,
            "age_seconds": None, "data_status": data_status, "model_trained_through": None,
            "stop_points": None, "target_points": None, "quantity": 1,
            "costs": Costs().describe(), "forecasts": None, "no_trade_expected_net_usd": 0.0,
            "minimum_net_edge_usd": 1.0, "training_paths": 0,
            "uncertainty_method": "Unavailable without adequate fitted data; missing evidence is not neutral.",
            "reasons": [reason, "Research only. Existing strategy and routing outcome remain authoritative."]}


def explain_shadow(*, candles: Sequence[Any], owner: str, contract_id: str, as_of: datetime,
                   root: Path | None = None, all_sessions: bool = False) -> dict:
    """Safe to call only after a Dry Run result; caller never replaces a signal."""
    result = unavailable(as_of=as_of, reason="No fitted, validated replacement is available.")
    try:
        # Do not silently filter another user's data, different contracts or old
        # partial observations into an apparently complete feature window.
        recent = list(candles)[-22:]
        rows = [Candle.from_row(row) for row in recent]
        if not rows or any(r.owner != owner or r.contract_id != contract_id for r in rows):
            raise ValueError("Missing candles or mismatched owner/contract; research cannot evaluate.")
        if rows[-1].partial:
            rows = rows[:-1]  # Only an explicitly partial trailing bar may be excluded.
        if not rows:
            raise ValueError("No completed candle available.")
        close = rows[-1].timestamp + BAR
        age = (utc(as_of) - close).total_seconds()
        result.update(candle_close_timestamp=close.isoformat(), age_seconds=age)
        if age < 0 or age > 120:
            result.update(data_status="stale")
            result["reasons"][0] = "Research needs a completed candle delivered within 120 seconds of its close."
            return result
        f = features(rows, as_of=as_of, all_sessions=all_sessions)
        result.update(data_status="fresh", stop_points=f.stop_points, target_points=math.ceil(f.stop_points * 1.5 / .25) * .25)
        path = model_path(owner, contract_id, rows[-1].live, root=root)
        if not path.exists():
            result["reasons"][0] = "No scoped research model artifact. Offline training, calibration and validation are still required."
            return result
        if path.stat().st_size > 4_000_000:
            raise ValueError("Research model artifact exceeds its size limit.")
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if (artifact.get("owner_hash") != scope_hash(owner) or artifact.get("contract_id") != contract_id
                or artifact.get("data_live") is not rows[-1].live):
            raise ValueError("Research model scope does not match this owner, contract and data subscription.")
        model = PathModel.from_dict(artifact["model"])
        if utc(as_of) - model.trained_through > timedelta(days=7):
            raise ValueError("Research model is stale; refresh offline evidence before using its forecasts.")
        forecast = model.forecast(f)
        result.update(forecast)
        # Even malicious/stale artifact metadata cannot claim calibration or authorize routing.
        result.update(action="NO_TRADE", routing_allowed=False, validation_status="unvalidated",
                      probability_basis="uncalibrated_model_estimate")
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError):
        # File details, identifiers and raw payloads do not belong in user-facing error messages.
        result.update(data_status="invalid", forecasts=None, research_action="NO_TRADE")
        result["reasons"][0] = "Research inputs or model failed scope, freshness or integrity checks; no forecast is available."
        return result
