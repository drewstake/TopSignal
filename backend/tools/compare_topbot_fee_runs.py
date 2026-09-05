"""Read-only audit of the registered $1.20 and $0.61 MNQ research matrices.

Reads only explicitly named immutable experiment directories; never opens a
market-data cache, provider API, strategy evaluator, or trading runtime. Writes
a unique Markdown/CSV/JSON comparison, preserving all input artifacts.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from tools.research_topbot import create_run_directory, fingerprint, write_new_json
from tools.summarize_topbot_research import load_runs, number, table, write_csv_new

FEE_NAME = "MNQ_FEES_PER_CONTRACT_PER_SIDE"
RUNNER = "backend/tools/research_topbot.py"
SCHEMA = "backend/app/bot_schemas.py"
FEE_MODULE = "backend/app/trading_costs.py"
FIXTURE = "backend/tools/fixtures/topbot_research.py"
AMENDMENT = """## September 4 fee amendment

The original protocol and already captured A04 manifests used $1.20 per side.
Topstep's published MNQ fee was verified as $0.61 per side, including exchange,
NFA and commission ($1.22 round trip). The base cost above and CLI defaults are
corrected on that external evidence; candidate rules, split dates and acceptance
criteria are unchanged. Historical manifests are immutable. Their $1.20 runs can
serve as fee stress cases, but the matrix needs a new explicitly $0.61 comparison
before strategy selection. See [the correction and baseline reruns](topbot-fee-correction.md)."""


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def normalized_protocol(text):
    # Earlier PowerShell-written snapshots replaced dash characters with U+FFFD.
    # Normalize only this disclosed punctuation issue, not arbitrary prose.
    text = text.replace("\ufffd", "-").replace("\u2013", "-").replace("\u2014", "-")
    body, marker, amendment = text.partition("## September 4 fee amendment")
    return (
        body.replace("$1.20 commission per contract per side", "$0.61 total fees per contract per side").strip(),
        not marker or (marker + amendment).strip() == AMENDMENT,
    )


def section(text, heading):
    if heading not in text:
        return None
    return text.split(heading, 1)[1].split("\n## ", 1)[0].strip()


class FeeDefaultNormalizer(ast.NodeTransformer):
    """Permit only the reviewed explicit-fee plumbing change in runner/schema."""

    def __init__(self, path):
        self.path = path

    def visit_ImportFrom(self, node):
        if node.module in {"app.trading_costs", "trading_costs"} and [(a.name, a.asname) for a in node.names] == [(FEE_NAME, None)]:
            return None
        return node

    def visit_FunctionDef(self, node):
        if self.path == RUNNER and node.name == "main":
            expected = ast.dump(ast.parse("sys.path.insert(0, str(BACKEND))").body[0], include_attributes=False)
            if node.body and ast.dump(node.body[0], include_attributes=False) == expected:
                node.body = node.body[1:]
        return self.generic_visit(node)

    def visit_Call(self, node):
        if self.path == RUNNER and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--commission-per-side":
            for keyword in node.keywords:
                if keyword.arg == "default":
                    if isinstance(keyword.value, ast.Name) and keyword.value.id == FEE_NAME or isinstance(keyword.value, ast.Constant) and keyword.value.value == 1.2:
                        keyword.value = ast.Constant(value=1.2)
            node.keywords = [keyword for keyword in node.keywords if not (keyword.arg == "help" and isinstance(keyword.value, ast.Constant) and keyword.value.value == "all transaction fees per contract per side; default 0.61 ($1.22 round trip)")]
        return self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if self.path == SCHEMA and isinstance(node.target, ast.Name) and node.target.id == "commission_per_contract" and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "Field":
            for keyword in node.value.keywords:
                if keyword.arg == "default" and isinstance(keyword.value, ast.Name) and keyword.value.id == FEE_NAME:
                    keyword.value = ast.Constant(value=0)
            node.value.keywords = [keyword for keyword in node.value.keywords if not (keyword.arg == "description" and isinstance(keyword.value, ast.Constant) and keyword.value.value == "Total transaction fees per contract per side, charged on entry and exit; excludes slippage. Defaults to TopstepX MNQ.")]
        return self.generic_visit(node)


def normalized_ast(text, path):
    return ast.dump(FeeDefaultNormalizer(path).visit(ast.parse(text)), include_attributes=False)


def audit_snapshot(run):
    manifest = run["manifest"]
    files = manifest["code"]["files"]
    root = Path(run["directory"]) / "sources"
    errors = []
    for path, recorded in files.items():
        source = (root / path).resolve()
        if not source.is_relative_to(root.resolve()):
            errors.append(f"unsafe source path: {path}")
        elif not source.is_file() or sha(source.read_bytes()) != recorded["sha256"]:
            errors.append(f"source hash mismatch or missing: {path}")
    if fingerprint(files) != manifest["code"]["combined_sha256"]:
        errors.append("combined source manifest fingerprint mismatch")
    if sha(manifest["protocol"].encode()) != manifest["protocol_sha256"]:
        errors.append("protocol fingerprint mismatch")
    return {"status": "pass" if not errors else "fail", "errors": errors, "verified_source_count": len(files)}


def source_comparison(old, new):
    a, b = old["manifest"]["code"]["files"], new["manifest"]["code"]["files"]
    changed = [path for path in sorted(set(a) | set(b)) if a.get(path) != b.get(path)]
    protected = sorted({path for path in set(a) | set(b) if path.startswith("backend/app/") and path not in {SCHEMA, FEE_MODULE}} | {FIXTURE, "backend/tools/research_rolls.py"})
    exact = {path: path in a and path in b and a[path] == b[path] for path in protected}
    normalized, diffs = {}, {}
    for path in (RUNNER, SCHEMA):
        before = (Path(old["directory"]) / "sources" / path).read_text(encoding="utf-8")
        after = (Path(new["directory"]) / "sources" / path).read_text(encoding="utf-8")
        normalized[path] = normalized_ast(before, path) == normalized_ast(after, path)
        diffs[path] = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="old/" + path, tofile="new/" + path))
    fee_source = Path(new["directory"]) / "sources" / FEE_MODULE
    fee_valid = False
    if fee_source.exists():
        statements = ast.parse(fee_source.read_text(encoding="utf-8")).body
        if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(statements[0].value.value, str):
            statements = statements[1:]
        fee_valid = ast.dump(ast.Module(body=statements, type_ignores=[]), include_attributes=False) == ast.dump(ast.parse(f"{FEE_NAME} = 0.61"), include_attributes=False)
    return {"protected_sources_exact": exact, "reviewed_fee_default_ast_equal": normalized,
            "fee_module_is_only_declared_constant": fee_valid, "all_source_paths_changed": changed,
            "reviewed_source_diffs": diffs,
            "status": "pass" if all(exact.values()) and all(normalized.values()) and fee_valid else "fail",
            "note": "All application source except the separately checked fee schema/constant is byte-identical. Other unused tool additions/default changes are listed; complete bundle identity is deliberately not claimed."}


def match_cases(old_cases, new_cases):
    def keyed(cases):
        result = {}
        for case in cases:
            key = (case["variant"], case["period"], case["slippage_ticks"])
            if key in result:
                raise ValueError(f"Duplicate declared comparison case: {key}; provide exactly one old and one new run per case")
            result[key] = case
        return result
    a, b = keyed(old_cases), keyed(new_cases)
    return [(key, a.get(key), b.get(key)) for key in sorted(set(a) | set(b))]


def registered_fixture(manifest):
    command = manifest["command"]
    raw = command[command.index("--fixture") + 1] if "--fixture" in command else FIXTURE
    return Path(raw).resolve() == (BACKEND.parent / FIXTURE).resolve()


def compare_case(old, new, old_run, new_run, sources, snapshots):
    om, nm = old_run["manifest"], new_run["manifest"]
    op, oa = normalized_protocol(om["protocol"])
    np, na = normalized_protocol(nm["protocol"])
    controls = {key: om.get(key) == nm.get(key) for key in (
        "cache_manifest", "periods", "starting_balance", "execution_minutes", "engine_version",
        "entry_delay_minutes", "execution_model", "roll_policy", "roll_execution", "calendar_risk_hook", "bootstrap_repetitions",
    )}
    controls.update({
        "hypothesis": old["definition"] == new["definition"],
        "registered_fixture": registered_fixture(om) and registered_fixture(nm),
        "candidate_settings": om["settings"][old["variant"]] == nm["settings"][new["variant"]],
        "non_fee_costs": {k: v for k, v in om["costs"].items() if k != "commission_per_side"} == {k: v for k, v in nm["costs"].items() if k != "commission_per_side"},
        "runtime": all(om["code"].get(k) == nm["code"].get(k) for k in ("python", "installed_distributions")),
        "acceptance_section": section(op, "## Existing-data shortlist requirements") is not None and section(op, "## Existing-data shortlist requirements") == section(np, "## Existing-data shortlist requirements"),
        "protocol_except_fee_and_disclosed_dash_encoding": op == np and oa and na,
        "old_fee_declared_1_20": old["commission_per_side"] == 1.2,
        "new_fee_is_explicit_0_61": new["commission_per_side"] == .61 and "--commission-per-side" in nm["command"] and nm["command"][nm["command"].index("--commission-per-side") + 1] == "0.61",
        "both_observed_minute_zero_extra_delay": om["execution_minutes"] == nm["execution_minutes"] == 1 and om.get("entry_delay_minutes", 0) == nm.get("entry_delay_minutes", 0) == 0,
        "neither_run_interrupted": old["selection_eligible"] and new["selection_eligible"],
        "snapshot_integrity": all(snapshots[r["directory"]]["status"] == "pass" for r in (old_run, new_run)),
        "protected_code_and_fee_plumbing": sources["status"] == "pass",
    })
    result = {"key": old["key"], "variant": old["variant"], "period": old["period"], "slippage_ticks": old["slippage_ticks"],
              "old_run": old["run_id"], "new_run": new["run_id"], "old_fee_per_side": old["commission_per_side"], "new_fee_per_side": new["commission_per_side"],
              "controls": controls, "old_status": old["status"], "new_status": new["status"],
              "status": "matched" if all(controls.values()) else "incompatible", "old_ledger_audit": old.get("audit"), "new_ledger_audit": new.get("audit")}
    if old["status"] != "completed" or new["status"] != "completed":
        if "audit_failed" in {old["status"], new["status"]}:
            result["status"] = "audit_failed"
        elif result["status"] == "matched":
            result["status"] = "incomplete"
        return result
    # Check actual replay assumptions in addition to the pre-test declarations.
    replay_paths = [Path(c["directory"]) / f"{c['key']}.replay.json" for c in (old, new)]
    if not all(path.is_file() for path in replay_paths):
        result["error"] = "complete replay assumptions artifact is missing"
        if result["status"] == "matched":
            result["status"] = "incomplete"
        return result
    assumptions = [json.loads(path.read_text(encoding="utf-8"))["assumptions"] for path in replay_paths]
    controls["actual_non_fee_assumptions"] = {k: v for k, v in assumptions[0].items() if k != "commission_per_contract"} == {k: v for k, v in assumptions[1].items() if k != "commission_per_contract"}
    controls["actual_fee_assumptions"] = assumptions[0]["commission_per_contract"] == 1.2 and assumptions[1]["commission_per_contract"] == .61
    controls["actual_replay_ranges"] = old["summary"]["range"] == new["summary"]["range"]
    metrics = [c["summary"]["metrics"] for c in (old, new)]
    for label, case, values in zip(("old", "new"), (old, new), metrics):
        manifest = om if label == "old" else nm
        controls[f"{label}_candidate_metadata"] = case["summary"]["candidate_definition"] == manifest["hypotheses"][case["variant"]] and case["summary"]["candidate_settings"] == manifest["settings"][case["variant"]]
        controls[f"{label}_actual_commission_total"] = abs(sum(t["commission"] for t in case["trades"]) - values["total_commission"]) < 1e-5
        result[label + "_metrics"] = values
        result[label + "_trade_ledger_sha256"] = case["summary"]["trades_sha256"]
        result[label + "_fill_path_sha256"] = fingerprint([{k: v for k, v in t.items() if k not in {"commission", "net_pnl"}} for t in case["trades"]])
    result["fill_path_equal"] = result["old_fill_path_sha256"] == result["new_fill_path_sha256"]
    result["delta"] = {key: metrics[1][key] - metrics[0][key] for key in (
        "trade_count", "net_pnl", "gross_pnl", "total_commission", "expectancy", "max_drawdown_dollars", "exposure_percent",
    ) if metrics[0].get(key) is not None and metrics[1].get(key) is not None}
    result["old_ledger_fee_refund_counterfactual"] = len(old["trades"]) * 2 * (1.2 - .61)
    result["net_change_beyond_old_ledger_fee_refund"] = result["delta"]["net_pnl"] - result["old_ledger_fee_refund_counterfactual"]
    if not all(controls.values()):
        result["status"] = "incompatible"
    return result


def export_comparison(old_directories, new_directories, output_root, label, expected_cases=72):
    all_paths = [p.resolve() for p in old_directories + new_directories]
    if len(set(all_paths)) != len(all_paths):
        raise ValueError("Old/new inputs must be distinct")
    if any(output_root.resolve().is_relative_to(p) for p in all_paths):
        raise ValueError("Output root must not be inside an immutable input run")
    old_runs, old_cases = load_runs(old_directories)
    new_runs, new_cases = load_runs(new_directories)
    runs = {r["directory"]: r for r in old_runs + new_runs}
    snapshots = {path: audit_snapshot(run) for path, run in runs.items()}
    source_pairs, results = {}, []
    for key, old, new in match_cases(old_cases, new_cases):
        if old is None or new is None:
            results.append({"key": (old or new)["key"], "status": "unmatched", "old_run": old["run_id"] if old else None, "new_run": new["run_id"] if new else None})
            continue
        pair = old["run_id"] + " -> " + new["run_id"]
        if pair not in source_pairs:
            source_pairs[pair] = source_comparison(runs[old["directory"]], runs[new["directory"]])
        results.append(compare_case(old, new, runs[old["directory"]], runs[new["directory"]], source_pairs[pair], snapshots))
    counts = Counter(r["status"] for r in results)
    complete = len(old_cases) == len(new_cases) == expected_cases and counts["matched"] == expected_cases and all(r["final_results_available"] for r in runs.values())
    index = {"status": "complete_matched_fee_comparison" if complete else "incomplete_or_incompatible_comparison",
             "expected_cases_per_fee": expected_cases, "old_planned_cases": len(old_cases), "new_planned_cases": len(new_cases), "status_counts": dict(counts),
             "snapshot_audits": snapshots, "source_pairs": source_pairs, "cases": results,
             "inputs": [{"directory": path, "manifest_sha256": run["manifest_sha256"], "interruption": run["interruption"]} for path, run in runs.items()],
             "limitations": "Retrospective matched-fee diagnostics. No strategies rerun by this exporter. No untouched evidence, live execution, fee-history reconstruction or profitability certification. Periods overlap; case P&L must not be summed across the matrix."}
    directory = create_run_directory(output_root, label)
    write_new_json(directory / "comparison.json", index)
    flat = []
    for result in results:
        row = {k: result.get(k) for k in ("key", "variant", "period", "slippage_ticks", "status", "old_run", "new_run", "old_fee_per_side", "new_fee_per_side", "fill_path_equal", "old_trade_ledger_sha256", "new_trade_ledger_sha256", "old_ledger_fee_refund_counterfactual", "net_change_beyond_old_ledger_fee_refund")}
        row["failed_controls"] = "; ".join(k for k, v in result.get("controls", {}).items() if not v)
        for prefix in ("old_metrics", "new_metrics", "delta"):
            for key, value in result.get(prefix, {}).items():
                if not isinstance(value, (dict, list)):
                    row[prefix + "_" + key] = value
        flat.append(row)
    write_csv_new(directory / "matched-fees.csv", flat)
    lines = ["# MNQ matched transaction-fee comparison", "", "**" + index["status"].upper().replace("_", " ") + "**", "",
             f"Expected {expected_cases} cases per fee. Old planned: {len(old_cases)}; new planned: {len(new_cases)}. Status counts: {dict(counts)}.", "",
             "Old: $1.20/contract/side ($2.40 round trip), preserved as higher-cost stress. New: explicit $0.61/contract/side ($1.22 round trip). Slippage remains separate. Current published fees are held constant across reused history.", "",
             index["limitations"], "", "## Provenance and fixed controls", ""]
    for pair, source in source_pairs.items():
        lines += [f"### {pair}", "", f"Protected source audit: **{source['status']}**. {source['note']}", "",
                  "Changed/added source paths: " + ", ".join(f"`{p}`" for p in source["all_source_paths_changed"]), ""]
    lines += ["Each paired case checks manifest/snapshot hashes, exact application and selected fixture sources, reviewed fee-only runner/API defaults, data manifest, candidate settings, periods, slippage, risk/fill assumptions, runtime, bootstrap settings, and the unchanged acceptance section. The protocol comparison permits only the published fee amendment and disclosed replacement-character dash repair. Actual trade fees must equal twice the declared per-side fee times quantity and reconcile to the summary total.", "",
              "## All paired cases", "", table(["Case", "Status", "Old trades", "New trades", "Old net $", "New net $", "Delta net $", "Delta trades", "Same fills"], [
                  [r["key"], r["status"], r.get("old_metrics", {}).get("trade_count", "—"), r.get("new_metrics", {}).get("trade_count", "—"), number(r.get("old_metrics", {}).get("net_pnl")), number(r.get("new_metrics", {}).get("net_pnl")), number(r.get("delta", {}).get("net_pnl")), r.get("delta", {}).get("trade_count", "—"), r.get("fill_path_equal", "—")] for r in results]), "",
              "The CSV/JSON retain full old/new metrics and deltas, ledger hashes and failed controls. Same fills compares every trade field except commission and net P&L. Lower fees can change daily loss gates and later opportunities even when trade counts stay equal. The old-ledger fee refund is a labeled counterfactual; it is never substituted for a fresh replay.", ""]
    failures = [r for r in results if r["status"] != "matched"]
    if failures:
        lines += ["## Missing or incompatible evidence", ""]
        for result in failures:
            lines += [f"- {result['key']}: {result['status']}; " + ", ".join(k for k, v in result.get("controls", {}).items() if not v)]
    with (directory / "report.md").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return directory, index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-runs", type=Path, nargs="+", required=True)
    parser.add_argument("--new-runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output-root", type=Path, default=BACKEND / "storage/research/fee-comparisons")
    parser.add_argument("--label", required=True)
    parser.add_argument("--expected-cases", type=int, default=72)
    args = parser.parse_args()
    output, result = export_comparison(args.old_runs, args.new_runs, args.output_root, args.label, args.expected_cases)
    print(json.dumps({"output_directory": str(output), "status": result["status"], "case_status_counts": result["status_counts"]}))
    return 0 if result["status"] == "complete_matched_fee_comparison" else 1


if __name__ == "__main__":
    raise SystemExit(main())
