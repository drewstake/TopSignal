"""Offline hourly Reddit adaptation; uses native hourly signals and minute fills.

Changes below are scoped to this process and restored on exit. No production
source or operator configuration is edited. The standard research runner records
the source snapshot, manifest, ledgers, failure records and summary of each run.
"""
from dataclasses import replace
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from tools import research_topbot as runner


def hourly_stream_specs(replay, config):
    key = replay._topbot_asset_stream_key("hour", 1)
    return {key: replay._TopBotReplayStreamSpec(
        key=key, unit="hour", unit_number=1, warmup_bars=200,
        contract_id=str(config.contract_id), symbol=config.symbol)}


def validate_hourly_config(config, original_validator):
    if (str(config.strategy_type) != "topbot_adaptive" or
            str(config.timeframe_unit) != "hour" or int(config.timeframe_unit_number) != 1):
        raise ValueError("This offline wrapper requires the fixed native-hourly research configuration")
    original_validator(SimpleNamespace(strategy_type=config.strategy_type,
        timeframe_unit="minute", timeframe_unit_number=5,
        fast_period=config.fast_period, slow_period=config.slow_period,
        strategy_params=config.strategy_params))


def make_hourly_engine(replay, fixture, variant, *, base_factory=runner.make_engine_class):
    base = base_factory(replay, fixture, variant)

    class HourlyRangeEngine(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            if hasattr(self, "execution_close_times"):
                # Eligibility applies when the signal becomes known at close.
                # In particular 07:59's minute closes at the allowed 08:00.
                # Actual entries still use the unmodified fill-time session gate.
                self.execution_in_session = np.fromiter(
                    (self._event_in_configured_session(t) for t in self.execution_close_times),
                    dtype=np.bool_, count=len(self.execution_close_times))

        def _fill_pending_signal(self, pending, *, candle):
            if pending.payload.get("signal_category") != "entry" or self.position is not None:
                return super()._fill_pending_signal(pending, candle=candle)
            # Only the execution open is known here. The original signal and
            # absolute range stop are immutable; actual stop risk is checked
            # again after a gap and adverse entry slippage.
            direction = 1 if pending.action == "BUY" else -1
            fill = self._slipped_price(float(candle.open_price), action=pending.action)
            stop = float(pending.payload["absolute_range_stop"])
            risk = direction * (fill - stop)
            if not 0 < risk <= 100:
                self.block_counts["hourly_invalid_fill_stop_risk"] += 1
                return
            reward = math.ceil(float(pending.payload["range_width"]) * 1.5 / self.settings.tick_size) * self.settings.tick_size
            payload = {**pending.payload, "entry_price": fill, "stop_loss": stop,
                       "take_profit": fill + direction * reward,
                       "planned_risk_points": risk, "planned_reward_points": reward}
            # Signal price is an internal reference for bracket-distance and
            # proposed-stop checks. With reference=fill, the original engine
            # preserves the absolute boundary; original signal time is retained.
            super()._fill_pending_signal(replace(pending, signal_price=fill, payload=payload), candle=candle)

        def run(self):
            report = super().run()
            report["assumptions"].update(
                research_execution="Native complete hourly MNQ signals; next observed one-minute open",
                bracket_rule="absolute opposite range stop; target 1.5 range widths from slipped fill",
                replication_status="MNQ adaptation of an incompletely specified Micro Russell strategy",
                evaluation_session_clock="execution minute close; 08:00 signal eligible; fill-time entry gate unchanged")
            return report

    return HourlyRangeEngine


def main():
    # Disable environment/provider access before importing the application.
    runner.offline_environment()
    from app.services import bot_backtesting as replay
    old_specs, old_factory = replay._topbot_stream_specs, runner.make_engine_class
    old_validator = replay._validate_replay_configuration
    replay._topbot_stream_specs = lambda config: hourly_stream_specs(replay, config)
    replay._validate_replay_configuration = lambda config: validate_hourly_config(config, old_validator)
    runner.make_engine_class = lambda r, f, v: make_hourly_engine(r, f, v, base_factory=old_factory)
    try:
        return runner.main()
    finally:
        replay._topbot_stream_specs, runner.make_engine_class = old_specs, old_factory
        replay._validate_replay_configuration = old_validator


if __name__ == "__main__":
    raise SystemExit(main())
