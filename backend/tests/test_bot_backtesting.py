import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
import app.services.bot_backtesting as backtesting_module
import app.services.bot_service as bot_service_module
from app.bot_schemas import BotBacktestIn, BotBacktestOut
from app.db import Base
from app.models import (
    BotBacktest,
    BotConfig,
    InstrumentMetadata,
    ProjectXMarketCandle,
)
from app.services.bot_backtesting import (
    InsufficientBacktestDataError,
    MalformedBacktestDataError,
    UnsupportedBacktestStrategyError,
    run_backtest,
)
from app.services.bot_service import SignalResult
from app.services.projectx_client import ProjectXClient


OWNER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"
CONTRACT_ID = "CON.F.US.MNQ.M26"
BASE_TIME = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def enable_legacy_projectx_backtest_fixtures(monkeypatch):
    """This module preserves pre-Databento engine fixtures, never app behavior."""

    monkeypatch.setattr(
        backtesting_module,
        "ALLOW_LEGACY_PROJECTX_BACKTEST_FIXTURES",
        True,
    )


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        InstrumentMetadata.__table__,
        BotConfig.__table__,
        ProjectXMarketCandle.__table__,
        BotBacktest.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=reversed(tables))
        engine.dispose()


def _config(**overrides: Any) -> BotConfig:
    values: dict[str, Any] = {
        "id": 101,
        "user_id": OWNER_ID,
        "account_id": 9001,
        "name": "Backtest fixture",
        "provider": "projectx",
        "enabled": False,
        "execution_mode": "dry_run",
        "strategy_type": "sma_cross",
        "strategy_params": {},
        "contract_id": CONTRACT_ID,
        "symbol": "MNQ",
        "timeframe_unit": "minute",
        "timeframe_unit_number": 5,
        "lookback_bars": 25,
        "fast_period": 1,
        "slow_period": 2,
        "order_size": 1,
        "max_contracts": 10,
        "max_daily_loss": 100_000,
        "max_trades_per_day": 100,
        "max_open_position": 10,
        "allowed_contracts": [CONTRACT_ID],
        "trading_start_time": "00:00",
        "trading_end_time": "23:59",
        "cooldown_seconds": 0,
        "max_data_staleness_seconds": 3600,
        "allow_market_depth": False,
    }
    values.update(overrides)
    return BotConfig(**values)


def _candle(
    timestamp: datetime,
    *,
    open_price: float = 100.0,
    high_price: float | None = None,
    low_price: float | None = None,
    close_price: float | None = None,
    volume: float = 100.0,
    user_id: str = OWNER_ID,
    is_partial: bool = False,
    unit: str = "minute",
    unit_number: int = 5,
    contract_id: str = CONTRACT_ID,
    symbol: str = "MNQ",
) -> ProjectXMarketCandle:
    close = open_price if close_price is None else close_price
    high = max(open_price, close) + 1.0 if high_price is None else high_price
    low = min(open_price, close) - 1.0 if low_price is None else low_price
    return ProjectXMarketCandle(
        user_id=user_id,
        contract_id=contract_id,
        symbol=symbol,
        live=False,
        unit=unit,
        unit_number=unit_number,
        candle_timestamp=timestamp,
        open_price=open_price,
        high_price=high,
        low_price=low,
        close_price=close,
        volume=volume,
        is_partial=is_partial,
        source="test",
    )


def _hold(candles: list[ProjectXMarketCandle]) -> SignalResult:
    latest = candles[-1] if candles else None
    return SignalResult(
        action="HOLD",
        reason="scripted hold",
        candle_timestamp=latest.candle_timestamp if latest is not None else None,
        price=float(latest.close_price) if latest is not None else None,
        raw_payload={},
    )


def _scripted_evaluator(
    script: dict[datetime, dict[str, Any]],
) -> Callable[[list[ProjectXMarketCandle]], SignalResult]:
    def evaluate(candles: list[ProjectXMarketCandle]) -> SignalResult:
        latest = candles[-1]
        timestamp = _utc(latest.candle_timestamp)
        instruction = script.get(timestamp)
        if instruction is None:
            return _hold(candles)
        return SignalResult(
            action=str(instruction["action"]),
            reason=str(instruction.get("reason") or "scripted signal"),
            candle_timestamp=timestamp,
            price=float(instruction.get("price", latest.close_price)),
            raw_payload=dict(instruction.get("payload") or {}),
        )

    return evaluate


def _run(
    candles: list[ProjectXMarketCandle],
    *,
    config: BotConfig | None = None,
    evaluator: Callable[[list[ProjectXMarketCandle]], SignalResult] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    starting_balance: float = 50_000,
    commission_per_contract: float = 0,
    slippage_ticks: float = 0,
    tick_size: float = 1,
    tick_value: float = 1,
    force_close_at_end: bool = True,
    replay_streams: dict[str, list[ProjectXMarketCandle]] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancellation_callback: Callable[[], bool] | None = None,
    include_evaluation_split: bool = True,
) -> dict[str, Any]:
    first = min(_utc(row.candle_timestamp) for row in candles)
    last = max(_utc(row.candle_timestamp) for row in candles)
    return run_backtest(
        config=config or _config(),
        candles=candles,
        start=start or first,
        end=end or (last + timedelta(minutes=5)),
        starting_balance=starting_balance,
        commission_per_contract=commission_per_contract,
        slippage_ticks=slippage_ticks,
        tick_size=tick_size,
        tick_value=tick_value,
        force_close_at_end=force_close_at_end,
        signal_evaluator=evaluator,
        replay_streams=replay_streams,
        progress_callback=progress_callback,
        cancellation_callback=cancellation_callback,
        include_evaluation_split=include_evaluation_split,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _persist_config(db_session, **overrides: Any) -> BotConfig:
    config = _config(id=None, **overrides)
    db_session.add(config)
    db_session.flush()
    return config


def test_signal_evaluation_receives_only_bars_closed_as_of_each_event():
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100 + index)
        for index in range(4)
    ]
    observed: list[list[datetime]] = []

    def recording_evaluator(candles: list[ProjectXMarketCandle]) -> SignalResult:
        observed.append([_utc(row.candle_timestamp) for row in candles])
        return _hold(candles)

    result = _run(
        bars,
        evaluator=recording_evaluator,
        end=BASE_TIME + timedelta(minutes=15),
    )

    assert result["range"]["bar_count"] == 3
    assert observed == [
        [BASE_TIME],
        [BASE_TIME, BASE_TIME + timedelta(minutes=5)],
        [
            BASE_TIME,
            BASE_TIME + timedelta(minutes=5),
            BASE_TIME + timedelta(minutes=10),
        ],
    ]
    assert all(BASE_TIME + timedelta(minutes=15) not in call for call in observed)


def test_real_strategy_reports_an_isolated_chronological_holdout():
    prices = [100, 99, 101, 102, 100, 98, 101, 103, 100, 97] * 2
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=price)
        for index, price in enumerate(prices)
    ]

    result = _run(bars)

    split = result["evaluation_split"]
    assert split["method"] == "chronological_80_20_fixed_parameters"
    assert split["validation_status"] == "diagnostic_only"
    assert split["in_sample"]["bar_count"] == 14
    assert split["holdout"]["bar_count"] == 4
    assert split["split_timestamp"] == (BASE_TIME + timedelta(minutes=80)).isoformat()
    assert set(split["holdout"]["metrics"]) == {
        "trade_count",
        "winning_trades",
        "losing_trades",
        "win_rate",
        "gross_pnl",
        "net_pnl",
        "profit_factor",
        "expectancy",
        "average_win",
        "average_loss",
        "payoff_ratio",
    }
    assert any("fresh cash, position" in note for note in split["notes"])
    assert any("not independent validation" in warning for warning in result["notes"])


def test_fast_replay_progress_is_exact_monotonic_and_throttled_to_percent_changes(monkeypatch):
    monkeypatch.setattr(backtesting_module, "monotonic", lambda: 10.0)
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100 + index)
        for index in range(250)
    ]
    progress: list[dict[str, Any]] = []

    result = run_backtest(
        config=_config(),
        candles=bars,
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=5 * len(bars)),
        starting_balance=50_000,
        commission_per_contract=0,
        slippage_ticks=0,
        tick_size=1,
        tick_value=1,
        signal_evaluator=_hold,
        progress_callback=progress.append,
    )

    replay = [event for event in progress if event["phase"] == "replaying"]
    percents = [event["percent"] for event in replay]
    assert percents[0] == 0
    assert percents[-1] == 100
    assert percents == sorted(set(percents))
    assert len(replay) <= 101
    assert replay[-1]["completed"] == result["range"]["bar_count"]
    assert replay[-1]["total"] == result["range"]["bar_count"]
    assert replay[-1]["remaining_percent"] == 0


def test_slow_replay_reports_candle_counts_before_first_percent(monkeypatch):
    clock = iter([10.0, 10.2, 11.0, 11.2, 12.0])
    monkeypatch.setattr(backtesting_module, "monotonic", lambda: next(clock))
    engine = object.__new__(backtesting_module.BacktestEngine)
    events = []
    engine.progress_callback = events.append
    engine._last_replay_progress_percent = -1
    engine._last_replay_progress_at = 0.0
    for completed in [0, 1, 50, 51, 100]:
        engine._report_replay_progress(completed=completed, total=504_360)
    assert [event["completed"] for event in events] == [0, 50, 100]
    assert all(event["percent"] == 0 for event in events)


def test_streamed_backtest_emits_progress_then_result_after_commit(monkeypatch):
    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    class FakeSession:
        committed = False
        rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    session = FakeSession()
    monkeypatch.setattr(main_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        main_module,
        "get_bot_config",
        lambda *_args, **_kwargs: SimpleNamespace(strategy_type="sma_cross"),
    )

    def fake_create(*_args, progress_callback, **_kwargs):
        progress_callback(
            {
                "phase": "replaying",
                "completed": 2,
                "total": 4,
                "percent": 50,
                "remaining_percent": 50,
            }
        )
        return SimpleNamespace(bar_count=4)

    monkeypatch.setattr(main_module, "create_bot_backtest", fake_create)
    monkeypatch.setattr(
        main_module,
        "serialize_bot_backtest",
        lambda _row: {"id": 99},
    )
    response = main_module._stream_trading_bot_backtest(
        FakeRequest(),
        user_id=OWNER_ID,
        bot_config_id=101,
        payload=BotBacktestIn(),
    )

    async def collect() -> str:
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    body = asyncio.run(collect())

    assert session.committed is True
    assert session.rolled_back is False
    assert body.index('"phase":"replaying"') < body.index('"phase":"complete"')
    assert body.index('"phase":"complete"') < body.index('"id":99')


def test_streamed_backtest_does_not_reserve_capacity_before_worker_starts(monkeypatch):
    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return True

    monkeypatch.setenv("BACKTEST_MAX_CONCURRENT_GLOBAL", "1")
    monkeypatch.setenv("BACKTEST_MAX_CONCURRENT_PER_USER", "1")

    response = main_module._stream_trading_bot_backtest(
        FakeRequest(),
        user_id=OWNER_ID,
        bot_config_id=101,
        payload=BotBacktestIn(),
    )
    lease = main_module._acquire_backtest_capacity(OWNER_ID)
    try:
        assert response.media_type == "text/event-stream"
    finally:
        lease.release()


def test_backtest_capacity_supersedes_same_user_and_limits_global(monkeypatch):
    monkeypatch.setenv("BACKTEST_MAX_CONCURRENT_GLOBAL", "2")
    monkeypatch.setenv("BACKTEST_MAX_CONCURRENT_PER_USER", "1")
    first = main_module._acquire_backtest_capacity(OWNER_ID)
    second = None
    replacement = None
    final = None
    try:
        replacement = main_module._acquire_backtest_capacity(OWNER_ID)
        assert first.is_cancelled() is True
        assert first.released is True
        assert replacement.is_cancelled() is False

        second = main_module._acquire_backtest_capacity(OTHER_USER_ID)
        with pytest.raises(HTTPException) as global_error:
            main_module._acquire_backtest_capacity("33333333-3333-3333-3333-333333333333")
        assert global_error.value.status_code == 429

        replacement.release()
        final = main_module._acquire_backtest_capacity(OWNER_ID)
    finally:
        first.release()
        if second is not None:
            second.release()
        if replacement is not None:
            replacement.release()
        if final is not None:
            final.release()


def test_backtest_cooperatively_stops_when_superseded():
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100 + index)
        for index in range(10)
    ]
    cancelled = False

    def progress(event: dict[str, Any]) -> None:
        nonlocal cancelled
        if event.get("phase") == "replaying" and int(event.get("completed") or 0) >= 2:
            cancelled = True

    with pytest.raises(
        backtesting_module.BacktestSupersededError,
        match="backtest_superseded_by_newer_run",
    ):
        _run(
            bars,
            evaluator=_hold,
            progress_callback=progress,
            cancellation_callback=lambda: cancelled,
        )


def test_backtest_strategy_override_does_not_mutate_saved_config():
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategy_params": {
                "ema_trend_pullback": {"rsi_period": 9},
            }
        },
    )
    payload = BotBacktestIn(strategy_type="ema_trend_pullback")

    effective = backtesting_module._config_for_backtest_request(config, payload)

    assert effective is not config
    assert effective.strategy_type == "ema_trend_pullback"
    assert effective.strategy_params["rsi_period"] == 14
    assert config.strategy_type == "topbot_adaptive"
    assert config.strategy_params["source_strategy_params"]["ema_trend_pullback"] == {
        "rsi_period": 9
    }



def test_backtest_instrument_override_does_not_mutate_saved_config():
    config = _config(
        strategy_type="sma_cross",
        symbol="MNQ",
        contract_id="CON.F.US.MNQ.M26",
    )
    payload = BotBacktestIn(instrument="ES")

    effective = backtesting_module._config_for_backtest_request(config, payload)

    assert effective is not config
    assert effective.symbol == "ES"
    assert effective.contract_id == "DATABENTO.CONTINUOUS.ES"
    assert effective.allowed_contracts == ["DATABENTO.CONTINUOUS.ES"]
    assert backtesting_module._contract_is_allowed(effective) is True
    assert effective.strategy_type == "sma_cross"
    assert config.symbol == "MNQ"
    assert config.contract_id == "CON.F.US.MNQ.M26"
    assert config.allowed_contracts == [CONTRACT_ID]


def test_backtest_instrument_override_can_open_a_replay_trade():
    config = _config(
        strategy_type="sma_cross",
        symbol="MNQ",
        contract_id="CON.F.US.MNQ.M26",
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="09:30",
        trading_end_time="15:45",
    )
    effective = backtesting_module._config_for_backtest_request(
        config,
        BotBacktestIn(instrument="NQ"),
    )
    bars = [
        _candle(
            BASE_TIME + timedelta(minutes=5 * index),
            close_price=100 + index,
            contract_id="DATABENTO.CONTINUOUS.NQ",
            symbol="NQ",
        )
        for index in range(3)
    ]

    result = _run(
        bars,
        config=effective,
        evaluator=_scripted_evaluator({BASE_TIME: {"action": "BUY", "price": 100}}),
    )

    assert result["metrics"]["trade_count"] == 1
    assert not any("contract not allowed" in warning for warning in result["warnings"])


def test_topbot_defers_replay_when_requested_start_precedes_available_warmup():
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100)
        for index in range(230)
    ]
    result = _run(
        bars,
        config=_config(
            strategy_type="topbot_adaptive",
            strategy_params={"source_strategies": ["sma_cross"]},
            lookback_bars=25,
        ),
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=1150),
    )

    assert result["range"]["start"] == (BASE_TIME + timedelta(minutes=995)).isoformat()
    assert result["range"]["bar_count"] == 31
    assert any("Used the first 199 candle(s) for warmup" in note for note in result["notes"])
    assert result["data_quality"]["warmup_available"] == 200
    assert result["data_quality"]["warmup_required"] == 200
    assert result["data_quality"]["available_start"] == BASE_TIME.isoformat()
    assert not any("warmup" in warning for warning in result["warnings"])
    assert not any("Stored candles began" in warning for warning in result["warnings"])



def test_topbot_only_evaluates_signals_inside_the_configured_session():
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    bars = [
        _candle(session_open + timedelta(minutes=5 * index), close_price=100)
        for index in range(-2, 2)
    ]
    evaluated: list[datetime] = []

    def evaluator(candles):
        evaluated.append(_utc(candles[-1].candle_timestamp))
        return _hold(candles)

    result = _run(
        bars,
        config=_config(
            strategy_type="topbot_adaptive",
            trading_start_time="09:30",
            trading_end_time="15:45",
        ),
        evaluator=evaluator,
        start=session_open - timedelta(minutes=10),
        end=session_open + timedelta(minutes=10),
    )

    assert result["range"]["bar_count"] == 4
    assert evaluated == [session_open, session_open + timedelta(minutes=5)]


def test_orb_evaluator_keeps_the_true_session_open_beyond_configured_lookback():
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    bars = [
        _candle(session_open + timedelta(minutes=5 * index), close_price=100 + index / 10)
        for index in range(35)
    ]
    observed_first_timestamps: list[datetime] = []

    def recording_evaluator(candles: list[ProjectXMarketCandle]) -> SignalResult:
        observed_first_timestamps.append(_utc(candles[0].candle_timestamp))
        return _hold(candles)

    _run(
        bars,
        config=_config(
            strategy_type="orb_fibonacci_pullback",
            lookback_bars=25,
            trading_start_time="09:30",
            trading_end_time="15:45",
        ),
        evaluator=recording_evaluator,
    )

    assert observed_first_timestamps[-1] == session_open


def test_session_vwap_evaluator_keeps_full_trading_day_prefix():
    session_open = datetime(2026, 7, 6, 22, 0, tzinfo=timezone.utc)
    bars = [
        _candle(
            session_open + timedelta(minutes=index),
            close_price=100 + index / 100,
            unit_number=1,
        )
        for index in range(40)
    ]
    observed_first_timestamps: list[datetime] = []

    def recording_evaluator(candles: list[ProjectXMarketCandle]) -> SignalResult:
        observed_first_timestamps.append(_utc(candles[0].candle_timestamp))
        return _hold(candles)

    _run(
        bars,
        config=_config(
            strategy_type="vwap_atr_mean_reversion",
            lookback_bars=25,
            timeframe_unit_number=1,
        ),
        evaluator=recording_evaluator,
        end=session_open + timedelta(minutes=40),
    )

    assert observed_first_timestamps[-1] == session_open


def test_session_vwap_rejects_an_interior_prefix_gap():
    session_open = datetime(2026, 7, 6, 22, 0, tzinfo=timezone.utc)
    bars = [
        _candle(
            session_open + timedelta(minutes=index),
            close_price=100 + index / 100,
            unit_number=1,
        )
        for index in range(32)
        if index != 10
    ]

    with pytest.raises(InsufficientBacktestDataError, match="missing session candle"):
        _run(
            bars,
            config=_config(
                strategy_type="vwap_atr_mean_reversion",
                lookback_bars=25,
                timeframe_unit_number=1,
            ),
            evaluator=_hold,
            start=session_open + timedelta(minutes=30),
            end=session_open + timedelta(minutes=32),
        )


def test_pending_signal_expires_at_session_end_instead_of_filling_next_day():
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    session_end = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    signal_bar_start = session_end - timedelta(minutes=5)
    next_open = datetime(2026, 7, 7, 13, 30, tzinfo=timezone.utc)
    bars = [
        _candle(session_open + timedelta(minutes=5 * index), close_price=100)
        for index in range(6)
    ]
    bars.append(_candle(next_open, close_price=100))

    def end_of_session_signal(candles: list[ProjectXMarketCandle]) -> SignalResult:
        if candles and _utc(candles[-1].candle_timestamp) == signal_bar_start:
            latest = candles[-1]
            return SignalResult(
                action="BUY",
                reason="session-end signal",
                candle_timestamp=latest.candle_timestamp,
                price=100,
                raw_payload={"stop_loss": 99, "take_profit": 101},
            )
        return _hold(candles)

    result = _run(
        bars,
        config=_config(
            strategy_type="orb_fibonacci_pullback",
            trading_start_time="09:30",
            trading_end_time="10:00",
        ),
        evaluator=end_of_session_signal,
        end=next_open + timedelta(minutes=5),
    )

    assert result["trades"] == []
    assert any("stale session signal" in warning for warning in result["notes"])


def test_signal_available_at_session_open_fills_on_the_opening_bar():
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    signal_bar_start = session_open - timedelta(minutes=5)
    bars = [
        _candle(
            signal_bar_start + timedelta(minutes=5 * index),
            close_price=100 + index,
        )
        for index in range(3)
    ]

    result = _run(
        bars,
        config=_config(trading_start_time="09:30", trading_end_time="15:45"),
        evaluator=_scripted_evaluator(
            {signal_bar_start: {"action": "BUY", "price": 100}}
        ),
    )

    assert result["metrics"]["trade_count"] == 1
    assert result["trades"][0]["signal_timestamp"] == signal_bar_start.isoformat()
    assert result["trades"][0]["entry_timestamp"] == session_open.isoformat()


def test_off_session_decision_is_not_misclassified_as_stale():
    before_open = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    bars = [
        _candle(before_open + timedelta(minutes=5 * index), close_price=100 + index)
        for index in range(3)
    ]

    result = _run(
        bars,
        config=_config(trading_start_time="09:30", trading_end_time="15:45"),
        evaluator=_scripted_evaluator({before_open: {"action": "BUY", "price": 100}}),
    )

    assert result["trades"] == []
    assert any("outside session" in warning for warning in result["notes"])
    assert not any("stale session signal" in warning for warning in result["notes"])


def test_missing_required_session_open_is_rejected_instead_of_approximated():
    late_start = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    bars = [
        _candle(late_start + timedelta(minutes=5 * index), close_price=100 + index)
        for index in range(2)
    ]

    with pytest.raises(InsufficientBacktestDataError, match="incomplete_session_history"):
        _run(
            bars,
            config=_config(
                strategy_type="orb_fibonacci_pullback",
                trading_start_time="09:30",
                trading_end_time="15:45",
            ),
            evaluator=_hold,
        )


def test_missing_orb_opening_range_bar_is_rejected():
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    bars = [
        _candle(session_open, close_price=100),
        _candle(session_open + timedelta(minutes=10), close_price=101),
        _candle(session_open + timedelta(minutes=15), close_price=102),
    ]

    with pytest.raises(InsufficientBacktestDataError, match="missing opening-range candle"):
        _run(
            bars,
            config=_config(
                strategy_type="orb_fibonacci_pullback",
                trading_start_time="09:30",
                trading_end_time="15:45",
            ),
            evaluator=_hold,
        )


def test_signal_fills_at_next_bar_open_never_on_signal_bar():
    bars = [
        _candle(BASE_TIME, open_price=100, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=103, close_price=104),
        _candle(BASE_TIME + timedelta(minutes=10), open_price=105, close_price=106),
    ]
    evaluator = _scripted_evaluator({BASE_TIME: {"action": "BUY", "price": 100}})

    result = _run(bars, evaluator=evaluator)

    trade = result["trades"][0]
    assert trade["signal_timestamp"] == BASE_TIME.isoformat()
    assert trade["entry_timestamp"] == (BASE_TIME + timedelta(minutes=5)).isoformat()
    assert trade["entry_price"] == 103
    assert trade["entry_timestamp"] > trade["signal_timestamp"]


def test_repeated_runs_are_byte_for_byte_deterministic():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=101, close_price=102),
        _candle(BASE_TIME + timedelta(minutes=10), open_price=102, close_price=104),
    ]
    evaluator = _scripted_evaluator({BASE_TIME: {"action": "BUY", "price": 100}})

    first = _run(
        bars,
        evaluator=evaluator,
        commission_per_contract=1.25,
        slippage_ticks=0.5,
        tick_size=0.25,
        tick_value=0.5,
    )
    second = _run(
        list(reversed(bars)),
        evaluator=evaluator,
        commission_per_contract=1.25,
        slippage_ticks=0.5,
        tick_size=0.25,
        tick_value=0.5,
    )

    assert first == second


def test_fees_slippage_quantity_and_tick_value_are_applied_to_both_sides():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(
            BASE_TIME + timedelta(minutes=5),
            open_price=100,
            high_price=102,
            low_price=99.5,
            close_price=101.5,
        ),
    ]
    evaluator = _scripted_evaluator({BASE_TIME: {"action": "BUY", "price": 100}})

    result = _run(
        bars,
        config=_config(order_size=2, max_contracts=2, max_open_position=2),
        evaluator=evaluator,
        commission_per_contract=3,
        slippage_ticks=2,
        tick_size=0.25,
        tick_value=5,
    )

    trade = result["trades"][0]
    assert trade["quantity"] == 2
    assert trade["entry_price"] == 100.5
    assert trade["exit_price"] == 101
    assert trade["gross_pnl"] == 20
    assert trade["commission"] == 12
    assert trade["net_pnl"] == 8
    assert result["metrics"]["gross_pnl"] == 20
    assert result["metrics"]["net_pnl"] == 8
    assert result["metrics"]["total_commission"] == 12
    assert result["equity_curve"][-1]["equity"] == 50_008


@pytest.mark.parametrize("quantity", [1, 5, 10, 20])
def test_default_mnq_fees_charge_122_cents_round_trip_per_contract(quantity):
    request = BotBacktestIn()
    assert request.commission_per_contract == 0.61
    assert BotBacktestIn(commission_per_contract=0).commission_per_contract == 0
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=100,
                high_price=100, low_price=100, close_price=100),
    ]
    result = _run(
        bars,
        config=_config(order_size=quantity, max_contracts=quantity, max_open_position=quantity),
        evaluator=_scripted_evaluator({BASE_TIME: {"action": "BUY", "price": 100}}),
        commission_per_contract=request.commission_per_contract,
        slippage_ticks=0, tick_size=0.25, tick_value=0.5,
    )
    trade = result["trades"][0]
    assert trade["gross_pnl"] == 0
    assert trade["commission"] == pytest.approx(1.22 * quantity)
    assert trade["net_pnl"] == pytest.approx(-1.22 * quantity)
    assert result["metrics"]["total_commission"] == pytest.approx(1.22 * quantity)
    assert result["equity_curve"][-1]["equity"] == pytest.approx(50_000 - 1.22 * quantity)


def test_opposite_targetless_signal_flattens_without_fictitious_reversal_entry():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=100, close_price=101),
        _candle(BASE_TIME + timedelta(minutes=10), open_price=102, close_price=101),
        _candle(BASE_TIME + timedelta(minutes=15), open_price=101, close_price=100),
    ]
    evaluator = _scripted_evaluator(
        {
            BASE_TIME: {"action": "BUY", "price": 100},
            BASE_TIME + timedelta(minutes=5): {"action": "SELL", "price": 101},
        }
    )

    result = _run(bars, evaluator=evaluator, tick_size=1, tick_value=10)

    assert [trade["side"] for trade in result["trades"]] == ["long"]
    assert [trade["gross_pnl"] for trade in result["trades"]] == [20]
    assert result["trades"][0]["exit_reason"] == "opposite_signal_flatten"
    assert result["metrics"]["trade_count"] == 1
    assert result["metrics"]["gross_pnl"] == 20
    assert result["metrics"]["long"]["trade_count"] == 1
    assert result["metrics"]["long"]["net_pnl"] == 20
    assert result["metrics"]["short"]["trade_count"] == 0
    assert result["metrics"]["short"]["net_pnl"] == 0
    assert sum(row["net_pnl"] for row in result["daily_results"]) == 20
    assert sum(row["net_pnl"] for row in result["monthly_results"]) == 20


def test_target_aware_atomic_reversal_is_blocked_like_live_routing():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=100, close_price=101),
        _candle(BASE_TIME + timedelta(minutes=10), open_price=102, close_price=101),
        _candle(BASE_TIME + timedelta(minutes=15), open_price=101, close_price=100),
    ]
    evaluator = _scripted_evaluator(
        {
            BASE_TIME: {
                "action": "BUY",
                "price": 100,
                "payload": {"target_position_qty": 1},
            },
            BASE_TIME + timedelta(minutes=5): {
                "action": "SELL",
                "price": 101,
                "payload": {"target_position_qty": -1, "signal_category": "reversal"},
            },
        }
    )

    result = _run(bars, evaluator=evaluator, tick_size=1, tick_value=10)

    assert [trade["side"] for trade in result["trades"]] == ["long"]
    assert result["trades"][0]["exit_reason"] == "forced_end_of_test"
    assert any("atomic reversal not supported" in warning for warning in result["notes"])


def test_delivery_change_segments_history_and_flattens_before_new_contract_prices():
    bars = [
        _candle(BASE_TIME, open_price=100, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=100, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=10), open_price=120, close_price=120),
        _candle(BASE_TIME + timedelta(minutes=15), open_price=121, close_price=121),
    ]
    for row in bars[:2]:
        row.source_raw_symbol = "MNQM6"
        row.source_instrument_id = 101
    for row in bars[2:]:
        row.source_raw_symbol = "MNQU6"
        row.source_instrument_id = 202

    observed_histories: list[list[str]] = []

    def evaluator(candles: list[ProjectXMarketCandle]) -> SignalResult:
        observed_histories.append([str(row.source_raw_symbol) for row in candles])
        if _utc(candles[-1].candle_timestamp) == BASE_TIME:
            return SignalResult(
                action="BUY",
                reason="scripted",
                candle_timestamp=BASE_TIME,
                price=100,
                raw_payload={},
            )
        return _hold(candles)

    result = _run(bars, evaluator=evaluator, tick_size=1, tick_value=10)

    assert result["trades"][0]["exit_reason"] == "contract_roll"
    assert result["trades"][0]["exit_price"] == 100
    assert result["trades"][0]["gross_pnl"] == 0
    assert observed_histories[2] == ["MNQU6"]
    assert all("MNQM6" not in history for history in observed_histories[2:])
    assert any("delivery change" in warning for warning in result["notes"])


def test_resting_target_executes_before_a_queued_gap_reversal():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(
            BASE_TIME + timedelta(minutes=5),
            open_price=100,
            high_price=100.5,
            low_price=99.5,
            close_price=100,
        ),
        _candle(
            BASE_TIME + timedelta(minutes=10),
            open_price=110,
            high_price=111,
            low_price=109,
            close_price=110,
        ),
    ]
    evaluator = _scripted_evaluator(
        {
            BASE_TIME: {
                "action": "BUY",
                "price": 100,
                "payload": {"stop_loss": 99, "take_profit": 101},
            },
            BASE_TIME + timedelta(minutes=5): {"action": "SELL", "price": 100},
        }
    )

    result = _run(bars, evaluator=evaluator)

    assert result["trades"][0]["exit_reason"] == "take_profit"
    assert result["trades"][0]["exit_price"] == 101
    assert result["trades"][1]["side"] == "short"


def test_daily_trade_and_position_limits_block_entries_without_silent_sizing():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=100, close_price=101),
        _candle(BASE_TIME + timedelta(minutes=10), open_price=102, close_price=101),
        _candle(BASE_TIME + timedelta(minutes=15), open_price=101, close_price=100),
    ]
    reversal = _scripted_evaluator(
        {
            BASE_TIME: {"action": "BUY", "price": 100},
            BASE_TIME + timedelta(minutes=5): {"action": "SELL", "price": 101},
            BASE_TIME + timedelta(minutes=10): {"action": "SELL", "price": 101},
        }
    )

    limited = _run(
        bars,
        config=_config(max_trades_per_day=1),
        evaluator=reversal,
    )
    oversized = _run(
        bars,
        config=_config(order_size=2, max_contracts=1, max_open_position=1),
        evaluator=_scripted_evaluator({BASE_TIME: {"action": "BUY", "price": 100}}),
    )

    assert [trade["side"] for trade in limited["trades"]] == ["long"]
    assert any("max trades per day" in warning for warning in limited["notes"])
    assert oversized["trades"] == []
    assert any("max contracts" in warning for warning in oversized["notes"])


@pytest.mark.parametrize(
    ("action", "stop", "target", "expected_side"),
    [
        ("BUY", 99.0, 101.0, "long"),
        ("SELL", 101.0, 99.0, "short"),
    ],
)
def test_same_bar_stop_and_target_uses_conservative_stop_first_rule(
    action: str,
    stop: float,
    target: float,
    expected_side: str,
):
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(
            BASE_TIME + timedelta(minutes=5),
            open_price=100,
            high_price=102,
            low_price=98,
            close_price=100,
        ),
    ]
    evaluator = _scripted_evaluator(
        {
            BASE_TIME: {
                "action": action,
                "price": 100,
                "payload": {"stop_loss": stop, "take_profit": target},
            }
        }
    )

    result = _run(bars, evaluator=evaluator)

    trade = result["trades"][0]
    assert trade["side"] == expected_side
    assert trade["exit_price"] == stop
    assert trade["exit_reason"] == "stop_loss_same_bar_conservative"
    assert trade["gross_pnl"] == -1
    assert trade["mae"] == 1
    assert result["assumptions"]["same_bar_exit_rule"] == (
        "stop_first_when_stop_and_target_are_both_touched"
    )


def test_bracket_tick_distances_are_anchored_to_the_actual_gap_fill():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(
            BASE_TIME + timedelta(minutes=5),
            open_price=105,
            high_price=106.25,
            low_price=104,
            close_price=106,
        ),
    ]
    evaluator = _scripted_evaluator(
        {
            BASE_TIME: {
                "action": "BUY",
                "price": 100,
                "payload": {"stop_loss": 98.8, "take_profit": 101.1},
            }
        }
    )

    result = _run(
        bars,
        evaluator=evaluator,
        tick_size=0.25,
        tick_value=0.5,
    )

    trade = result["trades"][0]
    assert trade["entry_price"] == 105
    assert trade["exit_price"] == 106
    assert trade["exit_reason"] == "take_profit"
    assert result["assumptions"]["bracket_rule"] == (
        "evaluator_levels_become_whole_tick_distances_anchored_to_actual_entry_fill"
    )


def test_decimal_tick_target_touch_is_not_missed_by_float_artifacts():
    bars = [
        _candle(BASE_TIME, open_price=25.1, close_price=25.1),
        _candle(
            BASE_TIME + timedelta(minutes=5),
            open_price=25.1,
            high_price=25.2,
            low_price=25.05,
            close_price=25.15,
        ),
    ]
    evaluator = _scripted_evaluator(
        {
            BASE_TIME: {
                "action": "BUY",
                "price": 25.1,
                "payload": {"stop_loss": 25.0, "take_profit": 25.2},
            }
        }
    )

    result = _run(bars, evaluator=evaluator, tick_size=0.1, tick_value=1)

    assert result["trades"][0]["exit_reason"] == "take_profit"
    assert result["trades"][0]["exit_price"] == 25.2


def test_final_open_position_is_forced_closed_or_explicitly_left_open():
    bars = [
        _candle(BASE_TIME, close_price=100),
        _candle(BASE_TIME + timedelta(minutes=5), open_price=101, close_price=102),
        _candle(BASE_TIME + timedelta(minutes=10), open_price=103, close_price=104),
    ]
    evaluator = _scripted_evaluator({BASE_TIME: {"action": "BUY", "price": 100}})

    forced = _run(bars, evaluator=evaluator, force_close_at_end=True)
    left_open = _run(bars, evaluator=evaluator, force_close_at_end=False)

    assert forced["trades"][0]["exit_reason"] == "forced_end_of_test"
    assert forced["trades"][0]["exit_timestamp"] == (
        BASE_TIME + timedelta(minutes=15)
    ).isoformat()
    assert forced["trades"][0]["exit_price"] == 104
    assert forced["assumptions"]["final_position_handling"] == (
        "forced_close_at_last_bar_close"
    )
    assert left_open["metrics"]["trade_count"] == 0
    assert left_open["assumptions"]["final_position_handling"] == "left_open"
    assert any("position remained open" in warning for warning in left_open["warnings"])


def test_insufficient_closed_data_is_rejected_and_partial_bars_are_never_replayed():
    with pytest.raises(InsufficientBacktestDataError, match="found 1"):
        _run([_candle(BASE_TIME)], evaluator=_hold)

    bars = [
        _candle(BASE_TIME),
        _candle(BASE_TIME + timedelta(minutes=5)),
        _candle(BASE_TIME + timedelta(minutes=10), is_partial=True),
    ]
    result = _run(bars, evaluator=_hold)

    assert result["range"]["bar_count"] == 2
    assert any("Excluded 1 partial candle" in warning for warning in result["warnings"])


def test_input_fingerprints_exclude_effectively_partial_cached_bars():
    premature = _candle(BASE_TIME)
    premature.fetched_at = BASE_TIME + timedelta(minutes=1)
    premature.raw_payload = {"t": BASE_TIME.isoformat()}
    closed = _candle(BASE_TIME + timedelta(minutes=5))
    closed.fetched_at = BASE_TIME + timedelta(minutes=10)
    closed.raw_payload = {"t": closed.candle_timestamp.isoformat()}

    assert backtesting_module.candle_input_fingerprint(
        [premature, closed]
    ) == backtesting_module.candle_input_fingerprint([closed])
    assert backtesting_module.candle_stream_input_fingerprint(
        {"asset:minute:5": [premature, closed]}
    ) == backtesting_module.candle_stream_input_fingerprint(
        {"asset:minute:5": [closed]}
    )


@pytest.mark.parametrize(
    ("bars", "error_fragment"),
    [
        (
            [_candle(BASE_TIME), _candle(BASE_TIME)],
            "duplicate_candle_timestamp",
        ),
        (
            [
                _candle(BASE_TIME, open_price=100, high_price=99, low_price=98, close_price=100),
                _candle(BASE_TIME + timedelta(minutes=5)),
            ],
            "invalid_candle_high",
        ),
        (
            [
                _candle(BASE_TIME, volume=-1),
                _candle(BASE_TIME + timedelta(minutes=5)),
            ],
            "negative_candle_volume",
        ),
        (
            [
                _candle(BASE_TIME),
                _candle(BASE_TIME + timedelta(minutes=5)),
            ],
            "mixed_contract_candles",
        ),
        (
            [
                _candle(BASE_TIME),
                _candle(BASE_TIME + timedelta(minutes=1)),
            ],
            "overlapping_candles",
        ),
    ],
)
def test_malformed_candle_streams_are_rejected(bars, error_fragment: str):
    if error_fragment == "mixed_contract_candles":
        bars[-1].contract_id = "CON.F.US.MES.M26"
    with pytest.raises(MalformedBacktestDataError, match=error_fragment):
        _run(bars, evaluator=_hold)


def test_unsupported_strategy_fails_explicitly_without_approximation():
    bars = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=5))]

    with pytest.raises(
        UnsupportedBacktestStrategyError,
        match="strategy_not_supported_for_backtesting:support_resistance",
    ):
        _run(bars, config=_config(strategy_type="support_resistance"))

    with pytest.raises(
        UnsupportedBacktestStrategyError,
        match="strategy_not_supported_for_backtesting:support_resistance",
    ):
        _run(
            bars,
            config=_config(strategy_type="support_resistance"),
            evaluator=_hold,
        )














def test_topbot_cache_preparation_skips_streams_that_already_cover_the_replay(
    db_session,
    monkeypatch,
):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["support_resistance"]},
        lookback_bars=25,
    )
    db_session.add_all([_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=5))])
    db_session.commit()

    class UnexpectedClient:
        def retrieve_bars(self, **_kwargs):
            raise AssertionError("covered streams should not be fetched again")

    monkeypatch.setattr(
        backtesting_module,
        "_cached_replay_stream_covers",
        lambda *_args, **_kwargs: True,
    )

    prepared = backtesting_module.prepare_bot_backtest_data(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=config.id,
        payload=BotBacktestIn(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(minutes=10),
        ),
        client=UnexpectedClient(),
    )

    assert prepared == 0


def test_topbot_cache_preparation_refetches_a_truncated_primary_stream(
    db_session,
    monkeypatch,
):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["sma_cross"]},
        lookback_bars=25,
    )
    db_session.add_all(
        [
            _candle(BASE_TIME),
            _candle(BASE_TIME + timedelta(minutes=5)),
        ]
    )
    db_session.commit()
    config.name = "Pending backtest preparation write"
    fetches: list[dict[str, Any]] = []

    def record_fetch(provider_db, **kwargs):
        assert db_session.in_transaction() is False
        assert provider_db is not db_session
        assert provider_db.in_transaction() is False
        fetches.append(kwargs)
        return []

    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "fetch_and_store_market_candles",
        record_fetch,
    )

    prepared = backtesting_module.prepare_bot_backtest_data(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=config.id,
        payload=BotBacktestIn(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(minutes=20),
        ),
        client=object(),
    )

    assert prepared == 1
    assert len(fetches) == 1
    assert fetches[0]["contract_id"] == CONTRACT_ID
    db_session.expire_all()
    assert db_session.get(BotConfig, int(config.id)).name == "Pending backtest preparation write"


@pytest.mark.parametrize(
    "execution_offsets",
    [
        [5, 10, 15],
        [0, 5, 10],
    ],
    ids=["leading-bar", "trailing-bar"],
)
def test_primary_cache_coverage_rejects_one_missing_boundary_bar(
    db_session,
    monkeypatch,
    execution_offsets,
):
    monkeypatch.setattr(
        backtesting_module,
        "_cached_replay_stream_covers",
        lambda *_args, **_kwargs: True,
    )
    execution_rows = [
        _candle(BASE_TIME + timedelta(minutes=offset))
        for offset in execution_offsets
    ]

    assert not backtesting_module._cached_primary_stream_covers(
        db_session,
        user_id=OWNER_ID,
        contract_id=CONTRACT_ID,
        unit="minute",
        unit_number=5,
        fetch_start=BASE_TIME - timedelta(days=1),
        requested_start=BASE_TIME,
        requested_end=BASE_TIME + timedelta(minutes=20),
        warmup_bars=1,
        execution_rows=execution_rows,
    )


def test_topbot_cache_preparation_pages_the_entire_provider_range(
    db_session,
    monkeypatch,
):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["sma_cross"]},
        lookback_bars=25,
    )
    primary_key = backtesting_module._topbot_asset_stream_key("minute", 5)
    primary_spec = backtesting_module._TopBotReplayStreamSpec(
        key=primary_key,
        unit="minute",
        unit_number=5,
        warmup_bars=1,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
    )
    fetches: list[dict[str, Any]] = []

    monkeypatch.setattr(
        backtesting_module,
        "_topbot_stream_specs",
        lambda _config: {primary_key: primary_spec},
    )
    monkeypatch.setattr(backtesting_module, "MAX_PROVIDER_FETCH_BARS", 20)
    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "fetch_and_store_market_candles",
        lambda *_args, **kwargs: fetches.append(kwargs) or [],
    )

    prepared = backtesting_module.prepare_bot_backtest_data(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=config.id,
        payload=BotBacktestIn(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(minutes=50),
        ),
        client=object(),
    )

    assert prepared == 1
    assert len(fetches) == 5
    assert fetches[0]["start"] == BASE_TIME - timedelta(minutes=375)
    assert fetches[-1]["end"] == BASE_TIME + timedelta(minutes=50)
    assert all(call["limit"] == 20 for call in fetches)
    assert all(
        current["start"] == previous["end"]
        for previous, current in zip(fetches, fetches[1:])
    )




def test_topbot_cache_coverage_rejects_overlapping_candles(db_session):
    canonical = [
        _candle(
            BASE_TIME - timedelta(hours=4 * index),
            unit="hour",
            unit_number=4,
        )
        for index in range(26, 0, -1)
    ]
    overlap = _candle(
        BASE_TIME - timedelta(hours=6),
        unit="hour",
        unit_number=4,
    )
    db_session.add_all([*canonical, overlap])
    db_session.commit()

    assert not backtesting_module._cached_replay_stream_covers(
        db_session,
        user_id=OWNER_ID,
        contract_id=CONTRACT_ID,
        unit="hour",
        unit_number=4,
        fetch_start=BASE_TIME - timedelta(days=7),
        requested_start=BASE_TIME,
        first_event=BASE_TIME + timedelta(minutes=5),
        last_event=BASE_TIME + timedelta(minutes=10),
        warmup_bars=25,
    )


def test_topbot_cache_coverage_rejects_an_open_session_gap(db_session):
    db_session.add_all(
        [
            _candle(BASE_TIME - timedelta(minutes=5)),
            _candle(BASE_TIME),
            _candle(BASE_TIME + timedelta(minutes=10)),
        ]
    )
    db_session.commit()

    assert not backtesting_module._cached_replay_stream_covers(
        db_session,
        user_id=OWNER_ID,
        contract_id=CONTRACT_ID,
        unit="minute",
        unit_number=5,
        fetch_start=BASE_TIME - timedelta(minutes=5),
        requested_start=BASE_TIME,
        first_event=BASE_TIME + timedelta(minutes=5),
        last_event=BASE_TIME + timedelta(minutes=15),
        warmup_bars=1,
    )


def test_topbot_cache_coverage_accepts_a_weekend_market_closure(db_session):
    friday_tail = _candle(datetime(2026, 7, 10, 20, 55, tzinfo=timezone.utc))
    sunday_open = _candle(datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc))
    for row in (friday_tail, sunday_open):
        row.raw_payload = {"isPartial": False}
    db_session.add_all([friday_tail, sunday_open])
    db_session.commit()

    assert backtesting_module._cached_replay_stream_covers(
        db_session,
        user_id=OWNER_ID,
        contract_id=CONTRACT_ID,
        unit="minute",
        unit_number=5,
        fetch_start=friday_tail.candle_timestamp,
        requested_start=sunday_open.candle_timestamp,
        first_event=_utc(sunday_open.candle_timestamp) + timedelta(minutes=5),
        last_event=_utc(sunday_open.candle_timestamp) + timedelta(minutes=5),
        warmup_bars=1,
    )




def test_topbot_cache_preparation_rejects_monthly_primary_candles(db_session):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        timeframe_unit="month",
        timeframe_unit_number=1,
    )

    with pytest.raises(
        backtesting_module.BacktestConfigurationError,
        match="TopBot Adaptive requires 5-minute candles",
    ):
        backtesting_module.prepare_bot_backtest_data(
            db_session,
            user_id=OWNER_ID,
            bot_config_id=config.id,
            payload=BotBacktestIn(
                start=BASE_TIME,
                end=BASE_TIME + timedelta(days=31),
            ),
            client=object(),
        )











def test_futures_gap_detection_ignores_weekends_but_flags_open_session_holes():
    friday_close = datetime(2026, 7, 10, 20, 55, tzinfo=timezone.utc)
    sunday_open = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)
    weekend_rows = [_candle(friday_close), _candle(sunday_open)]
    open_session_rows = [
        _candle(BASE_TIME),
        _candle(BASE_TIME + timedelta(minutes=10)),
    ]

    assert backtesting_module._count_futures_session_gaps(
        weekend_rows,
        interval_seconds=300,
    ) == 0
    assert backtesting_module._count_futures_session_gaps(
        open_session_rows,
        interval_seconds=300,
    ) == 1


def test_backtest_range_reports_actual_stored_coverage():
    bars = [_candle(BASE_TIME), _candle(BASE_TIME + timedelta(minutes=5))]

    result = _run(
        bars,
        evaluator=_hold,
        start=BASE_TIME,
        end=BASE_TIME + timedelta(days=1),
    )

    assert result["range"]["start"] == BASE_TIME.isoformat()
    assert result["range"]["end"] == (BASE_TIME + timedelta(minutes=10)).isoformat()
    assert any("Stored candles ended before the requested end" in warning for warning in result["warnings"])




def test_orb_rejects_an_incompatible_non_minute_timeframe():
    bars = [
        _candle(BASE_TIME, unit="hour", unit_number=1),
        _candle(BASE_TIME + timedelta(hours=1), unit="hour", unit_number=1),
    ]

    with pytest.raises(
        backtesting_module.BacktestConfigurationError,
        match="requires minute candles",
    ):
        _run(
            bars,
            config=_config(
                strategy_type="orb_fibonacci_pullback",
                timeframe_unit="hour",
                timeframe_unit_number=1,
            ),
            evaluator=_hold,
            end=BASE_TIME + timedelta(hours=2),
        )


def test_authenticated_route_reuses_real_strategy_and_never_routes_orders(
    db_session,
    monkeypatch,
):
    config = _persist_config(db_session, execution_mode="live")
    for offset, price in [(-10, 5.0), (-5, 4.0)]:
        db_session.add(
            _candle(
                BASE_TIME + timedelta(minutes=offset),
                open_price=price,
                close_price=price,
            )
        )
    prices = [3.0, 2.0, 3.0, 4.0]
    for index, price in enumerate(prices):
        db_session.add(
            _candle(
                BASE_TIME + timedelta(minutes=5 * index),
                open_price=price,
                close_price=price,
            )
        )
    db_session.commit()

    real_evaluator = backtesting_module.evaluate_sma_cross
    evaluator_calls: list[list[datetime]] = []

    def evaluator_spy(candles, *, fast_period, slow_period):
        evaluator_calls.append([_utc(row.candle_timestamp) for row in candles])
        return real_evaluator(candles, fast_period=fast_period, slow_period=slow_period)

    def unexpected_order_call(*args, **kwargs):
        raise AssertionError("backtest invoked a live order or client path")

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OWNER_ID)
    monkeypatch.setattr(backtesting_module, "evaluate_sma_cross", evaluator_spy)
    monkeypatch.setattr(bot_service_module, "_submit_order_attempt", unexpected_order_call)
    monkeypatch.setattr(
        ProjectXClient,
        "from_env",
        classmethod(lambda cls: unexpected_order_call()),
    )

    response = main_module.create_trading_bot_backtest(
        bot_config_id=config.id,
        payload=BotBacktestIn(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(minutes=20),
            starting_balance=25_000,
            commission_per_contract=0,
            slippage_ticks=0,
            force_close_at_end=True,
        ),
        db=db_session,
    )
    validated = BotBacktestOut.model_validate(response)

    assert validated.bot_config_id == config.id
    assert validated.range.bar_count == 4
    assert validated.metrics.trade_count == 1
    assert response["config_snapshot"]["execution_mode_at_run"] == "live"
    assert response["assumptions"]["configured_execution_mode_was_ignored"] == "live"
    assert response["assumptions"]["live_order_routing"] == "disabled_by_architecture"
    assert evaluator_calls
    assert db_session.query(BotBacktest).count() == 1


def test_authenticated_backtest_does_not_use_the_legacy_bar_cap(
    db_session,
    monkeypatch,
):
    config = _persist_config(db_session)
    db_session.add_all(
        [
            _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100 + index)
            for index in range(8)
        ]
    )
    db_session.commit()
    monkeypatch.setattr(backtesting_module, "MAX_BACKTEST_BARS", 3)

    row = backtesting_module.create_bot_backtest(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=config.id,
        payload=BotBacktestIn(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(minutes=40),
        ),
    )

    assert row.bar_count == 6
    assert row.bar_count > backtesting_module.MAX_BACKTEST_BARS
    assert db_session.query(BotBacktest).count() == 1




def test_backtest_route_scopes_bots_and_candles_to_authenticated_user(
    db_session,
    monkeypatch,
):
    config = _persist_config(db_session, user_id=OWNER_ID)
    db_session.add(_candle(BASE_TIME, user_id=OWNER_ID))
    for index in range(4):
        db_session.add(
            _candle(
                BASE_TIME + timedelta(minutes=5 * index),
                user_id=OTHER_USER_ID,
                close_price=100 + index,
            )
        )
    db_session.commit()
    payload = BotBacktestIn(
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=20),
    )

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OWNER_ID)
    with pytest.raises(HTTPException) as insufficient:
        main_module.create_trading_bot_backtest(
            bot_config_id=config.id,
            payload=payload,
            db=db_session,
        )
    assert insufficient.value.status_code == 422
    assert "insufficient_backtest_data" in str(insufficient.value.detail)

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OTHER_USER_ID)
    with pytest.raises(HTTPException) as hidden:
        main_module.create_trading_bot_backtest(
            bot_config_id=config.id,
            payload=payload,
            db=db_session,
        )
    assert hidden.value.status_code == 404
    assert hidden.value.detail == "bot_config_not_found"
    assert db_session.query(BotBacktest).count() == 0


def test_backtest_input_uses_absent_dates_for_full_history_and_keeps_paired_bounds():
    full_history = BotBacktestIn()
    bounded = BotBacktestIn(
        start=BASE_TIME,
        end=BASE_TIME + timedelta(days=800),
    )

    assert full_history.start is None
    assert full_history.end is None
    assert bounded.end - bounded.start == timedelta(days=800)
    with pytest.raises(ValueError, match="provided together"):
        BotBacktestIn(start=BASE_TIME)
    with pytest.raises(ValueError, match="must be after start"):
        BotBacktestIn(start=BASE_TIME, end=BASE_TIME)

    mixed_awareness = BotBacktestIn(
        start="2026-01-01T00:00:00",
        end="2026-01-02T00:00:00Z",
    )
    assert mixed_awareness.start is not None
    assert mixed_awareness.end is not None


def test_full_history_uses_all_fully_closed_exact_contract_bars_without_old_cap(
    db_session,
    monkeypatch,
):
    config = _persist_config(db_session, lookback_bars=25)
    exact_rows = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100 + index)
        for index in range(6)
    ]
    other_delivery = _candle(
        BASE_TIME - timedelta(minutes=5),
        contract_id="CON.F.US.MNQ.U26",
        symbol="MNQ",
    )
    inferred_partial = _candle(BASE_TIME + timedelta(minutes=30))
    inferred_partial.fetched_at = BASE_TIME + timedelta(minutes=32)
    inferred_partial.raw_payload = {"t": inferred_partial.candle_timestamp.isoformat()}
    explicit_partial = _candle(
        BASE_TIME + timedelta(minutes=35),
        is_partial=True,
    )
    not_yet_closed = _candle(
        BASE_TIME + timedelta(minutes=40),
    )
    db_session.add_all(
        [other_delivery, *exact_rows, inferred_partial, explicit_partial, not_yet_closed]
    )
    db_session.commit()

    monkeypatch.setattr(backtesting_module, "MAX_BACKTEST_BARS", 2)

    def unexpected_order_call(*_args, **_kwargs):
        raise AssertionError("backtest invoked an order path")

    monkeypatch.setattr(bot_service_module, "_submit_order_attempt", unexpected_order_call)

    row = backtesting_module.create_bot_backtest(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=config.id,
        payload=BotBacktestIn(
            starting_balance=25_000,
            commission_per_contract=0,
            slippage_ticks=0,
        ),
        now=BASE_TIME + timedelta(minutes=42),
    )
    result = backtesting_module.serialize_bot_backtest(row)
    validated = BotBacktestOut.model_validate(result)

    hard_minimum = backtesting_module._strategy_history_bars(config, hard_minimum=True)
    first_execution_index = hard_minimum - 1
    expected_execution = exact_rows[first_execution_index:]
    assert validated.range.contract_id == CONTRACT_ID
    assert validated.range.symbol == "MNQ"
    assert validated.range.timeframe_unit == "minute"
    assert validated.range.timeframe_unit_number == 5
    assert validated.range.start == _utc(expected_execution[0].candle_timestamp)
    assert validated.range.end == backtesting_module._candle_close_time(expected_execution[-1])
    assert validated.range.bar_count == len(expected_execution)
    assert validated.range.bar_count > backtesting_module.MAX_BACKTEST_BARS
    assert _utc(row.requested_start) == BASE_TIME
    assert _utc(row.requested_end) == BASE_TIME + timedelta(minutes=30)
    assert result["assumptions"]["live_order_routing"] == "disabled_by_architecture"


def test_full_history_route_is_deterministic_and_has_no_public_prepare_step(
    db_session,
    monkeypatch,
):
    config = _persist_config(db_session, execution_mode="live", lookback_bars=25)
    for index in range(6):
        db_session.add(
            _candle(
                BASE_TIME + timedelta(minutes=5 * index),
                close_price=100 + index,
            )
        )
    db_session.commit()

    def unexpected_order_call(*_args, **_kwargs):
        raise AssertionError("full-history backtest invoked an order path")

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OWNER_ID)
    monkeypatch.setattr(bot_service_module, "_submit_order_attempt", unexpected_order_call)
    payload = BotBacktestIn(
        starting_balance=25_000,
        commission_per_contract=0,
        slippage_ticks=0,
    )

    first = main_module.create_trading_bot_backtest(
        bot_config_id=config.id,
        payload=payload,
        db=db_session,
    )
    second = main_module.create_trading_bot_backtest(
        bot_config_id=config.id,
        payload=payload,
        db=db_session,
    )
    first_validated = BotBacktestOut.model_validate(first)

    assert first_validated.range.contract_id == CONTRACT_ID
    assert first_validated.range.bar_count == 4
    assert first["range"] == second["range"]
    assert first["metrics"] == second["metrics"]
    assert first["input_fingerprint"] == second["input_fingerprint"]
    assert first["assumptions"]["configured_execution_mode_was_ignored"] == "live"
    # OpenAPI traverses included routers as well as routes registered directly
    # on the app; router wrapper objects need not expose a .path attribute.
    public_paths = main_module.app.openapi()["paths"]
    assert "post" in public_paths["/api/bots/{bot_config_id}/backtests"]
    assert "/api/bots/{bot_config_id}/backtests/prepare" not in public_paths
    assert db_session.query(BotBacktest).count() == 2


@pytest.mark.parametrize(
    "cached_count",
    [0, 200],
    ids=["empty-primary-cache", "stale-primary-cache"],
)
def test_topbot_full_history_single_post_discovers_and_refreshes_primary_history(
    db_session,
    monkeypatch,
    cached_count,
):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["sma_cross"]},
        lookback_bars=25,
    )
    provider_bars = [
        {
            "timestamp": BASE_TIME + timedelta(minutes=5 * index),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 100,
            "is_partial": False,
            "raw_payload": {"isPartial": False},
        }
        for index in range(230)
    ]
    for bar in provider_bars[:cached_count]:
        db_session.add(
            _candle(
                bar["timestamp"],
                open_price=bar["open"],
                high_price=bar["high"],
                low_price=bar["low"],
                close_price=bar["close"],
                volume=bar["volume"],
            )
        )
    db_session.commit()

    class StubFullHistoryClient:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def search_contracts(self, **_kwargs):
            raise AssertionError("full-history discovery must retain the configured delivery")

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            start = _utc(kwargs["start"])
            end = _utc(kwargs["end"])
            rows = [
                bar
                for bar in provider_bars
                if start <= _utc(bar["timestamp"]) <= end
            ]
            return rows[-int(kwargs["limit"]):]

    client = StubFullHistoryClient()
    primary_history_loads = 0
    real_primary_history_loader = backtesting_module._load_primary_closed_candles

    def primary_history_loader_spy(*args, **kwargs):
        nonlocal primary_history_loads
        primary_history_loads += 1
        return real_primary_history_loader(*args, **kwargs)

    def unexpected_order_call(*_args, **_kwargs):
        raise AssertionError("TopBot full-history replay invoked an order path")

    monkeypatch.setattr(backtesting_module, "MAX_PROVIDER_FETCH_BARS", 50)
    monkeypatch.setattr(
        backtesting_module,
        "_MAX_PROVIDER_EMPTY_SPAN",
        timedelta(minutes=20),
    )
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(bot_service_module, "_submit_order_attempt", unexpected_order_call)
    monkeypatch.setattr(
        backtesting_module,
        "_load_primary_closed_candles",
        primary_history_loader_spy,
    )

    response = main_module.create_trading_bot_backtest(
        bot_config_id=config.id,
        payload=BotBacktestIn(
            commission_per_contract=0,
            slippage_ticks=0,
        ),
        db=db_session,
    )
    validated = BotBacktestOut.model_validate(response)

    assert client.calls
    assert all(call["contract_id"] == CONTRACT_ID for call in client.calls)
    assert validated.range.contract_id == CONTRACT_ID
    assert validated.range.start == BASE_TIME + timedelta(minutes=995)
    assert validated.range.end == BASE_TIME + timedelta(minutes=1150)
    assert validated.range.bar_count == 31
    assert primary_history_loads == 2
    assert (
        db_session.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.contract_id == CONTRACT_ID)
        .count()
        == 230
    )



def test_topbot_full_history_fails_explicitly_before_persisting_when_provider_budget_is_exhausted(
    db_session,
    monkeypatch,
):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["sma_cross"]},
        lookback_bars=25,
    )
    provider_bars = [
        {
            "timestamp": BASE_TIME + timedelta(minutes=5 * index),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 100,
            "is_partial": False,
            "raw_payload": {"isPartial": False},
        }
        for index in range(30)
    ]

    class StubClient:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            start = _utc(kwargs["start"])
            end = _utc(kwargs["end"])
            rows = [bar for bar in provider_bars if start <= _utc(bar["timestamp"]) <= end]
            return rows[-int(kwargs["limit"]):]

    client = StubClient()
    monkeypatch.setattr(backtesting_module, "MAX_PROVIDER_FETCH_BARS", 2)
    monkeypatch.setattr(backtesting_module, "MAX_BACKTEST_PROVIDER_REQUESTS", 2)
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: client,
    )

    with pytest.raises(HTTPException) as raised:
        main_module.create_trading_bot_backtest(
            bot_config_id=config.id,
            payload=BotBacktestIn(),
            db=db_session,
        )

    assert raised.value.status_code == 400
    assert "backtest_market_data_request_limit_exceeded" in str(raised.value.detail)
    assert "no partial backtest was saved" in str(raised.value.detail)
    assert len(client.calls) == 2
    assert db_session.query(BotBacktest).count() == 0
    assert db_session.query(ProjectXMarketCandle).count() == 0


def test_prepared_sma_replay_exactly_matches_legacy_evaluator_path():
    prices = [
        100,
        99,
        98,
        97,
        98,
        99,
        100,
        99,
        98,
        97,
        98,
        99,
        100,
        99,
        98,
        97,
        98,
        99,
        100,
        99,
        98,
        97,
        98,
        99,
        100,
        99,
        98,
        97,
        98,
        99,
        100,
        99,
    ]
    candles = backtesting_module._ClosedCandleList(
        _candle(
            BASE_TIME + timedelta(minutes=5 * index),
            open_price=price - 0.25,
            high_price=price + 0.75,
            low_price=price - 0.75,
            close_price=price,
        )
        for index, price in enumerate(prices)
    )
    config = _config(
        fast_period=2,
        slow_period=4,
        lookback_bars=8,
        max_trades_per_day=2,
    )
    start = BASE_TIME + timedelta(minutes=40)
    end = BASE_TIME + timedelta(minutes=5 * len(candles))
    run_options = {
        "config": config,
        "start": start,
        "end": end,
        "starting_balance": 25_000,
        "commission_per_contract": 1.25,
        "slippage_ticks": 1.5,
        "tick_size": 0.25,
        "tick_value": 0.50,
        "include_evaluation_split": False,
    }

    optimized = _run(candles, **run_options)
    legacy_calls = 0

    def legacy_evaluator(rows: list[ProjectXMarketCandle]) -> SignalResult:
        nonlocal legacy_calls
        legacy_calls += 1
        return bot_service_module.evaluate_sma_cross(
            rows,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
        )

    legacy = _run(candles, evaluator=legacy_evaluator, **run_options)

    assert legacy_calls == optimized["range"]["bar_count"]
    assert optimized["trades"]
    assert any(float(trade["commission"]) > 0 for trade in optimized["trades"])
    assert any("max trades per day" in warning for warning in optimized["notes"])
    for key in (
        "range",
        "metrics",
        "equity_curve",
        "drawdown_series",
        "daily_results",
        "monthly_results",
        "trades",
        "warnings",
        "notes",
        "data_quality",
    ):
        assert optimized[key] == legacy[key]
    assert optimized == legacy


def test_incremental_fingerprints_match_legacy_canonical_json_for_unsorted_streams():
    primary = [
        _candle(BASE_TIME + timedelta(minutes=10), close_price=103.25),
        _candle(BASE_TIME, close_price=101.25),
        _candle(
            BASE_TIME + timedelta(minutes=5),
            close_price=102.25,
            is_partial=True,
        ),
    ]
    hourly = [
        _candle(
            BASE_TIME - timedelta(hours=1),
            unit="hour",
            unit_number=1,
            close_price=99.5,
        ),
        _candle(
            BASE_TIME - timedelta(hours=2),
            unit="hour",
            unit_number=1,
            close_price=98.5,
        ),
    ]
    streams = {"z-primary": primary, "a-hourly": hourly}

    def canonical_row(
        row: ProjectXMarketCandle,
        *,
        stream: str | None = None,
    ) -> dict[str, Any]:
        canonical = {
            "contract_id": str(row.contract_id),
            "live": bool(row.live),
            "unit": str(row.unit),
            "unit_number": int(row.unit_number),
            "timestamp": _utc(row.candle_timestamp).isoformat(),
            "open": str(row.open_price),
            "high": str(row.high_price),
            "low": str(row.low_price),
            "close": str(row.close_price),
            "volume": str(row.volume),
            "is_partial": bool(row.is_partial),
        }
        if stream is not None:
            canonical = {"stream": stream, **canonical}
        return canonical

    def legacy_digest(rows: list[dict[str, Any]]) -> str:
        encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    expected_primary = legacy_digest(
        [
            canonical_row(row)
            for row in sorted(primary, key=lambda value: _utc(value.candle_timestamp))
            if not bool(row.is_partial)
        ]
    )
    expected_streams = legacy_digest(
        [
            canonical_row(row, stream=key)
            for key in sorted(streams)
            for row in sorted(
                streams[key],
                key=lambda value: _utc(value.candle_timestamp),
            )
            if not bool(row.is_partial)
        ]
    )

    assert backtesting_module.candle_input_fingerprint(primary) == expected_primary
    assert (
        backtesting_module.candle_stream_input_fingerprint(streams)
        == expected_streams
    )


def test_replay_processes_more_than_twenty_thousand_bars_deterministically_across_over_a_year():
    bar_count = backtesting_module.MAX_BACKTEST_BARS + 1
    oldest = BASE_TIME - timedelta(days=400)
    candles = backtesting_module._ClosedCandleList(
        [
            _candle(oldest, close_price=100),
            *[
                _candle(
                    BASE_TIME + timedelta(minutes=5 * index),
                    close_price=100 + (index % 3),
                )
                for index in range(bar_count - 1)
            ],
        ]
    )

    result = _run(candles, evaluator=_hold)
    repeated = _run(candles, evaluator=_hold)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    repeated_encoded = json.dumps(repeated, sort_keys=True, separators=(",", ":")).encode("utf-8")

    assert candles[-1].candle_timestamp - candles[0].candle_timestamp > timedelta(
        days=366
    )
    assert result["range"]["bar_count"] == bar_count
    assert result["range"]["start"] == oldest.isoformat()
    assert len(result["equity_curve"]) == bar_count + 1
    assert len(result["drawdown_series"]) == bar_count + 1
    assert result["metrics"]["trade_count"] == 0
    assert repeated_encoded == encoded
    assert hashlib.sha256(repeated_encoded).hexdigest() == hashlib.sha256(encoded).hexdigest()


def test_resource_budget_failure_occurs_before_backtest_persistence(
    db_session,
    monkeypatch,
):
    config = _persist_config(db_session)
    db_session.add_all(
        [
            _candle(
                BASE_TIME + timedelta(minutes=5 * index),
                close_price=100 + index,
            )
            for index in range(6)
        ]
    )
    db_session.commit()
    monkeypatch.setattr(backtesting_module, "BACKTEST_MEMORY_BUDGET_BYTES", 1)

    with pytest.raises(
        backtesting_module.BacktestConfigurationError,
        match="backtest_resource_budget_exceeded.*no partial result was saved",
    ):
        backtesting_module.create_bot_backtest(
            db_session,
            user_id=OWNER_ID,
            bot_config_id=int(config.id),
            payload=BotBacktestIn(
                start=BASE_TIME,
                end=BASE_TIME + timedelta(minutes=30),
            ),
            now=BASE_TIME + timedelta(minutes=35),
        )

    assert db_session.query(BotBacktest).count() == 0


def test_projected_loader_avoids_candle_identities_and_matches_orm_replay(
    db_session,
):
    config = _persist_config(
        db_session,
        fast_period=2,
        slow_period=4,
        lookback_bars=25,
    )
    prices = [100, 99, 98, 97, 98, 99, 100, 99, 98, 97, 98, 99]
    db_session.add_all(
        [
            _candle(
                BASE_TIME + timedelta(minutes=5 * index),
                open_price=price - 0.25,
                close_price=price,
            )
            for index, price in enumerate(prices)
        ]
    )
    db_session.commit()
    config_id = int(config.id)
    db_session.expunge_all()
    loaded_config = db_session.query(BotConfig).filter(BotConfig.id == config_id).one()

    projected = backtesting_module._load_primary_closed_candles(
        db_session,
        user_id=OWNER_ID,
        config=loaded_config,
        closed_by=BASE_TIME + timedelta(minutes=5 * len(prices)),
    )

    assert projected
    assert all(type(row) is backtesting_module._ProjectedCandle for row in projected)
    assert not any(
        isinstance(value, ProjectXMarketCandle)
        for value in db_session.identity_map.values()
    )

    orm_rows = (
        db_session.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == OWNER_ID)
        .filter(ProjectXMarketCandle.contract_id == CONTRACT_ID)
        .order_by(ProjectXMarketCandle.candle_timestamp.asc())
        .all()
    )
    start = BASE_TIME + timedelta(minutes=40)
    end = BASE_TIME + timedelta(minutes=5 * len(prices))

    assert backtesting_module.candle_input_fingerprint(
        projected
    ) == backtesting_module.candle_input_fingerprint(orm_rows)
    assert _run(
        projected,
        config=loaded_config,
        start=start,
        end=end,
    ) == _run(
        orm_rows,
        config=loaded_config,
        start=start,
        end=end,
    )






def test_evaluator_work_budget_failure_occurs_before_backtest_persistence(
    db_session,
    monkeypatch,
):
    config = _persist_config(db_session)
    db_session.add_all(
        [
            _candle(
                BASE_TIME + timedelta(minutes=5 * index),
                close_price=100 + index,
            )
            for index in range(6)
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        backtesting_module,
        "BACKTEST_EVALUATOR_WORK_BUDGET",
        1,
    )

    with pytest.raises(
        backtesting_module.BacktestConfigurationError,
        match="backtest_computation_limit_exceeded.*no partial result was saved",
    ):
        backtesting_module.create_bot_backtest(
            db_session,
            user_id=OWNER_ID,
            bot_config_id=int(config.id),
            payload=BotBacktestIn(
                start=BASE_TIME,
                end=BASE_TIME + timedelta(minutes=30),
            ),
            now=BASE_TIME + timedelta(minutes=35),
        )

    assert db_session.query(BotBacktest).count() == 0


def test_cache_coverage_budget_includes_retained_primary_rows(
    db_session,
    monkeypatch,
):
    db_session.add(
        _candle(
            BASE_TIME - timedelta(hours=1),
            unit="hour",
            unit_number=1,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        backtesting_module,
        "BACKTEST_MEMORY_BUDGET_BYTES",
        backtesting_module.ESTIMATED_REPLAY_CANDLE_BYTES,
    )

    with pytest.raises(
        backtesting_module.BacktestConfigurationError,
        match="backtest_resource_budget_exceeded",
    ):
        backtesting_module._cached_replay_stream_covers(
            db_session,
            user_id=OWNER_ID,
            contract_id=CONTRACT_ID,
            unit="hour",
            unit_number=1,
            fetch_start=BASE_TIME - timedelta(hours=2),
            requested_start=BASE_TIME,
            first_event=BASE_TIME,
            last_event=BASE_TIME,
            warmup_bars=1,
            reserved_replay_rows=1,
        )






def test_backtest_result_lru_is_bounded_and_returns_defensive_copies(monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_ENTRIES", "1")
    monkeypatch.setenv("TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_BYTES", "10000")
    cache = backtesting_module._BacktestResultLru()
    first = {"range": {"bar_count": 2}, "trades": []}
    second = {"range": {"bar_count": 3}, "trades": []}

    cache.put("first", first)
    loaded = cache.get("first")
    assert loaded == first
    assert loaded is not first
    loaded["range"]["bar_count"] = 999
    assert cache.get("first")["range"]["bar_count"] == 2

    cache.put("second", second)
    assert cache.get("first") is None
    assert cache.get("second") == second
