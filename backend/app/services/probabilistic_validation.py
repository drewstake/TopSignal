"""Chronological research evaluation. No broker/network/database access.

Holdout outcomes are intentionally absent from development reports. A future
final test needs independently verified unseen provenance; this module cannot
turn a favorable metric into trading permission.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
import math
from typing import Sequence

import numpy as np

from .probabilistic_strategy import (
    BAR, ET, HORIZON, MODEL_VERSIONS, Candle, Costs, PathModel, Sample,
    make_samples, price_paths, weighted_crps,
)
from .probabilistic_protocol import assert_protocol, protocol as registered_protocol


def audit_candles(rows: Sequence[Candle]) -> dict:
    identities = {(r.owner, r.live) for r in rows}
    if rows and len(identities) != 1:
        raise ValueError("Research requires exactly one owner and data subscription.")
    if any(b.timestamp <= a.timestamp for a, b in zip(rows, rows[1:])):
        raise ValueError("Duplicate or nonchronological candles must be resolved before research.")
    days: dict[str, set[int]] = {}
    for row in rows:
        local = row.timestamp.astimezone(ET)
        if row.valid() and local.weekday() < 5 and time(9, 30) <= local.time() < time(15, 45):
            days.setdefault(local.date().isoformat(), set()).add(local.hour * 60 + local.minute)
    expected = set(range(570, 945, 5))
    eligible = sorted(day for day, minutes in days.items() if expected <= minutes)
    return {"rows": len(rows), "invalid_or_partial_rows": sum(not r.valid() for r in rows),
            "first": rows[0].timestamp.isoformat() if rows else None, "last": rows[-1].timestamp.isoformat() if rows else None,
            "delivery_contracts": sorted({r.contract_id for r in rows}),
            "observed_regular_session_dates": len(days), "eligible_complete_sessions": eligible,
            "incomplete_regular_sessions": len(days) - len(eligible),
            "gap_intervals_including_closures": sum(b.timestamp - a.timestamp != BAR for a, b in zip(rows, rows[1:])),
            "required_session_bars": 75,
            "note": "Full 09:30–15:45 ET coverage; early-close days excluded. Feature warmup is checked separately."}


def walk_forward_plan(days: list[str], protocol: dict) -> dict:
    minimum = protocol["minimum_total_sessions"]
    holdout_count = protocol["untouched_final_sessions"]
    if len(days) < minimum:
        return {"status": "insufficient_data", "required_sessions": minimum, "available_sessions": len(days),
                "folds": [], "holdout_sessions": [], "holdout_evaluated": False}
    development, holdout = days[:-holdout_count], days[-holdout_count:]
    train, calibration, validation = (protocol[key] for key in
                                      ("training_sessions", "calibration_sessions", "validation_sessions"))
    folds = []
    for start in range(0, len(development) - train - calibration - validation + 1,
                       protocol["walk_forward_step_sessions"]):
        folds.append({"training": development[start:start + train],
                      "calibration": development[start + train:start + train + calibration],
                      "validation": development[start + train + calibration:start + train + calibration + validation]})
    return {"status": "ready", "folds": folds, "holdout_sessions": holdout,
            "holdout_evaluated": False, "required_sessions": minimum, "available_sessions": len(days)}


def day_start(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=ET)


def purged_partition(samples: Sequence[Sample], days: list[str], *, next_boundary: str | None = None) -> list[Sample]:
    chosen = set(days)
    return [s for s in samples if s.features.day in chosen and
            (next_boundary is None or s.label_end < day_start(next_boundary))]


def probability_scores(model: PathModel, samples: Sequence[Sample], costs: Costs) -> dict:
    brier, logs, crps, probabilities, outcomes = [], [], [], [], []
    score_days = []
    paths = model.paths
    for sample in samples:
        w = model.weights(sample.features)
        # A predeclared side on the non-overlapping 15-minute lattice: never
        # count complementary BUY/SELL outcomes as independent evidence.
        minute = sample.features.decision_at.astimezone(ET).minute
        if minute % 15:
            continue
        for side in (1,):
            scale = sample.features.volatility * math.sqrt(HORIZON)
            values, _, _ = price_paths(paths, stop=sample.features.stop_points, scale=scale, side=side, costs=costs)
            observed = float(price_paths(np.asarray([sample.path]), stop=sample.features.stop_points,
                                        scale=scale, side=side, costs=costs)[0][0])
            p = float(np.sum(w[values > 0]))
            outcome = int(observed > 0)
            brier.append((p - outcome) ** 2)
            logs.append(-math.log(max(1e-12, p if outcome else 1 - p)))
            crps.append(weighted_crps(values, w, observed))
            probabilities.append(p)
            outcomes.append(outcome)
            score_days.append(sample.features.day)
    bins = []
    for index in range(10):
        selected = [i for i, p in enumerate(probabilities) if min(9, int(p * 10)) == index]
        if selected:
            mean_p = float(np.mean([probabilities[i] for i in selected]))
            observed_rate = float(np.mean([outcomes[i] for i in selected]))
            bins.append({"lower": index / 10, "upper": (index + 1) / 10, "n": len(selected),
                         "predicted": mean_p, "observed": observed_rate,
                         "absolute_error": abs(mean_p - observed_rate)})
    adequate = [b for b in bins if b["n"] >= 30]
    intervals = {}
    unique_days = sorted(set(score_days))
    for metric, values in (("brier", brier), ("crps_usd", crps)):
        intervals[metric] = {}
        totals = np.asarray([sum(value for value, d in zip(values, score_days) if d == day) for day in unique_days])
        counts = np.asarray([score_days.count(day) for day in unique_days])
        for length in (5, 10):
            bounds = None
            if len(unique_days) >= 2 * length:
                indexes = block_indices(len(unique_days), length)
                draws = totals[indexes].sum(axis=1) / counts[indexes].sum(axis=1)
                bounds = [float(v) for v in np.quantile(draws, [.05 / 12, 1 - .05 / 12])]
            intervals[metric][str(length)] = bounds
    return {"predictions": len(probabilities), "brier": float(np.mean(brier)) if brier else None,
            "log_loss": float(np.mean(logs)) if logs else None,
            "crps_usd": float(np.mean(crps)) if crps else None, "reliability": bins,
            "max_calibration_error": max(b["absolute_error"] for b in adequate) if len(adequate) >= 3 else None,
            "day_block_confidence_intervals": intervals,
            "daily_scores": [{"day": day, "n": score_days.count(day),
                              "brier": float(np.mean([v for v, d in zip(brier, score_days) if d == day])),
                              "crps_usd": float(np.mean([v for v, d in zip(crps, score_days) if d == day]))}
                             for day in unique_days],
            "interpretation": "Unvalidated forecasts, scored on later sessions. Fitting a mixture does not establish calibration."}


def calibrate(model: PathModel, samples: Sequence[Sample], costs: Costs, grid: list[float]) -> tuple[PathModel, list[dict]]:
    if not samples:
        raise ValueError("No calibration labels; cannot select a distribution mixture.")
    attempts = []
    for mixture in grid:
        candidate = replace(model, pool_mix=mixture)
        ledger = simulate(candidate, samples, costs, sorted({s.features.day for s in samples}))
        attempts.append({"pool_mix": mixture, "net_rule_pnl": sum(t["net_usd"] for t in ledger), "trades": len(ledger)})
    # Prefer more shrinkage on exactly tied scores.
    selected = max(attempts, key=lambda row: (row["net_rule_pnl"], row["pool_mix"]))
    return replace(model, pool_mix=selected["pool_mix"]), attempts


def simulate(model: PathModel, samples: Sequence[Sample], costs: Costs, days: list[str], *,
             rule: str = "model", trade_through_ticks: int = 0) -> list[dict]:
    """Evaluate each new close; one position, fixed size, unchanged daily limits."""
    daily = dict.fromkeys(days, 0.0)
    counts = dict.fromkeys(days, 0)
    busy_until = None
    last_entry = None
    ledger = []
    vol_boundary = float(np.median([s.features.volatility for s in model.samples]))
    for sample in samples:
        f = sample.features
        if (busy_until is not None and f.decision_at < busy_until) or (
            last_entry is not None and (f.decision_at - last_entry).total_seconds() < registered_protocol()["cooldown_seconds"]
        ):
            continue
        if counts.get(f.day, 0) >= registered_protocol()["max_entries_per_day"]:
            continue
        # Protective risk is budgeted before entry; gaps can still exceed it.
        # The bracket is relative to the filled entry. Budget the stop, round
        # trip fees and adverse stop exit; entry slippage is already in entry.
        stop_risk = round(2 * f.stop_points + costs.describe()["fees_usd"] + 2 * costs.adverse_points_per_side, 8)
        if daily.get(f.day, 0) - stop_risk <= -registered_protocol()["max_daily_loss_usd"]:
            continue
        if rule == "model":
            action = model.forecast(f, costs)["research_action"]
        else:
            action = {"always_long": "BUY", "always_short": "SELL", "flat": "NO_TRADE"}[rule]
        if action == "NO_TRADE":
            continue
        net, kind, duration = price_paths(np.asarray([sample.path]), stop=f.stop_points,
                                         scale=f.volatility * math.sqrt(HORIZON), trade_through_ticks=trade_through_ticks,
                                         side=1 if action == "BUY" else -1, costs=costs)
        bars = int(duration[0])
        entry_at = sample.label_end - HORIZON * BAR
        busy_until = entry_at + bars * BAR
        last_entry = entry_at
        daily[f.day] = daily.get(f.day, 0) + float(net[0])
        counts[f.day] = counts.get(f.day, 0) + 1
        ledger.append({"decision_at": f.decision_at.isoformat(), "day": f.day, "action": action,
                       "net_usd": float(net[0]), "exit": ("stop", "time", "target")[int(kind[0])],
                       "net_r": float(net[0]) / (2 * f.stop_points),
                       "holding_minutes": bars * 5, "stop_points": f.stop_points,
                       "session_regime": "early_session" if f.decision_at.astimezone(ET).hour < 12 else "late_session",
                       "volatility_regime": "high_volatility" if f.volatility > vol_boundary else "low_volatility"})
    return ledger


def block_indices(n: int, length: int, repeats: int | None = None, seed: int = 20260921) -> np.ndarray:
    """Non-circular moving day blocks; no artificial end-to-start adjacency."""
    if n <= 0 or length <= 0:
        raise ValueError("Bootstrap needs nonempty days and a positive block length.")
    rng = np.random.default_rng(seed)
    repeats = repeats or registered_protocol()["bootstrap_replicates"]
    length = min(length, n)
    starts = rng.integers(0, n - length + 1, size=(repeats, math.ceil(n / length)))
    return (starts[:, :, None] + np.arange(length)).reshape(repeats, -1)[:, :n]


def summarize(ledger: list[dict], days: list[str], *, alpha: float = 0.05 / 6) -> dict:
    values = np.asarray([t["net_usd"] for t in ledger])
    day_net = np.asarray([sum(t["net_usd"] for t in ledger if t["day"] == day) for day in days])
    day_count = np.asarray([sum(t["day"] == day for t in ledger) for day in days])
    equity = np.concatenate(([0.0], np.cumsum(values)))
    drawdown = float(np.max(np.maximum.accumulate(equity) - equity))
    lower_bounds = {}
    for length in (5, 10):
        lower = None
        if len(days) >= 2 * length and len(values):
            indexes = block_indices(len(days), length)
            counts = day_count[indexes].sum(axis=1)
            net = day_net[indexes].sum(axis=1)
            if np.all(counts > 0):
                lower = float(np.quantile(net / counts, alpha / 2))
        lower_bounds[str(length)] = lower
    positive_days = day_net[day_net > 0]
    regime_metrics = {}
    for label in ("early_session", "late_session", "low_volatility", "high_volatility"):
        subset = [t["net_usd"] for t in ledger if label in (t["session_regime"], t["volatility_regime"])]
        regime_metrics[label] = {"trades": len(subset), "mean_net_usd": float(np.mean(subset)) if subset else None}
    return {"trades": len(values), "net_usd": float(values.sum()),
            "expectancy_usd": float(np.mean(values)) if len(values) else None,
            "win_rate": float(np.mean(values > 0)) if len(values) else None,
            "closed_equity_max_drawdown_usd": drawdown,
            "worst_trade_usd": float(values.min()) if len(values) else None,
            "expected_shortfall_95_usd": float(-np.mean(np.sort(values)[:max(1, math.ceil(len(values) * .05))])) if len(values) else None,
            "net_without_best_five_trades_usd": float(values.sum() - np.sort(values)[-5:].sum()) if len(values) > 5 else None,
            "net_without_best_five_days_usd": float(day_net.sum() - np.sort(day_net)[-5:].sum()) if len(days) > 5 else None,
            "best_day_share_positive_pnl": float(positive_days.max() / positive_days.sum()) if len(positive_days) else None,
            "turnover_contract_sides": 2 * len(values),
            "exposure_minutes": sum(t["holding_minutes"] for t in ledger),
            "exposure_fraction": sum(t["holding_minutes"] for t in ledger) / (375 * len(days)) if days else None,
            "expectancy_ci_lower_usd": lower_bounds, "confidence_familywise_alpha": 0.05,
            "multiplicity_configurations": 6, "regimes": regime_metrics,
            "daily": [{"day": day, "net_usd": float(net), "trades": int(count)}
                      for day, net, count in zip(days, day_net, day_count)],
            "limitations": ["Drawdown is closed-equity; intratrade mark-to-market drawdown is not established.",
                            "Block confidence intervals assume weak dependence; few-day diagnostics do not support inference."]}


def paired_daily_bound(candidate: dict, incumbent_daily: dict[str, float], days: list[str], *, alpha: float | None = None) -> dict:
    by_day = {row["day"]: row["net_usd"] for row in candidate["daily"]}
    # Incumbent positions may exit on another date inside the same test window.
    # Include those P&Ls rather than silently discarding a loss or a gain.
    days = sorted(set(days) | set(incumbent_daily))
    diff = np.asarray([by_day.get(day, 0) - incumbent_daily.get(day, 0) for day in days])
    alpha = alpha if alpha is not None else registered_protocol()["familywise_alpha"] / registered_protocol()["candidate_configuration_count"]
    return {str(length): float(np.quantile(diff[block_indices(len(days), length)].mean(axis=1), alpha / 2))
            if len(days) >= 2 * length else None for length in (5, 10)}


def development(rows: Sequence[Candle], protocol: dict, *, diagnostic: bool = False, progress=None) -> tuple[dict, dict[str, PathModel]]:
    assert_protocol(protocol)
    audit = audit_candles(rows)
    plan = walk_forward_plan(audit["eligible_complete_sessions"], protocol)
    report = {"data_audit": audit, "plan": plan, "mode": "diagnostic" if diagnostic else "development",
              "promotion_eligible": False, "selected_for_default": None, "holdout_evaluated": False,
              "status": "insufficient_data" if plan["status"] != "ready" else "development_only",
              "blockers": ["Final untouched holdout has not been evaluated.",
                           "Forward Dry Run and execution parity are not verified.",
                           "Reviewed installation is required; research never promotes automatically."],
              "folds": []}
    if plan["status"] != "ready":
        report["blockers"].insert(0, f"Need 200 complete sessions; found {len(audit['eligible_complete_sessions'])}.")
        if not diagnostic:
            return report, {}
        days = audit["eligible_complete_sessions"]
        if len(days) < 3:
            report["blockers"].append("Even a three-part diagnostic cannot be formed.")
            return report, {}
        n_train = max(1, len(days) * 3 // 5)
        n_cal = max(1, (len(days) - n_train) // 2)
        folds = [{"training": days[:n_train], "calibration": days[n_train:n_train + n_cal],
                  "validation": days[n_train + n_cal:]}]
        report["diagnostic_partition"] = folds[0]
    else:
        folds = plan["folds"]
    # Cut before generating ANY holdout path or feature. Only timestamp coverage
    # and fingerprints may include the reserved tail during development.
    if plan["holdout_sessions"]:
        rows = [r for r in rows if r.timestamp < day_start(plan["holdout_sessions"][0])]
    train_samples, train_rejections = make_samples(rows, stride=protocol["training_label_stride_bars"])
    # Simulate each live 5m close; probability scoring independently selects its 15m lattice.
    population = {}
    all_samples, rejections = make_samples(rows, stride=1, population_by_day=population)
    report["population_by_session"] = population
    report["population_definition"] = "MNQ regular session, 2020 onward; sigma*sqrt(3) <= 25 points before tick rounding"
    report["label_rejections"] = rejections
    report["training_label_rejections"] = train_rejections
    # These transforms depend only on the fixed input history, not on a model
    # or fold. Build once, then apply the same chronological partition below.
    stressed_samples = {}
    for name, options in (
        ("delay_one_bar", {"delay_bars": 1}),
        ("stop_multiplier_0_75", {"stop_multiplier": .75}),
        ("stop_multiplier_1_25", {"stop_multiplier": 1.25}),
        ("missing_every_20th_bar", {}),
        ("unknown_volume", {}),
    ):
        stress_rows = ([r for i, r in enumerate(rows) if i % 20 != 0] if name == "missing_every_20th_bar"
                       else [replace(r, volume=None) for r in rows] if name == "unknown_volume" else rows)
        stressed_samples[name] = make_samples(stress_rows, stride=1, **options)
        if progress is not None:
            progress(f"Prepared {name} scenarios")
    del stress_rows
    costs = Costs()
    exported: dict[str, PathModel] = {}
    for fold_index, fold in enumerate(folds, 1):
        if progress is not None:
            progress(f"Fold {fold_index}/{len(folds)}: validation {fold['validation'][0]} through {fold['validation'][-1]}")
        train = purged_partition(train_samples, fold["training"], next_boundary=fold["calibration"][0])
        calibration = purged_partition(all_samples, fold["calibration"], next_boundary=fold["validation"][0])
        test = purged_partition(all_samples, fold["validation"])
        entry = {"sessions": fold, "training_paths": len(train), "calibration_paths": len(calibration),
                 "validation_paths": len(test), "models": {}}
        totals = {key: sum(population.get(day, {}).get(key, 0) for day in fold["validation"])
                  for key in ("candidate_windows", "accepted", "volatility_cap", "other_rejections")}
        totals["cap_rejection_share"] = totals["volatility_cap"] / totals["candidate_windows"] if totals["candidate_windows"] else None
        entry["validation_population"] = totals
        report["folds"].append(entry)
        if len(train) < 2 or not calibration or not test:
            entry["status"] = "insufficient_labels"
            continue
        for version in MODEL_VERSIONS:
            model = PathModel(version, tuple(train))
            attempts = []
            if version != "empirical_pool_v1":
                model, attempts = calibrate(model, calibration, costs, protocol["calibration_pool_mixture_grid"])
            scores = probability_scores(model, test, costs)
            ledger = simulate(model, test, costs, fold["validation"])
            result = {"pool_mix": model.pool_mix, "calibration_attempts": attempts, "scores": scores,
                      "performance": summarize(ledger, fold["validation"]), "stress": {},
                      "training_cutoff": model.trained_through.isoformat(), "ledger": ledger}
            result["baselines"] = {name: simulate(model, test, costs, fold["validation"], rule=name)
                                   for name in protocol["baseline_rules"]}
            trade_through = simulate(model, test, costs, fold["validation"], trade_through_ticks=1)
            result["stress"]["target_trade_through_1_tick"] = {**summarize(trade_through, fold["validation"]), "ledger": trade_through}
            for name, stressed_cost in (
                ("spread_2_ticks", replace(costs, spread_ticks=2)),
                ("spread_4_ticks", replace(costs, spread_ticks=4)),
                ("slippage_2_ticks", replace(costs, slippage_ticks=2)),
                ("slippage_4_ticks", replace(costs, slippage_ticks=4)),
            ):
                stress_ledger = simulate(model, test, stressed_cost, fold["validation"])
                result["stress"][name] = {**summarize(stress_ledger, fold["validation"]), "ledger": stress_ledger}
            for name, (stressed, dropped) in stressed_samples.items():
                stressed = purged_partition(stressed, fold["validation"])
                stress_ledger = simulate(model, stressed, costs, fold["validation"])
                result["stress"][name] = {**summarize(stress_ledger, fold["validation"]),
                                          "ledger": stress_ledger,
                                          "eligible_forecasts": len(stressed), "rejections": dropped}
            entry["models"][version] = result
            exported[version] = model
            if progress is not None:
                progress(f"Fold {fold_index}/{len(folds)}: {version} complete")
    return report, exported


def pool_development(report: dict, *, multiplicity_count: int) -> dict:
    """Inference is on the disjoint out-of-sample ledger, never averaged fold CIs."""
    spec = registered_protocol()
    count = max(multiplicity_count, spec["candidate_configuration_count"])
    alpha = spec["familywise_alpha"] / count  # Conservative family correction, includes research history.
    pooled = {}
    days = [day for fold in report["folds"] for day in fold["sessions"]["validation"]]
    if len(days) != len(set(days)):
        raise ValueError("Validation folds overlap")
    for version in MODEL_VERSIONS:
        results = [fold["models"][version] for fold in report["folds"] if version in fold["models"]]
        if not results:
            continue
        ledger = [trade for r in results for trade in r["ledger"]]
        daily_scores = [day for r in results for day in r["scores"]["daily_scores"]]
        n = sum(day["n"] for day in daily_scores)
        bins = []
        for index in range(10):
            parts = [b for r in results for b in r["scores"]["reliability"] if b["lower"] == index / 10]
            total = sum(b["n"] for b in parts)
            if total:
                predicted = sum(b["predicted"] * b["n"] for b in parts) / total
                observed = sum(b["observed"] * b["n"] for b in parts) / total
                bins.append({"lower": index / 10, "upper": (index+1)/10, "n": total,
                             "predicted": predicted, "observed": observed, "absolute_error": abs(predicted-observed)})
        adequate = [b for b in bins if b["n"] >= spec["acceptance"]["minimum_observations_per_reliability_bin"]]
        scores = {metric: sum(d[metric] * d["n"] for d in daily_scores) / n if n else None
                  for metric in ("brier", "crps_usd")}
        scores.update(predictions=n, daily_scores=daily_scores, reliability=bins,
                      max_calibration_error=max(b["absolute_error"] for b in adequate) if len(adequate) >= 3 else None)
        performance = summarize(ledger, days, alpha=alpha)
        performance["multiplicity_configurations"] = count
        stress = {}
        for name in results[0]["stress"]:
            stress[name] = summarize([t for r in results for t in r["stress"][name]["ledger"]], days, alpha=alpha)
        baseline_ledgers = {name: [t for r in results for t in r["baselines"][name]] for name in spec["baseline_rules"]}
        result = {"performance": performance, "scores": scores, "stress": stress, "ledger": ledger,
                  "frozen_pool_mix": results[-1]["pool_mix"], "folds": len(results),
                  "baselines": {name: summarize(trades, days, alpha=alpha) for name, trades in baseline_ledgers.items()},
                  "paired_incumbent_lower_usd_per_day": paired_daily_bound(performance, dict.fromkeys(days, 0.0), days, alpha=alpha)}
        pooled[version] = result
    baseline = pooled.get("empirical_pool_v1")
    for version, result in pooled.items():
        checks = acceptance_checks(result, baseline, minimum_trades=spec["acceptance"]["minimum_validation_trades"])
        # These are reviewed separately at final installation, never fabricated
        # as hard-coded false placeholders inside the offline decision.
        for key in ("untouched_holdout", "forward_dry_run", "fill_exit_parity", "intratrade_drawdown_verified"):
            checks.pop(key)
        checks["at_least_three_folds"] = result["folds"] >= spec["minimum_walk_forward_folds"]
        checks["no_negative_fold"] = all(fold["models"][version]["performance"]["net_usd"] >= 0
                                          for fold in report["folds"] if version in fold["models"])
        checks["registered_development"] = report["mode"] == "development"
        result["paired_score_upper_bounds"] = {}
        candidate_days = {r["day"]: r for r in result["scores"]["daily_scores"]}
        base_days = {r["day"]: r for r in baseline["scores"]["daily_scores"]}
        for metric in ("brier", "crps_usd"):
            shared = sorted(set(candidate_days) & set(base_days))
            diffs = np.asarray([candidate_days[d][metric]-base_days[d][metric] for d in shared])
            bounds = {str(length): float(np.quantile(diffs[block_indices(len(shared), length)].mean(axis=1), 1-alpha/2))
                      if len(shared) >= 2*length else None for length in spec["bootstrap_day_block_lengths"]}
            result["paired_score_upper_bounds"][metric] = bounds
            checks[metric+"_vs_pool"] = bool(bounds) and all(v is not None and v <= 0 for v in bounds.values())
        checks["beats_directional_baselines"] = all(
            result["performance"]["net_usd"] > result["baselines"][name]["net_usd"] for name in ("always_long", "always_short"))
        result["acceptance_checks"] = checks
    report["pooled"] = pooled
    report["multiplicity_count"] = count
    report["status"] = "offline_passed" if any(all(r["acceptance_checks"].values()) for name, r in pooled.items()
                                                if name in spec["candidates"]) else "offline_failed"
    return report


def bootstrap_seed_sensitivity(ledger: list[dict], days: list[str], *, alpha: float) -> dict:
    totals = np.asarray([sum(t["net_usd"] for t in ledger if t["day"] == d) for d in days])
    counts = np.asarray([sum(t["day"] == d for t in ledger) for d in days])
    result = {}
    for length in registered_protocol()["bootstrap_day_block_lengths"]:
        lowers = []
        if len(days) >= 2*length and ledger:
            for seed in range(1, 21):
                indices = block_indices(len(days), length, seed=seed)
                n = counts[indices].sum(axis=1)
                if np.all(n > 0):
                    lowers.append(float(np.quantile(totals[indices].sum(axis=1)/n, alpha/2)))
        result[str(length)] = {"seeds": len(lowers), "minimum": min(lowers) if lowers else None,
                               "maximum": max(lowers) if lowers else None}
    return result


def zero_drift_diagnostic(*, refits: int = 100, seed: int = 20260926) -> dict:
    """Synthetic multiplicity diagnostic, never evidence for promotion."""
    from .probabilistic_strategy import Features
    rng = np.random.default_rng(seed)
    cells = [(-1., -1.), (-1., 1.), (1., -1.), (1., 1.)]
    any_trade = 0
    for _ in range(refits):
        samples = []
        for index in range(360):
            at = datetime(2024, 1, 1, 10, tzinfo=ET) + timedelta(days=index//6, minutes=15*(index%6))
            c = cells[index % 4]
            f = Features((0., c[0], c[1], 0.), 4., 2., at, at.date().isoformat())
            points = np.concatenate(([0.], np.cumsum(rng.normal(0, 2, HORIZON))))
            path = tuple((a/(2*math.sqrt(HORIZON)), max(a,b)/(2*math.sqrt(HORIZON)),
                          min(a,b)/(2*math.sqrt(HORIZON)), b/(2*math.sqrt(HORIZON)))
                         for a,b in zip(points, points[1:]))
            samples.append(Sample(f, path, at+HORIZON*BAR))
        model = PathModel("bayesian_cells_v1", tuple(samples))
        forecasts = [model.forecast(replace(samples[-1].features, values=(0.,a,b,0.),
                     decision_at=model.trained_through+BAR)) for a,b in cells]
        any_trade += any(f["research_action"] != "NO_TRADE" for f in forecasts)
    return {"seed": seed, "refits": refits, "tests_per_refit": 8, "refits_with_any_trade": any_trade,
            "observed_fraction": any_trade/refits, "synthetic_only": True,
            "limitation": "Zero-drift Gaussian scenarios with matched volatility; not market calibration or profitability evidence."}


def acceptance_checks(result: dict, baseline: dict, *, minimum_trades: int = 200) -> dict[str, bool]:
    """Necessary offline checks only. Missing/undefined evidence is failure."""
    p, score = result["performance"], result["scores"]
    def finite_cmp(value, bound, op):
        return value is not None and math.isfinite(value) and op(value, bound)
    above = lambda a, b: a > b
    below = lambda a, b: a <= b
    checks = {
        "trade_count": p["trades"] >= minimum_trades,
        "positive_expectancy_bounds": all(finite_cmp(v, 0, above) for v in p["expectancy_ci_lower_usd"].values()),
        "calibration": finite_cmp(score["max_calibration_error"], registered_protocol()["acceptance"]["max_absolute_calibration_error"], below),
        "drawdown_closed_equity": finite_cmp(p["closed_equity_max_drawdown_usd"], registered_protocol()["acceptance"]["maximum_drawdown_usd"], below),
        "intratrade_drawdown_verified": False,
        "tail_loss": finite_cmp(p["expected_shortfall_95_usd"], registered_protocol()["acceptance"]["maximum_expected_shortfall_95_usd_per_trade"], below),
        "without_best_days": finite_cmp(p["net_without_best_five_days_usd"], 0, above),
        "without_best_trades": finite_cmp(p["net_without_best_five_trades_usd"], 0, above),
        "day_concentration": finite_cmp(p["best_day_share_positive_pnl"], registered_protocol()["acceptance"]["maximum_positive_pnl_share_best_day"], below),
        "regime_support": all(r["trades"] >= 30 and finite_cmp(r["mean_net_usd"], 0, lambda a, b: a >= b)
                              for r in p["regimes"].values()),
        "cost_latency_parameter_stress": all(finite_cmp(r["net_usd"], 0, above) for key, r in result["stress"].items()
                                              if not key.startswith(("missing_", "unknown_"))),
        "missing_volume_abstention": result["stress"]["unknown_volume"]["trades"] == 0,
        "paired_incumbent_advantage": all(finite_cmp(v, 0, above) for v in result.get("paired_incumbent_lower_usd_per_day", {"missing": None}).values()),
        "untouched_holdout": False, "forward_dry_run": False, "fill_exit_parity": False,
    }
    for metric in ("brier", "crps_usd"):
        b = baseline["scores"][metric]
        checks[metric + "_vs_pool"] = b is not None and finite_cmp(score[metric], b, below)
    return checks
