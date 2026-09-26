"""Local experiment binding and reviewed frozen-specification refits."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re

from .probabilistic_protocol import digest, implementation_sha, protocol
from .probabilistic_strategy import BAR, HORIZON, PathModel, make_samples, utc

REQUIRED_EVIDENCE = ("untouched_holdout", "forward_dry_run", "fill_exit_parity", "intratrade_drawdown", "experiment_ledger")
EVIDENCE_CHECKS = {
    "untouched_holdout": {"positive_expectancy_bounds", "positive_paired_advantage", "calibration", "risk_limits", "cost_stresses", "unseen_provenance"},
    "forward_dry_run": {"observed_sessions", "inside_research_confidence_band", "session_population", "calibration", "routing_disposition"},
    "fill_exit_parity": {"bar_alignment", "fees_reconciled", "bracket_mode", "slippage", "target_nonfills", "exit_drift", "halt_behavior", "retrieve_bars_limit"},
    "intratrade_drawdown": {"complete_marks", "within_registered_limit"},
    "experiment_ledger": {"complete_research_history", "multiplicity_recomputed"},
}
OFFLINE_CHECKS = frozenset((
    "trade_count", "positive_expectancy_bounds", "calibration", "drawdown_closed_equity",
    "tail_loss", "without_best_days", "without_best_trades", "day_concentration", "regime_support",
    "cost_latency_parameter_stress", "missing_volume_abstention", "paired_incumbent_advantage",
    "brier_vs_pool", "crps_usd_vs_pool", "at_least_three_folds", "no_negative_fold",
    "registered_development", "beats_directional_baselines",
))


def record_evidence(record: dict, *, root: Path, as_of: datetime) -> str:
    """Archive an explicitly reviewed observation record; never promote a model."""
    if (record.get("kind") not in REQUIRED_EVIDENCE or not record.get("reviewed_by")
            or not record.get("checks") or any(type(v) is not bool for v in record["checks"].values())
            or not EVIDENCE_CHECKS.get(record.get("kind"), set()) <= record["checks"].keys()
            or record.get("protocol_sha256") != digest(protocol())):
        raise ValueError("Evidence requires reviewer, registered protocol and explicit boolean checks")
    report = json.loads(experiment_path(root, record.get("experiment_id")).read_text(encoding="utf-8"))
    if record.get("model_version") not in report.get("pooled", {}):
        raise ValueError("Evidence references no evaluated model")
    if utc(datetime.fromisoformat(record["observed_through"])) > utc(as_of):
        raise ValueError("Evidence comes from the future")
    checksum = digest(record)
    path = root / "probabilistic-v3/evidence" / (checksum + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True, allow_nan=False)
    return checksum


def experiment_path(root: Path, experiment_id: str) -> Path:
    if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-f0-9]{64}", experiment_id):
        raise ValueError("Invalid experiment reference")
    return root / "probabilistic-v3/experiments" / experiment_id / "report.json"


def validate_binding(artifact: dict, *, root: Path, as_of: datetime) -> tuple[PathModel, dict]:
    path = experiment_path(root, artifact.get("experiment_id"))
    if path.stat().st_size > 25_000_000:
        raise ValueError("Experiment exceeds its size bound")
    report = json.loads(path.read_text(encoding="utf-8"))
    if (artifact.get("experiment_sha256") != digest(report)
            or artifact.get("protocol_sha256") != digest(protocol())
            or artifact.get("implementation_sha256") != implementation_sha()
            or report.get("protocol_sha256") != artifact["protocol_sha256"]
            or report.get("implementation_sha256") != artifact["implementation_sha256"]):
        raise ValueError("Experiment/specification/implementation binding mismatch")
    version = artifact["model"]["version"]
    result = report.get("pooled", {}).get(version, {})
    checks = result.get("acceptance_checks", {})
    if (report.get("mode") != "development" or report.get("status") != "offline_passed"
            or report.get("experiment_id") != artifact["experiment_id"] or not OFFLINE_CHECKS <= checks.keys()
            or not all(v is True for v in checks.values())):
        raise ValueError("Artifact requires a passed development experiment")
    model = PathModel.from_dict(artifact["model"])
    if model.version not in protocol()["candidates"]:
        raise ValueError("A baseline cannot be installed as a candidate")
    if model.pool_mix != result.get("frozen_pool_mix"):
        raise ValueError("Refit changed the frozen model specification")
    if model.trained_through > utc(as_of) - HORIZON * BAR:
        raise ValueError("Training labels violate the live horizon embargo")
    if utc(as_of) - model.trained_through > timedelta(days=protocol()["refit"]["max_age_days"]):
        raise ValueError("Refit is stale")
    review = artifact.get("review", {})
    if (artifact.get("validation_status") not in {"offline_passed", "passed"} or not review.get("reviewed_by")
            or review.get("model_version") != model.version):
        raise ValueError("Artifact has not passed installation review")
    if artifact["validation_status"] == "offline_passed":
        return model, report  # Forward observation only; cannot authorize live routing.
    evidence = dict(review.get("evidence", {}))
    for name in REQUIRED_EVIDENCE:
        item = evidence.get(name, {})
        if item.get("passed") is not True or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", ""))):
            raise ValueError("Missing reviewed evidence: " + name)
        # References must resolve to immutable local evidence, not arbitrary
        # strings in a hand-edited model file. Never follow supplied paths.
        evidence_path = root / "probabilistic-v3/evidence" / (item["sha256"] + ".json")
        if evidence_path.stat().st_size > 25_000_000:
            raise ValueError("Evidence exceeds its size bound")
        record = json.loads(evidence_path.read_text(encoding="utf-8"))
        if (digest(record) != item["sha256"] or record.get("kind") != name
                or record.get("experiment_id") != artifact["experiment_id"]
                or record.get("protocol_sha256") != artifact["protocol_sha256"] or record.get("model_version") != model.version
                or not record.get("reviewed_by") or not EVIDENCE_CHECKS[name] <= record.get("checks", {}).keys()
                or not all(v is True for v in record["checks"].values())):
            raise ValueError("Evidence binding/checks mismatch: " + name)
        observed = utc(datetime.fromisoformat(record["observed_through"]))
        if observed > utc(as_of):
            raise ValueError("Evidence comes from the future")
        # Summary facts are taken from the hashed evidence, never the review.
        evidence[name] = {**record, "sha256": item["sha256"]}
    if evidence["forward_dry_run"].get("sessions", 0) < protocol()["acceptance"]["minimum_forward_dry_run_sessions"]:
        raise ValueError("Insufficient forward sessions")
    if evidence["untouched_holdout"].get("first_session", "") <= protocol()["untouched_after"]:
        raise ValueError("Holdout predates the specification freeze")
    if (evidence["untouched_holdout"].get("sessions", 0) < protocol()["untouched_final_sessions"]
            or evidence["untouched_holdout"].get("trades", 0) < protocol()["acceptance"]["minimum_final_trades"]):
        raise ValueError("Insufficient untouched holdout")
    count = evidence["experiment_ledger"].get("configuration_count", 0)
    if count < protocol()["candidate_configuration_count"] or count > report.get("multiplicity_count", 0):
        raise ValueError("Experiment must be evaluated with the verified multiplicity count")
    if evidence["experiment_ledger"].get("historical_count_verified") is not True:
        raise ValueError("Historical experiment count is unverified")
    return model, report


def refit(rows, *, as_of: datetime, version: str, pool_mix: float) -> PathModel:
    """Refit the frozen rule on the last 60 complete training sessions."""
    from .probabilistic_validation import audit_candles
    cutoff = utc(as_of) - HORIZON * BAR
    observed = [row for row in rows if row.timestamp + BAR <= cutoff]
    days = audit_candles(observed)["eligible_complete_sessions"][-protocol()["refit"]["training_sessions"]:]
    if len(days) < protocol()["refit"]["training_sessions"]:
        raise ValueError("Refit requires the complete rolling training window")
    samples, _ = make_samples(observed, stride=protocol()["training_label_stride_bars"])
    chosen = tuple(s for s in samples if s.features.day in set(days) and s.label_end <= cutoff)
    if len(chosen) < protocol()["minimum_training_examples"]:
        raise ValueError("Insufficient independent refit paths")
    return PathModel(version, chosen, pool_mix)


def install(artifact: dict, *, root: Path, owner: str, live: bool, as_of: datetime) -> Path:
    from .probabilistic_shadow import model_path, scope_hash
    validate_binding(artifact, root=root, as_of=as_of)
    if artifact.get("owner_hash") != scope_hash(owner) or artifact.get("data_live") is not live:
        raise ValueError("Refit owner/subscription mismatch")
    if artifact.get("root_symbol") != "MNQ" or artifact.get("roll_policy") != protocol()["roll_policy"]:
        raise ValueError("Refit root/roll policy mismatch")
    destination = model_path(owner, "CON.F.US.MNQ", live, root=root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(".pending")
    pending.write_text(json.dumps(artifact, sort_keys=True, allow_nan=False), encoding="utf-8")
    pending.replace(destination)
    return destination
