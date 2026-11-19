from __future__ import annotations

from pathlib import Path

import pytest

from tools.research_topbot import concentration, create_run_directory, historical_screen, write_new_json


def test_manifests_and_run_directories_cannot_overwrite_previous_evidence(tmp_path):
    first = create_run_directory(tmp_path, "same-hypothesis")
    second = create_run_directory(tmp_path, "same-hypothesis")
    assert first != second
    manifest = first / "manifest.json"
    write_new_json(manifest, {"hypothesis": "before the test"})
    with pytest.raises(FileExistsError):
        write_new_json(manifest, {"hypothesis": "after seeing the outcome"})
    assert "before the test" in manifest.read_text()
    with pytest.raises(ValueError):
        create_run_directory(tmp_path, "../overwrite")


def test_profit_concentration_exposes_one_trade_and_one_year_dependence():
    trades = [{"net_pnl": value} for value in [1000, 10, -300, -300]]
    sessions = [
        {"session": "2020-01-01", "net_pnl": 1000},
        {"session": "2021-01-01", "net_pnl": -590},
    ]
    result = concentration(trades, sessions)
    assert result["net_excluding_best_trade"] == -590
    assert result["net_excluding_best_calendar_year"] == -590
    assert result["top_1_percent_positive_profit_share"] == pytest.approx(1000 / 1010)


def test_historical_screen_does_not_promote_missing_or_reused_evidence():
    assert historical_screen({})["status"] == "incomplete"
    passing = {
        "metrics": {"trade_count": 600, "net_pnl": 2000, "profit_factor": 1.2},
        "years": {str(year): {"mark_to_market_net_pnl": 300} for year in range(2020, 2026)},
        "uncertainty": {"estimates": [
            {"block_sessions": block, "mean_session_pnl_95_percent_interval": [1, 10]}
            for block in (5, 20)
        ]},
        "concentration": {"net_excluding_best_5_trades": 1000, "net_excluding_best_calendar_year": 1700},
    }
    summaries = {(period, slip): passing for period in ("full", "development", "diagnostic") for slip in (1.0, 2.0)}
    screen = historical_screen(summaries)
    assert screen["status"] == "passes_measured_gates_only"
    assert not screen["confirmed_profitability"]
    assert len(screen["pending_requirements"]) == 2
    summaries[("diagnostic", 2.0)] = {"metrics": {"net_pnl": -1}}
    screen = historical_screen(summaries)
    assert screen["status"] == "fails_registered_screen"
    assert [row["gate"] for row in screen["gates"] if row["status"] == "fail"] == ["diagnostic_net_at_2_ticks"]
