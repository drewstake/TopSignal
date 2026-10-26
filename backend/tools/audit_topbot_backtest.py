"""Read-only entry-gate audit of the local historical TopBot runner."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from threading import Event
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-09-23T00:00:00+00:00")
    parser.add_argument("--end", default="2026-10-24T00:00:00+00:00")
    parser.add_argument("--model-source", choices=["historical_refit", "saved_running_model"], default="historical_refit")
    parser.add_argument("--owner", default="offline-backtest-audit")
    parser.add_argument("--diagnose", action="store_true", help="Measure threshold sensitivity without changing decisions or fitting a model")
    args = parser.parse_args()
    from research_probabilistic_topbot import offline_guard
    offline_guard()
    from app.services import topbot_backtest as runner
    from app.services.probabilistic_strategy import PathModel
    from app.services.topbot_backtest_jobs import BacktestRequest
    original = PathModel.forecast
    decisions, reasons, sensitivity = [], Counter(), []
    def traced(self, f, costs):
        result = original(self, f, costs)
        best = max(result["forecasts"], key=lambda side: result["forecasts"][side]["lower_utility_usd"])
        row = {"time": f.decision_at.isoformat(), "side": best, "stop": f.stop_points,
               "training_paths": len(self.samples), **result["forecasts"][best]}
        decisions.append(row)
        reasons[result["research_action"]] += 1
        if args.diagnose:
            from app.services.probabilistic_strategy import Costs
            free = original(self, f, Costs(commission_per_side=0, spread_ticks=0, slippage_ticks=0, latency_ticks=0))
            choices = result["forecasts"]
            sensitivity.append({"cell": f"momentum_{'up' if f.values[1] >= 0 else 'down'}_volatility_{'rising' if f.values[2] >= 0 else 'falling'}",
                "stop": f.stop_points, "max_expected_net": max(v["expected_net_usd"] for v in choices.values()),
                "max_lower_utility": max(v["lower_utility_usd"] for v in choices.values()),
                "min_penalty": min(v["uncertainty_penalty_usd"] for v in choices.values()),
                "max_penalty": max(v["uncertainty_penalty_usd"] for v in choices.values()),
                "critical_value": choices[best]["critical_value"],
                "zero_cost_max_lower_utility": max(v["lower_utility_usd"] for v in free["forecasts"].values())})
        return result
    PathModel.forecast = traced
    try:
        result = runner.run(BacktestRequest(start=datetime.fromisoformat(args.start), end=datetime.fromisoformat(args.end), model_source=args.model_source),
            owner=args.owner, cancel=Event(), progress=lambda *a: None)
    finally:
        PathModel.forecast = original
    diagnosis = None
    if sensitivity:
        cells = {}
        for cell in sorted({d["cell"] for d in sensitivity}):
            values = [d for d in sensitivity if d["cell"] == cell]
            cells[cell] = {"forecasts": len(values), "max_expected_net": max(d["max_expected_net"] for d in values),
                           "max_lower_utility": max(d["max_lower_utility"] for d in values)}
        diagnosis = {"basis": "Forecast opportunities, not simulated trades; may overlap. Diagnostic only, no threshold changes.",
            "cells": cells, "positive_mean_no_uncertainty": sum(d["max_expected_net"] > 0 for d in sensitivity),
            "mean_above_one_no_uncertainty": sum(d["max_expected_net"] > 1 for d in sensitivity),
            "lower_utility_above_zero": sum(d["max_lower_utility"] > 0 for d in sensitivity),
            "lower_utility_above_one": sum(d["max_lower_utility"] > 1 for d in sensitivity),
            "zero_execution_costs_lower_utility_above_one": sum(d["zero_cost_max_lower_utility"] > 1 for d in sensitivity),
            "zero_execution_costs_best_lower_utility": max(d["zero_cost_max_lower_utility"] for d in sensitivity),
            "penalty_range": [min(d["min_penalty"] for d in sensitivity), max(d["max_penalty"] for d in sensitivity)],
            "critical_value_range": [min(d["critical_value"] for d in sensitivity), max(d["critical_value"] for d in sensitivity)]}
    print(json.dumps({"range": result["range"], "config": result["config_snapshot"], "skipped": result["skipped"],
        "actions": reasons, "trades": len(result["trades"]), "forecast_count": len(decisions),
        "positive_expected_net": sum(d["expected_net_usd"] > 0 for d in decisions),
        "supported": sum(d["training_paths"] >= 300 and d["effective_days"] >= 20 for d in decisions),
        "closest_to_entry": sorted(decisions, key=lambda d: d["lower_utility_usd"], reverse=True)[:5],
        "diagnostics": result.get("entry_diagnostics"), "sensitivity": diagnosis}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
