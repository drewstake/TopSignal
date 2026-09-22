"""Bounded MNQ collection, deterministic replay, and incremental-value research.

See docs/topbot-depth-research.md. Only probe/collect use the network, and only
through the market-data allowlist. No application server/worker is started.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def runtime(*, network: bool, env_file: Path | None):
    if network and env_file:
        from dotenv import dotenv_values
        # Copy only market credentials/URLs, never DB, execution, worker or hub messages.
        allowed = {"PROJECTX_API_BASE_URL", "PROJECTX_BASE_URL", "PROJECTX_GATEWAY_URL", "PROJECTX_USERNAME",
                   "PROJECTX_USER_NAME", "PROJECTX_API_KEY", "PROJECTX_MARKET_HUB_URL", "TOPSTEP_API_BASE_URL",
                   "TOPSTEPX_API_BASE_URL", "TOPSTEP_USERNAME", "TOPSTEPX_USERNAME", "TOPSTEP_API_KEY", "TOPSTEPX_API_KEY", "PX_API_KEY"}
        for name, value in dotenv_values(env_file).items():
            if name in allowed and value:
                os.environ.setdefault(name, value)
    if not network:
        from research_probabilistic_topbot import offline_guard
        offline_guard()
    os.environ.update(PYTHON_DOTENV_DISABLED="1", DATABASE_URL="sqlite+pysqlite:///:memory:",
                      TOPSIGNAL_DB_SCHEMA_INIT="skip", TOPSIGNAL_BOT_WORKER_ENABLED="false",
                      TOPSIGNAL_LIVE_EXECUTION_ENABLED="false", TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false")


def replay(path: Path, *, delay_ms: int = 0, drop_every: int = 0, levels: int = 5):
    from app.services.depth_capture import replay_events
    from app.services.depth_research import ResearchBook
    from dataclasses import replace
    from app.services.probabilistic_strategy import Candle, features as candle_features, BAR
    book = None
    candles = {}
    next_sample = None
    samples = 0
    for event in replay_events(path):
        if delay_ms:
            event = replace(event, received_at=event.received_at + timedelta(milliseconds=delay_ms))
        if book is None or book.scope != (event.owner_hash, event.contract_id, event.data_live):
            book = ResearchBook(event.owner_hash, event.contract_id, event.data_live, levels=levels)
            candles = {}
            next_sample = event.received_at.replace(microsecond=0) + timedelta(seconds=1)
        while next_sample < event.received_at:
            samples += 1
            if samples > 30000:
                raise ValueError("Replay exceeds a bounded capture session.")
            status = book.status(next_sample)
            features = book.features(next_sample)
            usable = features is not None
            f = None
            try:
                rows = [candles[k] for k in sorted(candles)][-21:]
                if rows and 0 <= (next_sample - rows[-1].timestamp - BAR).total_seconds() <= 120:
                    cf = candle_features(rows, as_of=next_sample)
                    f = {"values": list(cf.values), "stop": cf.stop_points, "volatility": cf.volatility,
                         "close_at": cf.decision_at.isoformat()}
            except ValueError:
                pass
            yield {"at": next_sample.isoformat(), "owner_hash": event.owner_hash,
                   "contract_id": event.contract_id, "data_live": event.data_live, "synthetic": book.synthetic,
                   "status": status, "features": features, "candle_features": f,
                   "mid": (max(book.bids) + min(book.asks)) / 2 if usable else None,
                   "bid": max(book.bids) if usable else None, "ask": min(book.asks) if usable else None,
                   "buy_sweep": book.sweep("BUY", 1, next_sample) if usable else None,
                   "sell_sweep": book.sweep("SELL", 1, next_sample) if usable else None}
            next_sample += timedelta(seconds=1)
        if drop_every and event.index % drop_every == 0:
            continue  # Local index gap makes missingness explicit, never silently repairs it.
        book.apply(event)
        if event.kind == "candle":
            raw = event.payload
            try:
                row = Candle(__import__("app.services.depth_research", fromlist=["timestamp"]).timestamp(raw["timestamp"]),
                             float(raw["open"]), float(raw["high"]), float(raw["low"]), float(raw["close"]),
                             float(raw["volume"]), event.contract_id, event.owner_hash, event.data_live, bool(raw["is_partial"]))
                if row.valid() and row.timestamp + BAR <= event.received_at:
                    # Keep the first observed completed value; later corrections
                    # never rewrite information that was available at a decision.
                    candles.setdefault(row.timestamp, row)
                    for key in sorted(candles)[:-36]:
                        del candles[key]
            except (KeyError, TypeError, ValueError):
                pass


def make_fixture(root: Path):
    """Known complete MBP snapshots; deliberately not a ProjectX semantics claim."""
    from app.services.depth_capture import LocalCapture
    from app.services.depth_research import digest
    capture = LocalCapture(root=root, owner_hash=digest("synthetic-fixture"), synthetic=True)
    start = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    contract = "CON.F.US.MNQ.U26"
    for i in range(120):
        now = start + timedelta(seconds=i)
        if i == 0:
            payload = {"complete": True, "sequence": 1, "bids": [[20000 - .25 * k, 10 + k] for k in range(5)],
                       "asks": [[20000.25 + .25 * k, 8 + k] for k in range(5)]}
            kind = "snapshot"
        else:
            payload = {"type": 2, "price": 20000, "volume": 10 + i % 3, "sequence": i + 1}
            kind = "depth"
        capture.append(contract=contract, kind=kind, payload=payload, provider_at=now, received_at=now,
                       monotonic_ns=i * 1000000000, source="explicit_snapshot_v1")
    # End at fixture time, so historical replay never fills wall-clock years of gaps.
    capture.append(contract=contract, kind="disconnect", payload={}, received_at=start + timedelta(seconds=120),
                   monotonic_ns=120000000000, source="explicit_snapshot_v1")
    result = capture.finish("synthetic_correctness_fixture")
    return capture.path, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("probe", "collect", "fixture", "replay", "validate"))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--owner-hash")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--max-mib", type=int, default=64)
    parser.add_argument("--max-events", type=int, default=250000)
    parser.add_argument("--capture", type=Path, action="append", default=[])
    parser.add_argument("--sqlite", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    runtime(network=args.mode in {"probe", "collect"}, env_file=args.env_file)
    from app.services.depth_capture import LocalCapture, ReadOnlyMarketClient, atomic_json, check_storage
    from app.services.depth_research import canonical, digest
    from app.services.probabilistic_shadow import cache_root
    root = cache_root()
    if args.mode in {"probe", "collect"}:
        from app.services.depth_capture import collect
        client = ReadOnlyMarketClient.from_env()
        # Probe defaults to a separate credential scope and is never silently
        # associated with a TopSignal account. Collection for Dry Run is explicit.
        owner = args.owner_hash or digest("probe:" + client.username)
        capture = LocalCapture(root=root, owner_hash=owner, max_bytes=args.max_mib * 1024 ** 2, max_events=args.max_events)
        result = asyncio.run(collect(client, capture, seconds=min(args.seconds, 60) if args.mode == "probe" else args.seconds))
        output = {"capture_path": str(capture.path), "report": result}
    elif args.mode == "fixture":
        path, result = make_fixture(root)
        output = {"capture_path": str(path), "synthetic": True, "report": result}
    elif args.mode == "replay":
        if len(args.capture) != 1:
            parser.error("replay needs exactly one --capture directory")
        path = args.capture[0]
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        code = {str(p.relative_to(ROOT)): __import__("hashlib").sha256(p.read_bytes()).hexdigest() for p in
                [Path(__file__), ROOT / "backend/app/services/depth_research.py", ROOT / "backend/app/services/probabilistic_strategy.py"]}
        key = digest({"manifest": manifest, "replay_version": "mnq-depth-replay-v1", "code": code})
        destination = root / "depth-v1/derived" / key
        destination.mkdir(parents=True, exist_ok=True)
        rows = list(replay(path))
        content = b"".join(canonical(row) + b"\n" for row in rows)
        dataset = destination / "observations.ndjson"
        if dataset.exists() and dataset.read_bytes() != content:
            raise ValueError("Refusing to replace a different immutable replay.")
        if not dataset.exists():
            check_storage(root, len(content))
            with dataset.open("xb") as handle:
                handle.write(content)
        output = {"derived_path": str(dataset), "samples": len(rows), "usable_samples": sum(r["features"] is not None for r in rows),
                  "synthetic": manifest["synthetic"], "sha256": __import__("hashlib").sha256(content).hexdigest(),
                  "profitability_evidence": False}
    else:
        from app.services.depth_validation import run_experiment
        output = run_experiment(captures=args.capture, sqlite_path=args.sqlite, owner_hash=args.owner_hash, root=root)
    if args.summary:
        atomic_json(args.summary, output)
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
