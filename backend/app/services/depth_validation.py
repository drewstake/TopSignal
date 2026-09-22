"""Paired chronological L2 incremental-value experiments, with fail-closed gates.

Labels are sampled executable-quote scenarios, never claimed actual fills. The
reserved final tail is excluded before labels, scaling or fitted models exist.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, time, timedelta
from hashlib import sha256
import json
import platform
from pathlib import Path

import numpy as np

from .depth_capture import atomic_json, replay_events, check_storage
from .depth_model import DepthModel, Example, FEATURE_SETS, VERSION
from .depth_research import canonical, digest, timestamp
from .probabilistic_strategy import Candle, Features, Costs, PathModel, make_samples, ET
from .probabilistic_validation import walk_forward_plan, block_indices, summarize, day_start

ROOT = Path(__file__).resolve().parents[3]
# Filled with the preregistered canonical hash; drift is rejected before replay.
PROTOCOL_SHA256 = "6508fab2be577ae380c8b35b4b159332d22abe48aad62e1da414a076a9313937"
FEED_STRESSES = {"delay_250ms": {"delay_ms": 250}, "delay_1000ms": {"delay_ms": 1000},
                 "delay_3000ms_stale": {"delay_ms": 3000}, "drop_every_20": {"drop_every": 20},
                 "levels_3": {"levels": 3}, "levels_10": {"levels": 10}}


def protocol() -> dict:
    result = json.loads((ROOT / "docs/topbot-depth-protocol-v1.json").read_text(encoding="utf-8"))
    if digest(result) != PROTOCOL_SHA256:
        raise ValueError("Depth protocol drift: register a new version before changing an experiment.")
    return result


def build_examples(rows: list[dict], *, allowed_days: set[str]) -> tuple[list[Example], dict]:
    """Decision uses the first received complete candle and a causal depth window."""
    output = []; rejected = Counter(); used_closes = set()
    for i, row in enumerate(rows):
        at = timestamp(row["at"]); local = at.astimezone(ET); day = local.date().isoformat()
        if day not in allowed_days or not time(9, 35) <= local.time() <= time(15, 30, 59):
            continue
        cf = row.get("candle_features")
        if row.get("features") is None or cf is None:
            rejected["missing_causal_features"] += 1; continue
        key = (row["contract_id"], cf["close_at"])
        if key in used_closes:
            continue
        used_closes.add(key)
        future = rows[i + 1:i + 901]
        if len(future) != 900 or any(r["mid"] is None or r["contract_id"] != row["contract_id"]
            or timestamp(r["at"]) != at + timedelta(seconds=j + 1) for j, r in enumerate(future)):
            rejected["missing_contiguous_outcome"] += 1; continue
        end = at + timedelta(seconds=900)
        if end.astimezone(ET).time() > time(15, 45) or end.astimezone(ET).date() != local.date():
            rejected["session_exit_deadline"] += 1; continue
        stop = float(cf["stop"])
        if not 4 <= stop <= 25:
            rejected["stop_risk_cap"] += 1; continue
        net, duration, costs = [], [], []
        shorts = {h: [] for h in (1, 5, 30)}
        for side, entry_key, exit_key in ((1, "buy_sweep", "sell_sweep"), (-1, "sell_sweep", "buy_sweep")):
            entry = future[0][entry_key]["displayed_vwap"] + side * .25
            costs.append(side * (future[0][entry_key]["displayed_vwap"] - row["mid"]) * 2)
            chosen = future[-1][exit_key]["displayed_vwap"]; seconds = 900
            target = np.ceil(1.5 * stop / .25) * .25
            for second, observation in enumerate(future, 1):
                exit_price = observation[exit_key]["displayed_vwap"]
                move = side * (exit_price - entry)
                if move <= -stop or move >= target:
                    chosen = exit_price if move <= -stop else entry + side * target
                    seconds = second; break
            net.append(2 * (side * (chosen - entry) - .25) - 1.22)
            duration.append(seconds / 60)
            for horizon in shorts:
                quote = future[horizon - 1][exit_key]["displayed_vwap"]
                shorts[horizon].append(2 * (side * (quote - entry) - .25) - 1.22)
        f = Features(tuple(cf["values"]), stop, cf["volatility"], at, day)
        output.append(Example(at, end, row["contract_id"], f, row["features"], tuple(net), tuple(duration), tuple(costs),
                              {k: tuple(v) for k, v in shorts.items()}))
    return output, dict(rejected)


def confidence(values_by_day: dict[str, float], *, family: int = 20) -> dict:
    values = np.asarray([values_by_day[d] for d in sorted(values_by_day)])
    return {str(length): [float(x) for x in np.quantile(values[block_indices(len(values), length, seed=20260922)].mean(axis=1),
                                                     [.05 / (2 * family), 1 - .05 / (2 * family)])]
            if len(values) >= 2 * length else None for length in (5, 10)}


def scores(predictions: list[dict], samples: list[Example], *, horizon: int = 900) -> dict:
    p, y, by_day = [], [], defaultdict(list)
    for prediction, sample in zip(predictions, samples):
        for index, side in enumerate(("BUY", "SELL")):
            prob = prediction["forecasts"][side]["probability_net_positive"]
            observed = (sample.net if horizon == 900 else sample.short_net[horizon])[index] > 0
            p.append(prob); y.append(observed); by_day[sample.day].append((prob - observed) ** 2)
    if not p:
        return {"brier": None, "log_loss": None, "reliability": [], "forecasts": 0, "daily_brier": {}}
    p = np.asarray(p); y = np.asarray(y, dtype=float); safe = np.clip(p, 1e-9, 1 - 1e-9)
    reliability = []
    for i in range(10):
        selected = (p >= i / 10) & ((p < (i + 1) / 10) if i < 9 else (p <= 1))
        reliability.append({"bin": [i / 10, (i + 1) / 10], "n": int(selected.sum()),
                            "predicted": float(p[selected].mean()) if selected.any() else None,
                            "observed": float(y[selected].mean()) if selected.any() else None})
    return {"brier": float(np.mean((p - y) ** 2)), "log_loss": float(np.mean(-y * np.log(safe) - (1 - y) * np.log1p(-safe))),
            "reliability": reliability, "forecasts": len(p), "daily_brier": {d: float(np.mean(v)) for d, v in by_day.items()}}


def ledger(samples: list[Example], proposals: list[str], *, extra_cost: float = 0., vol_boundary: float = 0.) -> list[dict]:
    output = []; daily = defaultdict(float); count = Counter(); busy_until = None; last_entry = None
    for sample, proposal in zip(samples, proposals):
        if proposal == "NO_TRADE" or (busy_until and sample.at < busy_until) or (last_entry and sample.at - last_entry < timedelta(seconds=300)):
            continue
        if count[sample.day] >= 30 or daily[sample.day] - 2 * sample.candle.stop_points - 3.22 - extra_cost < -250:
            continue
        index = 0 if proposal == "BUY" else 1
        net = sample.net[index] - extra_cost
        daily[sample.day] += net; count[sample.day] += 1
        last_entry = sample.at; busy_until = sample.at + timedelta(minutes=sample.duration[index])
        output.append({"day": sample.day, "decision_at": sample.at.isoformat(), "action": proposal, "net_usd": net,
                       "holding_minutes": sample.duration[index], "stop_points": sample.candle.stop_points, "exit": "sampled_quote_proxy",
                       "session_regime": "early_session" if sample.at.astimezone(ET).hour < 12 else "late_session",
                       "volatility_regime": "high_volatility" if sample.candle.volatility > vol_boundary else "low_volatility"})
    return output


def matched_forecasts(models: dict[str, DepthModel], samples: list[Example]):
    valid = []; output = {key: [] for key in models}; excluded = Counter()
    for sample in samples:
        try:
            forecasts = {key: model.forecast(sample.vector(key), at=sample.at) for key, model in models.items()}
        except ValueError:
            excluded["outside_joint_model_domain"] += 1; continue
        valid.append(sample)
        for key, forecast in forecasts.items():
            output[key].append(forecast)
    return valid, output, dict(excluded)


def evaluate_fold(training: list[Example], validation: list[Example], days: list[str], *, path_models: dict[str, PathModel] | None = None):
    # Fixed phase selects nonoverlapping 15-minute outcome windows by actual
    # receive-time decision, avoiding adjacent 5m labels in training.
    nonoverlap = []
    for sample in training:
        if not nonoverlap or sample.at >= nonoverlap[-1].label_end:
            nonoverlap.append(sample)
    if len(nonoverlap) < 300 or len({s.day for s in nonoverlap}) < 20:
        return {"status": "insufficient_training_paths", "paths": len(nonoverlap)}, {}
    models = {key: DepthModel.fit(nonoverlap, key) for key in FEATURE_SETS}
    samples, forecasts, exclusions = matched_forecasts(models, validation)
    boundary = float(np.median([s.candle.volatility for s in nonoverlap]))
    results = {}
    for key, predictions in forecasts.items():
        score = scores(predictions, samples)
        trades = ledger(samples, [p["research_action"] for p in predictions], vol_boundary=boundary)
        pnl = summarize(trades, days, alpha=.05 / 20)
        pnl["multiplicity_configurations"] = 20
        errors = defaultdict(list)
        for sample, prediction in zip(samples, predictions):
            for index, action in enumerate(("BUY", "SELL")):
                errors[sample.day].append(abs(prediction["forecasts"][action]["execution_cost_proxy_usd"] - sample.execution_cost[index]))
        results[key] = {"directional": score, "trading": pnl,
                        "execution": {"mean_absolute_error_usd": float(np.mean([v for values in errors.values() for v in values])) if errors else None,
                                      "daily_mae": {d: float(np.mean(v)) for d, v in errors.items()},
                                      "basis": "One-second displayed sweep cost proxy, not observed fills; passive fill uncertainty unavailable."}}
    comparisons = {}
    if samples:
        for key in models:
            baseline = results["level1"]; other = results[key]
            brier = {d: baseline["directional"]["daily_brier"][d] - v for d, v in other["directional"]["daily_brier"].items()}
            mae = {d: baseline["execution"]["daily_mae"][d] - v for d, v in other["execution"]["daily_mae"].items()}
            base_net = {r["day"]: r["net_usd"] for r in baseline["trading"]["daily"]}
            delta_net = {r["day"]: r["net_usd"] - base_net[r["day"]] for r in other["trading"]["daily"]}
            comparisons[key] = {"brier_improvement_ci": confidence(brier), "execution_mae_improvement_ci": confidence(mae),
                                "daily_net_improvement_ci": confidence(delta_net)}
    filtering = {}
    for name, model in (path_models or {}).items():
        base_actions = [model.forecast(s.candle)["research_action"] for s in samples]
        variants = {"unfiltered": base_actions}
        for key in ("level1", "level2"):
            variants[key] = [a if a != "NO_TRADE" and p["forecasts"][a]["lower_utility_usd"] > 1 else "NO_TRADE"
                             for a, p in zip(base_actions, forecasts[key])]
        filtering[name] = {key: summarize(ledger(samples, actions, vol_boundary=boundary), days, alpha=.05 / 20)
                           for key, actions in variants.items()}
        l1_daily = {r["day"]: r["net_usd"] for r in filtering[name]["level1"]["daily"]}
        filtering[name]["incremental_vs_level1_ci"] = confidence({r["day"]: r["net_usd"] - l1_daily[r["day"]]
                                                                 for r in filtering[name]["level2"]["daily"]})
    short = {}
    for horizon in (1, 5, 30):
        try:
            pair = {key: DepthModel.fit(nonoverlap, key, horizon_seconds=horizon) for key in ("candles", "level1", "level2")}
            chosen, predictions, missing = matched_forecasts(pair, validation)
            short[str(horizon)] = {"status": "diagnostic_only", "excluded": missing,
                                    "scores": {key: scores(p, chosen, horizon=horizon) for key, p in predictions.items()}}
        except ValueError as exc:
            short[str(horizon)] = {"status": "insufficient_classes_or_fit", "reason": str(exc)}
    stresses = {}
    for extra in (1., 3.):
        actions = [max(p["forecasts"], key=lambda a: p["forecasts"][a]["lower_utility_usd"])
                   if max(f["lower_utility_usd"] for f in p["forecasts"].values()) - extra > 1 else "NO_TRADE" for p in forecasts["level2"]]
        stresses[f"extra_cost_{extra}"] = summarize(ledger(samples, actions, extra_cost=extra, vol_boundary=boundary), days, alpha=.05 / 20)
    for penalty in (.5, 2.):
        fitted = DepthModel.fit(nonoverlap, "level2", penalty=penalty)
        selected, predictions, _ = matched_forecasts({"level2": fitted}, samples)
        stresses[f"ridge_{penalty}"] = scores(predictions["level2"], selected)
    return {"status": "evaluated", "training_paths": len(nonoverlap), "training_sessions": len({s.day for s in nonoverlap}),
            "paired_decisions": len(samples), "excluded": exclusions, "models": results, "incremental_vs_level1": comparisons,
            "A_existing_candidate_filters": filtering, "short_horizon_diagnostics": short, "stresses": stresses}, models


def stress_forecasts(model: DepthModel, samples: list[Example], observed: dict, days: list[str]) -> dict:
    """Keep decision times and original realized outcomes fixed; delay only inputs.

    Missing/stale stressed observations are actual abstentions, not zero feature
    vectors. Only the supported subset has a probability score; disclose attrition.
    """
    proposals = []; selected = []; predictions = []; reasons = Counter()
    for sample in samples:
        row = observed.get((sample.contract_id, sample.at.isoformat()))
        try:
            if row is None or row["features"] is None or row.get("candle_features") is None:
                raise ValueError("Unavailable stressed input.")
            altered = replace(sample, depth=row["features"], candle=replace(sample.candle, values=tuple(row["candle_features"]["values"])))
            prediction = model.forecast(altered.vector(model.feature_set), at=sample.at)
            predictions.append(prediction); selected.append(sample); proposals.append(prediction["research_action"])
        except (ValueError, KeyError, TypeError):
            reasons["missing_stale_or_outside_domain"] += 1; proposals.append("NO_TRADE")
    trades = ledger(samples, proposals)
    return {"eligible_decisions": len(samples), "forecasts_available": len(selected), "abstentions": dict(reasons),
            "probability_scores_supported_subset": scores(predictions, selected),
            "trading": summarize(trades, days, alpha=.05 / 20), "economic_robustness_proven": False}


def fold_checks(metrics: dict) -> dict:
    """Explicit missing evidence remains false, including unavailable intervals."""
    checks = {"paired_brier_improvement": False, "log_loss_no_worse": False, "paired_daily_net_improvement": False,
              "positive_net_expectancy": False, "calibration": False, "drawdown_and_tail": False,
              "concentration_and_outlier_removal": False, "regimes": False, "execution_proxy_improvement": False,
              "filter_improvement": False, "cost_and_feed_stresses": False}
    if metrics.get("status") != "evaluated" or not metrics.get("paired_decisions"):
        return checks
    def positive_ci(values):
        return len(values) == 2 and all(v is not None and v[0] > 0 for v in values.values())
    candidate, baseline = metrics["models"]["level2"], metrics["models"]["level1"]
    comparison = metrics["incremental_vs_level1"]["level2"]
    trading = candidate["trading"]
    checks["paired_brier_improvement"] = positive_ci(comparison["brier_improvement_ci"])
    checks["log_loss_no_worse"] = candidate["directional"]["log_loss"] <= baseline["directional"]["log_loss"]
    checks["paired_daily_net_improvement"] = positive_ci(comparison["daily_net_improvement_ci"])
    checks["execution_proxy_improvement"] = positive_ci(comparison["execution_mae_improvement_ci"])
    intervals = trading.get("expectancy_ci_lower_usd", {})
    checks["positive_net_expectancy"] = len(intervals) == 2 and all(v is not None and v > 0 for v in intervals.values())
    bins = candidate["directional"]["reliability"]
    populated = [b for b in bins if b["n"]]
    checks["calibration"] = bool(populated) and all(b["n"] >= 30 and abs(b["predicted"] - b["observed"]) <= .05 for b in populated)
    # Metric names are shared with the original validator; unobserved metrics
    # cannot quietly satisfy an acceptance condition.
    checks["drawdown_and_tail"] = (trading.get("closed_equity_max_drawdown_usd") is not None and trading["closed_equity_max_drawdown_usd"] <= 1000
                                     and trading.get("expected_shortfall_95_usd") is not None and trading["expected_shortfall_95_usd"] <= 100)
    checks["concentration_and_outlier_removal"] = (trading.get("net_without_best_five_days_usd") is not None and trading["net_without_best_five_days_usd"] > 0
        and trading.get("net_without_best_five_trades_usd") is not None and trading["net_without_best_five_trades_usd"] > 0
        and trading.get("best_day_share_positive_pnl") is not None and trading["best_day_share_positive_pnl"] <= .2)
    regimes = trading.get("regimes", {})
    checks["regimes"] = len(regimes) == 4 and all(r.get("trades", 0) >= 30 and r.get("mean_net_usd") is not None and r["mean_net_usd"] >= 0 for r in regimes.values())
    filters = metrics.get("A_existing_candidate_filters", {})
    checks["filter_improvement"] = len(filters) == 2 and all(positive_ci(r["incremental_vs_level1_ci"]) for r in filters.values())
    feeds = metrics.get("feed_stresses", {})
    costs = [v for k, v in metrics["stresses"].items() if k.startswith("extra_cost")]
    checks["cost_and_feed_stresses"] = (len(feeds) == 6 and all(v["forecasts_available"] > 0 and v["trading"].get("net_usd", -1) >= 0 for v in feeds.values())
                                             and bool(costs) and all(v.get("net_usd", -1) > 0 for v in costs))
    return checks


def run_experiment(*, captures: list[Path], sqlite_path: Path | None, owner_hash: str | None, root: Path) -> dict:
    from research_depth_topbot import replay
    p = protocol()
    manifests = []
    coverage = defaultdict(set); candle_minutes = defaultdict(set); day_contracts = defaultdict(set); reasons = Counter()
    snapshots = 0; usable = 0; fixture_count = 0
    for path in captures:
        m = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        manifests.append(m)
        if owner_hash and m["owner_hash"] != owner_hash:
            raise ValueError("Capture does not belong to selected owner.")
        fixture_count += bool(m["synthetic"])
        for event in replay_events(path):
            if event.kind == "candle":
                raw = event.payload
                try:
                    c = Candle(timestamp(raw["timestamp"]), raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"],
                               event.contract_id, event.owner_hash, event.data_live, raw["is_partial"])
                    local = c.timestamp.astimezone(ET); day = local.date().isoformat()
                    if c.valid() and local.weekday() < 5 and time(9, 30) <= local.time() < time(15, 45):
                        candle_minutes[day].add(local.hour * 60 + local.minute)
                        day_contracts[day].add(event.contract_id)
                except (KeyError, TypeError, ValueError):
                    reasons["invalid_candle_payload"] += 1
        for row in replay(path):
            snapshots += 1
            now = timestamp(row["at"]); local = now.astimezone(ET); day = local.date().isoformat()
            reasons[row["status"]["reason"]] += 1
            if row["features"] is not None:
                usable += 1
                if local.weekday() < 5 and time(9, 30) <= local.time() < time(15, 45):
                    coverage[day].add(now)
                    day_contracts[day].add(row["contract_id"])
    if len({(m["owner_hash"], m["data_live"]) for m in manifests}) > 1:
        raise ValueError("Never pool owners or data subscriptions.")
    days = sorted(d for d, times in coverage.items() if len(times) >= .99 * 22500
                  and set(range(570, 945, 5)) <= candle_minutes[d] and len(day_contracts[d]) == 1)
    plan = walk_forward_plan(days, p)
    audit = {"captures": len(captures), "synthetic_captures": fixture_count, "raw_events": sum(m["events"] for m in manifests),
             "sampled_seconds": snapshots, "usable_depth_seconds": usable, "independent_paired_sessions": len(days),
             "required_sessions": 200, "invalid_or_warmup_reasons": dict(reasons), "complete_session_dates": days}
    legacy = None
    if sqlite_path:
        from research_probabilistic_topbot import read_candles
        from .probabilistic_validation import audit_candles
        rows, _, fingerprint, observations = read_candles(sqlite_path, "CON.F.US.MNQ.U26", owner_hash)
        legacy = {"candles": audit_candles(rows), "candle_sha256": fingerprint, "observations": observations,
                  "note": "Historical audit only. Missing original candle receipt times and depth; not joined to future captures."}
    result = {"protocol_version": p["protocol_version"], "protocol_sha256": digest(p), "audit": audit,
              "legacy_snapshot_audit": legacy, "plan": plan, "folds": [], "status": "insufficient_data",
              "example_rejections": {},
              "promotion": False, "routing_allowed": False, "holdout_evaluated": False,
              "remaining_evidence": ["Verified multi-level entitlement and complete snapshot/update semantics.",
                 "200 paired complete sessions; 300 nonoverlapping training paths and 20 effective days.",
                 "Incremental value over Level 1, unseen 60-session final test, all parent gates, and 20 forward Dry Run sessions.",
                 "Actual execution, latency and intratrade drawdown verification; quote proxies cannot establish fills."]}
    fitted_models = {}
    if plan["status"] == "ready" and not fixture_count:
        allowed = set(days) - set(plan["holdout_sessions"])
        examples = []; candles = {}; example_rejections = Counter()
        for path in captures:
            # Filter reserved dates BEFORE labels or models. Audit only inspected coverage.
            rows = [r for r in replay(path) if timestamp(r["at"]).astimezone(ET).date().isoformat() in allowed]
            built, counts = build_examples(rows, allowed_days=allowed)
            examples.extend(built)
            example_rejections.update(counts)
            for e in replay_events(path):
                if e.kind == "candle" and e.received_at.astimezone(ET).date().isoformat() in allowed:
                    b = e.payload; t = timestamp(b["timestamp"])
                    candles.setdefault((e.contract_id, t), Candle(t, b["open"], b["high"], b["low"], b["close"], b["volume"],
                                                                e.contract_id, e.owner_hash, e.data_live, b["is_partial"]))
        examples.sort(key=lambda s: s.at)
        result["example_rejections"] = dict(example_rejections)
        keys = [(s.contract_id, s.at.replace(minute=s.at.minute // 5 * 5, second=0, microsecond=0)) for s in examples]
        if len(set(keys)) != len(keys):
            raise ValueError("Overlapping captures need an explicit provenance decision; no duplicate sampling.")
        paths = []
        for contract in sorted({key[0] for key in candles}):
            stream = [candles[key] for key in sorted(candles) if key[0] == contract]
            paths.extend(make_samples(stream, stride=3)[0])
        paths.sort(key=lambda s: s.features.decision_at)
        for fold in plan["folds"]:
            training = [s for s in examples if s.day in fold["training"] and s.label_end < day_start(fold["calibration"][0])]
            validation = [s for s in examples if s.day in fold["validation"]]
            train_paths = [s for s in paths if s.features.day in fold["training"] and s.label_end < day_start(fold["calibration"][0])]
            base_models = {key: PathModel(key, tuple(train_paths[-4096:])) for key in ("bayesian_cells_v1", "kernel_paths_v1")} if len(train_paths) >= 2 else {}
            try:
                metrics, models = evaluate_fold(training, validation, fold["validation"], path_models=base_models)
            except ValueError as exc:
                metrics, models = {"status": "fit_unavailable", "reason": str(exc)}, {}
            if "level2" in models:
                wanted = {(s.contract_id, s.at.isoformat()) for s in validation}
                metrics["feed_stresses"] = {}
                for name, kwargs in FEED_STRESSES.items():
                    observed = {}
                    for path in captures:
                        for row in replay(path, **kwargs):
                            key = (row["contract_id"], row["at"])
                            if key in wanted:
                                observed[key] = row
                    metrics["feed_stresses"][name] = stress_forecasts(models["level2"], validation, observed, fold["validation"])
            metrics["acceptance_checks"] = fold_checks(metrics)
            result["folds"].append({"partition": fold, "results": metrics})
            fitted_models.update({f"fold_{len(result['folds'])}_{key}": value.to_dict() for key, value in models.items()})
        result["status"] = "development_only"
    # Deterministic feed perturbations operate on raw receive times and missing
    # event indices. A failed stress cannot become a favorable zero-loss trial.
    stress = {}
    for name, kwargs in FEED_STRESSES.items():
        total = valid = 0
        for path in captures:
            for row in replay(path, **kwargs):
                total += 1; valid += row["features"] is not None
        stress[name] = {"sampled_seconds": total, "usable_seconds": valid, "economic_robustness_proven": False}
    result["feed_stresses"] = stress
    result["acceptance"] = {"minimum_200_sessions": len(days) >= 200,
                            "minimum_200_validation_trades": sum(f["results"].get("models", {}).get("level2", {}).get("trading", {}).get("trades", 0) for f in result["folds"]) >= 200,
                            "minimum_three_supported_folds": len(result["folds"]) >= 3 and all(f["results"]["status"] == "evaluated" for f in result["folds"]),
                            "all_incremental_value_checks": bool(result["folds"]) and all(all(f["results"]["acceptance_checks"].values()) for f in result["folds"]),
                            "verified_unseen_holdout": False, "minimum_200_final_trades": False,
                            "twenty_forward_dry_run_sessions": False, "actual_execution_and_intratrade_drawdown": False,
                            "parent_acceptance": False}
    sources = sorted((ROOT / "backend/app").rglob("*.py")) + [ROOT / "backend/requirements.txt",
        ROOT / "backend/tools/research_depth_topbot.py", ROOT / "backend/tools/research_probabilistic_topbot.py"]
    result["code_sha256"] = digest({str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in sources})
    result["source_sha256"] = digest(manifests)
    result["runtime"] = {"python": platform.python_version(), "numpy": np.__version__}
    key = digest(result)
    destination = root / "depth-v1/experiments" / key
    destination.mkdir(parents=True, exist_ok=True)
    for name, payload in (("report.json", result), ("models.json", fitted_models)):
        file = destination / name
        if file.exists() and file.read_bytes() != canonical(payload):
            raise ValueError("Immutable experiment contents differ.")
        if not file.exists():
            check_storage(root, len(canonical(payload)))
            atomic_json(file, payload)
    return result | {"experiment_id": key}
