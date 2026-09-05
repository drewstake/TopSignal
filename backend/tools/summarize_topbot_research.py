"""Export immutable MNQ experiment evidence into Markdown and complete CSVs.

Input run directories are read only. A unique output directory contains every
available trade ledger, a case-level metrics CSV and a complete research report.
Missing artifacts stay explicitly incomplete; a file alone never proves that a
research process remains live. This exporter does not rerun or select strategies.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from tools.research_topbot import (
    REUSED_HISTORY, create_run_directory, fingerprint, historical_screen,
    write_new_json,
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv_new(path, rows, *, fields=None):
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def audit_ledger(summary, trades, sessions, commission_per_side):
    problems = []
    if fingerprint(trades) != summary["trades_sha256"]:
        problems.append("trade ledger SHA256 differs from immutable summary")
    if sessions is not None and fingerprint(sessions) != summary["sessions_sha256"]:
        problems.append("session ledger SHA256 differs from immutable summary")
    previous_exit = None
    for index, trade in enumerate(trades, 1):
        quantity = float(trade["quantity"])
        commission = float(trade["commission"])
        if quantity != 1:
            problems.append(f"trade {index}: quantity differs from one-contract protocol")
        if abs(commission - 2 * commission_per_side * quantity) > 1e-6:
            problems.append(f"trade {index}: round-trip commission does not reconcile")
        if abs(float(trade["net_pnl"]) - (float(trade["gross_pnl"]) - commission)) > 1e-6:
            problems.append(f"trade {index}: net/gross/commission do not reconcile")
        entry = datetime.fromisoformat(trade["entry_timestamp"])
        exit_time = datetime.fromisoformat(trade["exit_timestamp"])
        if exit_time < entry or previous_exit is not None and entry < previous_exit:
            problems.append(f"trade {index}: chronological position intervals overlap or reverse")
        previous_exit = exit_time
    ledger_net = sum(float(row["net_pnl"]) for row in trades)
    metrics = summary["metrics"]
    if len(trades) != metrics["trade_count"] or abs(ledger_net - metrics["net_pnl"]) > 1e-5:
        problems.append("ledger count/net differs from summary metrics")
    if sessions is not None and abs(sum(row["net_pnl"] for row in sessions) - ledger_net) > 1e-5:
        problems.append("exact session-marked net differs from closed ledger net")
    return {
        "status": "pass" if not problems else "fail", "problems": problems,
        "trade_count": len(trades), "ledger_net_pnl": ledger_net,
        "session_count": len(sessions) if sessions is not None else None,
        "limitations": "Accounting and artifact consistency checks; not independent verification of market data, signal causality, fill realism or future profitability.",
    }


def load_runs(directories):
    runs, cases = [], []
    if len(set(path.resolve() for path in directories)) != len(directories):
        raise ValueError("duplicate run directories are not allowed")
    for run_number, directory in enumerate(sorted(directories), 1):
        directory = directory.resolve()
        manifest = read_json(directory / "manifest.json")
        interruption = read_json(directory / "interruption.json") if (directory / "interruption.json").exists() else None
        run = {"label": f"R{run_number:02d}", "directory": str(directory), "manifest": manifest,
               "manifest_sha256": fingerprint(manifest), "final_results_available": (directory / "results.json").exists(),
               "interruption": interruption}
        runs.append(run)
        for variant, definition in manifest["hypotheses"].items():
            for period, window in manifest["periods"].items():
                for slip in manifest["costs"]["slippage_ticks_each_side"]:
                    key = f"{variant}__{period}__slip-{slip:g}"
                    case = {"run": run["label"], "run_id": directory.name, "key": key,
                            "variant": variant, "definition": definition, "period": period,
                            "window": window, "slippage_ticks": slip,
                            "commission_per_side": manifest["costs"]["commission_per_side"],
                            "code_sha256": manifest["code"]["combined_sha256"],
                            "protocol_sha256": manifest["protocol_sha256"],
                            "source_fingerprint": manifest["cache_manifest"]["source_fingerprint"],
                            "execution_minutes": manifest["execution_minutes"], "directory": str(directory),
                            "entry_delay_minutes": manifest.get("entry_delay_minutes", 0),
                            "selection_eligible": interruption is None, "interruption": interruption}
                    summary_path = directory / f"{key}.summary.json"
                    failure_path = directory / f"{key}.failure.json"
                    if summary_path.exists():
                        case["summary"] = read_json(summary_path)
                        trades_path = directory / f"{key}.trades.json"
                        sessions_path = directory / f"{key}.sessions.json"
                        if not trades_path.exists():
                            case.update(status="incomplete", error="summary exists but complete trade ledger is missing")
                        else:
                            case["trades"] = read_json(trades_path)
                            case["sessions"] = read_json(sessions_path) if sessions_path.exists() else None
                            case["audit"] = audit_ledger(case["summary"], case["trades"], case["sessions"], case["commission_per_side"])
                            case["status"] = "completed" if case["audit"]["status"] == "pass" else "audit_failed"
                            if case["sessions"] is None:
                                case.update(status="incomplete", error="complete session-mark ledger is missing")
                    elif failure_path.exists():
                        case.update(status="failed", failure=read_json(failure_path))
                    elif (directory / f"{key}.started.json").exists():
                        case.update(status="incomplete", error=(
                            "started replay interrupted: " + interruption["reason"] if interruption else
                            "start recorded without final result; current process state is unknown"
                        ))
                    else:
                        case.update(status="incomplete", error="not run before recorded interruption" if interruption else "no result artifact available")
                    cases.append(case)
    return runs, cases


def repeated_configurations(cases):
    grouped = defaultdict(list)
    for case in cases:
        identity = fingerprint({key: case[key] for key in (
            "variant", "definition", "period", "window", "slippage_ticks",
            "commission_per_side", "source_fingerprint", "execution_minutes",
            "entry_delay_minutes",
        )})
        grouped[identity].append(case)
    repeats = []
    for identity, group in grouped.items():
        if len(group) < 2:
            continue
        hashes = {case["summary"]["trades_sha256"] for case in group if case["status"] == "completed"}
        repeats.append({
            "configuration_sha256": identity, "variant": group[0]["variant"],
            "period": group[0]["period"], "slippage_ticks": group[0]["slippage_ticks"],
            "entry_delay_minutes": group[0]["entry_delay_minutes"],
            "runs": [case["run"] for case in group],
            "completed_instances": sum(case["status"] == "completed" for case in group),
            "completed_ledger_hashes_agree": len(hashes) == 1 if hashes else None,
            "code_sha256_values": sorted({case["code_sha256"] for case in group}),
            "note": "Same declared candidate, data, window and costs; source identities may differ and are retained. Repeated replay is not another independent strategy test.",
        })
    return repeats


def number(value, digits=2):
    return "—" if value is None else f"{float(value):,.{digits}f}"


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers, rows):
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(value) for value in row) + " |" for row in rows),
    ])


def markdown_report(runs, cases, repeats):
    counts = Counter(case["status"] for case in cases)
    incomplete = counts["incomplete"] > 0 or any(not run["final_results_available"] for run in runs)
    lines = ["# MNQ TopBot research evidence", "",
             f"Generated {datetime.now(timezone.utc).isoformat()}. **{'INCOMPLETE SNAPSHOT' if incomplete else 'ALL PLANNED CASES HAVE RESULT ARTIFACTS'}**.", "",
             f"{len(runs)} input runs; {len(cases)} planned replay cases; {counts['completed']} completed and accounting-checked; {counts['failed']} execution failures; {counts['audit_failed']} artifact/accounting failures; {counts['incomplete']} incomplete.", "",
             f"{sum(not case['selection_eligible'] for case in cases)} cases belong to explicitly interrupted runs and are ineligible for selection, including any completed results. No eligibility calculation combines incompatible source bundles, data fingerprints, protocols or costs.", "",
             REUSED_HISTORY, "", "No live orders were authorized or placed by these offline tools. Parameter-neighbor testing and a frozen candidate on genuinely untouched data are separate unmet requirements unless separately documented.", "",
             "## Provenance and pre-test records", ""]
    for run in runs:
        manifest = run["manifest"]
        manifest_path = (Path(run["directory"]) / "manifest.json").as_posix()
        lines += [f"### {run['label']} — {manifest['run_id']}", "",
                  f"[Immutable hypothesis manifest](<{manifest_path}>). Created {manifest['created_at']}. Final run index present: {run['final_results_available']}.", "",
                  f"Git revision `{manifest['code']['git_revision']}`; source bundle `{manifest['code']['combined_sha256']}`; data `{manifest['cache_manifest']['source_fingerprint']}`; protocol `{manifest['protocol_sha256']}`.", "",
                  f"Execution: {manifest.get('execution_model', manifest['execution_minutes'])}. Additional entry delay: {manifest.get('entry_delay_minutes', 0)} minute(s). Roll: {manifest.get('roll_execution', 'see engine assumptions')}. Costs: ${manifest['costs']['commission_per_side']:.2f}/contract/side; slippage grid {manifest['costs']['slippage_ticks_each_side']} ticks each side. Starting balance ${manifest['starting_balance']:,.2f}; one contract.", "",
                  f"Calendar risk: {manifest.get('calendar_risk_hook', 'Not included in this earlier runner; see fixture rules and source snapshot.')}", ""]
        if run["interruption"]:
            lines += ["**SUPERSEDED / INELIGIBLE FOR SELECTION:** " + run["interruption"]["reason"], ""]
    lines += ["## Complete case inventory", "", table(
        ["Run", "Variant", "Period", "Slip", "Delay min", "Artifact status", "Selection", "Trades", "Net $", "PF", "Expectancy $", "Max DD $", "Exposure %"],
        [[case["run"], case["variant"], case["period"], number(case["slippage_ticks"], 0), case["entry_delay_minutes"], case["status"], "excluded" if not case["selection_eligible"] else "screen pending",
          case.get("summary", {}).get("metrics", {}).get("trade_count", "—"),
          *[number(case.get("summary", {}).get("metrics", {}).get(key)) for key in
            ("net_pnl", "profit_factor", "expectancy", "max_drawdown_dollars", "exposure_percent")]] for case in cases]), ""]
    errors = [case for case in cases if case["status"] != "completed"]
    if errors:
        lines += ["### Failures and missing evidence", ""]
        for case in errors:
            error = case.get("error") or case.get("failure", {}).get("error") or "; ".join(case.get("audit", {}).get("problems", []))
            lines += [f"- {case['run']} / {case['key']}: {error}"]
        lines += [""]
    lines += ["## Repeated configurations", ""]
    if repeats:
        lines += [table(["Variant", "Period", "Slip", "Delay min", "Runs", "Completed", "Ledger hashes agree", "Source bundles"], [
            [row["variant"], row["period"], row["slippage_ticks"], row["entry_delay_minutes"], ", ".join(row["runs"]), row["completed_instances"],
             row["completed_ledger_hashes_agree"], len(row["code_sha256_values"])] for row in repeats]), "",
            "A repeat is another execution of the same declared configuration, not new independent evidence. Source changes remain visible in the provenance section; exact ledger agreement is reported separately.", ""]
    else:
        lines += ["No repeated declared configurations in the supplied runs.", ""]

    grouped = defaultdict(list)
    for case in cases:
        grouped[(case["variant"], fingerprint(case["definition"]), case["code_sha256"],
                 case["source_fingerprint"], case["protocol_sha256"], case["commission_per_side"],
                 case["execution_minutes"], case["entry_delay_minutes"], case["selection_eligible"])].append(case)
    lines += ["## Hypotheses and diagnostics", ""]
    for identity, group in sorted(grouped.items()):
        variant, definition_hash, code_hash, data_hash, protocol_hash, commission, execution_minutes, entry_delay_minutes, eligible = identity
        definition = group[0]["definition"]
        lines += [f"### {variant} — source {code_hash[:12]}", "", definition["description"], "",
                  f"**Pre-test hypothesis:** {definition['hypothesis']}", "",
                  "Parameters:", "", "```json", json.dumps(definition["parameters"], indent=2, sort_keys=True), "```", "",
                  f"Definition `{definition_hash}`; source `{code_hash}`; data `{data_hash}`; protocol `{protocol_hash}`. Commission ${commission:.2f}/side; execution {execution_minutes} minute; additional entry delay {entry_delay_minutes} minute(s). This compatibility group is evaluated separately.", ""]
        completed = [case for case in group if case["status"] == "completed"]
        # Latest supplied artifact for each period/cost is only a display choice;
        # every result, including duplicates, remains in the inventory/CSV.
        summaries = {(case["period"], float(case["slippage_ticks"])): case["summary"] for case in completed}
        screen = historical_screen(summaries)
        if not eligible:
            screen["status"] = "superseded_ineligible_for_selection"
            lines += ["**Preliminary results excluded from selection because this run was intentionally interrupted for an execution defect.** " + group[0]["interruption"]["reason"], ""]
        lines += [f"Registered numerical screen: **{screen['status']}**. {screen['scope']}", "",
                  table(["Gate", "Observed", "Required", "Status"], [
                      [row["gate"], number(row["observed"]), row["required"], row["status"]] for row in screen["gates"]]), "",
                  "Pending: " + "; ".join(screen["pending_requirements"]) + ". Confirmed profitability: false.", ""]
        candidates = [case for case in completed if case["period"] == "full" and case["slippage_ticks"] == 1]
        if not candidates:
            lines += ["Full-history one-tick detail is unavailable in this snapshot.", ""]
            continue
        reference = candidates[-1]
        summary = reference["summary"]
        metrics = summary["metrics"]
        lines += [f"Full-history one-tick detail reference: {reference['run']}. Gross P&L after slippage ${metrics['gross_pnl']:,.2f}; commissions ${metrics['total_commission']:,.2f}; net ${metrics['net_pnl']:,.2f}. Actual range {summary['range']['start']} to {summary['range']['end']} ({summary['range']['bar_count']:,} execution bars).", "",
                  table(["Direction", "Trades", "Net $", "PF", "Expectancy $", "Win %"], [
                      [direction, metrics[direction]["trade_count"], *[number(metrics[direction].get(key)) for key in ("net_pnl", "profit_factor", "expectancy", "win_rate")]] for direction in ("long", "short")]), "",
                  table(["Year", "Trades", "Closed net $", "Marked net $", "Long net $", "Short net $"], [
                      [year, row["trade_count"], number(row["net_pnl"]), number(row["mark_to_market_net_pnl"]),
                       number(row["long"]["net_pnl"]), number(row["short"]["net_pnl"])] for year, row in summary["years"].items()]), "",
                  summary["year_trade_attribution"] + ".", "",
                  "Session uncertainty:", "", table(["Block sessions", "95% mean $/session interval", "95% same-length net $ interval", "Positive resample fraction"], [
                      [row["block_sessions"], " to ".join(number(value) for value in row["mean_session_pnl_95_percent_interval"]),
                       " to ".join(number(value) for value in row["same_length_net_pnl_95_percent_interval"]), number(row["resampled_fraction_positive"], 4)] for row in summary["uncertainty"]["estimates"]]), "",
                  summary["uncertainty"]["limitation"], "",
                  "Profit concentration:", "", table(["Measure", "Value"], [[key, number(value)] for key, value in summary["concentration"].items()]), "",
                  "Exits: " + ", ".join(f"{reason}={count}" for reason, count in sorted(Counter(row["exit_reason"] for row in reference["trades"]).items())) + ".", "",
                  "Exposure by direction counts positions open at observed bar closes; overall engine exposure also includes intrabar-only positions. " + json.dumps(summary["exposure_by_side"], sort_keys=True), "",
                  "Data quality, exclusions, risk blocks and engine warnings are retained in the source summary/replay JSON. They are part of the evidence, not permissions to interpolate executable candles.", ""]
        warnings = summary.get("warnings", []) + summary.get("notes", [])
        if warnings:
            lines += ["Recorded warnings and notes:", "", *[f"- {cell(warning)}" for warning in warnings], ""]
    lines += ["## Export audit and limits", "",
              "Every available full trade ledger is exported separately with its case identity in the filename. Case metrics include failures and missing work. Input JSON and source snapshots were read without alteration. Trade hashes, one-contract size, commissions, net arithmetic, position chronology, counts and exact session-marked P&L were checked; failures stay visible.", "",
              "Chronological diagnostics use fresh portfolios. A positive reused-history result does not establish future profitability. These intervals assume the observed session distribution and only preserve dependence inside each moving block; they do not correct repeated strategy selection or execution-model error. Maximum drawdown uses observed minute-close equity and can miss worse intraminute equity excursions. One-minute OHLC cannot resolve within-minute order paths; ambiguous bars use stop-first handling.", "",
              "The standalone report contains no live-trading authorization and does not replace a frozen-candidate evaluation on genuinely untouched data.", ""]
    return "\n".join(lines)


def export_runs(directories, *, output_root, label):
    runs, cases = load_runs(directories)
    repeats = repeated_configurations(cases)
    output = create_run_directory(output_root, label)
    (output / "trades").mkdir()
    metrics_rows = []
    for case in cases:
        row = {key: case[key] for key in (
            "run", "run_id", "variant", "period", "slippage_ticks", "commission_per_side", "status",
            "source_fingerprint", "code_sha256", "protocol_sha256", "selection_eligible",
            "execution_minutes", "entry_delay_minutes",
        )}
        row.update(requested_start=case["window"]["start"], requested_end=case["window"]["end"])
        if "summary" in case:
            metrics = case["summary"]["metrics"]
            for key in ("trade_count", "gross_pnl", "total_commission", "net_pnl", "profit_factor", "expectancy", "max_drawdown_dollars", "exposure_percent", "win_rate"):
                row[key] = metrics.get(key)
            for direction in ("long", "short"):
                row[f"{direction}_trades"] = metrics.get(direction, {}).get("trade_count")
                row[f"{direction}_net_pnl"] = metrics.get(direction, {}).get("net_pnl")
            row["trades_sha256"] = case["summary"]["trades_sha256"]
        if "trades" in case:
            csv_path = output / "trades" / f"{case['run']}__{case['key']}.csv"
            fields = list(dict.fromkeys(key for trade in case["trades"] for key in trade)) or ["id", "side", "quantity", "entry_timestamp", "exit_timestamp", "gross_pnl", "commission", "net_pnl"]
            write_csv_new(csv_path, case["trades"], fields=fields)
            row["trade_csv"] = str(csv_path.resolve())
        row["error"] = case.get("error") or case.get("failure", {}).get("error") or "; ".join(case.get("audit", {}).get("problems", []))
        row["interruption_reason"] = case["interruption"]["reason"] if case["interruption"] else ""
        metrics_rows.append(row)
    write_csv_new(output / "case-metrics.csv", metrics_rows)
    with (output / "report.md").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown_report(runs, cases, repeats))
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exporter_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inputs": [{key: run[key] for key in ("label", "directory", "manifest_sha256", "final_results_available", "interruption")} for run in runs],
        "case_status_counts": dict(Counter(case["status"] for case in cases)),
        "repeated_configurations": repeats,
        "ledger_audits": {f"{case['run']}:{case['key']}": case["audit"] for case in cases if "audit" in case},
        "limitations": REUSED_HISTORY,
    }
    write_new_json(output / "export-index.json", index)
    return output, index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output-root", type=Path, default=BACKEND / "storage/research/reports")
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    output, index = export_runs(args.runs, output_root=args.output_root, label=args.label)
    print(json.dumps({"output_directory": str(output.resolve()), "case_status_counts": index["case_status_counts"]}))
    return 1 if index["case_status_counts"].get("audit_failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
