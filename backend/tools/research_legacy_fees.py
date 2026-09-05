"""Prepare or execute frozen, paired fee-only checks of the four legacy variants.

Preparation never imports the replay engine or evaluates candles. Execution
delegates to a frozen copy of compare_topbot_variants.py, preserving its rules
and five-minute execution path while retaining every original engine result.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from time import perf_counter
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ("baseline", "bracket_only", "trend_alignment", "no_chase")
PERIODS = ("selection", "diagnostic", "full")
FEES = (1.20, 0.61)
SLIPPAGE = (1, 2)
FINGERPRINT = "e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a"
DRIVER = "backend/tools/research_legacy_fees.py"
COMPARISON = "backend/tools/compare_topbot_variants.py"
FIXTURE = "backend/tools/fixtures/topbot_v4.py"
DOC = "docs/topbot-legacy-fee-audit-2026-09-04.md"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False, default=str)
        stream.write("\n")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def cases():
    return [{"key": f"{variant}__{period}__slip-{slip}__fee-{fee:.2f}",
             "variant": variant, "period": period, "slippage_ticks": slip, "commission_per_side": fee}
            for period in PERIODS for variant in VARIANTS for slip in SLIPPAGE for fee in FEES]


def offline_environment():
    for name in tuple(os.environ):
        if name.startswith(("PROJECTX_", "TOPSTEP_", "TOPSTEPX_", "SUPABASE_", "DATABENTO_")):
            os.environ.pop(name, None)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_REQUIRED="false", TOPSIGNAL_BOT_WORKER_ENABLED="false", TOPSIGNAL_LIVE_EXECUTION_ENABLED="false",
        TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false", TOPSIGNAL_DB_SCHEMA_INIT="skip")


def disable_network():
    def denied(*args, **kwargs):
        raise RuntimeError("Network access is prohibited in the frozen legacy fee experiment")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied


def cache_snapshot(cache_root):
    root = Path(cache_root).resolve()
    manifest = read_json(root / "current.json")
    require(manifest["cache_format_version"] == 6 and manifest["source_fingerprint"] == FINGERPRINT,
            "Expected the verified format-6 Databento cache")
    version = (root / manifest["version_dir"]).resolve()
    require(version.is_relative_to(root), "Cache version escapes its root")
    entry = manifest["series"]["MNQ:5m"]
    series = (version / entry["path"]).resolve()
    require(series.is_relative_to(version), "Five-minute series escapes the cache version")
    files = [root / "current.json", version / "manifest.json", *sorted(series.glob("*.npy")), series / "metadata.json"]
    require(sum(path.suffix == ".npy" for path in files) == 10, "Expected all ten five-minute arrays")
    pinned = {str(path): {"bytes": path.stat().st_size, "sha256": digest(path)} for path in files}
    for archive in manifest["archives"]:
        path = Path(archive["path"]).resolve()
        require(path.stat().st_size == archive["size"] and digest(path) == archive["sha256"], "Source archive differs from cache manifest")
        pinned[str(path)] = {"bytes": archive["size"], "sha256": archive["sha256"]}
    return {"root": str(root), "manifest": manifest, "pinned_files": pinned}


def prepare(args):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,60}", args.label), "Invalid preparation label")
    cache = cache_snapshot(args.cache_dir)
    paths = sorted((ROOT / "backend/app").rglob("*.py"))
    paths += [ROOT / relative for relative in (DRIVER, COMPARISON, FIXTURE, DOC, "backend/tests/test_research_legacy_fees.py",
              "docs/topbot-fee-correction.md", "docs/topbot-improvement-comparison.md")]
    source_bytes = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in paths}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = Path(args.output_root).resolve() / f"{stamp}-{args.label}-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    for name, content in source_bytes.items():
        target = output / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(content)
    files = {name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
             for name, content in source_bytes.items()}
    require(all((ROOT / name).read_bytes() == content for name, content in source_bytes.items()),
            "Source changed during capture; preserved preparation is incomplete and must not run")
    require(all(digest(output / "sources" / name) == item["sha256"] for name, item in files.items()), "Source copy hash mismatch")
    old = cache["manifest"]["series"]["MNQ:1m"]
    git_revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    manifest = {"status": "prepared_not_launched", "created_at": datetime.now(timezone.utc).isoformat(),
        "objective": "Fee-only paired rerun of all four unchanged legacy comparison variants",
        "source_files": files, "source_bundle_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "git_revision": git_revision, "python": sys.version, "cache": cache, "cases": cases(),
        "periods": {"selection": {"start": "2020-01-01T00:00:00+00:00", "end": "2024-01-01T00:00:00+00:00"},
            "diagnostic": {"start": "2024-01-01T00:00:00+00:00", "end_ns": old["source_end_ns"]},
            "full": {"start_ns": old["first_timestamp_ns"], "end_ns": old["source_end_ns"]}},
        "fixed_parameters": {"variants": VARIANTS, "fees_per_contract_per_side": FEES, "slippage_ticks_each_side": SLIPPAGE,
            "starting_balance": 50000, "position_size": 1, "baseline_fixture": FIXTURE,
            "entry_exit_rule_changes": False, "new_data_allowed": False},
        "execution_model": "Unmodified frozen comparison tool and application-style five-minute replay; no separate minute execution stream",
        "capture_method": "Wrap BacktestEngine.run in memory to save and return its original unmodified result",
        "limitations": ["All dates were previously examined; these are retrospective diagnostics.",
            "The original rules are fixed, but current frozen engine/calendar v6 may differ from old published runs.",
            "Fee changes can alter risk gates and trades; results must be rerun rather than refunded arithmetically.",
            "These legacy aggregate-bar results do not replace corrected minute-execution research or independent confirmation."],
        "estimated_runtime": {"sequential_minutes": [15, 25], "three_period_workers_minutes": [8, 15], "measurement_status": "planning estimate, not benchmarked"}}
    write_new(output / "manifest.json", manifest)
    print(json.dumps({"prepared_directory": str(output), "manifest_sha256": digest(output / "manifest.json"),
        "source_bundle_sha256": manifest["source_bundle_sha256"], "cases": len(manifest["cases"]),
        "strategy_evaluated": False, "period_workers": list(PERIODS)}, indent=2))
    return 0


def validate_prepared(directory):
    directory = Path(directory).resolve()
    manifest = read_json(directory / "manifest.json")
    require(manifest["status"] == "prepared_not_launched" and manifest["cases"] == cases(), "Prepared matrix differs from the fixed plan")
    for name, item in manifest["source_files"].items():
        path = (directory / "sources" / name).resolve()
        require(path.is_relative_to(directory / "sources") and digest(path) == item["sha256"], "Frozen source hash mismatch")
    for name, item in manifest["cache"]["pinned_files"].items():
        path = Path(name)
        require(path.is_file() and path.stat().st_size == item["bytes"] and digest(path) == item["sha256"], "Pinned cache/archive changed")
    return manifest


def capture_case(comparison, replay, *, case, directory, cache_root, fixture):
    """Observe the tool's sole engine run without changing its arguments/result."""
    directory.mkdir(parents=True, exist_ok=False)
    write_new(directory / "started.json", {"case": case, "started_at": datetime.now(timezone.utc).isoformat()})
    original_run, original_argv = replay.BacktestEngine.run, sys.argv
    captured = []
    started = perf_counter()

    def retain_result(engine, *args, **kwargs):
        require(not captured, "Comparison unexpectedly ran more than one engine for a single variant")
        require(engine.settings.commission_per_contract == case["commission_per_side"]
                and engine.settings.slippage_ticks == case["slippage_ticks"], "Engine costs differ from fixed case")
        result = original_run(engine, *args, **kwargs)
        write_new(directory / "replay.json", result)
        write_new(directory / "trades.json", result["trades"])
        require(result["assumptions"]["commission_per_contract"] == case["commission_per_side"], "Result fee differs from case")
        require(result["assumptions"]["slippage_ticks"] == case["slippage_ticks"], "Result slippage differs from case")
        for trade in result["trades"]:
            quantity, commission = float(trade["quantity"]), float(trade["commission"])
            require(quantity == 1.0 and math.isfinite(commission)
                    and abs(commission - 2 * case["commission_per_side"] * quantity) <= 1e-8,
                    "Trade quantity or round-trip fee differs from fixed one-contract case")
            require(abs(float(trade["net_pnl"]) - (float(trade["gross_pnl"]) - commission)) <= 1e-8,
                    "Trade net/gross/fee arithmetic does not reconcile")
        captured.append(result)
        return result

    replay.BacktestEngine.run = retain_result
    sys.argv = [str(comparison.__file__), "--cache-dir", str(cache_root), "--baseline-source", str(fixture),
                "--period", case["period"], "--variants", case["variant"], "--slippage", str(case["slippage_ticks"]),
                "--commission-per-side", f"{case['commission_per_side']:.2f}", "--output", str(directory / "comparison.json")]
    try:
        with (directory / "stdout.log").open("x", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                comparison.main()
        require(len(captured) == 1, "Comparison did not produce its expected engine result")
        saved = read_json(directory / "comparison.json")
        original = saved["results"][case["variant"]]
        trade_hash = hashlib.sha256(json.dumps(captured[0]["trades"], sort_keys=True).encode()).hexdigest()
        require(trade_hash == original["trades_sha256"], "Captured ledger differs from comparison's ledger hash")
        result = {"status": "completed", "case": case, "seconds": perf_counter()-started,
            "trade_count": len(captured[0]["trades"]), "trades_sha256": trade_hash,
            "artifact_sha256": {name: digest(directory / name) for name in ("comparison.json", "replay.json", "trades.json", "stdout.log")}}
        write_new(directory / "completed.json", result)
        return result
    except Exception as exc:
        with (directory / "failure.log").open("x", encoding="utf-8") as log:
            log.write(traceback.format_exc())
        result = {"status": "failed", "case": case, "error_type": type(exc).__name__, "error_message": str(exc),
                  "failure_log_sha256": digest(directory / "failure.log"), "seconds": perf_counter()-started}
        write_new(directory / "failed.json", result)
        return result
    finally:
        replay.BacktestEngine.run = original_run
        sys.argv = original_argv


def execute(args):
    directory = Path(args.prepared_dir).resolve()
    require(Path(__file__).resolve() == directory / "sources" / DRIVER, "Execution must use the captured driver")
    manifest = validate_prepared(directory)
    offline_environment()
    disable_network()
    write_new(directory / f"worker-{args.period}-started.json", {"period": args.period,
        "manifest_sha256": digest(directory / "manifest.json"), "started_at": datetime.now(timezone.utc).isoformat()})
    backend = directory / "sources/backend"
    sys.path.insert(0, str(backend))
    from app.services import bot_backtesting as replay
    require(Path(replay.__file__).resolve().is_relative_to(backend), "Replay module is not from the frozen source")
    spec = importlib.util.spec_from_file_location("frozen_legacy_comparison", directory / "sources" / COMPARISON)
    comparison = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = comparison
    spec.loader.exec_module(comparison)
    require(tuple(comparison.CANDIDATES) == VARIANTS, "Frozen comparison variants differ from fixed plan")
    results = []
    for case in manifest["cases"]:
        if case["period"] != args.period:
            continue
        validate_prepared(directory)
        result = capture_case(comparison, replay, case=case, directory=directory / "cases" / case["key"],
            cache_root=manifest["cache"]["root"], fixture=directory / "sources" / FIXTURE)
        results.append(result)
        print(json.dumps(result), flush=True)
    validate_prepared(directory)
    write_new(directory / f"worker-{args.period}-finished.json", {"period": args.period,
        "finished_at": datetime.now(timezone.utc).isoformat(), "results": results,
        "completed_cases": sum(result["status"] == "completed" for result in results)})
    return int(any(result["status"] != "completed" for result in results))


def launch(args):
    directory = Path(args.prepared_dir).resolve()
    validate_prepared(directory)
    offline_environment()
    command = [sys.executable, str(directory / "sources" / DRIVER), "_execute", "--prepared-dir", str(directory), "--period", args.period]
    return subprocess.run(command, cwd=directory, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    prep = sub.add_parser("prepare", help="freeze the complete fixed matrix without evaluating market data")
    prep.add_argument("--cache-dir", type=Path, default=ROOT / "backend/storage/databento-calendar-v6")
    prep.add_argument("--output-root", type=Path, default=ROOT / "backend/storage/research/legacy-fee-pairs")
    prep.add_argument("--label", default="legacy-fee-only")
    for mode in ("run", "_execute"):
        run = sub.add_parser(mode)
        run.add_argument("--prepared-dir", required=True, type=Path)
        run.add_argument("--period", required=True, choices=PERIODS)
    args = parser.parse_args(argv)
    return {"prepare": prepare, "run": launch, "_execute": execute}[args.mode](args)


if __name__ == "__main__":
    raise SystemExit(main())
