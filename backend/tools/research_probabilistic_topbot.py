"""Reproducible offline MNQ audit/development; never connects to a provider.

Example (from repo root):
  backend/.venv/Scripts/python backend/tools/research_probabilistic_topbot.py \
      --sqlite backend/storage/offline/topsignal.sqlite3 --mode diagnostic

Historical data stays in the configured filesystem cache. This command reads an
existing local SQLite snapshot; it is not a Databento/cloud import or restoration.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
# Execution and risk constants are versioned in the model, not operator tuning
# controls. Refuse a changed declaration instead of reporting tests of a protocol
# that the implementation did not actually follow.
REGISTERED_PROTOCOL_SHA256 = "68f3f3d49ed1aa3026d2b978799bec278e8177972abdde052c1cfeb303f6046e"


def offline_guard() -> None:
    # Applied before any app imports; operator dotenv credentials are not loaded.
    keep_cache = os.getenv("TOPSIGNAL_DATABENTO_CACHE_DIR")
    for name in tuple(os.environ):
        if name.startswith(("PROJECTX_", "TOPSTEP_", "TOPSTEPX_", "DATABENTO_", "SUPABASE_", "TOPSIGNAL_")) or name in {
            "DATABASE_URL", "MIGRATION_DATABASE_URL", "CREDENTIALS_ENCRYPTION_KEY"
        }:
            os.environ.pop(name, None)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", DATABASE_URL="sqlite+pysqlite:///:memory:",
                      TOPSIGNAL_DB_SCHEMA_INIT="skip", TOPSIGNAL_LIVE_EXECUTION_ENABLED="false",
                      TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false", TOPSIGNAL_BOT_WORKER_ENABLED="false")
    if keep_cache:
        os.environ["TOPSIGNAL_DATABENTO_CACHE_DIR"] = keep_cache
    def reject_network(event, args):
        if event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple):
                try:
                    if ipaddress.ip_address(address[0]).is_loopback:
                        return
                except ValueError:
                    pass
            raise RuntimeError("Offline research cannot connect to external services.")
    sys.addaudithook(reject_network)


def digest(payload) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def verify_protocol(protocol: dict) -> None:
    if digest(protocol) != REGISTERED_PROTOCOL_SHA256:
        raise ValueError("Protocol differs from registered v2. Version the implementation and register a new experiment before changing assumptions.")


def read_candles(path: Path, contract: str, owner_hash: str | None):
    from app.services.probabilistic_strategy import Candle
    from app.services.probabilistic_shadow import scope_hash
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")  # One consistent read snapshot if the local app is writing concurrently.
        owners = [row[0] for row in connection.execute(
            "SELECT DISTINCT user_id FROM projectx_market_candles WHERE contract_id=? AND live=0 AND unit='minute' AND unit_number=5", (contract,))]
        if owner_hash:
            owners = [owner for owner in owners if scope_hash(owner) == owner_hash]
        if len(owners) != 1:
            raise ValueError("Choose exactly one owner with --owner-hash (full SHA-256); cross-owner pooling is forbidden.")
        owner = owners[0]
        raw = connection.execute("""SELECT user_id,contract_id,symbol,live,unit,unit_number,candle_timestamp,
            open_price,high_price,low_price,close_price,volume,is_partial,source FROM projectx_market_candles
            WHERE user_id=? AND contract_id=? AND live=0 AND unit='minute' AND unit_number=5 ORDER BY candle_timestamp""",
                                 (owner, contract)).fetchall()
        candles = []
        canonical = []
        for row in raw:
            data = dict(row)
            data["candle_timestamp"] = datetime.fromisoformat(data["candle_timestamp"])
            candles.append(Candle.from_row(SimpleNamespace(**data)))
            canonical.append({key: str(value) for key, value in dict(row).items() if key != "user_id"})
        observation_audit = []
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_observations'").fetchone()
        if exists:
            for row in connection.execute("""SELECT source,event_type,count(*) AS records,
                min(provider_timestamp) AS first_provider,max(provider_timestamp) AS last_provider,
                sum(CASE WHEN side IS NOT NULL THEN 1 ELSE 0 END) AS explicit_side_records,
                sum(CASE WHEN bid IS NOT NULL AND ask IS NOT NULL THEN 1 ELSE 0 END) AS two_sided_quotes
                FROM market_observations WHERE user_id=? AND contract_id=? GROUP BY source,event_type""", (owner, contract)):
                observation_audit.append(dict(row))
        return candles, owner, digest(canonical), observation_audit
    finally:
        connection.close()


def incumbent_replay(rows, start, end, eligible_entry_days):
    """Protocol v2 benchmark: flat, i.e. no position and $0 net on every session.

    The EMA/VWAP incumbent used by protocol v1 was removed from TopSignal.
    """
    daily = {day: 0.0 for day in sorted(eligible_entry_days)}
    return {"revision": "flat_no_trade", "metrics": None, "trade_count": 0,
            "daily_net_usd": daily, "notes": [], "probability_scores": None,
            "cost_basis": "No trades, so no fees or slippage.",
            "limitation": "Doing nothing is the benchmark; a candidate must show a positive paired daily lower bound against $0."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--contract", default="CON.F.US.MNQ.U26")
    parser.add_argument("--owner-hash")
    parser.add_argument("--mode", choices=("audit", "diagnostic", "development"), default="audit")
    parser.add_argument("--summary", type=Path, help="Optional bounded aggregate report; contains no candles or scenario arrays")
    args = parser.parse_args()
    offline_guard()
    import numpy as np
    from app.services.probabilistic_shadow import cache_root, scope_hash
    from app.services.probabilistic_validation import (
        audit_candles, walk_forward_plan, development, day_start, paired_daily_bound, acceptance_checks,
    )
    protocol_path = ROOT / "docs/topbot-probabilistic-protocol-v2.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    verify_protocol(protocol)
    rows, owner, source_hash, observation_audit = read_candles(args.sqlite, args.contract, args.owner_hash)
    # Hash all application Python sources so an indirect dependency cannot silently
    # reuse an older experiment identity. Never include operator env files.
    sources = sorted((ROOT / "backend/app").rglob("*.py"))
    sources.extend((Path(__file__), ROOT / "backend/requirements.txt"))
    provenance = {"source_sha256": source_hash, "owner_hash": scope_hash(owner), "contract_id": args.contract,
                  "protocol_sha256": digest(protocol), "mode": args.mode, "python": platform.python_version(), "numpy": np.__version__,
                  "implementation_sha256": digest({p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in sources}),
                  "source_kind": "existing_local_sqlite_read_only", "data_live": False}
    if args.mode == "audit":
        audit = audit_candles(rows)
        report, models = {"data_audit": audit, "plan": walk_forward_plan(audit["eligible_complete_sessions"], protocol),
                          "status": "audit_only", "promotion_eligible": False}, {}
    else:
        report, models = development(rows, protocol, diagnostic=args.mode == "diagnostic")
        for fold in report["folds"]:
            days = fold["sessions"]["validation"]
            from datetime import timedelta
            start, end = day_start(days[0]), day_start(days[-1]) + timedelta(days=1)
            incumbent = incumbent_replay(rows, start, end, set(days))
            fold["incumbent"] = incumbent
            baseline = fold["models"].get("empirical_pool_v1")
            for result in fold["models"].values():
                result["paired_incumbent_lower_usd_per_day"] = paired_daily_bound(result["performance"], incumbent["daily_net_usd"], days)
                result["acceptance_checks"] = acceptance_checks(result, baseline)
    report.update(provenance=provenance, observations=observation_audit,
                  operational_effects={"orders": 0, "runs_started": 0, "database_writes": 0,
                                       "historical_restores": 0, "default_changed": False})
    run_id = digest(provenance)
    folder = cache_root() / "probabilistic-v1/experiments" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    full = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    destination = folder / "report.json"
    if destination.exists() and destination.read_text(encoding="utf-8") != full:
        raise ValueError("Existing experiment differs despite the same provenance; refusing to overwrite.")
    destination.write_text(full, encoding="utf-8")
    for version, model in models.items():
        artifact = {**provenance, "model": model.to_dict(), "validation_status": "unvalidated"}
        # Experiment artifacts are never automatically installed in the active shadow lookup.
        (folder / f"{version}.json").write_text(json.dumps(artifact, sort_keys=True, allow_nan=False), encoding="utf-8")
    summary = json.loads(full)
    for fold in summary.get("folds", []):
        for result in fold["models"].values():
            result.pop("ledger", None)
            result["performance"].pop("daily", None)
            for stress in result["stress"].values():
                stress.pop("daily", None)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "promotion_eligible": False,
                      "complete_sessions": len(report["data_audit"]["eligible_complete_sessions"]),
                      "report": str(destination), "experiment": run_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
