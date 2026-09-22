"""Synthetic correctness evidence only. Never treat these fixtures as MNQ alpha."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.depth_capture import LocalCapture, CaptureLimit, ReadOnlyMarketClient, active_mnq, replay_events, latest_path, atomic_json
from app.services.depth_research import Event, ResearchBook, L1_NAMES, L2_NAMES, digest
from app.services.depth_model import DepthModel, Example, FEATURE_SETS
from app.services.depth_validation import build_examples, evaluate_fold, confidence, protocol, matched_forecasts, fold_checks, stress_forecasts
from app.services.probabilistic_strategy import Features, ET
from app.services.probabilistic_shadow import scope_hash

NOW = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
CONTRACT = "CON.F.US.MNQ.U26"
OWNER = scope_hash("test-owner")


def event(index=1, *, kind="snapshot", at=NOW, payload=None, **kwargs):
    if payload is None:
        payload = {"complete": True, "bids": [[20000 - .25 * i, 20.] for i in range(5)],
                   "asks": [[20000.25 + .25 * i, 10.] for i in range(5)]}
    return Event(OWNER, CONTRACT, False, "fixture", index, at, int((at - NOW).total_seconds() * 1e9), kind,
                 payload, at, "explicit_snapshot_v1", True, **kwargs)


def ready_book():
    book = ResearchBook(OWNER, CONTRACT)
    book.apply(event())
    for i in range(1, 31):
        book.apply(event(i + 1, kind="depth", at=NOW + timedelta(seconds=i),
                         payload={"type": 2, "price": 20000, "volume": 20.}))
    return book


def test_exact_feature_formulas_and_sweep_costs():
    book = ready_book(); at = NOW + timedelta(seconds=30)
    f = book.features(at)
    assert f["imbalance_1"] == pytest.approx(1 / 3)
    assert f["imbalance_5"] == pytest.approx(1 / 3)
    assert f["weighted_mid_ticks"] == pytest.approx(1 / 6)
    assert f["weighted_imbalance_5"] == pytest.approx(1 / 3)
    assert f["shape_asymmetry"] == pytest.approx(0)
    assert f["concentration_asymmetry"] == pytest.approx(0)
    assert f["ofi_30s"] == 0
    assert f["persistence_30s"] == pytest.approx(1 / 3)
    assert book.sweep("BUY", 1, at)["cost_from_mid_usd"] == .25
    assert book.sweep("BUY", 11, at)["displayed_vwap"] == pytest.approx((10 * 20000.25 + 20000.5) / 11)
    assert book.sweep("BUY", 51, at) is None
    assert book.status(at)["capability"] == "fixture_level2"


def test_unknown_projectx_snapshot_semantics_never_become_actionable():
    book = ResearchBook(OWNER, CONTRACT)
    for i, (side, p) in enumerate(((2, 20000), (2, 19999.75), (1, 20000.25), (1, 20000.5)), 1):
        e = replace(event(i, kind="depth", payload={"type": side, "price": p, "volume": 10}), source="projectx", synthetic=False)
        book.apply(e)
    assert book.status(NOW)["capability"] == "verified_level2"
    assert book.status(NOW)["reason"] == "unverified_snapshot_boundary"
    assert not book.synced and book.features(NOW) is None


@pytest.mark.parametrize("kind", ["disconnect", "reset", "gap", "roll", "end"])
def test_gaps_and_rolls_require_new_complete_snapshot(kind):
    book = ready_book()
    book.apply(event(32, kind=kind, at=NOW + timedelta(seconds=31), payload={}))
    assert book.features(NOW + timedelta(seconds=31)) is None
    assert not book.synced
    book.apply(event(33, at=NOW + timedelta(seconds=32)))
    assert book.synced and book.features(NOW + timedelta(seconds=32)) is None  # Window warmup restarts.


@pytest.mark.parametrize("mutation", ["delete", "cross", "late", "index_gap", "clock", "future", "nan", "negative", "tick"])
def test_bad_book_never_supplies_features(mutation):
    book = ready_book()
    e = event(32, kind="depth", at=NOW + timedelta(seconds=31), payload={"type": 2, "price": 20000, "volume": 20})
    if mutation == "delete": e = replace(e, payload=e.payload | {"volume": 0})
    if mutation == "cross": e = replace(e, payload=e.payload | {"price": 20001})
    if mutation == "late": e = replace(e, provider_at=NOW + timedelta(seconds=29))
    if mutation == "index_gap": e = replace(e, index=33)
    if mutation == "clock": e = replace(e, received_at=NOW + timedelta(seconds=29))
    if mutation == "future": e = replace(e, provider_at=NOW + timedelta(seconds=32))
    if mutation == "nan": e = replace(e, payload=e.payload | {"volume": "NaN"})
    if mutation == "negative": e = replace(e, payload=e.payload | {"volume": -1})
    if mutation == "tick": e = replace(e, payload=e.payload | {"price": 20000.1})
    book.apply(e)
    assert book.features(e.received_at) is None


def test_absolute_updates_duplicates_and_zero_quantities_are_not_deltas():
    book = ready_book()
    at = NOW + timedelta(seconds=31)
    e = event(32, kind="depth", at=at, payload={"type": 2, "price": 20000, "volume": 25, "currentVolume": 2})
    book.apply(e); f = book.features(at)
    assert book.bids[20000] == 25 and f["ofi_30s"] == pytest.approx(5 / 35)
    book.apply(replace(e, index=33))
    assert book.features(at) == f and book.counts["duplicate_absolute_update"] == 1
    book.apply(replace(e, index=34, payload=e.payload | {"volume": 0}, provider_at=at + timedelta(seconds=1), received_at=at + timedelta(seconds=1)))
    assert 20000 not in book.bids


def test_scope_and_freshness_and_future_queries():
    book = ready_book()
    with pytest.raises(ValueError, match="Cross-owner"):
        book.apply(replace(event(32), owner_hash=digest("other")))
    assert book.features(NOW + timedelta(seconds=33)) is None
    assert book.features(NOW + timedelta(seconds=29)) is None


def test_local_capture_rotates_verifies_hashes_and_does_not_overwrite(tmp_path):
    capture = LocalCapture(root=tmp_path, owner_hash=OWNER, max_bytes=20000, segment_bytes=512, synthetic=True)
    for i in range(1, 5):
        e = event(i, at=NOW + timedelta(seconds=i))
        capture.append(contract=CONTRACT, kind=e.kind, payload=e.payload, received_at=e.received_at,
                       provider_at=e.provider_at, source=e.source, monotonic_ns=e.monotonic_ns)
    result = capture.finish()
    assert len(result["segments"]) > 1
    rows = list(replay_events(capture.path))
    assert rows == list(replay_events(capture.path))
    assert len(rows) == result["events"] == 5
    with (capture.path / "000000.ndjson").open("ab") as f:
        f.write(b"corruption")
    with pytest.raises(ValueError, match="integrity"):
        list(replay_events(capture.path))


def test_capture_bounds_and_no_broker_mutation(tmp_path):
    capture = LocalCapture(root=tmp_path, owner_hash=OWNER, max_bytes=4096, max_events=1, synthetic=True)
    capture.append(contract=CONTRACT, kind="connect", payload={})
    with pytest.raises(CaptureLimit): capture.append(contract=CONTRACT, kind="connect", payload={})
    with pytest.raises(CaptureLimit): LocalCapture(root=tmp_path, owner_hash=OWNER, max_bytes=4096)
    capture.finish()
    client = ReadOnlyMarketClient(base_url="https://api.topstepx.com", username="fixture", api_key="fixture")
    for path in ("/api/Order/place", "/api/Order/cancel", "/api/Position/closeContract", "/api/Account/search"):
        with pytest.raises(RuntimeError, match="research permits"):
            client._request_once("POST", path, payload={}, with_auth=True)


def test_roll_selection_never_guesses_nearest_expiry():
    rows = [{"id": CONTRACT, "active_contract": False, "tick_size": .25, "tick_value": .5},
            {"id": "CON.F.US.MNQ.Z26", "active_contract": True, "tick_size": .25, "tick_value": .5}]
    assert active_mnq(rows) == "CON.F.US.MNQ.Z26"
    rows[0]["active_contract"] = True
    with pytest.raises(ValueError): active_mnq(rows)


def examples(n=400):
    samples = []
    for i in range(n):
        at = NOW - timedelta(days=70) + timedelta(days=i // 10, minutes=15 * (i % 10))
        x = float((i % 7) - 3) / 3
        f = Features((x, x / 2, .1, .2), 4., 2., at, at.astimezone(ET).date().isoformat())
        depth = {key: .1 * x for key in L1_NAMES + L2_NAMES}; depth["spread_ticks"] = 1.
        buy = 4. if i % 3 else -8.; sell = -buy - 3.
        samples.append(Example(at, at + timedelta(seconds=900), CONTRACT, f, depth, (buy, sell), (15., 15.), (.25, .25),
                               {1: (buy, sell), 5: (buy, sell), 30: (buy, sell)}))
    return samples


def test_fit_uses_training_only_and_serializes_deterministically():
    samples = examples()
    model = DepthModel.fit(samples, "level2")
    query = replace(samples[-1], at=NOW)
    prediction = model.forecast(query.vector("level2"), at=NOW)
    restored = DepthModel.from_dict(json.loads(json.dumps(model.to_dict())))
    assert restored.forecast(query.vector("level2"), at=NOW) == prediction
    assert prediction["action"] == "NO_TRADE" and prediction["routing_allowed"] is False
    assert prediction["probability_basis"] == "uncalibrated_model_estimate"
    with pytest.raises(ValueError, match="precede"):
        model.forecast(query.vector("level2"), at=model.trained_through)
    broken = model.to_dict(); broken["scale"][0] = 0
    with pytest.raises(ValueError): DepthModel.from_dict(broken)


def test_high_win_rate_negative_expectancy_does_not_force_trade():
    samples = [replace(s, net=(1., -2.) if i % 10 else (-50., 40.)) for i, s in enumerate(examples())]
    model = DepthModel.fit(samples, "candles")
    result = model.forecast(samples[-1].vector("candles"), at=NOW)
    assert result["forecasts"]["BUY"]["probability_net_positive"] > .8
    assert result["forecasts"]["BUY"]["expected_net_usd"] < 0
    assert result["research_action"] != "BUY"


def test_paired_cohort_removes_missing_features_for_every_baseline():
    train = examples(); model = {k: DepthModel.fit(train, k) for k in FEATURE_SETS}
    valid = replace(train[-1], at=NOW)
    bad = replace(valid, depth=valid.depth | {"log_depth": float("nan")})
    chosen, forecasts, excluded = matched_forecasts(model, [valid, bad])
    assert chosen == [valid] and all(len(v) == 1 for v in forecasts.values())
    assert excluded["outside_joint_model_domain"] == 1


def test_minimum_sample_gates_and_serial_intervals():
    result, models = evaluate_fold(examples(20), [], [])
    assert result["status"] == "insufficient_training_paths" and models == {}
    assert confidence({"2026-01-01": 10}) == {"5": None, "10": None}
    assert protocol()["minimum_total_sessions"] == 200
    assert protocol()["untouched_final_sessions"] == 60


def sampled_rows():
    f = ready_book().features(NOW + timedelta(seconds=30))
    rows = []
    for i in range(1801):
        at = NOW + timedelta(seconds=i)
        rows.append({"at": at.isoformat(), "contract_id": CONTRACT, "mid": 20000.125,
                     "features": f, "candle_features": {"values": [0., 0., 0., 0.], "stop": 4., "volatility": 1., "close_at": NOW.isoformat()},
                     "buy_sweep": {"displayed_vwap": 20000.25}, "sell_sweep": {"displayed_vwap": 20000.}})
    return rows


def test_outcomes_are_separate_from_features_and_holdout_is_excluded():
    rows = sampled_rows(); allowed = {NOW.date().isoformat()}
    samples, _ = build_examples(rows, allowed_days=allowed)
    assert len(samples) == 1 and samples[0].net == pytest.approx((-2.72, -2.72))
    assert build_examples(rows, allowed_days=set())[0] == []
    modified = [dict(row) for row in rows]; modified[900]["sell_sweep"] = {"displayed_vwap": 20003.}
    changed, _ = build_examples(modified, allowed_days=allowed)
    assert changed[0].depth == samples[0].depth and changed[0].net != samples[0].net
    modified[400]["mid"] = None
    assert build_examples(modified, allowed_days=allowed)[0] == []


def test_fixture_replay_delay_drop_and_determinism(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from research_depth_topbot import make_fixture, replay
    path, _ = make_fixture(tmp_path)
    base = list(replay(path))
    assert base == list(replay(path))
    assert sum(row["features"] is not None for row in base) == 90
    assert all(row["synthetic"] for row in base)
    assert not any(row["features"] for row in replay(path, delay_ms=3000))
    assert not any(row["features"] for row in replay(path, drop_every=20))


def shadow_candles():
    return [SimpleNamespace(user_id="test-owner", contract_id=CONTRACT, live=False, unit="minute", unit_number=5,
                            candle_timestamp=NOW - timedelta(minutes=5 * (21 - i)),
                            open_price=20000 + .5 * i, high_price=20001 + .5 * i, low_price=19999 + .5 * i,
                            close_price=20000 + .5 * i, volume=100, is_partial=False) for i in range(21)]


def write_latest(tmp_path, *, stale=False, wrong_scope=False):
    at = NOW + timedelta(seconds=30)
    book = ready_book(); status = book.status(at)
    # Loader fixtures simulate an externally verified capture; not market evidence.
    status.update(capability="verified_level2", synthetic=False)
    payload = {"schema": "mnq-depth-latest-v1", "owner_hash": digest("other") if wrong_scope else OWNER,
               "contract_id": CONTRACT, "data_live": False, "capture_id": "fixture",
               "as_of": (at - timedelta(seconds=10) if stale else at).isoformat(), "status": status,
               "features": book.features(at), "source": "projectx", "fixture": False}
    atomic_json(latest_path(OWNER, CONTRACT, False, tmp_path), payload)
    return payload


@pytest.mark.parametrize("state", ["missing", "stale", "owner", "fixture", "nan"])
def test_shadow_missing_stale_or_untrusted_data_abstains(tmp_path, state):
    from app.services.depth_shadow import explain_depth
    if state != "missing":
        payload = write_latest(tmp_path, stale=state == "stale", wrong_scope=state == "owner")
        if state == "fixture":
            payload["fixture"] = True
            atomic_json(latest_path(OWNER, CONTRACT, False, tmp_path), payload)
        if state == "nan":
            payload["status"]["age_seconds"] = float("nan")
            latest_path(OWNER, CONTRACT, False, tmp_path).write_text(json.dumps(payload))
    result = explain_depth(owner="test-owner", contract_id=CONTRACT, candles=shadow_candles(), as_of=NOW + timedelta(seconds=30), root=tmp_path)
    assert result["forecasts"] is None and result["action"] == "NO_TRADE"
    if state in {"owner", "fixture", "nan"}:
        assert result["book_status"] == "invalid"
    if state == "stale":
        assert result["book_status"] == "stale_or_disconnected"
    assert result["routing_allowed"] is False
    json.dumps(result, allow_nan=False)


def test_shadow_pair_forecasts_and_api_schema_never_enable_routing(tmp_path):
    from app.services.depth_shadow import explain_depth
    from app.services.depth_capture import stream_key
    from app.services.probabilistic_strategy import Candle, features
    from app.bot_schemas import BotDepthResearchOut
    payload = write_latest(tmp_path)
    candle = features([Candle.from_row(r) for r in shadow_candles()], as_of=NOW)
    samples = []
    for s in examples():
        at = s.at + timedelta(days=30)
        samples.append(replace(s, at=at, label_end=s.label_end + timedelta(days=30), depth=payload["features"],
                               candle=replace(candle, decision_at=at, day=at.astimezone(ET).date().isoformat())))
    models = {key: DepthModel.fit(samples, key).to_dict() for key in ("level1", "level2")}
    path = tmp_path / "depth-v1/models" / OWNER / (stream_key(CONTRACT, False) + ".json")
    atomic_json(path, {"owner_hash": OWNER, "contract_id": CONTRACT, "data_live": False, "synthetic": False,
                       "protocol_sha256": digest(protocol()), "models": models, "routing_allowed": True, "calibrated": True})
    result = explain_depth(owner="test-owner", contract_id=CONTRACT, candles=shadow_candles(), as_of=NOW + timedelta(seconds=30), root=tmp_path)
    assert result["forecasts"] is not None and result["depth_contribution"] is not None, result
    assert result["routing_allowed"] is False and result["action"] == "NO_TRADE"
    assert result["probability_basis"] == "uncalibrated_model_estimate"
    assert BotDepthResearchOut.model_validate(result).model_dump(mode="json") == result


def test_all_three_baselines_and_ablations_run_on_identical_decisions():
    train = examples()
    validation = []
    for i, sample in enumerate(examples(20)):
        at = NOW + timedelta(days=i // 10, minutes=15 * (i % 10))
        validation.append(replace(sample, at=at, label_end=at + timedelta(seconds=900),
                                  candle=replace(sample.candle, decision_at=at, day=at.astimezone(ET).date().isoformat())))
    result, models = evaluate_fold(train, validation, sorted({s.day for s in validation}))
    assert result["status"] == "evaluated"
    assert set(result["models"]) == set(FEATURE_SETS)
    assert len({r["directional"]["forecasts"] for r in result["models"].values()}) == 1
    assert result["incremental_vs_level1"]["level2"]["brier_improvement_ci"] == {"5": None, "10": None}
    assert all(m.training_examples == 400 for m in models.values())
    checks = fold_checks(result)
    assert checks["positive_net_expectancy"] is False and checks["calibration"] is False
    stressed = stress_forecasts(models["level2"], validation, {}, sorted({s.day for s in validation}))
    assert stressed["forecasts_available"] == 0 and stressed["trading"]["expectancy_usd"] is None


def test_market_only_collector_records_null_gaps_and_subscription_acks(tmp_path, monkeypatch):
    from app.services.depth_capture import collect
    from app.services import projectx_hubs
    class Client:
        def search_contracts(self, **kwargs):
            return [{"id": CONTRACT, "active_contract": True, "tick_size": .25, "tick_value": .5}]
        def get_access_token(self): return "fixture-token-never-persisted"
        def retrieve_bars(self, **kwargs): return []
    class Socket:
        def __init__(self): self.reads = 0; self.sent = []
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def send(self, value): self.sent.append(json.loads(value.rstrip("\x1e")))
        async def recv(self):
            self.reads += 1
            if self.reads == 1: return "{}\x1e"
            if self.reads == 2:
                now = datetime.now(timezone.utc).isoformat()
                frames = [{"type": 3, "invocationId": "2"},
                          {"type": 1, "target": "GatewayDepth", "arguments": [CONTRACT, None]},
                          {"type": 1, "target": "GatewayDepth", "arguments": [CONTRACT, {"timestamp": now, "type": 4, "price": 20000, "volume": 10}]}]
                return "\x1e".join(json.dumps(f) for f in frames) + "\x1e"
            await asyncio.sleep(2)
            return '{"type":6}\x1e'
    socket = Socket()
    monkeypatch.setattr(projectx_hubs, "_open_hub", lambda *args, **kwargs: socket)
    capture = LocalCapture(root=tmp_path, owner_hash=OWNER, max_bytes=20000)
    report = asyncio.run(collect(Client(), capture, seconds=1))
    assert report["evidence"]["subscription_acknowledgements"]["SubscribeContractMarketDepth"] == "accepted"
    events = list(replay_events(capture.path))
    assert any(e.kind == "gap" and e.payload.get("raw", "missing") is None for e in events)
    assert all("fixture-token" not in json.dumps(e.to_dict()) for e in events)
    assert {f.get("target") for f in socket.sent if f.get("target")} == {"SubscribeContractMarketDepth", "SubscribeContractQuotes", "SubscribeContractTrades"}
    assert not report["snapshot_completion_verified"]


def test_retention_stops_capture_without_deleting_records(tmp_path):
    import os
    old = tmp_path / "depth-v1/captures" / OWNER / "old/manifest.json"
    old.parent.mkdir(parents=True); old.write_text("{}")
    timestamp = (NOW - timedelta(days=365)).timestamp()
    os.utime(old, (timestamp, timestamp))
    with pytest.raises(CaptureLimit, match="30 days"):
        LocalCapture(root=tmp_path, owner_hash=OWNER, max_bytes=4096)
    assert old.read_text() == "{}"


def test_existing_path_candidates_are_filtered_without_creating_new_sides():
    from app.services.probabilistic_strategy import PathModel, Sample
    train = examples()
    path = ((0., .6, -.1, .5), (.5, .8, .4, .7), (.7, .9, .6, .8))
    base = {name: PathModel(name, tuple(Sample(s.candle, path, s.label_end) for s in train))
            for name in ("bayesian_cells_v1", "kernel_paths_v1")}
    validation = [replace(s, at=NOW + timedelta(minutes=15 * i), label_end=NOW + timedelta(minutes=15 * (i + 1)),
                          candle=replace(s.candle, decision_at=NOW + timedelta(minutes=15 * i), day=NOW.date().isoformat()))
                  for i, s in enumerate(examples(10))]
    result, _ = evaluate_fold(train, validation, [NOW.date().isoformat()], path_models=base)
    assert set(result["A_existing_candidate_filters"]) == set(base)
    for stats in result["A_existing_candidate_filters"].values():
        assert stats["unfiltered"]["trades"] > 0
        assert stats["level2"]["trades"] <= stats["unfiltered"]["trades"]
        assert stats["incremental_vs_level1_ci"] == {"5": None, "10": None}
