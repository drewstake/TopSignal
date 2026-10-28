"""Explicitly unvalidated, dry-run-only model artifacts; no promotion path."""
from datetime import timedelta
import os

from .probabilistic_protocol import digest, implementation_sha, protocol
from .probabilistic_strategy import BAR, HORIZON, PathModel, utc, REGISTERED_STOP_POLICY, UNCAPPED_STOP_POLICY

FLAG = "TOPSIGNAL_UNVALIDATED_DRY_RUN"
UNCAPPED_FLAG = "TOPSIGNAL_OBSERVATION_UNCAPPED_STOPS"


def stop_policy():
    if os.getenv(UNCAPPED_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}:
        if not enabled():
            raise ValueError("Uncapped stops require explicit unvalidated dry-run mode")
        return UNCAPPED_STOP_POLICY
    return REGISTERED_STOP_POLICY


def requested():
    return os.getenv(FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def enabled():
    if not requested():
        return False
    for key in ("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION"):
        if os.getenv(key, "").strip().lower() not in {"0", "false", "no", "off"}:
            raise ValueError("Unvalidated observation requires both live execution gates explicitly disabled")
    return True


def observation_path(owner, contract_id, live, *, root=None):
    from .probabilistic_shadow import model_path
    path = model_path(owner, contract_id, live, root=root)
    return path.parent.parent.parent / "unvalidated-models" / path.parent.name / path.name


def load_model(artifact, *, as_of):
    if not enabled():
        raise ValueError("Unvalidated observation is disabled")
    if (artifact.get("validation_status") != "unvalidated"
            or artifact.get("purpose") != "dry_run_observation_only"
            or artifact.get("routing_allowed") is not False
            or artifact.get("protocol_sha256") != digest(protocol())
            or artifact.get("implementation_sha256") != implementation_sha()
            or artifact.get("model_sha256") != digest(artifact.get("model"))):
        raise ValueError("Unvalidated artifact integrity or mode mismatch")
    model = PathModel.from_dict(artifact["model"])
    if (model.stop_policy != stop_policy()
            or artifact.get("observation_stop_policy", REGISTERED_STOP_POLICY) != model.stop_policy):
        raise ValueError("Observation stop policy requires a matching refitted artifact")
    if model.version != "bayesian_cells_v1":
        raise ValueError("Observation requires the selected mathematical model")
    if model.trained_through > utc(as_of) - HORIZON * BAR:
        raise ValueError("Observation training labels violate the horizon embargo")
    age = (utc(as_of) - model.trained_through).total_seconds() / 86400
    return model, {"observation_only": True, "model_age_days": round(age, 3), "stop_policy": model.stop_policy,
                   "training_stale": age > protocol()["refit"]["max_age_days"],
                   "artifact_sha256": digest(artifact)}
