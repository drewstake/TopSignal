"""Offline native-one-minute wrapper for the explicitly independent scalper proxy.

Only the running process's research stream specification is overridden. The
production engine and its normal five-minute TopBot preset remain unchanged.
All CLI flags and immutable output manifests come from research_topbot.
"""
from __future__ import annotations

from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from tools import research_topbot


def one_minute_stream_specs(config):
    from app.services import bot_backtesting as replay

    if str(config.timeframe_unit) != "minute" or int(config.timeframe_unit_number) != 1:
        raise ValueError("Scalper research wrapper requires native one-minute signals")
    key = replay._topbot_asset_stream_key("minute", 1)
    return {key: replay._TopBotReplayStreamSpec(
        key=key, unit="minute", unit_number=1, warmup_bars=200,
        contract_id=str(config.contract_id), symbol=config.symbol,
    )}


def one_minute_configuration_validator(original):
    def validate(config):
        from app.services import bot_backtesting as replay

        # Preserve every normal configuration check except the production
        # five-minute-only restriction. Authentic research streams stay 1m.
        if str(config.strategy_type) != "topbot_adaptive" or str(config.symbol) != "MNQ":
            raise ValueError("Scalper research is restricted to MNQ TopBot fixtures")
        one_minute_stream_specs(config)
        view = replay._SourceConfigView(config, strategy_type=str(config.strategy_type),
            strategy_params=config.strategy_params, fast_period=int(config.fast_period),
            slow_period=int(config.slow_period))
        view.timeframe_unit_number = 5
        original(view)
    return validate


def make_one_minute_engine(replay, fixture, variant, *, base_factory=research_topbot.make_engine_class):
    base = base_factory(replay, fixture, variant)

    class OneMinuteResearchEngine(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            # Evaluation happens at bar close. An opening-time mask suppresses
            # the 09:59 minute's 10:00 decision at this fixture's session start.
            self.execution_in_session = replay.np.fromiter(
                (self._event_in_configured_session(stamp) for stamp in self.execution_close_times),
                dtype=replay.np.bool_, count=len(self.execution_close_times))

        def run(self):
            report = super().run()
            report["assumptions"].update(
                strategy_revision=fixture.REVISION,
                replication_status="independent MNQ one-minute proxy, not original private five-second CFD strategy",
                signal_session_clock="minute close; includes 09:59 bar closing at 10:00",
                signal_market_fill="next nominal minute open if observed; missing minute cancels pending signal",
                calendar_clock_fill="first observed open at/after six-minute or known session deadline")
            return report

    return OneMinuteResearchEngine


def main():
    research_topbot.offline_environment()
    from app.services import bot_backtesting as replay

    original = replay._topbot_stream_specs
    original_validator = replay._validate_replay_configuration
    original_factory = research_topbot.make_engine_class
    replay._topbot_stream_specs = one_minute_stream_specs
    replay._validate_replay_configuration = one_minute_configuration_validator(original_validator)
    research_topbot.make_engine_class = lambda r, f, v: make_one_minute_engine(r, f, v, base_factory=original_factory)
    try:
        return research_topbot.main()
    finally:
        replay._topbot_stream_specs = original
        replay._validate_replay_configuration = original_validator
        research_topbot.make_engine_class = original_factory


if __name__ == "__main__":
    raise SystemExit(main())
