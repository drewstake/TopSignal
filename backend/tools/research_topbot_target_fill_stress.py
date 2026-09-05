"""Offline opening-drive stress: require one tick through every resting target.

Use the normal research CLI arguments and explicit --commission-per-side 0.61.
Only the original opening_drive, observed-minute execution and zero added entry
delay are permitted. All targets still fill at their original limit, adjusted
by configured adverse slippage and fees. A touch alone leaves exposure open.
This is a queue/nonfill sensitivity, not proof of actual queue execution.
No historical experiment runs on import; the ordinary runner's offline network
block and immutable source/report capture remain in force when main is invoked.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from tools import research_topbot as _original_runner

BASE_ENGINE_VERSION = "5.3.0-entry-latency-stress"
STRESS_ENGINE_VERSION = BASE_ENGINE_VERSION + "+target-through-1tick-v1"
EXECUTION_MODEL = "observed_1m_target_through_one_tick_v1"
TARGET_CONFIRMATION_TICKS = 1
SOURCE_HASHES = {
    "app/services/bot_backtesting.py": "dbce7ee22b1b4b9d36561241240fe551d45ba4f8230fb35e74bca2f0b835309e",
    "tools/research_topbot.py": "57fc67b564465abbceba9209fa0f6af2c198b2aa0bc5de18697351a5e882865e",
    "tools/fixtures/topbot_research.py": "d0230d261f3e5f00f6f876756086b873987eb540ae2c6a6b1798ba2b376d80e6",
}


def verify_sources():
    for relative, expected in SOURCE_HASHES.items():
        source = (BACKEND / relative).read_text(encoding="utf-8")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected:
            raise RuntimeError(f"Frozen target-stress dependency changed: {relative}; review and preregister before updating")


def stress_metadata():
    return {
        "model": EXECUTION_MODEL,
        "target_confirmation_ticks": TARGET_CONFIRMATION_TICKS,
        "long_confirmation": "observed open or high >= original target + one tick",
        "short_confirmation": "observed open or low <= original target - one tick",
        "target_fill_price": "original target, then configured adverse exit slippage and per-side fees",
        "touch_without_confirmation": "position and original brackets persist; later signals and calendar exits see that position",
        "stop_priority": "unchanged original stop-gap and stop-first intrabar behavior, including ambiguity labels",
        "interpretation": "Research queue/nonfill sensitivity; one-tick trade-through does not prove executable queue priority",
        "source_normalized_sha256": dict(SOURCE_HASHES),
    }


def stamp_report(value):
    value.update(
        base_engine_version=BASE_ENGINE_VERSION,
        engine_version=STRESS_ENGINE_VERSION,
        execution_model=EXECUTION_MODEL,
        execution_sensitivity=stress_metadata(),
    )
    if "assumptions" in value:
        value["assumptions"].update(
            base_engine_version=BASE_ENGINE_VERSION,
            engine_version=STRESS_ENGINE_VERSION,
            execution_model=EXECUTION_MODEL,
            target_confirmation_ticks=TARGET_CONFIRMATION_TICKS,
            target_fill_rule="one tick through original target required at observed open or extreme; fill at original target with configured adverse slippage",
            target_nonfill_rule="touch alone retains position and original brackets",
            gap_rule="stops fill at adverse gap open; targets need one tick through and receive no price improvement",
        )
    for case in value.get("results", {}).values():
        if isinstance(case, dict):
            stamp_report(case)
    return value


def make_stress_engine_class(replay, fixture, variant):
    verify_sources()
    if replay.BACKTEST_ENGINE_VERSION != BASE_ENGINE_VERSION:
        raise RuntimeError("Unexpected base engine version for frozen target-fill stress")

    class TargetThroughEngine(replay.BacktestEngine):
        def __init__(self, **kwargs):
            if kwargs.get("execution_candles") is None:
                raise ValueError("Target-fill stress requires observed one-minute execution")
            if kwargs.get("entry_delay_minutes", 0) != 0:
                raise ValueError("Target-fill stress isolates target confirmation; added entry delay must be zero")
            if kwargs["settings"].commission_per_contract != .61:
                raise ValueError("Target-fill stress requires explicit current-base fees of 0.61 per side")
            super().__init__(**kwargs)

        def _target_confirmed(self, observed_price):
            position = self.position
            if position is None or position.take_profit is None:
                return False
            distance = TARGET_CONFIRMATION_TICKS * self.settings.tick_size
            if position.side == "long":
                return observed_price >= position.take_profit + distance
            return observed_price <= position.take_profit - distance

        def _process_open_gap(self, candle):
            position = self.position
            if position is None:
                return
            raw_open, stop = float(candle.open_price), position.stop_loss
            stop_hit = stop is not None and (
                raw_open <= stop if position.side == "long" else raw_open >= stop
            )
            if stop_hit or self._target_confirmed(raw_open):
                # The original method keeps stop-gap priority, original target
                # price, timestamp and excursion handling. The surrounding
                # ResearchEngine then evaluates its independent calendar clock.
                super()._process_open_gap(candle)

        def _process_intrabar_bracket(self, candle):
            position = self.position
            if position is None:
                return
            low, high, stop = float(candle.low_price), float(candle.high_price), position.stop_loss
            stop_hit = stop is not None and (low <= stop if position.side == "long" else high >= stop)
            extreme = high if position.side == "long" else low
            if stop_hit or self._target_confirmed(extreme):
                # In particular, a stop plus an unconfirmed target touch still
                # gets the original conservative stop and ambiguity label.
                super()._process_intrabar_bracket(candle)
            else:
                self._update_excursion(low, high)

        def run(self):
            return stamp_report(super().run())

    # The private proxy places stress beneath the runner's ResearchEngine in
    # the MRO. Its clock hook therefore runs even when the target did not fill.
    private_replay = SimpleNamespace(**{**vars(replay), "BacktestEngine": TargetThroughEngine})
    return _original_runner.make_engine_class(private_replay, fixture, variant)


def load_private_runner():
    verify_sources()
    source_path = BACKEND / "tools/research_topbot.py"
    runner = ModuleType("_private_target_fill_stress_runner")
    runner.__file__, runner.__package__ = str(source_path), "tools"
    exec(compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec"), runner.__dict__)
    runner.__doc__ = __doc__
    class StressArgumentParser(argparse.ArgumentParser):
        def add_argument(self, *arguments, **kwargs):
            if "--commission-per-side" in arguments:
                kwargs.update(required=True, choices=(.61,))
            elif "--execution-minutes" in arguments:
                kwargs["choices"] = (1,)
            elif "--entry-delay-minutes" in arguments:
                kwargs["choices"] = (0,)
            elif "--variants" in arguments:
                kwargs["choices"] = ("opening_drive",)
            return super().add_argument(*arguments, **kwargs)

    # Accurate CLI help and final parsing without changing the shared argparse
    # module or the original runner's parser behavior.
    runner.argparse = SimpleNamespace(**{**vars(argparse), "ArgumentParser": StressArgumentParser})
    runner.make_engine_class = make_stress_engine_class
    original_write = runner.write_new_json

    def write_stress_json(path, value):
        if isinstance(value, dict):
            if path.name == "manifest.json":
                if (value["execution_minutes"] != 1 or value["entry_delay_minutes"] != 0
                        or value["costs"]["commission_per_side"] != .61):
                    raise ValueError("Manifest does not describe the registered target-fill stress")
            # Mutate the private runner's in-memory manifest BEFORE writing so
            # its subsequent started/results fingerprints match the saved file.
            stamp_report(value)
        original_write(path, value)

    runner.write_new_json = write_stress_json
    return runner


def validate_stress_arguments(arguments):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--commission-per-side", type=float, required=True)
    parser.add_argument("--execution-minutes", type=int, choices=(1,), default=1)
    parser.add_argument("--entry-delay-minutes", type=int, choices=(0,), default=0)
    parser.add_argument("--variants", nargs="+", required=True)
    parser.add_argument("--fixture", type=Path, default=BACKEND / "tools/fixtures/topbot_research.py")
    args, _ = parser.parse_known_args(arguments)
    if args.commission_per_side != .61:
        parser.error("registered target-fill stress requires explicit --commission-per-side 0.61")
    if args.variants != ["opening_drive"]:
        parser.error("registered target-fill stress permits only --variants opening_drive")
    if args.fixture.resolve() != (BACKEND / "tools/fixtures/topbot_research.py").resolve():
        parser.error("registered target-fill stress requires the unchanged original fixture")


def main():
    if not any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        validate_stress_arguments(sys.argv[1:])
    return load_private_runner().main()


if __name__ == "__main__":
    raise SystemExit(main())
