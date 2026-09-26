"""Causal, numerical and execution invariants; synthetic cases are not evidence of alpha."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.probabilistic_strategy import (
    BAR, ET, Costs, Candle, Features, Sample, PathModel, MODEL_VERSIONS,
    features, make_samples, price_paths, weighted_crps,
)
from app.services.probabilistic_shadow import explain_shadow, model_path, scope_hash
from app.services.probabilistic_validation import (
    audit_candles, walk_forward_plan, purged_partition, block_indices, simulate, summarize,
    paired_daily_bound, probability_scores,
)


CONTRACT = "CON.F.US.MNQ.U26"
NOW = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)


def candles(n=25):
    start = NOW - 21 * BAR
    return [Candle(start + i * BAR, 20000 + i * .5 - .25, 20000 + i * .5 + 1,
                   20000 + i * .5 - 1, 20000 + i * .5, 100.0, CONTRACT, "owner") for i in range(n)]


WIN_PATH = ((0, .6, -.1, .5), (.5, .8, .4, .7), (.7, .9, .6, .8))
LOSS_PATH = ((0, .2, -.1, .1), (-20, -19, -21, -20), (-20, -19, -21, -20))


def fitted(version="empirical_pool_v1", *, n=360, losing=0):
    start = NOW - timedelta(days=62)
    samples = []
    for i in range(n):
        now = start + timedelta(days=i // 6, minutes=15 * (i % 6))
        f = Features((1.0, 1.0, .1, .0), 4.0, 2.0, now, now.astimezone(ET).date().isoformat())
        samples.append(Sample(f, LOSS_PATH if i < losing else WIN_PATH, now + 3 * BAR))
    return PathModel(version, tuple(samples))


def query():
    return Features((1.0, 1.0, .1, .0), 4.0, 2.0, NOW, NOW.astimezone(ET).date().isoformat())


@pytest.mark.parametrize("mutation", ["gap", "duplicate", "owner", "contract", "subscription", "volume", "nan", "partial"])
def test_required_input_corruption_abstains(mutation):
    rows = candles(21)
    if mutation == "gap": rows[9] = replace(rows[9], timestamp=rows[9].timestamp + BAR)
    if mutation == "duplicate": rows[9] = replace(rows[9], timestamp=rows[8].timestamp)
    if mutation == "owner": rows[9] = replace(rows[9], owner="other")
    if mutation == "contract": rows[9] = replace(rows[9], contract_id="CON.F.US.MNQ.Z26")
    if mutation == "subscription": rows[9] = replace(rows[9], live=True)
    if mutation == "volume": rows[9] = replace(rows[9], volume=None)
    if mutation == "nan": rows[9] = replace(rows[9], close=float("nan"))
    if mutation == "partial": rows[9] = replace(rows[9], partial=True)
    with pytest.raises(ValueError): features(rows, as_of=NOW)


def test_features_cannot_use_unclosed_candles_and_labels_need_complete_future():
    rows = candles()
    with pytest.raises(ValueError, match="not closed"):
        features(rows[:21], as_of=NOW - timedelta(seconds=1))
    samples, _ = make_samples(rows)
    assert samples and samples[0].features.decision_at == NOW
    assert samples[0].label_end == NOW + 3 * BAR
    modified = list(rows)
    modified[22] = replace(modified[22], high=99999)
    changed, _ = make_samples(modified)
    assert changed[0].features == samples[0].features  # A future outcome cannot alter features.
    assert changed[0].path != samples[0].path
    modified[22] = replace(modified[22], partial=True)
    blocked, reasons = make_samples(modified)
    assert blocked == []
    assert any("future path" in key for key in reasons)


def test_stops_are_risk_derived_and_high_volatility_does_not_increase_cap():
    f = features(candles(21), as_of=NOW)
    assert f.stop_points == 4
    volatile = [replace(r, open=r.open + i * 20, high=r.high + i * 20,
                        low=r.low + i * 20, close=r.close + i * 20) for i, r in enumerate(candles(21))]
    with pytest.raises(ValueError, match="risk cap"):
        features(volatile, as_of=NOW)


def test_first_passage_stop_first_gap_losses_and_short_mirror():
    paths = np.array([
        [[0, 2, -2, 1], [1, 1, 1, 1], [1, 1, 1, 1]],
        LOSS_PATH,
        [[0, .1, -.1, 0], [2, 3, -3, 0], [0, 0, 0, 0]],
    ])
    zero = Costs(0, 0, 0, 0)
    net, kinds, duration = price_paths(paths, stop=4, side=1, costs=zero)
    np.testing.assert_allclose(net, [-8, -160, 12])
    assert list(kinds) == [0, 0, 2]  # Known target gap happens before later intrabar low.
    assert list(duration) == [1, 2, 2]
    mirrored = -paths[:, :, [0, 2, 1, 3]]
    short_net, short_kind, _ = price_paths(mirrored, stop=4, side=-1, costs=zero)
    np.testing.assert_allclose(short_net, net)
    np.testing.assert_array_equal(short_kind, kinds)


def test_brackets_are_fill_anchored_and_costs_are_counted_once():
    costs = Costs()
    assert costs.describe()["total_usd"] == pytest.approx(3.22)
    path = np.array([[[0, .2, -.2, .1], [.1, .2, -.2, .1], [.1, .2, -.2, .1]]])
    raw, _, _ = price_paths(path, stop=4, side=1, costs=Costs(0, 0, 0, 0))
    net, kind, _ = price_paths(path, stop=4, side=1, costs=costs)
    assert net[0] == pytest.approx(raw[0] - 3.22)
    assert kind[0] == 1
    # -3.75 touches the fill-anchored stop at -3.5; it does not touch a
    # hypothetical bracket anchored at the unadjusted reference open (-4).
    shifted = np.array([[[0, .2, -.9375, 0], [0, .1, -.1, 0], [0, .1, -.1, 0]]])
    assert price_paths(shifted, stop=4, side=1, costs=costs)[1][0] == 0


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_invalid_costs_are_rejected(bad):
    with pytest.raises(ValueError): Costs(spread_ticks=bad)


@pytest.mark.parametrize("version", MODEL_VERSIONS)
def test_forecast_is_deterministic_probabilistic_and_cannot_authorize_orders(version):
    model = fitted(version)
    forecast = model.forecast(query())
    assert forecast == PathModel.from_dict(json.loads(json.dumps(model.to_dict()))).forecast(query())
    assert forecast["research_action"] == "BUY"
    assert forecast["action"] == "NO_TRADE" and forecast["routing_allowed"] is False
    assert forecast["probability_basis"] == "uncalibrated_model_estimate"
    for side in ("BUY", "SELL"):
        out = forecast["forecasts"][side]
        assert sum(out[name] for name in ("probability_stop", "probability_target", "probability_time_exit")) == pytest.approx(1)
        assert out["probability_net_positive"] + out["probability_net_nonpositive"] == pytest.approx(1)
    assert "samples" not in forecast and '"path":' not in json.dumps(forecast)


def test_high_win_probability_is_insufficient_when_tail_losses_dominate():
    forecast = fitted(losing=18).forecast(query())
    buy = forecast["forecasts"]["BUY"]
    assert buy["probability_net_positive"] == pytest.approx(.95)
    assert buy["expected_net_usd"] < 0
    assert forecast["research_action"] != "BUY"


def test_overlapping_training_labels_cannot_leak_into_prediction():
    model = fitted()
    with pytest.raises(ValueError, match="cutoff"):
        model.forecast(replace(query(), decision_at=model.trained_through))
    bad = model.to_dict()
    bad["samples"][0]["label_end"] = bad["samples"][0]["decision_at"]
    with pytest.raises(ValueError): PathModel.from_dict(bad)


def test_small_sample_has_no_action_and_cluster_uncertainty_is_not_iid_trade_uncertainty():
    forecast = fitted(n=60).forecast(query())
    assert forecast["research_action"] == "NO_TRADE"
    assert forecast["forecasts"]["BUY"]["effective_days"] == pytest.approx(10)
    varied = fitted(losing=30).forecast(query())
    assert varied["forecasts"]["BUY"]["standard_error_usd"] > 0


def test_crps_matches_pairwise_definition():
    y = np.array([-10, 1, 5, 100.0])
    w = np.array([.1, .3, .4, .2])
    brute = np.sum(w * np.abs(y - 4)) - .5 * np.sum(w[:, None] * w * np.abs(y[:, None] - y))
    assert weighted_crps(y, w, 4) == pytest.approx(brute)


def test_splits_reserve_tail_and_purge_boundary_labels():
    from pathlib import Path
    from tools.research_probabilistic_topbot import verify_protocol
    registered = json.loads((Path(__file__).resolve().parents[2] / "docs/topbot-probabilistic-protocol-v2.json").read_text())
    verify_protocol(registered)
    with pytest.raises(ValueError, match="register a new experiment"):
        verify_protocol({**registered, "commission_per_side_usd": 0})
    protocol = {"minimum_total_sessions": 200, "untouched_final_sessions": 60,
                "training_sessions": 60, "calibration_sessions": 20,
                "validation_sessions": 20, "walk_forward_step_sessions": 20}
    days = [(NOW + timedelta(days=i)).date().isoformat() for i in range(200)]
    plan = walk_forward_plan(days, protocol)
    assert len(plan["folds"]) == 3
    assert plan["holdout_sessions"] == days[-60:]
    for fold in plan["folds"]:
        assert not set(plan["holdout_sessions"]) & set(sum(fold.values(), []))
        assert fold["training"][-1] < fold["calibration"][0] < fold["validation"][0]
    assert walk_forward_plan(days[:199], protocol)["status"] == "insufficient_data"
    s = fitted(n=2).samples[0]
    boundary = s.label_end.astimezone(ET).date().isoformat()
    assert purged_partition([s], [s.features.day], next_boundary=boundary) == []


def test_bootstrap_preserves_day_blocks_and_zero_trade_metrics_are_unknown():
    indexes = block_indices(50, 5, repeats=2)
    assert np.all(np.diff(indexes.reshape(2, 10, 5), axis=2) % 50 == 1)
    assert np.array_equal(indexes, block_indices(50, 5, repeats=2))
    summary = summarize([], [str(i) for i in range(30)])
    assert summary["expectancy_usd"] is None
    assert summary["expectancy_ci_lower_usd"] == {"5": None, "10": None}


def test_paired_comparison_keeps_incumbent_pnl_on_non_entry_dates():
    days = [f"2026-08-{i:02d}" for i in range(1, 21)]
    candidate = {"daily": [{"day": day, "net_usd": 0} for day in days]}
    incumbent = {day: 0 for day in days}
    incumbent["2026-08-21"] = -100
    result = paired_daily_bound(candidate, incumbent, days)
    assert result["5"] is not None  # 21 paired days, including the incumbent exit.


def as_rows(rows):
    return [SimpleNamespace(unit="minute", unit_number=5, contract_id=r.contract_id, user_id=r.owner,
                            candle_timestamp=r.timestamp, open_price=r.open, high_price=r.high,
                            low_price=r.low, close_price=r.close, volume=r.volume,
                            live=r.live, is_partial=r.partial) for r in rows]


def test_shadow_without_artifact_has_unknown_probabilities_and_scoped_freshness(tmp_path):
    rows = as_rows(candles(21))
    forecast = explain_shadow(candles=rows, owner="owner", contract_id=CONTRACT, as_of=NOW, root=tmp_path)
    assert forecast["data_status"] == "fresh"
    assert forecast["forecasts"] is None
    assert forecast["probability_basis"] == "unavailable"
    assert forecast["costs"]["total_usd"] == pytest.approx(3.22)
    stale = explain_shadow(candles=rows, owner="owner", contract_id=CONTRACT, as_of=NOW + timedelta(minutes=3), root=tmp_path)
    assert stale["data_status"] == "stale" and stale["forecasts"] is None
    crossed = explain_shadow(candles=rows, owner="other", contract_id=CONTRACT, as_of=NOW, root=tmp_path)
    assert crossed["data_status"] == "invalid" and crossed["forecasts"] is None


def test_shadow_artifact_cannot_claim_calibration_or_change_routing(tmp_path):
    path = model_path("owner", CONTRACT, False, root=tmp_path)
    path.parent.mkdir(parents=True)
    artifact = {"owner_hash": scope_hash("owner"), "contract_id": CONTRACT, "data_live": False,
                "model": fitted().to_dict(), "validation_status": "calibrated", "routing_allowed": True}
    path.write_text(json.dumps(artifact), encoding="utf-8")
    result = explain_shadow(candles=as_rows(candles(21)), owner="owner", contract_id=CONTRACT, as_of=NOW, root=tmp_path)
    assert result["forecasts"] is not None
    assert result["validation_status"] == "unvalidated" and result["routing_allowed"] is False
    assert result["action"] == "NO_TRADE"
    artifact["owner_hash"] = scope_hash("other")
    path.write_text(json.dumps(artifact), encoding="utf-8")
    rejected = explain_shadow(candles=as_rows(candles(21)), owner="owner", contract_id=CONTRACT, as_of=NOW, root=tmp_path)
    assert rejected["forecasts"] is None and rejected["data_status"] == "invalid"


def test_replay_does_not_overlap_positions_or_force_trades():
    model = fitted()
    q = query()
    samples = [Sample(replace(q, decision_at=NOW + i * BAR), WIN_PATH, NOW + (i + 3) * BAR) for i in range(10)]
    ledger = simulate(model, samples, Costs(), [q.day])
    assert [t["decision_at"] for t in ledger] == [(NOW + i * BAR).isoformat() for i in (0, 3, 6, 9)]
    assert simulate(fitted(n=20), samples, Costs(), [q.day]) == []


def test_audit_rejects_duplicate_timestamps_and_mixed_tenants():
    with pytest.raises(ValueError, match="Duplicate"):
        audit_candles(candles(21) + [candles(21)[-1]])
    rows = candles(21)
    rows[-1] = replace(rows[-1], owner="other")
    with pytest.raises(ValueError, match="one owner"):
        audit_candles(rows)


def test_probability_scores_report_inadequate_calibration_instead_of_claiming_success():
    model = fitted()
    sample = Sample(query(), WIN_PATH, NOW + 3 * BAR)
    scores = probability_scores(model, [sample], Costs())
    assert scores["predictions"] == 2
    assert scores["max_calibration_error"] is None
    assert scores["day_block_confidence_intervals"]["brier"]["5"] is None


def test_development_never_builds_features_or_labels_from_reserved_holdout(monkeypatch):
    from app.services import probabilistic_validation as validation
    days = [(NOW + timedelta(days=i)).date().isoformat() for i in range(200)]
    protocol = {"minimum_total_sessions": 200, "untouched_final_sessions": 60,
                "training_sessions": 60, "calibration_sessions": 20,
                "validation_sessions": 20, "walk_forward_step_sessions": 20}
    monkeypatch.setattr(validation, "audit_candles", lambda _: {"eligible_complete_sessions": days})
    cutoff = validation.day_start(days[-60])
    rows = [replace(candles(1)[0], timestamp=cutoff - BAR), replace(candles(1)[0], timestamp=cutoff)]
    calls = []
    def observed(input_rows, **kwargs):
        calls.append(kwargs)
        assert len(input_rows) == 1 and all(r.timestamp < cutoff for r in input_rows)
        return [], {}
    monkeypatch.setattr(validation, "make_samples", observed)
    report, _ = validation.development(rows, protocol)
    assert len(calls) == 2
    assert report["holdout_evaluated"] is False and report["promotion_eligible"] is False


def test_nonfinite_or_extreme_model_features_fail_closed(tmp_path):
    model = fitted()
    with pytest.raises(ValueError, match="numerical domain"):
        model.forecast(replace(query(), values=(1e308, 1, 0, 0)))
    artifact = json.loads(json.dumps(model.to_dict()))
    artifact["samples"][0]["path"][0] = [0, float("nan"), 0, 0]
    with pytest.raises(ValueError): PathModel.from_dict(artifact)
