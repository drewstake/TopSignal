"""Real-engine regression for the first permitted hourly decision at 08:00 ET."""
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.models import BotConfig
from app.services import bot_backtesting as replay
from app.services.topbot import TOPBOT_SETTINGS
from app.services.trading_day import TRADING_TZ, futures_session_is_open
from tools.fixtures import reddit_hourly_range as fixture
from tools.research_reddit_hourly import (
    hourly_stream_specs, make_hourly_engine, validate_hourly_config,
)


def candle(timestamp, *, unit, opening, high, low, close):
    return SimpleNamespace(
        candle_timestamp=timestamp, open_price=opening, high_price=high,
        low_price=low, close_price=close, volume=100, unit=unit, unit_number=1,
        symbol="MNQ", contract_id="CON.F.US.MNQ.U26", user_id="boundary-research",
        is_partial=False, live=False, fetched_at=None,
        source_instrument_id=1, source_raw_symbol="MNQU6",
    )


def test_hourly_close_at_eight_enters_at_eight_through_real_engine(monkeypatch):
    decision = datetime(2026, 7, 6, 8, tzinfo=TRADING_TZ).astimezone(timezone.utc)
    timestamps = []
    timestamp = decision-timedelta(hours=1)
    while len(timestamps) < 200:
        if futures_session_is_open(timestamp) and futures_session_is_open(timestamp+timedelta(minutes=59)):
            timestamps.append(timestamp)
        timestamp -= timedelta(hours=1)
    timestamps.reverse()
    hourly = []
    for i, timestamp in enumerate(timestamps):
        price = 1010 if i < 189 else 900
        hourly.append(candle(timestamp, unit="hour", opening=price,
                             high=price+5, low=price-5, close=price))
    hourly[-1].close_price = 906
    hourly[-1].high_price = 908
    assert hourly[-1].candle_timestamp == decision-timedelta(hours=1)
    assert fixture.evaluate(hourly, fixture.VARIANT).action == "BUY"

    execution = [
        candle(decision-timedelta(minutes=1), unit="minute", opening=905.5,
               high=906, low=905, close=906),
        candle(decision, unit="minute", opening=910, high=911, low=909, close=910),
        candle(decision+timedelta(minutes=1), unit="minute", opening=910,
               high=911, low=909, close=910),
    ]
    settings = deepcopy(TOPBOT_SETTINGS)
    settings.update(fixture.get_settings(fixture.VARIANT))
    config = BotConfig(
        id=1, user_id="boundary-research", account_id=1, name="Hourly boundary",
        provider="projectx", enabled=False, execution_mode="dry_run",
        contract_id="CON.F.US.MNQ.U26", **settings,
    )
    original_validator = replay._validate_replay_configuration
    monkeypatch.setattr(replay, "_topbot_stream_specs", lambda config: hourly_stream_specs(replay, config))
    monkeypatch.setattr(replay, "_validate_replay_configuration",
                        lambda config: validate_hourly_config(config, original_validator))
    engine = make_hourly_engine(replay, fixture, fixture.VARIANT)(
        config=config, candles=hourly,
        replay_streams={replay._topbot_asset_stream_key("hour", 1): hourly},
        execution_candles=execution,
        settings=replay.BacktestSettings(
            start=decision-timedelta(minutes=1), end=decision+timedelta(minutes=2),
            starting_balance=50000, commission_per_contract=.61,
            slippage_ticks=1, tick_size=.25, tick_value=.5,
        ),
    )
    # The preceding minute must allow evaluation at its CLOSE, while an actual
    # fill at its OPEN remains outside the configured entry window.
    assert engine.execution_in_session[0]
    assert not engine._event_in_configured_session(decision-timedelta(minutes=1))
    assert engine._event_in_configured_session(decision)
    assert not engine._can_enter(decision-timedelta(minutes=1))
    assert engine._can_enter(decision)
    result = engine.run()
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["entry_timestamp"] == decision.isoformat()
    assert trade["signal_timestamp"] == (decision-timedelta(hours=1)).isoformat()
    assert trade["entry_price"] == 910.25
    assert trade["stop_loss"] == 895
