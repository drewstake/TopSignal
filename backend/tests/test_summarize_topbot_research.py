from __future__ import annotations

import csv
import json

from tools.research_topbot import concentration, fingerprint
from tools.summarize_topbot_research import export_runs, load_runs, repeated_configurations


def make_run(tmp_path, name="run", *, code="a", interrupted=False, missing=False):
    directory = tmp_path / name
    directory.mkdir()
    window = {"start": "2024-01-01T00:00:00+00:00", "end": "2025-01-01T00:00:00+00:00"}
    manifest = {
        "run_id": name, "created_at": "2026-09-05T00:00:00+00:00",
        "hypotheses": {"candidate": {"description": "Fixed example", "hypothesis": "Test a specific observation", "parameters": {"risk": 50}}},
        "periods": {"full": window}, "costs": {"slippage_ticks_each_side": [1], "commission_per_side": 1.2},
        "code": {"git_revision": "commit", "combined_sha256": code * 64},
        "cache_manifest": {"source_fingerprint": "data"}, "protocol_sha256": "protocol",
        "execution_minutes": 1, "starting_balance": 50_000,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    if interrupted:
        (directory / "interruption.json").write_text(json.dumps({"reason": "Risk parity defect; preliminary results excluded."}))
    if missing:
        (directory / "candidate__full__slip-1.started.json").write_text("{}")
        return directory
    trade = {"id": 1, "side": "long", "quantity": 1, "entry_timestamp": "2024-01-02T15:00:00+00:00",
             "exit_timestamp": "2024-01-02T16:00:00+00:00", "exit_reason": "take_profit",
             "gross_pnl": 12.4, "commission": 2.4, "net_pnl": 10}
    sessions = [{"session": "2024-01-02", "net_pnl": 10, "ending_equity": 50_010}]
    direction = {"trade_count": 1, "net_pnl": 10, "profit_factor": None, "expectancy": 10, "win_rate": 100}
    metrics = {"trade_count": 1, "gross_pnl": 12.4, "total_commission": 2.4, "net_pnl": 10,
               "profit_factor": None, "expectancy": 10, "max_drawdown_dollars": 1, "exposure_percent": 10,
               "long": direction, "short": {**direction, "trade_count": 0, "net_pnl": 0}}
    summary = {
        "metrics": metrics, "trades_sha256": fingerprint([trade]), "sessions_sha256": fingerprint(sessions),
        "range": {**window, "bar_count": 100},
        "years": {"2024": {"trade_count": 1, "net_pnl": 10, "mark_to_market_net_pnl": 10,
                             "long": {"net_pnl": 10}, "short": {"net_pnl": 0}}},
        "year_trade_attribution": "exit year", "uncertainty": {"estimates": [], "limitation": "Synthetic test"},
        "concentration": concentration([trade], sessions), "exposure_by_side": {}, "warnings": [], "notes": [],
    }
    for suffix, contents in (("trades", [trade]), ("sessions", sessions), ("summary", summary)):
        (directory / f"candidate__full__slip-1.{suffix}.json").write_text(json.dumps(contents))
    (directory / "results.json").write_text("{}")
    return directory


def test_export_preserves_input_and_exports_every_trade_to_unique_output(tmp_path):
    run = make_run(tmp_path)
    before = {path.name: path.read_bytes() for path in run.iterdir()}
    output, index = export_runs([run], output_root=tmp_path / "exports", label="report")
    assert {path.name: path.read_bytes() for path in run.iterdir()} == before
    assert index["case_status_counts"] == {"completed": 1}
    with (output / "trades/R01__candidate__full__slip-1.csv").open(newline="") as handle:
        trades = list(csv.DictReader(handle))
    assert len(trades) == 1
    assert trades[0]["net_pnl"] == "10"
    assert "ALL PLANNED CASES HAVE RESULT ARTIFACTS" in (output / "report.md").read_text(encoding="utf-8")
    second, _ = export_runs([run], output_root=tmp_path / "exports", label="report")
    assert second != output


def test_interrupted_results_and_missing_work_remain_ineligible(tmp_path):
    completed = make_run(tmp_path, "completed-but-invalid", interrupted=True)
    missing = make_run(tmp_path, "not-finished", interrupted=True, missing=True)
    output, index = export_runs([completed, missing], output_root=tmp_path / "exports", label="partial")
    report = (output / "report.md").read_text(encoding="utf-8")
    assert index["case_status_counts"] == {"completed": 1, "incomplete": 1}
    assert "INCOMPLETE SNAPSHOT" in report
    assert "superseded_ineligible_for_selection" in report
    assert "Risk parity defect; preliminary results excluded." in report
    with (output / "case-metrics.csv").open(newline="") as handle:
        assert all(row["selection_eligible"] == "False" for row in csv.DictReader(handle))


def test_different_source_bundles_never_merge_numerical_screen(tmp_path):
    first = make_run(tmp_path, "first", code="a")
    second = make_run(tmp_path, "second", code="b")
    runs, cases = load_runs([first, second])
    repeats = repeated_configurations(cases)
    assert repeats[0]["completed_ledger_hashes_agree"] is True
    assert len(repeats[0]["code_sha256_values"]) == 2
    output, _ = export_runs([first, second], output_root=tmp_path / "exports", label="separate")
    report = (output / "report.md").read_text(encoding="utf-8")
    assert report.count("### candidate — source") == 2
    assert report.count("This compatibility group is evaluated separately.") == 2


def test_changed_trade_ledger_is_visible_as_failed_audit(tmp_path):
    run = make_run(tmp_path)
    path = run / "candidate__full__slip-1.trades.json"
    trades = json.loads(path.read_text())
    trades[0]["net_pnl"] = 999
    path.write_text(json.dumps(trades))
    _, cases = load_runs([run])
    assert cases[0]["status"] == "audit_failed"
    assert "trade ledger SHA256 differs" in cases[0]["audit"]["problems"][0]


def test_entry_delay_stress_has_separate_identity_and_compatibility_group(tmp_path):
    old_default = make_run(tmp_path, "old-default")
    explicit_zero = make_run(tmp_path, "explicit-zero")
    delayed = make_run(tmp_path, "delayed")
    for directory, delay in ((explicit_zero, 0), (delayed, 1)):
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["entry_delay_minutes"] = delay
        path.write_text(json.dumps(manifest))
    _, cases = load_runs([old_default, explicit_zero, delayed])
    repeats = repeated_configurations(cases)
    assert len(repeats) == 1
    assert repeats[0]["entry_delay_minutes"] == 0
    assert repeats[0]["completed_instances"] == 2
    output, _ = export_runs([old_default, explicit_zero, delayed], output_root=tmp_path / "exports", label="delay-separation")
    report = (output / "report.md").read_text(encoding="utf-8")
    assert report.count("This compatibility group is evaluated separately.") == 2
    with (output / "case-metrics.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert sorted(row["entry_delay_minutes"] for row in rows) == ["0", "0", "1"]
