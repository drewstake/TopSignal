"""Synthetic regression checks; none of these constitute trading evidence."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.probabilistic_artifacts import validate_binding, experiment_path
from app.services.probabilistic_protocol import digest, protocol, assert_protocol
from app.services.probabilistic_data import aggregate_observed_minutes
from app.services.probabilistic_strategy import Costs, price_paths
from app.services import bot_service
from test_probabilistic_strategy import NOW
from test_topbot_mathematical import install


def test_full_review_binding_accepts_hashed_evidence_and_rejects_tampering(tmp_path):
    from app.services.probabilistic_artifacts import EVIDENCE_CHECKS, record_evidence
    from test_probabilistic_strategy import fitted
    shift = timedelta(days=120)
    model = fitted("bayesian_cells_v1")
    model = replace(model, samples=tuple(replace(s,
        features=replace(s.features, decision_at=s.features.decision_at+shift,
                         day=(s.features.decision_at+shift).date().isoformat()),
        label_end=s.label_end+shift) for s in model.samples))
    path = install(tmp_path, model=model)
    artifact = json.loads(path.read_text())
    report_path = experiment_path(tmp_path, artifact["experiment_id"])
    report = json.loads(report_path.read_text())
    report["multiplicity_count"] = 6
    report_path.write_text(json.dumps(report))
    artifact["experiment_sha256"] = digest(report)
    artifact["validation_status"] = "passed"
    references = {}
    for kind, checks in EVIDENCE_CHECKS.items():
        record = {"kind": kind, "checks": dict.fromkeys(checks, True), "reviewed_by": "SYNTHETIC TEST ONLY",
                  "experiment_id": artifact["experiment_id"], "model_version": model.version,
                  "protocol_sha256": digest(protocol()), "observed_through": (NOW+shift).isoformat(),
                  "first_session": "2026-09-28", "sessions": 60, "trades": 200,
                  "historical_count_verified": True, "configuration_count": 6}
        checksum = record_evidence(record, root=tmp_path, as_of=NOW+shift)
        references[kind] = {"passed": True, "sha256": checksum}
    artifact["review"]["evidence"] = references
    accepted, _ = validate_binding(artifact, root=tmp_path, as_of=NOW+shift)
    assert accepted.version == model.version
    evidence_path = tmp_path / "probabilistic-v3/evidence" / (references["forward_dry_run"]["sha256"]+".json")
    evidence_path.write_text(evidence_path.read_text().replace('"sessions": 60', '"sessions": 1'))
    with pytest.raises(ValueError, match="mismatch"):
        validate_binding(artifact, root=tmp_path, as_of=NOW+shift)


@pytest.mark.parametrize("fault", ["future", "missing_check", "changed_code", "missing_evidence", "unbound_report", "changed_pool_mix"])
def test_loader_rejects_incomplete_or_changed_governance(tmp_path, fault):
    path = install(tmp_path)
    artifact = json.loads(path.read_text())
    if fault == "future":
        sample = artifact["model"]["samples"][-1]
        sample["decision_at"] = NOW.isoformat()
        sample["label_end"] = (NOW + timedelta(minutes=15)).isoformat()
    elif fault == "missing_check":
        record_path = experiment_path(tmp_path, artifact["experiment_id"])
        report = json.loads(record_path.read_text())
        report["pooled"][artifact["model"]["version"]]["acceptance_checks"].pop("no_negative_fold")
        record_path.write_text(json.dumps(report))
        artifact["experiment_sha256"] = digest(report)
    elif fault == "changed_code":
        artifact["implementation_sha256"] = "0" * 64
    elif fault == "missing_evidence":
        artifact["validation_status"] = "passed"
    elif fault == "unbound_report":
        artifact["experiment_id"] = "../outside"
    else:
        artifact["model"]["pool_mix"] = 0.5
    with pytest.raises(ValueError):
        validate_binding(artifact, root=tmp_path, as_of=NOW)


def test_registered_protocol_cannot_be_tuned_in_place():
    changed = dict(protocol(), minimum_training_examples=2)
    with pytest.raises(ValueError, match="registered"):
        assert_protocol(changed)


def test_partial_historical_inventory_is_counted_without_claiming_completeness(tmp_path):
    from tools.research_probabilistic_v3 import read_historical_ledger
    path = tmp_path / "ledger.json"
    record = {"reviewed_by": "synthetic inventory", "historical_count_verified": False,
              "configurations": [{"id": "prior-run-1"}, {"id": "prior-run-2"}]}
    path.write_text(json.dumps(record), encoding="utf-8")
    accepted = read_historical_ledger(path)
    assert len(accepted["configurations"]) == 2 and accepted["historical_count_verified"] is False
    record["configurations"].append({"id": "prior-run-1"})
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        read_historical_ledger(path)


def test_zero_drift_diagnostic_queries_all_four_fitted_cells(monkeypatch):
    from app.services import probabilistic_validation as validation
    cells = []
    def observe(self, features):
        cells.append((features.values[1] >= 0, features.values[2] >= 0))
        return {"research_action": "NO_TRADE"}
    monkeypatch.setattr(validation.PathModel, "forecast", observe)
    result = validation.zero_drift_diagnostic(refits=1)
    assert len(cells) == len(set(cells)) == 4
    assert result["tests_per_refit"] == 8


def test_report_packages_large_directional_baselines_as_verified_local_files(tmp_path):
    from tools.research_probabilistic_v3 import archive_ledgers
    trades = [{"decision_at": NOW.isoformat(), "net_usd": 1.0}] * 2000
    report = {"folds": [{"baselines": {"always_long": trades, "flat": []}}],
              "pooled": {"baseline": {"baselines": {"always_long": {"net_usd": 2000}}}}}
    archive_ledgers(report, tmp_path)
    reference = report["folds"][0]["baselines"]["always_long"]["ledger_reference"]
    stored = json.loads((tmp_path / reference["path"]).read_text(encoding="utf-8"))
    assert stored == trades and digest(stored) == reference["sha256"]
    assert len(json.dumps(report)) < 2000
    assert report["pooled"]["baseline"]["baselines"]["always_long"]["net_usd"] == 2000


def test_volatility_cap_has_its_own_reason_and_measured_sigma(tmp_path):
    from test_probabilistic_strategy import candles, as_rows, CONTRACT
    from app.services.probabilistic_shadow import explain_shadow
    rows = [replace(r, open=r.open+i*20, high=r.high+i*20, low=r.low+i*20, close=r.close+i*20)
            for i,r in enumerate(candles(21))]
    result = explain_shadow(candles=as_rows(rows), owner="owner", contract_id=CONTRACT, as_of=NOW, root=tmp_path)
    assert result["reason_code"] == "volatility_above_risk_cap"
    assert result["volatility_sigma"] > 14 and result["implied_stop_points"] > 25


def test_micro_classification_and_drawdown_use_explicit_bases():
    from app.services.topstep_fees import _is_micro_contract
    from app.services.projectx_metrics import TradeMetricSample, compute_trade_summary
    assert _is_micro_contract(symbol="MNQ", contract_id=None)
    assert not _is_micro_contract(symbol="MYSTERY", contract_id=None)
    trades = [TradeMetricSample(timestamp=NOW+timedelta(minutes=i), pnl=pnl, fees=0, commissions=0)
              for i,pnl in enumerate((100, -50))]
    assert compute_trade_summary(trades, starting_balance=1000)["risk_drawdown_score"] == 5
    assert compute_trade_summary(trades)["risk_drawdown_score"] is None


def test_quiet_minutes_form_observed_bar_without_interpolation_or_cross_delivery():
    start = datetime(2021, 12, 31, 14, 30, tzinfo=timezone.utc)
    def row(minute, symbol="MNQH2"):
        return SimpleNamespace(candle_timestamp=start+timedelta(minutes=minute),
            source_raw_symbol=symbol, open_price=100+minute, high_price=101+minute,
            low_price=99+minute, close_price=100+minute, volume=2)
    result, quality = aggregate_observed_minutes([row(0), row(4), row(5), row(7, "MNQM2")], owner="test")
    assert len(result) == 1
    assert (result[0].open, result[0].close, result[0].volume) == (100, 104, 4)
    assert quality["empty_minutes_by_day"]["2021-12-31"] == 3
    assert quality["mixed_contract_buckets_dropped"] == 1
    assert quality["dec_31_2021_0000_to_1700_et_minutes"] == 4


def test_target_touch_requires_later_trade_through_under_stress():
    path = np.asarray([[[0, 6, -1, 2], [2, 5, 1, 3], [3, 5, 2, 3]]])
    usual = price_paths(path, stop=4, scale=1, side=1, costs=Costs(0, 0, 0, 0))
    stressed = price_paths(path, stop=4, scale=1, side=1, costs=Costs(0, 0, 0, 0), trade_through_ticks=1)
    assert usual[1][0] == 2 and stressed[1][0] == 1
    assert usual[0][0] > stressed[0][0]


def test_contract_selection_requires_unique_active_root():
    rows = [{"id": "CON.F.US.MNQ.U26", "active_contract": True},
            {"id": "CON.F.US.NQ.Z26", "active_contract": True}]
    assert bot_service._pick_market_contract(rows, root="MNQ") == rows[0]
    assert bot_service._pick_market_contract(rows, root="ES") is None
    assert bot_service._pick_market_contract(rows+[rows[0]], root="MNQ") is None


def test_legacy_strategies_need_explicit_flag(monkeypatch):
    monkeypatch.delenv("TOPSIGNAL_ENABLE_LEGACY_STRATEGIES")
    with pytest.raises(ValueError, match="Legacy"):
        bot_service._require_strategy_available("sma_cross")
    bot_service._require_strategy_available("topbot_adaptive")


def test_chart_and_backend_share_calendar_and_vwap_fixture():
    from app.services.trading_day import futures_session_is_open
    from app.services.bot_market_analysis import _session_vwap
    fixture = json.loads((Path(__file__).resolve().parents[2] / "docs/fixtures/market-context.json").read_text())
    for row in fixture["sessions"]:
        assert futures_session_is_open(datetime.fromisoformat(row["timestamp"]), symbol="MNQ") is row["open"]
    candles = [{**row, "timestamp": datetime.fromisoformat(row["timestamp"])} for row in fixture["vwap"]["candles"]]
    assert _session_vwap(candles) == pytest.approx(fixture["vwap"]["expected"])
