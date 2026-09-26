"""Registered local-only development, parity audit, and reviewed model refits."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def read_historical_ledger(path: Path) -> dict:
    historical = json.loads(path.read_text(encoding="utf-8"))
    entries = historical.get("configurations", [])
    if (not historical.get("reviewed_by") or type(historical.get("historical_count_verified")) is not bool
            or not entries or any(not isinstance(row.get("id"), str) or not row["id"] for row in entries)
            or len({row["id"] for row in entries}) != len(entries)):
        raise ValueError("Historical ledger needs reviewed, unique configuration IDs and explicit completeness status")
    return historical


def main():
    from research_probabilistic_topbot import offline_guard, read_candles
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--mode", choices=("audit", "diagnostic", "development"), default="audit")
    parser.add_argument("--sqlite", type=Path, help="Optional read-only ProjectX overlap for parity, never training")
    parser.add_argument("--contract", help="Explicit ProjectX delivery for the optional parity audit")
    parser.add_argument("--owner-hash")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--refit-experiment", help="Passed experiment SHA; requires --review and --owner")
    parser.add_argument("--review", type=Path, help="Reviewed evidence JSON; validation_status offline_passed or passed")
    parser.add_argument("--owner")
    parser.add_argument("--historical-ledger", type=Path, help="Reviewed pre-v3 configuration ledger; incomplete inventories increase the correction but cannot authorize promotion")
    parser.add_argument("--record-evidence", type=Path, help="Archive reviewed observed evidence without installing or promoting")
    args = parser.parse_args()
    if args.sqlite and not args.contract:
        parser.error("The ProjectX parity audit requires an explicit --contract")
    offline_guard()
    from app.services.probabilistic_shadow import cache_root, scope_hash
    from app.services.probabilistic_protocol import digest, implementation_sha, protocol
    from app.services.probabilistic_data import read_databento, parity_report
    from app.services.probabilistic_validation import audit_candles, walk_forward_plan, development, pool_development, bootstrap_seed_sensitivity
    from app.services.databento_cache import DatabentoCacheError
    from app.services.probabilistic_artifacts import experiment_path, refit, install, record_evidence
    spec = protocol()
    root = (args.cache_root or cache_root()).resolve()
    if args.record_evidence:
        record = json.loads(args.record_evidence.read_text(encoding="utf-8"))
        checksum = record_evidence(record, root=root, as_of=datetime.now(timezone.utc))
        print(json.dumps({"evidence_sha256": checksum, "promoted": False}))
        return 0
    rows, source_hash, quality = [], None, {}
    missing = None
    try:
        rows, source_hash, quality = read_databento(root)
    except (DatabentoCacheError, FileNotFoundError, ValueError) as exc:
        missing = str(exc)
    if args.refit_experiment:
        if missing or not args.review or not args.owner:
            parser.error("Refit needs local history, a reviewed evidence file and an owner")
        report = json.loads(experiment_path(root, args.refit_experiment).read_text(encoding="utf-8"))
        review = json.loads(args.review.read_text(encoding="utf-8"))
        version = review["model_version"]
        now = datetime.now(timezone.utc)
        model = refit(rows, as_of=now, version=version, pool_mix=report["pooled"][version]["frozen_pool_mix"])
        artifact = {"experiment_id": args.refit_experiment, "experiment_sha256": digest(report),
                    "protocol_sha256": digest(spec), "implementation_sha256": implementation_sha(),
                    "owner_hash": scope_hash(args.owner), "root_symbol": "MNQ", "roll_policy": spec["roll_policy"],
                    "data_live": False, "model": model.to_dict(), "validation_status": review["validation_status"],
                    "review": review}
        path = install(artifact, root=root, owner=args.owner, live=False, as_of=now)
        print(json.dumps({"installed": str(path), "validation_status": artifact["validation_status"]}))
        return 0
    historical = None
    if args.historical_ledger:
        try:
            historical = read_historical_ledger(args.historical_ledger)
        except ValueError as exc:
            parser.error(str(exc))
    px, parity_hash = [], None
    if args.sqlite:
        px, _, parity_hash, _ = read_candles(args.sqlite, args.contract, args.owner_hash)
    provenance = {"source_sha256": source_hash, "protocol_sha256": digest(spec),
                  "implementation_sha256": implementation_sha(), "mode": args.mode,
                  "parity_source_sha256": parity_hash, "historical_ledger_sha256": digest(historical) if historical else None,
                  "source_kind": "local_databento_causal_volume_roll", "roll_policy": spec["roll_policy"]}
    experiment_id = digest(provenance)
    destination = experiment_path(root, experiment_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # An exclusive preregistration file is written before any model is fitted or
    # scored. Prior records are immutable and count toward family multiplicity.
    registration = destination.with_name("registration.json")
    if not registration.exists():
        with registration.open("x", encoding="utf-8") as stream:
            json.dump({**provenance, "experiment_id": experiment_id,
                       "registered_at": datetime.now(timezone.utc).isoformat(),
                       "configurations": spec["candidate_configuration_count"],
                       "historical_count_verified": bool(historical and historical["historical_count_verified"])}, stream, indent=2)
    records = [json.loads(p.read_text(encoding="utf-8")) for p in destination.parent.parent.glob("*/registration.json")]
    count = sum(r["configurations"] for r in records if r["mode"] != "audit") + (len(historical["configurations"]) if historical else 0)
    if destination.exists():
        report = json.loads(destination.read_text(encoding="utf-8"))
    else:
        if args.mode == "audit" or missing:
            audit = audit_candles(rows)
            report = {"data_audit": audit, "plan": walk_forward_plan(audit["eligible_complete_sessions"], spec),
                      "status": "insufficient_data" if missing else "audit_only", "folds": [],
                      "promotion_eligible": False, "blockers": [missing] if missing else []}
        else:
            report, _ = development(rows, spec, diagnostic=args.mode == "diagnostic",
                                    progress=lambda message: print(message, file=sys.stderr, flush=True))
            if report["folds"]:
                pool_development(report, multiplicity_count=count)
                days = [d for f in report["folds"] for d in f["sessions"]["validation"]]
                for result in report["pooled"].values():
                    result["bootstrap_seed_sensitivity"] = bootstrap_seed_sensitivity(result["ledger"], days,
                        alpha=spec["familywise_alpha"]/max(count, spec["candidate_configuration_count"]))
        report.update(provenance, experiment_id=experiment_id, data_quality=quality,
                      historical_experiment_count_verified=bool(historical and historical["historical_count_verified"]), registered_experiment_count=len(records),
                      multiplicity_count=max(count, spec["candidate_configuration_count"]),
                      promotion_eligible=False,
                      operational_effects={"orders": 0, "database_writes": 0, "historical_restores": 0})
        if args.sqlite:
            report["projectx_parity"] = parity_report(rows, px)
        archive_ledgers(report, destination.parent)
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
    summary = {"experiment_id": experiment_id, "status": report["status"], "promotion_eligible": False,
               "complete_sessions": len(report["data_audit"]["eligible_complete_sessions"]),
               "blockers": report.get("blockers", []), "report": str(destination),
               "projectx_parity": report.get("projectx_parity")}
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0


def archive_ledgers(report, directory):
    """Large replay ledgers stay in local companion files, never model metadata."""
    from app.services.probabilistic_protocol import digest
    def archive(node):
        if isinstance(node, dict):
            # Fold baselines are lists until pooling is complete. Package them
            # exactly like candidate/stress ledgers so long histories do not
            # exceed the bounded report accepted by the artifact loader.
            if isinstance(node.get("baselines"), dict):
                for name, value in list(node["baselines"].items()):
                    if isinstance(value, list):
                        node["baselines"][name] = {"ledger": value}
            if isinstance(node.get("ledger"), list):
                ledger = node.pop("ledger")
                checksum = digest(ledger)
                path = directory / "ledgers" / (checksum + ".json")
                path.parent.mkdir(exist_ok=True)
                if not path.exists():
                    with path.open("x", encoding="utf-8") as stream:
                        json.dump(ledger, stream, sort_keys=True, allow_nan=False)
                node["ledger_reference"] = {"sha256": checksum, "rows": len(ledger), "path": path.relative_to(directory).as_posix()}
            for child in node.values():
                archive(child)
        elif isinstance(node, list):
            for child in node:
                archive(child)
    archive(report)


if __name__ == "__main__":
    raise SystemExit(main())
