"""Read-only final accounting/source audit and export of the fixed legacy fee pairs."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ("baseline", "bracket_only", "trend_alignment", "no_chase")
PERIODS = ("selection", "diagnostic", "full")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def close(left, right, tolerance=0.01):
    return math.isfinite(float(left)) and math.isfinite(float(right)) and abs(float(left)-float(right)) <= tolerance


def trade_core(trade):
    return {key: value for key, value in trade.items() if key not in {"commission", "net_pnl"}}


def execution_key(trade):
    return tuple(trade[key] for key in ("side", "entry_timestamp", "exit_timestamp", "entry_price", "exit_price", "quantity", "exit_reason"))


def accounting(replay, trades, fee, slippage):
    errors = []
    metrics, assumptions = replay["metrics"], replay["assumptions"]
    if assumptions["commission_per_contract"] != fee or assumptions["slippage_ticks"] != slippage:
        errors.append("case_cost_assumptions_mismatch")
    if len(trades) != metrics["trade_count"]:
        errors.append("trade_count_mismatch")
    previous_exit = None
    for index, trade in enumerate(trades):
        qty, commission = float(trade["quantity"]), float(trade["commission"])
        if qty != 1.0 or not close(commission, 2*fee*qty, 1e-8):
            errors.append(f"trade_{index}_quantity_or_commission")
        if not close(trade["net_pnl"], float(trade["gross_pnl"])-commission, 1e-8):
            errors.append(f"trade_{index}_net_arithmetic")
        direction = 1 if trade["side"] == "long" else -1 if trade["side"] == "short" else 0
        price_pnl = direction * qty * (float(trade["exit_price"])-float(trade["entry_price"])) * 2
        if not direction or not close(trade["gross_pnl"], price_pnl, 1e-8):
            errors.append(f"trade_{index}_gross_price_arithmetic")
        entry, exit_ = (datetime.fromisoformat(trade[key].replace("Z", "+00:00")) for key in ("entry_timestamp", "exit_timestamp"))
        if entry > exit_ or (previous_exit is not None and entry < previous_exit):
            errors.append(f"trade_{index}_position_chronology")
        previous_exit = exit_
    for ledger_key, metric_key in (("gross_pnl", "gross_pnl"), ("net_pnl", "net_pnl"), ("commission", "total_commission")):
        total = math.fsum(float(trade[ledger_key]) for trade in trades)
        if not close(total, metrics[metric_key]):
            errors.append(f"ledger_{metric_key}_total")
        for grouped_key in ("daily_results", "monthly_results"):
            if not close(math.fsum(float(row[ledger_key]) for row in replay[grouped_key]), total):
                errors.append(f"{grouped_key}_{ledger_key}_total")
    for side in ("long", "short"):
        subset = [trade for trade in trades if trade["side"] == side]
        if len(subset) != metrics[side]["trade_count"] or not close(math.fsum(trade["net_pnl"] for trade in subset), metrics[side]["net_pnl"]):
            errors.append(f"{side}_metrics_total")
    if replay["equity_curve"] and not close(replay["equity_curve"][-1]["equity"], 50000+metrics["net_pnl"]):
        errors.append("final_equity_reconciliation")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "backend/storage/research/legacy-fee-audits")
    args = parser.parse_args(argv)
    directory = args.prepared_dir.resolve()
    if not all((directory / f"worker-{period}-finished.json").is_file() for period in PERIODS):
        parser.error("All three period completion files are required; no partial export was created")
    manifest = read(directory / "manifest.json")
    errors, inputs, rows, data = [], {}, [], {}
    if fingerprint(manifest["source_files"]) != manifest["source_bundle_sha256"]:
        errors.append("source_bundle_manifest_hash")
    for name, saved in manifest["source_files"].items():
        path = (directory / "sources" / name).resolve()
        if not path.is_relative_to(directory / "sources") or not path.is_file() or sha(path) != saved["sha256"]:
            errors.append(f"frozen_source:{name}")
    for name, saved in manifest["cache"]["pinned_files"].items():
        path = Path(name)
        if not path.is_file() or sha(path) != saved["sha256"]:
            errors.append(f"cache_input:{name}")
    manifest_hash = sha(directory / "manifest.json")
    for period in PERIODS:
        start = read(directory / f"worker-{period}-started.json")
        finish = read(directory / f"worker-{period}-finished.json")
        if start["manifest_sha256"] != manifest_hash or finish["completed_cases"] != 16:
            errors.append(f"worker_manifest_or_completion:{period}")
    for case in manifest["cases"]:
        folder = directory / "cases" / case["key"]
        local_errors = []
        if not (folder / "completed.json").is_file():
            errors.append(f"case_not_completed:{case['key']}")
            continue
        completed = read(folder / "completed.json")
        if read(folder / "started.json")["case"] != case:
            local_errors.append("started_case_identity")
        if completed["case"] != case or completed["status"] != "completed":
            local_errors.append("completed_case_identity")
        for name, expected in completed["artifact_sha256"].items():
            path = folder / name
            if not path.is_file() or sha(path) != expected:
                local_errors.append(f"saved_artifact_hash:{name}")
        comparison, replay, trades = (read(folder / name) for name in ("comparison.json", "replay.json", "trades.json"))
        original = comparison["results"][case["variant"]]
        ledger_hash = fingerprint(trades)
        if trades != replay["trades"] or ledger_hash != original["trades_sha256"] or ledger_hash != completed["trades_sha256"]:
            local_errors.append("complete_trade_ledger_hash")
        if original["metrics"] != replay["metrics"] or original["assumptions"] != replay["assumptions"] or original["config_snapshot"] != replay["config_snapshot"]:
            local_errors.append("comparison_full_result_difference")
        if comparison["commission_per_side"] != case["commission_per_side"] or comparison["slippage_ticks"] != case["slippage_ticks"]:
            local_errors.append("comparison_cost_difference")
        if comparison["source_fingerprint"] != manifest["cache"]["manifest"]["source_fingerprint"]:
            local_errors.append("case_data_fingerprint")
        local_errors += accounting(replay, trades, case["commission_per_side"], case["slippage_ticks"])
        errors.extend(f"{case['key']}:{error}" for error in local_errors)
        metrics = replay["metrics"]
        rows.append({**case, **{key: metrics[key] for key in ("trade_count", "net_pnl", "gross_pnl", "total_commission", "profit_factor", "expectancy", "max_drawdown_dollars")},
            "long_net_pnl": metrics["long"]["net_pnl"], "short_net_pnl": metrics["short"]["net_pnl"],
            "trades_sha256": ledger_hash, "accounting_errors": len(local_errors), "seconds": completed["seconds"]})
        inputs[case["key"]] = {name: sha(folder / name) for name in ("started.json", "completed.json", "comparison.json", "replay.json", "trades.json", "stdout.log")}
        data[(case["variant"], case["period"], case["slippage_ticks"], case["commission_per_side"])] = (comparison, replay, trades)
    pairs = []
    for period in PERIODS:
        for variant in VARIANTS:
            for slip in (1, 2):
                if (variant, period, slip, 1.2) not in data or (variant, period, slip, 0.61) not in data:
                    continue
                old, new = (data[(variant, period, slip, fee)] for fee in (1.2, 0.61))
                differing = [key for key in ("period", "requested_start", "requested_end", "baseline_source", "baseline_source_sha256", "candidates", "source_fingerprint", "slippage_ticks") if old[0][key] != new[0][key]]
                differing += [key for key in ("config_snapshot", "range") if old[1][key] != new[1][key]]
                old_assumptions = {key: value for key, value in old[1]["assumptions"].items() if key != "commission_per_contract"}
                new_assumptions = {key: value for key, value in new[1]["assumptions"].items() if key != "commission_per_contract"}
                if old_assumptions != new_assumptions:
                    differing.append("assumptions_beyond_fee")
                if differing:
                    errors.append(f"pair_controls:{variant}:{period}:{slip}:{differing}")
                om, nm = old[1]["metrics"], new[1]["metrics"]
                old_keys, new_keys = (Counter(execution_key(trade) for trade in item[2]) for item in (old, new))
                pair = {"variant": variant, "period": period, "slippage_ticks": slip, "nonfee_controls_identical": not differing,
                    "old_trade_count": om["trade_count"], "new_trade_count": nm["trade_count"],
                    "trade_count_change": nm["trade_count"]-om["trade_count"],
                    "old_net_pnl": om["net_pnl"], "new_net_pnl": nm["net_pnl"], "net_change": nm["net_pnl"]-om["net_pnl"],
                    "old_profit_factor": om["profit_factor"], "new_profit_factor": nm["profit_factor"],
                    "old_drawdown": om["max_drawdown_dollars"], "new_drawdown": nm["max_drawdown_dollars"],
                    "gross_change": nm["gross_pnl"]-om["gross_pnl"], "total_fee_reduction": om["total_commission"]-nm["total_commission"],
                    "core_ledger_identical": fingerprint([trade_core(t) for t in old[2]]) == fingerprint([trade_core(t) for t in new[2]]),
                    "execution_rows_removed": sum((old_keys-new_keys).values()), "execution_rows_added": sum((new_keys-old_keys).values())}
                if not close(pair["net_change"], pair["gross_change"]+pair["total_fee_reduction"]):
                    errors.append(f"pair_net_delta:{variant}:{period}:{slip}")
                pairs.append(pair)
    historical = []
    for path in sorted((ROOT / "backend/storage/databento").glob("topbot-*-comparison.json")):
        old = read(path)
        for variant, result in old.get("results", {}).items():
            identity = (variant, old["period"], int(old["slippage_ticks"]), float(old["commission_per_side"]))
            if identity not in data:
                continue
            control = data[identity]
            old_metrics, now = result["metrics"], control[1]["metrics"]
            historical.append({"file": path.name, "original_file_sha256": sha(path), "variant": variant,
                "period": old["period"], "slippage_ticks": old["slippage_ticks"],
                "original_source_fingerprint": old["source_fingerprint"], "current_source_fingerprint": control[0]["source_fingerprint"],
                "original_net_pnl": old_metrics["net_pnl"], "control_net_pnl": now["net_pnl"],
                "net_pnl_matches": close(old_metrics["net_pnl"], now["net_pnl"]),
                "original_trades": old_metrics["trade_count"], "control_trades": now["trade_count"],
                "all_metrics_exact": old_metrics == now, "trade_ledger_hash_exact": result["trades_sha256"] == fingerprint(control[2])})
    published_replays = []
    for name, variant in (("topbot-v4-replay-report.json", "baseline"), ("topbot-v5-replay-report.json", "bracket_only")):
        path = ROOT / "backend/storage/databento" / name
        if not path.is_file() or (variant, "full", 1, 1.2) not in data:
            continue
        original = read(path)
        current = data[(variant, "full", 1, 1.2)][1]
        published_replays.append({"file": name, "original_file_sha256": sha(path), "variant": variant,
            "original_source_fingerprint": original["source_fingerprint"],
            "original_net_pnl": original["metrics"]["net_pnl"], "control_net_pnl": current["metrics"]["net_pnl"],
            "original_trade_count": original["metrics"]["trade_count"], "control_trade_count": current["metrics"]["trade_count"],
            "all_metrics_exact": original["metrics"] == current["metrics"],
            "full_ledger_available_in_original_report": isinstance(original["trades"], list)})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = args.output_root.resolve() / f"{stamp}-legacy-fee-audit-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    (output / "trades").mkdir()
    for identity, (_, _, trades) in data.items():
        variant, period, slip, fee = identity
        write_csv(output / "trades" / f"{variant}__{period}__slip-{slip}__fee-{fee:.2f}.csv", trades)
    report = {"prepared_directory": str(directory), "manifest_sha256": manifest_hash,
        "source_bundle_sha256": manifest["source_bundle_sha256"], "source_fingerprint": manifest["cache"]["manifest"]["source_fingerprint"],
        "source_files_checked": len(manifest["source_files"]), "cache_files_checked": len(manifest["cache"]["pinned_files"]),
        "read_only_input_audit": True, "quarantine_accessed": False, "strategy_change_selected": False,
        "case_count": len(rows), "pair_count": len(pairs), "errors": errors, "case_inputs": inputs,
        "cases": rows, "pairs": pairs, "original_control_comparisons": historical,
        "published_replay_controls": published_replays,
        "historical_controls_all_metrics_exact": all(item["all_metrics_exact"] for item in historical),
        "historical_controls_all_trade_hashes_exact": all(item["trade_ledger_hash_exact"] for item in historical),
        "export_script_sha256": sha(Path(__file__))}
    write(output / "audit.json", report)
    write_csv(output / "cases.csv", rows)
    write_csv(output / "pairs.csv", pairs)
    write_csv(output / "historical-controls.csv", historical)
    write_csv(output / "published-replay-controls.csv", published_replays)
    lines = ["# Fixed legacy fee-only paired results", "", f"Cases: {len(rows)}; pairs: {len(pairs)}; audit errors: {len(errors)}.",
        "", "All dates are reused. These results use the frozen application-style five-minute comparison path, not the separate observed-minute research engine.", ""]
    for period in PERIODS:
        lines += [f"## {period.title()}", "", "| Variant | Slip | Net at 1.20 | Net at 0.61 | PF at 0.61 | DD at 0.61 | Trades old → new | Core ledger same |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
        for pair in (item for item in pairs if item["period"] == period):
            pf = "n/a" if pair["new_profit_factor"] is None else f"{pair['new_profit_factor']:.4f}"
            lines.append(f"| {pair['variant']} | {pair['slippage_ticks']} | {pair['old_net_pnl']:,.2f} | {pair['new_net_pnl']:,.2f} | {pf} | {pair['new_drawdown']:,.2f} | {pair['old_trade_count']} → {pair['new_trade_count']} | {pair['core_ledger_identical']} |")
        lines.append("")
    lines += ["## Control and accounting checks", "", f"Checked {len(historical)} original saved higher-cost comparisons; exact metric matches: {sum(item['all_metrics_exact'] for item in historical)}, exact ledger hashes: {sum(item['trade_ledger_hash_exact'] for item in historical)}.",
        "", "Full trade CSVs retain every field. JSON inputs were not edited. Per-trade gross/price arithmetic, fees, net arithmetic, chronology, totals, side breakdowns, daily/monthly totals and final equity were checked. Pair source/rules/data/config/range/nonfee assumptions were compared. Reported drawdown remains the engine's measure, not an independently recomputed tick-level statistic.", ""]
    with (output / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    print(json.dumps({"output_directory": str(output), "cases": len(rows), "pairs": len(pairs), "errors": errors,
        "historical_control_count": len(historical), "historical_metrics_exact": report["historical_controls_all_metrics_exact"],
        "historical_trade_hashes_exact": report["historical_controls_all_trade_hashes_exact"]}, indent=2))
    return int(bool(errors) or len(rows) != 48 or len(pairs) != 24)


if __name__ == "__main__":
    raise SystemExit(main())
