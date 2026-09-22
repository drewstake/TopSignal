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


def audit_candles(rows: Sequence[Candle]) -> dict:
    identities = {(r.owner, r.contract_id, r.live) for r in rows}
    if len(identities) != 1:
        raise ValueError("Research requires exactly one owner, contract and data subscription.")
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
            "first": rows[0].timestamp.isoformat(), "last": rows[-1].timestamp.isoformat(),
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
        for side in (1, -1):
            values, _, _ = price_paths(paths, stop=sample.features.stop_points, side=side, costs=costs)
            observed = float(price_paths(np.asarray([sample.path]), stop=sample.features.stop_points,
                                        side=side, costs=costs)[0][0])
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
            "interpretation": "Unvalidated forecasts, scored on later sessions. Fitting a mixture does not establish calibration."}


def calibrate(model: PathModel, samples: Sequence[Sample], costs: Costs, grid: list[float]) -> tuple[PathModel, list[dict]]:
    if not samples:
        raise ValueError("No calibration labels; cannot select a distribution mixture.")
    attempts = []
    for mixture in grid:
        scores = probability_scores(replace(model, pool_mix=mixture), samples, costs)
        attempts.append({"pool_mix": mixture, "crps_usd": scores["crps_usd"]})
    # Prefer more shrinkage on exactly tied scores.
    selected = min(attempts, key=lambda row: (row["crps_usd"], -row["pool_mix"]))
    return replace(model, pool_mix=selected["pool_mix"]), attempts


def simulate(model: PathModel, samples: Sequence[Sample], costs: Costs, days: list[str]) -> list[dict]:
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
            last_entry is not None and (f.decision_at - last_entry).total_seconds() < 300
        ):
            continue
        if counts.get(f.day, 0) >= 30:
            continue
        # Protective risk is budgeted before entry; gaps can still exceed it.
        stop_risk = 2 * f.stop_points + costs.describe()["total_usd"]
        if daily.get(f.day, 0) - stop_risk < -250:
            continue
        forecast = model.forecast(f, costs)
        action = forecast["research_action"]
        if action == "NO_TRADE":
            continue
        net, kind, duration = price_paths(np.asarray([sample.path]), stop=f.stop_points,
                                         side=1 if action == "BUY" else -1, costs=costs)
        bars = int(duration[0])
        entry_at = sample.label_end - HORIZON * BAR
        busy_until = entry_at + bars * BAR
        last_entry = entry_at
        daily[f.day] = daily.get(f.day, 0) + float(net[0])
        counts[f.day] = counts.get(f.day, 0) + 1
        ledger.append({"decision_at": f.decision_at.isoformat(), "day": f.day, "action": action,
                       "net_usd": float(net[0]), "exit": ("stop", "time", "target")[int(kind[0])],
                       "holding_minutes": bars * 5, "stop_points": f.stop_points,
                       "session_regime": "early_session" if f.decision_at.astimezone(ET).hour < 12 else "late_session",
                       "volatility_regime": "high_volatility" if f.volatility > vol_boundary else "low_volatility"})
    return ledger


def block_indices(n: int, length: int, repeats: int = 2000, seed: int = 20260921) -> np.ndarray:
    """Circular fixed-length day block bootstrap; same seed enables paired comparisons."""
    if n <= 0 or length <= 0:
        raise ValueError("Bootstrap needs nonempty days and a positive block length.")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(repeats, math.ceil(n / length)))
    return ((starts[:, :, None] + np.arange(length)) % n).reshape(repeats, -1)[:, :n]


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


def paired_daily_bound(candidate: dict, incumbent_daily: dict[str, float], days: list[str]) -> dict:
    by_day = {row["day"]: row["net_usd"] for row in candidate["daily"]}
    # Incumbent positions may exit on another date inside the same test window.
    # Include those P&Ls rather than silently discarding a loss or a gain.
    days = sorted(set(days) | set(incumbent_daily))
    diff = np.asarray([by_day.get(day, 0) - incumbent_daily.get(day, 0) for day in days])
    return {str(length): float(np.quantile(diff[block_indices(len(days), length)].mean(axis=1), .05 / 12))
            if len(days) >= 2 * length else None for length in (5, 10)}


def development(rows: Sequence[Candle], protocol: dict, *, diagnostic: bool = False) -> tuple[dict, dict[str, PathModel]]:
    audit = audit_candles(rows)
    plan = walk_forward_plan(audit["eligible_complete_sessions"], protocol)
    report = {"data_audit": audit, "plan": plan, "mode": "diagnostic" if diagnostic else "development",
              "promotion_eligible": False, "selected_for_default": None, "holdout_evaluated": False,
              "status": "insufficient_data" if plan["status"] != "ready" else "development_only",
              "blockers": ["Final untouched holdout has not been evaluated.",
                           "Forward Dry Run and execution parity are not verified.",
                           "No routing adapter or default-promotion mechanism exists in this research revision."],
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
    train_samples, train_rejections = make_samples(rows, stride=3)
    all_samples, rejections = make_samples(rows)
    report["label_rejections"] = rejections
    report["training_label_rejections"] = train_rejections
    costs = Costs()
    exported: dict[str, PathModel] = {}
    for fold in folds:
        train = purged_partition(train_samples, fold["training"], next_boundary=fold["calibration"][0])
        calibration = purged_partition(all_samples, fold["calibration"], next_boundary=fold["validation"][0])
        test = purged_partition(all_samples, fold["validation"])
        entry = {"sessions": fold, "training_paths": len(train), "calibration_paths": len(calibration),
                 "validation_paths": len(test), "models": {}}
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
            for name, stressed_cost in (
                ("spread_2_ticks", replace(costs, spread_ticks=2)),
                ("spread_4_ticks", replace(costs, spread_ticks=4)),
                ("slippage_2_ticks", replace(costs, slippage_ticks=2)),
                ("slippage_4_ticks", replace(costs, slippage_ticks=4)),
            ):
                stress_ledger = simulate(model, test, stressed_cost, fold["validation"])
                result["stress"][name] = summarize(stress_ledger, fold["validation"])
            for name, stress_rows, options in (
                ("delay_one_bar", rows, {"delay_bars": 1}),
                ("stop_multiplier_0_75", rows, {"stop_multiplier": .75}),
                ("stop_multiplier_1_25", rows, {"stop_multiplier": 1.25}),
                ("missing_every_20th_bar", [r for i, r in enumerate(rows) if i % 20 != 0], {}),
                ("unknown_volume", [replace(r, volume=None) for r in rows], {}),
            ):
                stressed, dropped = make_samples(stress_rows, **options)
                stressed = purged_partition(stressed, fold["validation"])
                stress_ledger = simulate(model, stressed, costs, fold["validation"])
                result["stress"][name] = {**summarize(stress_ledger, fold["validation"]),
                                          "eligible_forecasts": len(stressed), "rejections": dropped}
            entry["models"][version] = result
            exported[version] = model
    return report, exported


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
        "calibration": finite_cmp(score["max_calibration_error"], .05, below),
        "drawdown_closed_equity": finite_cmp(p["closed_equity_max_drawdown_usd"], 1000, below),
        "intratrade_drawdown_verified": False,
        "tail_loss": finite_cmp(p["expected_shortfall_95_usd"], 100, below),
        "without_best_days": finite_cmp(p["net_without_best_five_days_usd"], 0, above),
        "without_best_trades": finite_cmp(p["net_without_best_five_trades_usd"], 0, above),
        "day_concentration": finite_cmp(p["best_day_share_positive_pnl"], .20, below),
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
