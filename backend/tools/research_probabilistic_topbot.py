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
REGISTERED_PROTOCOL_SHA256 = "70cb218611e47b77470126a9c4dfc5cfb89b59fef7dead547aaf90427a8aaa96"


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
        raise ValueError("Protocol differs from registered v3. Version the implementation and register a new experiment before changing assumptions.")


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
    from research_probabilistic_v3 import main as run_v3
    return run_v3()


if __name__ == "__main__":
    raise SystemExit(main())
