import os
from datetime import datetime, timedelta, timezone
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


def test_topbot_defers_replay_when_requested_start_precedes_available_warmup():
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100)
        for index in range(30)
    ]
    result = _run(
        bars,
        config=_config(
            strategy_type="topbot_adaptive",
            strategy_params={"source_strategies": ["sma_cross"]},
            lookback_bars=25,
        ),
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=150),
    )

    assert result["range"]["start"] == (BASE_TIME + timedelta(minutes=120)).isoformat()
    assert result["range"]["bar_count"] == 6
    assert any("Deferred replay by 24 candle(s)" in warning for warning in result["warnings"])


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
    next_open = datetime(2026, 7, 7, 13, 30, tzinfo=timezone.utc)
    bars = [
        _candle(session_open + timedelta(minutes=5 * index), close_price=100)
        for index in range(8)
    ]
    bars.append(_candle(next_open, close_price=100))

    def end_of_session_signal(candles: list[ProjectXMarketCandle]) -> SignalResult:
        if candles and any(_utc(row.candle_timestamp) == session_end for row in candles):
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
    assert any("stale session signal" in warning for warning in result["warnings"])


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


def test_long_and_short_reversal_accounting_and_period_breakdowns():
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

    assert [trade["side"] for trade in result["trades"]] == ["long", "short"]
    assert [trade["gross_pnl"] for trade in result["trades"]] == [20, 20]
    assert result["trades"][0]["exit_reason"] == "position_reversal"
    assert result["trades"][1]["exit_reason"] == "forced_end_of_test"
    assert result["metrics"]["trade_count"] == 2
    assert result["metrics"]["gross_pnl"] == 40
    assert result["metrics"]["long"]["trade_count"] == 1
    assert result["metrics"]["long"]["net_pnl"] == 20
    assert result["metrics"]["short"]["trade_count"] == 1
    assert result["metrics"]["short"]["net_pnl"] == 20
    assert sum(row["net_pnl"] for row in result["daily_results"]) == 40
    assert sum(row["net_pnl"] for row in result["monthly_results"]) == 40


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
    assert any("max trades per day" in warning for warning in limited["warnings"])
    assert oversized["trades"] == []
    assert any("max contracts" in warning for warning in oversized["warnings"])


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


def test_topbot_replay_synchronizes_slower_source_streams_without_lookahead(monkeypatch):
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": ["support_resistance"],
            "minimum_directional_votes": 1,
            "minimum_score": 70,
            "minimum_reward_risk": 1.5,
        },
        lookback_bars=25,
    )
    main_bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100)
        for index in range(-25, 26)
    ]
    one_hour = [
        _candle(BASE_TIME - timedelta(hours=1), unit="hour", unit_number=1),
        _candle(BASE_TIME, unit="hour", unit_number=1),
    ]
    four_hour = [
        _candle(BASE_TIME - timedelta(hours=4), unit="hour", unit_number=4),
        _candle(BASE_TIME, unit="hour", unit_number=4),
    ]
    observed: list[tuple[list[datetime], list[datetime]]] = []
    topbot_actions: list[tuple[str, str, dict[str, Any]]] = []

    def fake_dispatch(identifier, *args, **kwargs):
        assert identifier == "support_resistance"
        higher = kwargs["higher_timeframe_candles"]
        lower = kwargs["lower_timeframe_candles"]
        observed.append(
            (
                [_utc(row.candle_timestamp) for row in higher],
                [_utc(row.candle_timestamp) for row in lower],
            )
        )
        latest = lower[-1]
        return SignalResult(
            action="BUY",
            reason="synchronized support source",
            candle_timestamp=_utc(latest.candle_timestamp),
            price=float(latest.close_price),
                raw_payload={"stop_loss": 90.0, "take_profit": 120.0},
        )

    monkeypatch.setattr(backtesting_module.bot_service_module, "dispatch_strategy_evaluator", fake_dispatch)
    monkeypatch.setattr(backtesting_module.bot_service_module, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "build_signal_trade_evaluation",
        lambda **_kwargs: {"total_score": 85},
    )
    real_topbot_evaluator = backtesting_module.bot_service_module.evaluate_topbot_adaptive

    def topbot_spy(*args, **kwargs):
        signal = real_topbot_evaluator(*args, **kwargs)
        topbot_actions.append((signal.action, signal.reason, signal.raw_payload))
        return signal

    monkeypatch.setattr(backtesting_module.bot_service_module, "evaluate_topbot_adaptive", topbot_spy)

    result = _run(
        main_bars,
        config=config,
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=130),
        replay_streams={
            backtesting_module._topbot_asset_stream_key("hour", 1): one_hour,
            backtesting_module._topbot_asset_stream_key("hour", 4): four_hour,
        },
    )

    assert observed == [
        ([BASE_TIME - timedelta(hours=4)], [BASE_TIME - timedelta(hours=1)]),
        ([BASE_TIME - timedelta(hours=4)], [BASE_TIME - timedelta(hours=1), BASE_TIME]),
    ]
    assert {row[0] for row in topbot_actions} == {"BUY"}, topbot_actions[:2]
    assert result["metrics"]["trade_count"] == 1, (result["warnings"], topbot_actions[:2])
    assert not any("TopBot source support_resistance failed" in warning for warning in result["warnings"])


def test_topbot_replay_deduplicates_repeated_signal_from_unchanged_slow_stream(monkeypatch):
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": ["support_resistance"],
            "minimum_directional_votes": 1,
            "minimum_score": 70,
            "minimum_reward_risk": 1.5,
        },
        lookback_bars=25,
    )
    main_bars = [
        _candle(
            BASE_TIME + timedelta(minutes=5 * index),
            close_price=100,
            high_price=121 if index == 3 else 101,
            low_price=99,
        )
        for index in range(-25, 10)
    ]
    one_hour = [_candle(BASE_TIME - timedelta(hours=1), unit="hour", unit_number=1)]
    four_hour = [_candle(BASE_TIME - timedelta(hours=4), unit="hour", unit_number=4)]

    def fake_dispatch(identifier, *args, **kwargs):
        assert identifier == "support_resistance"
        latest = kwargs["lower_timeframe_candles"][-1]
        return SignalResult(
            action="BUY",
            reason="one slow source signal",
            candle_timestamp=_utc(latest.candle_timestamp),
            price=100.0,
            raw_payload={"stop_loss": 90.0, "take_profit": 120.0},
        )

    monkeypatch.setattr(backtesting_module.bot_service_module, "dispatch_strategy_evaluator", fake_dispatch)
    monkeypatch.setattr(backtesting_module.bot_service_module, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "build_signal_trade_evaluation",
        lambda **_kwargs: {"total_score": 85},
    )

    result = _run(
        main_bars,
        config=config,
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=50),
        replay_streams={
            backtesting_module._topbot_asset_stream_key("hour", 1): one_hour,
            backtesting_module._topbot_asset_stream_key("hour", 4): four_hour,
        },
    )

    assert result["metrics"]["trade_count"] == 1, result["warnings"]
    assert result["trades"][0]["exit_reason"] == "take_profit"


def test_topbot_replay_records_missing_auxiliary_stream_as_source_failure():
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": ["support_resistance"],
            "minimum_directional_votes": 1,
        },
        lookback_bars=25,
    )
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100)
        for index in range(-25, 4)
    ]

    result = _run(
        bars,
        config=config,
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=20),
    )

    assert result["metrics"]["trade_count"] == 0
    assert any("asset:hour:1" in warning for warning in result["warnings"])
    assert not any("TopBot source support_resistance failed" in warning for warning in result["warnings"])
    assert any("TopBot excluded source(s)" in warning for warning in result["warnings"])


def test_topbot_replay_excludes_a_benchmark_that_stops_before_the_replay_tail(
    monkeypatch,
):
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": ["atr_adjusted_relative_strength"],
            "minimum_directional_votes": 1,
        },
        lookback_bars=25,
    )
    bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100)
        for index in range(-25, 4)
    ]
    benchmark = _candle(
        BASE_TIME - timedelta(minutes=5),
        contract_id="CON.F.US.MES.M26",
        symbol="F.US.MES",
    )

    def unexpected_dispatch(identifier, *_args, **_kwargs):
        raise AssertionError(f"stale source {identifier} should have been excluded")

    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "dispatch_strategy_evaluator",
        unexpected_dispatch,
    )

    result = _run(
        bars,
        config=config,
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=20),
        replay_streams={
            backtesting_module._topbot_benchmark_stream_key(
                "atr_adjusted_relative_strength"
            ): [benchmark],
        },
    )

    assert result["metrics"]["trade_count"] == 0
    assert any(
        "benchmark:atr_adjusted_relative_strength" in warning
        and "TopBot excluded source(s)" in warning
        for warning in result["warnings"]
    )


def test_topbot_benchmark_specs_use_mes_with_the_asset_delivery():
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": [
                "atr_adjusted_relative_strength",
                "relative_strength_spy",
            ],
            "source_strategy_params": {
                "atr_adjusted_relative_strength": {"benchmark_symbol": "SPY"},
                "relative_strength_spy": {"benchmark_symbol": "SPY"},
            },
        },
        contract_id="CON.F.US.MNQ.M26",
    )

    specs = backtesting_module._topbot_stream_specs(config)

    for source in ("atr_adjusted_relative_strength", "relative_strength_spy"):
        spec = specs[backtesting_module._topbot_benchmark_stream_key(source)]
        assert spec.contract_id == "CON.F.US.MES.M26"
        assert spec.symbol == "F.US.MES"
        assert spec.unit == "minute"
        assert spec.unit_number == 5


def test_topbot_cache_preparation_deduplicates_shared_mes_benchmark(
    db_session,
):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": [
                "atr_adjusted_relative_strength",
                "relative_strength_spy",
            ],
        },
        lookback_bars=25,
    )

    class StubClient:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def search_contracts(self, **_kwargs):
            raise AssertionError("exact historical deliveries should not be re-resolved")

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = StubClient()
    prepared = backtesting_module.prepare_bot_backtest_data(
        db_session,
        user_id=OWNER_ID,
        bot_config_id=config.id,
        payload=BotBacktestIn(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(minutes=20),
        ),
        client=client,
    )

    assert prepared == 2
    assert {call["contract_id"] for call in client.calls} == {
        CONTRACT_ID,
        "CON.F.US.MES.M26",
    }
    assert sum(call["contract_id"] == "CON.F.US.MES.M26" for call in client.calls) == 1


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


def test_topbot_replay_excludes_legacy_unmarked_four_hour_tail_snapshot():
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["support_resistance"]},
    )
    fetched_after_close = datetime(2026, 7, 10, tzinfo=timezone.utc)
    canonical = [
        _candle(
            timestamp,
            unit="hour",
            unit_number=4,
        )
        for timestamp in (
            datetime(2026, 4, 26, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 27, 2, 0, tzinfo=timezone.utc),
        )
    ]
    for row in canonical:
        row.fetched_at = fetched_after_close
        row.raw_payload = {"t": row.candle_timestamp.isoformat()}
    stale_tail = _candle(
        datetime(2026, 4, 27, 0, 0, tzinfo=timezone.utc),
        unit="hour",
        unit_number=4,
    )
    stale_tail.fetched_at = datetime(2026, 4, 27, 3, 39, tzinfo=timezone.utc)
    stale_tail.raw_payload = {"t": stale_tail.candle_timestamp.isoformat()}
    spec = backtesting_module._topbot_stream_specs(config)[
        backtesting_module._topbot_asset_stream_key("hour", 4)
    ]

    rows, excluded = backtesting_module._validate_topbot_replay_stream(
        [canonical[0], stale_tail, canonical[1]],
        spec=spec,
        config=config,
    )

    assert [_utc(row.candle_timestamp) for row in rows] == [
        datetime(2026, 4, 26, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 27, 2, 0, tzinfo=timezone.utc),
    ]
    assert excluded == 1


def test_topbot_cache_preparation_rejects_monthly_primary_candles(db_session):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        timeframe_unit="month",
        timeframe_unit_number=1,
    )

    with pytest.raises(
        backtesting_module.BacktestConfigurationError,
        match="TopBot Adaptive does not support month candles",
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


@pytest.mark.parametrize(
    "event_start",
    [
        datetime(2026, 7, 6, 13, 20, tzinfo=timezone.utc),  # Before 09:30 ET.
        datetime(2026, 7, 6, 20, 0, tzinfo=timezone.utc),   # After 15:45 ET.
        datetime(2026, 7, 5, 14, 0, tzinfo=timezone.utc),   # Sunday.
    ],
)
def test_topbot_orb_skips_events_outside_the_configured_session(
    event_start,
    monkeypatch,
):
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["orb_fibonacci_pullback"]},
        lookback_bars=25,
        trading_start_time="09:30",
        trading_end_time="15:45",
    )
    bars = [
        _candle(event_start + timedelta(minutes=5 * index), close_price=100)
        for index in range(-25, 2)
    ]

    def unexpected_dispatch(identifier, *_args, **_kwargs):
        raise AssertionError(f"closed-session source {identifier} should not be dispatched")

    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "dispatch_strategy_evaluator",
        unexpected_dispatch,
    )

    result = _run(
        bars,
        config=config,
        start=event_start,
        end=event_start + timedelta(minutes=10),
    )

    assert not any(
        "TopBot source orb_fibonacci_pullback failed" in warning
        for warning in result["warnings"]
    )


def test_topbot_delayed_orb_does_not_dispatch_during_a_sunday_closure(monkeypatch):
    event_start = datetime(2026, 7, 5, 14, 0, tzinfo=timezone.utc)
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["delayed_orb_confirmation"]},
        lookback_bars=25,
        trading_start_time="09:30",
        trading_end_time="15:45",
    )
    bars = [
        _candle(event_start + timedelta(minutes=5 * index), close_price=100)
        for index in range(-25, 2)
    ]
    one_minute = [
        _candle(
            event_start + timedelta(minutes=index),
            unit="minute",
            unit_number=1,
        )
        for index in range(10)
    ]

    def unexpected_dispatch(identifier, *_args, **_kwargs):
        raise AssertionError(f"closed-session source {identifier} should not be dispatched")

    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "dispatch_strategy_evaluator",
        unexpected_dispatch,
    )

    result = _run(
        bars,
        config=config,
        start=event_start,
        end=event_start + timedelta(minutes=10),
        replay_streams={
            backtesting_module._topbot_asset_stream_key("minute", 1): one_minute,
        },
    )

    assert not any(
        "TopBot source delayed_orb_confirmation failed" in warning
        for warning in result["warnings"]
    )


def test_topbot_orb_still_reports_a_genuinely_missing_session_open():
    event_start = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["orb_fibonacci_pullback"]},
        lookback_bars=25,
        trading_start_time="09:30",
        trading_end_time="15:45",
    )
    bars = [
        *[
            _candle(event_start - timedelta(days=1) + timedelta(minutes=5 * index))
            for index in range(25)
        ],
        _candle(event_start),
        _candle(event_start + timedelta(minutes=5)),
    ]

    result = _run(
        bars,
        config=config,
        start=event_start,
        end=event_start + timedelta(minutes=10),
    )

    assert any(
        "TopBot source orb_fibonacci_pullback failed" in warning
        and "requires the session-opening candle" in warning
        for warning in result["warnings"]
    )


def test_topbot_delayed_orb_excludes_a_truncated_one_minute_tail():
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={"source_strategies": ["delayed_orb_confirmation"]},
        lookback_bars=25,
        trading_start_time="09:30",
        trading_end_time="15:45",
    )
    bars = [
        *[
            _candle(session_open - timedelta(days=1) + timedelta(minutes=5 * index))
            for index in range(25)
        ],
        _candle(session_open),
        _candle(session_open + timedelta(minutes=5)),
    ]
    one_minute = [
        _candle(
            session_open + timedelta(minutes=index),
            unit="minute",
            unit_number=1,
        )
        for index in range(5)
    ]

    result = _run(
        bars,
        config=config,
        start=session_open,
        end=session_open + timedelta(minutes=10),
        replay_streams={
            backtesting_module._topbot_asset_stream_key("minute", 1): one_minute,
        },
    )

    assert any(
        "TopBot excluded source(s)" in warning
        and "delayed_orb_confirmation (asset:minute:1)" in warning
        for warning in result["warnings"]
    )
    assert not any(
        "TopBot source delayed_orb_confirmation failed" in warning
        for warning in result["warnings"]
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


def test_topbot_replay_has_an_adapter_for_every_default_source(monkeypatch):
    config = _config(
        strategy_type="topbot_adaptive",
        strategy_params={},
        lookback_bars=25,
        trading_start_time="10:00",
    )
    main_bars = [
        _candle(BASE_TIME + timedelta(minutes=5 * index), close_price=100)
        for index in range(-192, 3)
    ]
    expected_sources = set(
        bot_service_module._normalize_strategy_params("topbot_adaptive", {})["source_strategies"]
    )
    observed_sources: set[str] = set()

    def fake_dispatch(identifier, *args, **kwargs):
        observed_sources.add(str(identifier))
        return SignalResult(
            action="HOLD",
            reason=f"{identifier} held",
            candle_timestamp=BASE_TIME,
            price=100.0,
            raw_payload={},
        )

    benchmark = [
        _candle(
            BASE_TIME + timedelta(minutes=5 * index),
            contract_id="CON.F.US.MES.M26",
            symbol="F.US.MES",
        )
        for index in range(-5, 3)
    ]
    monkeypatch.setattr(backtesting_module.bot_service_module, "dispatch_strategy_evaluator", fake_dispatch)

    result = _run(
        main_bars,
        config=config,
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=15),
        replay_streams={
            backtesting_module._topbot_asset_stream_key("minute", 1): [
                _candle(
                    BASE_TIME + timedelta(minutes=index),
                    unit="minute",
                    unit_number=1,
                )
                for index in range(15)
            ],
            backtesting_module._topbot_asset_stream_key("hour", 1): [
                _candle(BASE_TIME - timedelta(hours=1), unit="hour", unit_number=1)
            ],
            backtesting_module._topbot_asset_stream_key("hour", 4): [
                _candle(BASE_TIME - timedelta(hours=4), unit="hour", unit_number=4)
            ],
            backtesting_module._topbot_asset_stream_key("day", 1): [
                _candle(BASE_TIME - timedelta(days=1), unit="day", unit_number=1)
            ],
            backtesting_module._topbot_benchmark_stream_key(
                "atr_adjusted_relative_strength"
            ): benchmark,
            backtesting_module._topbot_benchmark_stream_key(
                "relative_strength_spy"
            ): benchmark,
        },
    )

    assert observed_sources == expected_sources
    assert result["metrics"]["trade_count"] == 0
    assert not any("topbot_replay_adapter_missing" in warning for warning in result["warnings"])


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


def test_authenticated_topbot_route_loads_and_fingerprints_synchronized_streams(
    db_session,
    monkeypatch,
):
    config = _persist_config(
        db_session,
        strategy_type="topbot_adaptive",
        strategy_params={
            "source_strategies": ["support_resistance"],
            "minimum_directional_votes": 1,
            "minimum_score": 70,
            "minimum_reward_risk": 1.5,
        },
        lookback_bars=25,
    )
    for index in range(-25, 4):
        db_session.add(
            _candle(
                BASE_TIME + timedelta(minutes=5 * index),
                close_price=100,
            )
        )
    one_hour = _candle(BASE_TIME - timedelta(hours=1), unit="hour", unit_number=1)
    four_hour = _candle(BASE_TIME - timedelta(hours=4), unit="hour", unit_number=4)
    db_session.add_all([one_hour, four_hour])
    db_session.commit()

    def fake_dispatch(identifier, *args, **kwargs):
        assert identifier == "support_resistance"
        latest = kwargs["lower_timeframe_candles"][-1]
        price = float(latest.close_price)
        return SignalResult(
            action="BUY",
            reason="stored synchronized source",
            candle_timestamp=_utc(latest.candle_timestamp),
            price=price,
            raw_payload={"stop_loss": price - 10, "take_profit": price + 20},
        )

    class StubHistoryClient:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def search_contracts(self, **_kwargs):
            raise AssertionError("configured deliveries must not be re-resolved")

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    history_client = StubHistoryClient()

    def unexpected_order_call(*_args, **_kwargs):
        raise AssertionError("TopBot backtest invoked an order path")

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OWNER_ID)
    monkeypatch.setattr(backtesting_module.bot_service_module, "dispatch_strategy_evaluator", fake_dispatch)
    monkeypatch.setattr(backtesting_module.bot_service_module, "build_bot_market_analysis", lambda **_kwargs: {})
    monkeypatch.setattr(
        backtesting_module.bot_service_module,
        "build_signal_trade_evaluation",
        lambda **_kwargs: {"total_score": 85},
    )
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: history_client,
    )
    monkeypatch.setattr(bot_service_module, "_submit_order_attempt", unexpected_order_call)
    payload = BotBacktestIn(
        start=BASE_TIME,
        end=BASE_TIME + timedelta(minutes=20),
        commission_per_contract=0,
        slippage_ticks=0,
    )

    first = main_module.create_trading_bot_backtest(
        bot_config_id=config.id,
        payload=payload,
        db=db_session,
    )
    first_validated = BotBacktestOut.model_validate(first)

    assert first_validated.engine_version == "1.3.0"
    assert first_validated.metrics.trade_count == 1
    assert not any("strategy_not_supported" in warning for warning in first["warnings"])
    assert history_client.calls
    assert all(call["contract_id"] == CONTRACT_ID for call in history_client.calls)

    one_hour.close_price = 101
    one_hour.high_price = 102
    db_session.commit()
    second = main_module.create_trading_bot_backtest(
        bot_config_id=config.id,
        payload=payload,
        db=db_session,
    )

    assert second["input_fingerprint"] != first["input_fingerprint"]
    assert db_session.query(BotBacktest).count() == 2


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
    assert "/api/bots/{bot_config_id}/backtests/prepare" not in {
        route.path for route in main_module.app.routes
    }
    assert db_session.query(BotBacktest).count() == 2


@pytest.mark.parametrize(
    "cached_count",
    [0, 25],
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
        for index in range(30)
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

    def unexpected_order_call(*_args, **_kwargs):
        raise AssertionError("TopBot full-history replay invoked an order path")

    monkeypatch.setattr(backtesting_module, "MAX_PROVIDER_FETCH_BARS", 5)
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
    assert validated.range.start == BASE_TIME + timedelta(minutes=120)
    assert validated.range.end == BASE_TIME + timedelta(minutes=150)
    assert validated.range.bar_count == 6
    assert (
        db_session.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.contract_id == CONTRACT_ID)
        .count()
        == 30
    )
