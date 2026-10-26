"""Local, chronological TopBot research runs. No broker, model promotion or SQL writes."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from threading import Event
from zoneinfo import ZoneInfo

import numpy as np

from .databento_cache import get_default_databento_cache
from .probabilistic_protocol import digest, protocol
from .probabilistic_strategy import BAR, HORIZON, Candle, Costs, PathModel, VolatilityAboveRiskCap, contiguous, features, make_samples, price_paths, utc
from .probabilistic_validation import audit_candles
from .topbot_session import entry_boundary_reason

ENGINE = "topbot-saved-model-3"
TRAINING_SESSIONS = 240


class Cancelled(Exception):
    pass


def check_cancel(cancel: Event):
    if cancel.is_set():
        raise Cancelled()


def fit_prior_model(rows, start, *, training_sessions=TRAINING_SESSIONS):
    # Entire training sessions and their outcome labels precede the test period.
    prior = [r for r in rows if r.timestamp + BAR <= start - timedelta(minutes=15)]
    days = audit_candles(prior)["eligible_complete_sessions"][-training_sessions:]
    if len(days) < training_sessions:
        raise ValueError(f"Choose a later start date: TopBot needs {training_sessions} complete prior training sessions; only {len(days)} are available.")
    chosen_days = set(days)
    first = datetime.fromisoformat(days[0]).replace(tzinfo=ZoneInfo("America/New_York")) - timedelta(days=1)
    samples, _ = make_samples([r for r in prior if r.timestamp >= first], stride=3)
    selected = tuple(s for s in samples if s.features.day in chosen_days and s.label_end <= start - timedelta(minutes=15))[-4096:]
    if len(selected) < protocol()["minimum_training_examples"]:
        raise ValueError("Insufficient valid training paths before the requested period.")
    return PathModel("bayesian_cells_v1", selected, pool_mix=0.0), days


def replay(rows, model, start, end, balance, costs, *, cancel, progress):
    """Decide using past bars before looking at outcomes; fail on unknown exposure."""
    from .bot_backtesting import _build_drawdown_series, _build_metrics, _period_results
    trades, curve = [], [{"timestamp": start.isoformat(), "equity": balance, "realized_pnl": 0., "unrealized_pnl": 0.}]
    daily, counts, skipped, gates = Counter(), Counter(), Counter(), Counter()
    closest = []
    volatility_stops = {"min": None, "max": None}
    busy_until = last_entry = None
    realized = 0.
    indices = [i for i, r in enumerate(rows) if start <= r.timestamp + BAR < end]
    if len(indices) < 2:
        raise ValueError("No usable closed five-minute candles in this period.")
    rules = protocol()
    for done, i in enumerate(indices):
        if done % 100 == 0:
            check_cancel(cancel)
            progress("replaying", done, len(indices))
        now = rows[i].timestamp + BAR
        if entry_boundary_reason(now):
            skipped["outside_entry_session"] += 1
            continue
        gates["in_session"] += 1
        if now + HORIZON * BAR > end:
            skipped["horizon_after_test_end"] += 1
            continue
        if busy_until and now < busy_until:
            skipped["position_open"] += 1
            continue
        if last_entry and (now - last_entry).total_seconds() < rules["cooldown_seconds"]:
            skipped["cooldown"] += 1
            continue
        try:
            f = features(rows[max(0, i-20):i+1], as_of=now)
        except VolatilityAboveRiskCap as exc:
            skipped["volatility_above_risk_cap"] += 1
            volatility_stops["min"] = min(volatility_stops["min"] or exc.stop, exc.stop)
            volatility_stops["max"] = max(volatility_stops["max"] or exc.stop, exc.stop)
            continue
        except ValueError:
            skipped["missing_or_invalid_feature_history"] += 1
            continue
        gates["valid_features"] += 1
        risk = 2 * f.stop_points + costs.describe()["fees_usd"] + 2 * costs.adverse_points_per_side
        if counts[f.day] >= rules["max_entries_per_day"] or daily[f.day] - risk <= -rules["max_daily_loss_usd"]:
            skipped["daily_risk_limit"] += 1
            continue
        forecast = model.forecast(f, costs)
        gates["model_evaluations"] += 1
        best_side = max(forecast["forecasts"], key=lambda s: forecast["forecasts"][s]["lower_utility_usd"])
        best = forecast["forecasts"][best_side]
        support = (forecast["training_paths"] >= rules["minimum_training_examples"]
                   and best["effective_days"] >= rules["minimum_effective_days_per_forecast"])
        edge = best["lower_utility_usd"] > rules["minimum_net_edge_usd"]
        gates["supported_forecasts"] += int(support)
        gates["edge_above_threshold"] += int(edge)
        closest.append({"time": now.isoformat(), "side": best_side, "stop_points": f.stop_points,
            "expected_net_usd": best["expected_net_usd"], "uncertainty_penalty_usd": best["uncertainty_penalty_usd"],
            "lower_utility_usd": best["lower_utility_usd"], "effective_days": best["effective_days"],
            "supported": support, "action": forecast["research_action"]})
        closest.sort(key=lambda item: item["lower_utility_usd"], reverse=True)
        del closest[5:]  # Bound result size independently of input candle count.
        action = forecast["research_action"]
        if action not in {"BUY", "SELL"}:
            skipped["insufficient_training_support" if not support else "no_model_edge"] += 1
            continue
        future = rows[i+1:i+1+HORIZON]
        if len(future) != HORIZON or not contiguous(rows[i:i+1+HORIZON]):
            raise ValueError(f"Unknown trade outcome at {now.isoformat()}: missing candles or a contract roll during exposure. Choose a range without this gap; no partial performance result was booked.")
        side = 1 if action == "BUY" else -1
        reference = future[0].open
        path = np.asarray([[[v-reference for v in (r.open, r.high, r.low, r.close)] for r in future]])
        net, kind, duration = price_paths(path, stop=f.stop_points, side=side, costs=costs, scale=1.)
        bars, pnl = int(duration[0]), round(float(net[0]), 8)
        entry = reference + side * costs.adverse_points_per_side
        gross = pnl + costs.describe()["fees_usd"]
        exit_price = entry + side * gross / 2
        busy_until = now + bars * BAR  # conservative: no exit-bar re-entry
        last_entry = now
        daily[f.day] += pnl
        counts[f.day] += 1
        realized += pnl
        exposed = future[:bars]
        adverse = min((r.low-entry if side == 1 else entry-r.high) * 2 for r in exposed)
        favorable = max((r.high-entry if side == 1 else entry-r.low) * 2 for r in exposed)
        trades.append({"id": len(trades)+1, "side": "long" if side == 1 else "short", "quantity": 1,
            "signal_timestamp": now.isoformat(), "entry_timestamp": now.isoformat(), "entry_price": entry,
            "exit_timestamp": busy_until.isoformat(), "exit_price": round(exit_price, 8),
            "exit_reason": ("stop", "time", "target")[int(kind[0])], "gross_pnl": round(gross, 8),
            "commission": costs.describe()["fees_usd"], "net_pnl": pnl, "mae": min(0., adverse), "mfe": max(0., favorable),
            "bars_held": bars, "contract": rows[i].contract_id, "stop_price": entry-side*f.stop_points,
            "target_price": entry+side*forecast["target_points"], "lower_utility_usd": forecast["forecasts"][action]["lower_utility_usd"]})
        curve.append({"timestamp": busy_until.isoformat(), "equity": balance+realized, "realized_pnl": realized, "unrealized_pnl": 0.})
    check_cancel(cancel)
    curve.append({"timestamp": end.isoformat(), "equity": balance+realized, "realized_pnl": realized, "unrealized_pnl": 0.})
    drawdown = _build_drawdown_series(curve)
    metrics = _build_metrics(trades, equity_curve=curve, drawdown_series=drawdown,
        exposure_percent=sum(t["bars_held"] for t in trades)*100/max(1, len(indices)))
    return dict(trades=trades, equity_curve=curve, drawdown_series=drawdown, metrics=metrics,
                daily_results=_period_results(trades, monthly=False), monthly_results=_period_results(trades, monthly=True),
                skipped=dict(skipped), bar_count=len(indices), entry_diagnostics={
                    "observed_decisions": len(indices), "in_session": gates["in_session"],
                    "valid_features": gates["valid_features"], "model_evaluations": gates["model_evaluations"],
                    "supported_forecasts": gates["supported_forecasts"], "edge_above_threshold": gates["edge_above_threshold"],
                    "entries": len(trades), "maximum_stop_points": rules["maximum_candidate_stop_points"],
                    "minimum_net_edge_usd": rules["minimum_net_edge_usd"], "round_trip_cost_usd": costs.describe()["total_usd"],
                    "blocked_stop_range": volatility_stops, "closest_forecasts": closest,
                    "model_binding": "historical_refit", "runtime_gates": "Historical forecasts only; installed-artifact, receipt-time, account and broker routing gates are not replayed."})


def run(payload, *, owner, cancel, progress):
    start, end = utc(payload.start), utc(payload.end)
    saved = None
    if payload.model_source == "saved_running_model":
        from .topbot_saved_model import load_saved_model
        model, saved = load_saved_model(owner, expected_hash=payload.model_sha256)
        if start < model.trained_through + timedelta(minutes=15):
            raise ValueError(f"This model trained through {model.trained_through.isoformat()}. Start on or after {saved['first_full_test_day']} to avoid replaying its training data. No historical refit was used.")
    store = get_default_databento_cache()
    bounds = store.series_bounds("MNQ", "minute", 5)
    if not bounds:
        raise ValueError("Local Databento MNQ five-minute history is not installed.")
    end = min(end, bounds[1], datetime.now(timezone.utc))
    if start >= end:
        raise ValueError("The requested period has no available closed history.")
    progress("loading", 0, 1)
    # 550 calendar days provides room for 240 prior complete sessions and warmup.
    source = store.open_candles(user_id=owner, contract_id="DATABENTO.CONTINUOUS.MNQ", root_symbol="MNQ",
        unit="minute", unit_number=5, start=max(bounds[0], start-timedelta(days=7 if saved else 550)), end=end, closed_by=end)
    rows = []
    for i, row in enumerate(source):
        if i % 2048 == 0:
            check_cancel(cancel)
            progress("loading", i, len(source))
        rows.append(Candle(utc(row.candle_timestamp), float(row.open_price), float(row.high_price), float(row.low_price),
            float(row.close_price), float(row.volume), "CON.F.US.MNQ."+str(row.source_raw_symbol), owner))
    if saved:
        days = sorted({s.features.day for s in model.samples})
    else:
        progress("training", 0, 1)
        model, days = fit_prior_model(rows, start)
    check_cancel(cancel)
    costs = Costs(commission_per_side=payload.commission_per_contract, slippage_ticks=payload.slippage_ticks)
    output = replay(rows, model, start, end, payload.starting_balance, costs, cancel=cancel, progress=progress)
    output["entry_diagnostics"]["model_binding"] = payload.model_source
    if saved:
        output["entry_diagnostics"]["runtime_gates"] = "Verified saved TopBot weights; historical candle-close execution, without live receipt timing or broker/account gates."
    model_hash = digest(model.to_dict())
    source_hash = getattr(source[0], "source_file_sha256", None) if len(source) else None
    fingerprint = digest({"source": source_hash, "model": model_hash, "request": payload.model_dump(mode="json"), "engine": ENGINE})
    output.update(id=int(fingerprint[:12], 16), bot_config_id=None, engine_version=ENGINE,
        input_fingerprint=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat(),
        range={"start": start.isoformat(), "end": end.isoformat(), "bar_count": output.pop("bar_count"),
               "contract_id": "DATABENTO.CONTINUOUS.MNQ", "symbol": "MNQ", "timeframe_unit": "minute", "timeframe_unit_number": 5},
        config_snapshot={"strategy_type": "topbot_adaptive", "revision": "mnq_bayesian_payoff_v1", "starting_balance": payload.starting_balance,
            "training_sessions": len(days), "training_start": days[0], "training_end": days[-1], "training_paths": len(model.samples),
            "trained_through": model.trained_through.isoformat(), "model_sha256": model_hash, "pool_mix": model.pool_mix, "position_size": 1,
            "model_binding": payload.model_source, **(saved or {})},
        assumptions={"fill_model": "next_bar_open_with_adverse_costs", "signal_timing": "closed five-minute bars",
            "event_order": "known open gap, then stop before target, then time exit", "same_bar_exit_rule": "stop_first",
            "bracket_rule": "volatility stop 4–25 points; target 1.5x rounded up to tick", "gap_rule": "stops at worse opening price",
            "final_position_handling": "no entries without the full 15-minute horizon before end", "position_rule": "one MNQ contract, one position",
            "session_rule": "09:35–15:30 America/New_York, exchange calendar enforced", "commission_rule": "per contract per side",
            "slippage_rule": "adverse entry and exit, including spread and latency", "pnl_rule": "$2 per MNQ point",
            "metric_basis": "net closed-trade equity; drawdown excludes unrealized intratrade swings", "market_data": "local Databento exact 5m candles",
            "live_order_routing": "disabled", "timezone": "America/New_York", "commission_per_contract": costs.commission_per_side,
            "slippage_ticks": costs.slippage_ticks, "tick_size": .25, "tick_value": .5, "engine_version": ENGINE,
            "configured_execution_mode_was_ignored": "historical research only", "costs": costs.describe()},
        warnings=[("Retrospective replay of verified saved TopBot weights. Dates after training may still precede artifact creation; this is not a record of actual forward trades." if saved else "Historical research, not validated performance. This model is fitted before the test window and remains frozen; it is not the currently installed live artifact."),
                  "OHLC simulation assumes fills at the next five-minute open. Execution latency and intrabar order are not observed. Drawdown uses closed-trade equity."],
        notes=[("Uses a checksum-verified snapshot of the running model, with unchanged training paths and mixture. No refitting. Requests overlapping its training cutoff are rejected." if saved else "Training: latest 240 complete prior sessions, up to 4,096 non-overlapping paths, 15-minute label embargo, default Bayesian mixture 0. No fitting on the test period."),
               "Risk: one MNQ, 300-second cooldown, at most 30 daily entries, $250 daily stop-risk admission. Price gaps can exceed that limit.",
               "Only complete cached five-minute buckets are used. Missing feature history is skipped; missing exposure fails the run. No candles are synthesized.",
               "MAE/MFE use exposed candle extremes, including the entire exit candle, and may include moves after the actual exit.",
               "Results are held temporarily on this backend; export trades or analysis before restarting it. No training artifact or historical candles are saved to the database."],
        data_quality=None)
    return output
