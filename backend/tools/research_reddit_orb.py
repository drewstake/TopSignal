"""Offline Reddit ORB runner preserving absolute opening-range-low stops.

The ordinary research CLI, source capture, portfolios, fees, risk gate, calendar,
and ledgers remain in use. This process-local adapter computes brackets from
the actual slipped entry instead of moving the source's stop on a gap.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from types import ModuleType

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from tools import research_topbot as original

EXECUTION_ADAPTATION = {
    "name": "reddit_orb_absolute_range_stop_v1",
    "stop": "absolute opening-range low; no fill anchoring shift",
    "target": "actual slipped entry plus 1.5R rounded upward to quarter point",
    "risk_cap": "reject actual entry risk outside [0.25, 100] points; never tighten the stop",
    "signal": "closed strict 5m triples; only 15m boundaries before noon ET",
    "fill": "exact next boundary minute open plus adverse slippage; reject missing minute",
}


def make_engine_class(replay, fixture, variant):
    if variant != "reddit_orb15_long":
        raise ValueError("This execution adapter supports only reddit_orb15_long")
    base = original.make_engine_class(replay, fixture, variant)

    class AbsoluteRangeStopEngine(base):
        def _fill_pending_signal(self, pending, *, candle):
            if pending.payload.get("signal_category") == "entry" and self.position is None:
                entry = self._slipped_price(float(candle.open_price), action=pending.action)
                plan = fixture.fill_plan(entry, float(pending.payload["range_low"]))
                if plan is None:
                    self.block_counts["orb_actual_fill_risk_outside_025_100_points"] += 1
                    return
                # Base risk calculation and tick anchoring now use actual fill
                # as their reference; stop remains the source range boundary.
                pending = replace(pending, signal_price=entry,
                                  payload={**pending.payload, **plan})
            return super()._fill_pending_signal(pending, candle=candle)

        def run(self):
            result = super().run()
            result["assumptions"].update(EXECUTION_ADAPTATION)
            return result

    return AbsoluteRangeStopEngine


def load_private_runner():
    path = BACKEND / "tools/research_topbot.py"
    runner = ModuleType("_reddit_orb_private_runner")
    runner.__file__, runner.__package__ = str(path), "tools"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), runner.__dict__)
    runner.make_engine_class = make_engine_class
    write_original = runner.write_new_json

    def write_json(path, value):
        if isinstance(value, dict):
            value["execution_adaptation"] = dict(EXECUTION_ADAPTATION)
        write_original(path, value)

    runner.write_new_json = write_json
    return runner


if __name__ == "__main__":
    raise SystemExit(load_private_runner().main())
