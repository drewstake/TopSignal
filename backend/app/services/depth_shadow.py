"""Read-only, bounded Dry Run explanation. Has no broker or model-install path."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .depth_capture import latest_path, stream_key
from .depth_model import CANDLE_NAMES, FEATURE_SETS, VERSION, DepthModel
from .depth_research import timestamp
from .probabilistic_shadow import cache_root, scope_hash
from .probabilistic_strategy import Candle, BAR, features


def _reject_constant(value):
    raise ValueError("Nonfinite JSON constant.")


def explain_depth(*, owner: str, contract_id: str, candles: Sequence[Any], as_of: datetime, root: Path | None = None) -> dict:
    result = {"model_version": VERSION, "horizon_seconds": 900, "action": "NO_TRADE", "research_action": "NO_TRADE",
              "routing_allowed": False, "probability_basis": "unavailable", "feed_capability": "unverified",
              "synchronized": False, "book_status": "missing", "age_seconds": None, "bid_levels": 0, "ask_levels": 0,
              "forecasts": None, "depth_contribution": None, "trained_through": None,
              "estimated_round_trip_cost_usd": None, "cost_basis": "Fees $1.22 plus observed spread and $1 assumed slippage; one-second entry-delay scenario. Actual fills unverified.",
              "uncertainty_method": "Unavailable without a scoped model and synchronized book.",
              "reasons": ["No verified local depth capture for this owner and contract. Depth research abstains."]}
    try:
        rows = [Candle.from_row(row) for row in list(candles)[-22:]]
        if not rows or any(row.owner != owner or row.contract_id != contract_id or row.live != rows[-1].live for row in rows):
            return result
        data_live = rows[-1].live
        owner_hash = scope_hash(owner)
        path = latest_path(owner_hash, contract_id, data_live, root)
        if not path.exists():
            return result
        if path.stat().st_size > 32768:
            raise ValueError("Oversized latest-state artifact.")
        artifact = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
        if (artifact.get("schema") != "mnq-depth-latest-v1" or artifact.get("owner_hash") != owner_hash
                or artifact.get("contract_id") != contract_id or artifact.get("data_live") is not data_live
                or artifact.get("fixture") is not False or artifact.get("source") != "projectx"):
            raise ValueError("Wrong capture scope/provenance.")
        status = artifact["status"]
        if any(type(status[k]) is not int or not 0 <= status[k] <= 100 for k in ("bid_levels", "ask_levels")):
            raise ValueError("Invalid level counts.")
        capability = status["capability"]
        if capability not in {"unverified", "level1", "verified_level2"} or status.get("synthetic") is not False:
            raise ValueError("Synthetic data cannot verify a real subscription.")
        elapsed = (as_of - timestamp(artifact["as_of"])).total_seconds()
        age = elapsed + status["age_seconds"] if status.get("age_seconds") is not None else None
        if not math.isfinite(elapsed) or (age is not None and not math.isfinite(age)):
            raise ValueError("Invalid age.")
        result.update(feed_capability=capability, book_status=status["reason"], age_seconds=age,
                      bid_levels=status["bid_levels"], ask_levels=status["ask_levels"])
        if (not 0 <= elapsed <= 2 or age is None or not 0 <= age <= 2
                or status.get("provider_age_seconds") is None or not 0 <= status["provider_age_seconds"] + elapsed <= 2):
            result.update(book_status="stale_or_disconnected", reasons=["Depth capture is stale, disconnected, or missing trustworthy timestamps. No fallback is validated."])
            return result
        if (capability != "verified_level2" or status.get("synchronized") is not True
                or status.get("feature_ready") is not True or min(status["bid_levels"], status["ask_levels"]) < 5):
            result["reasons"] = ["Level 2 and a complete synchronized book are required. A subscription acknowledgement or best quotes alone are insufficient.", status["reason"]]
            return result
        result["synchronized"] = True
        if rows[-1].partial:
            rows = rows[:-1]
        if not rows or not 0 <= (as_of - rows[-1].timestamp - BAR).total_seconds() <= 120:
            result["reasons"] = ["No recent completed candle for the paired depth forecast."]
            return result
        f = features(rows, as_of=as_of)
        values = dict(zip(CANDLE_NAMES, f.values)) | artifact["features"]
        if any(not math.isfinite(float(values[name])) for name in FEATURE_SETS["level2"]):
            raise ValueError("Invalid feature values.")
        result["estimated_round_trip_cost_usd"] = 2.22 + .5 * values["spread_ticks"]
        model_path = (root or cache_root()) / "depth-v1/models" / owner_hash / (stream_key(contract_id, data_live) + ".json")
        if not model_path.exists():
            result["reasons"] = ["Synchronized observations are available, but no scoped paired research model is installed. Validation is incomplete."]
            return result
        if model_path.stat().st_size > 256000:
            raise ValueError("Oversized model artifact.")
        fitted = json.loads(model_path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
        if (fitted.get("owner_hash") != owner_hash or fitted.get("contract_id") != contract_id
                or fitted.get("data_live") is not data_live or fitted.get("synthetic") is not False):
            raise ValueError("Model scope/provenance mismatch.")
        from .depth_validation import protocol
        from .depth_research import digest
        if fitted.get("protocol_sha256") != digest(protocol()):
            raise ValueError("Model protocol mismatch.")
        models = {key: DepthModel.from_dict(fitted["models"][key]) for key in ("level1", "level2")}
        if len({(m.training_examples, m.training_days, m.trained_through) for m in models.values()}) != 1:
            raise ValueError("Contribution requires a paired training cohort.")
        predictions = {}
        for key, model in models.items():
            if model.feature_set != key or model.horizon_seconds != 900 or as_of - model.trained_through > timedelta(days=7):
                raise ValueError("Wrong or stale research model.")
            predictions[key] = model.forecast([values[name] for name in FEATURE_SETS[key]], at=as_of)
        depth = predictions["level2"]; l1 = predictions["level1"]
        result.update(probability_basis="uncalibrated_model_estimate", forecasts=depth["forecasts"],
                      research_action=depth["research_action"], trained_through=depth["trained_through"],
                      uncertainty_method=depth["uncertainty_method"], reasons=depth["reasons"],
                      depth_contribution={side: {"probability_difference": depth["forecasts"][side]["probability_net_positive"] - l1["forecasts"][side]["probability_net_positive"],
                                                "expected_net_difference_usd": depth["forecasts"][side]["expected_net_usd"] - l1["forecasts"][side]["expected_net_usd"]} for side in ("BUY", "SELL")})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        result.update(book_status="invalid", synchronized=False, forecasts=None, depth_contribution=None,
                      age_seconds=None, estimated_round_trip_cost_usd=None,
                      probability_basis="unavailable", research_action="NO_TRADE",
                      reasons=["Depth inputs or model failed integrity, scope, freshness or numerical checks. No forecast is available."])
    return result
