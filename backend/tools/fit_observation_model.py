"""Fit an unvalidated mathematical artifact for explicitly authorized dry-run observation."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uncapped-stops", action="store_true",
                        help="Fit the separate dry-run population without the 25-point ceiling")
    args = parser.parse_args()
    from research_probabilistic_topbot import offline_guard
    offline_guard()
    from app.services.probabilistic_data import read_databento
    from app.services.probabilistic_protocol import digest, implementation_sha, protocol
    from app.services.probabilistic_shadow import scope_hash
    from app.services.probabilistic_validation import audit_candles
    now = datetime.now(timezone.utc)
    rows, source_hash, _ = read_databento(args.cache_root)
    days = audit_candles(rows)["eligible_complete_sessions"][-240:]
    if len(days) < 240:
        raise ValueError("Observation fitting requires 240 complete sessions")
    # Keep sufficient preceding bars for features; never train on future labels.
    from app.services.probabilistic_strategy import ET
    from datetime import timedelta
    cutoff = datetime.fromisoformat(days[0]).replace(tzinfo=ET) - timedelta(days=1)
    recent = [r for r in rows if r.timestamp >= cutoff]
    # The existing Bayesian model's default mixture, not a tuned or validated choice.
    from app.services.probabilistic_strategy import make_samples, PathModel, BAR, REGISTERED_STOP_POLICY, UNCAPPED_STOP_POLICY
    from app.services.probabilistic_payoff import PAPER_PAYOFF
    policy = UNCAPPED_STOP_POLICY if args.uncapped_stops else REGISTERED_STOP_POLICY
    samples, _ = make_samples(recent, stride=3, stop_policy=policy, payoff_semantics=PAPER_PAYOFF)
    chosen = tuple(s for s in samples if s.features.day in set(days) and s.label_end <= now-timedelta(minutes=15))
    if len(chosen) < protocol()["minimum_training_examples"]:
        raise ValueError("Insufficient independent observation paths")
    model = PathModel("bayesian_cells_v1", chosen[-4096:], pool_mix=0.0, stop_policy=policy, payoff_semantics=PAPER_PAYOFF)
    artifact = {"purpose": "dry_run_observation_only", "validation_status": "unvalidated",
        "routing_allowed": False, "owner_hash": scope_hash(args.owner), "root_symbol": "MNQ",
        "roll_policy": protocol()["roll_policy"], "data_live": False,
        "protocol_sha256": digest(protocol()), "implementation_sha256": implementation_sha(),
        "source_sha256": source_hash, "created_at": now.isoformat(),
        "observation_stop_policy": policy,
        "payoff_semantics": PAPER_PAYOFF,
        "training_sessions": days, "training_window_policy": "240 complete sessions, latest 4096 eligible stride-three paths; unvalidated observation only",
        "model": model.to_dict(),
        "limitations": ["No passed experiment; not validated.", "Training may be stale; observation only.",
                        "Default Bayesian mixture 0.0; no new parameter selection or profitability claim."]}
    artifact["model_sha256"] = digest(artifact["model"])
    artifact["experiment_id"] = "observation-" + digest({"source": source_hash, "created_at": now.isoformat()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(artifact, stream, sort_keys=True, allow_nan=False)
    print(json.dumps({"artifact": str(args.output), "samples": len(model.samples),
                      "trained_through": model.trained_through.isoformat(),
                      "validation_status": "unvalidated", "sha256": digest(artifact)}))


if __name__ == "__main__":
    main()
