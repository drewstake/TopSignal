import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import (
    Account,
    BotConfig,
    BotDecision,
    BotOrderAttempt,
    BotRiskEvent,
    BotRun,
    ProjectXMarketCandle,
    ProjectXTradeEvent,
)
from app.services.bot_service import SignalResult, evaluate_bot_config
import app.services.bot_service as bot_service_module


USER_ID = "00000000-0000-0000-0000-000000000000"
ACCOUNT_ID = 9001
CONTRACT_ID = "CON.F.US.MNQ.M26"
SYMBOL = "MNQ"


ORDER_PATH_TABLES = [
    Account.__table__,
    ProjectXMarketCandle.__table__,
    BotConfig.__table__,
    BotRun.__table__,
    BotDecision.__table__,
    BotOrderAttempt.__table__,
    BotRiskEvent.__table__,
    ProjectXTradeEvent.__table__,
]


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=ORDER_PATH_TABLES)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(ORDER_PATH_TABLES)))
        engine.dispose()


class RecordingProjectXClient:
    def __init__(self):
        self.place_order_calls = []

    def retrieve_bars(self, **_kwargs):
        raise AssertionError("order-path tests inject candles and should not fetch provider bars")

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        return {
            "order_id": "px-order-1",
            "raw_payload": {"orderId": "px-order-1", "success": True},
            "request_payload": kwargs,
        }


def _fresh_candles(count: int = 12) -> list[ProjectXMarketCandle]:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=count * 5)
    candles: list[ProjectXMarketCandle] = []
    for index in range(count):
        close = 100.0 + index * 0.1
        candles.append(
            ProjectXMarketCandle(
                user_id=USER_ID,
                contract_id=CONTRACT_ID,
                symbol=SYMBOL,
                live=False,
                unit="minute",
                unit_number=5,
                candle_timestamp=base + timedelta(minutes=index * 5),
                open_price=close - 0.05,
                high_price=close + 0.25,
                low_price=close - 0.25,
                close_price=close,
                volume=100,
                is_partial=False,
            )
        )
    return candles


def _topbot_signal(action: str, timestamp: datetime) -> SignalResult:
    if action == "BUY":
        stop_loss = 99.0
        take_profit = 102.0
    else:
        stop_loss = 101.0
        take_profit = 98.0

    return SignalResult(
        action=action,
        reason=f"TopBot Adaptive test {action}",
        candle_timestamp=timestamp,
        price=100.0,
        raw_payload={
            "strategy_type": "topbot_adaptive",
            "signal_category": "entry",
            "entry_price": 100.0,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": 1.0,
            "reward_r_multiple": 2.0,
            "topbot_adaptive": {
                "decision": "TAKE",
                "selected_action": action,
            },
        },
    )


def _add_account_and_config(db, *, execution_mode: str) -> tuple[Account, BotConfig]:
    account = Account(
        id=ACCOUNT_ID,
        user_id=USER_ID,
        provider="projectx",
        external_id=str(ACCOUNT_ID),
        name=f"Practice {ACCOUNT_ID}",
        account_state="ACTIVE",
        can_trade=True,
        is_visible=True,
    )
    config = BotConfig(
        user_id=USER_ID,
        account_id=ACCOUNT_ID,
        name=f"TopBot {execution_mode}",
        enabled=True,
        execution_mode=execution_mode,
        strategy_type="topbot_adaptive",
        strategy_params={},
        contract_id=CONTRACT_ID,
        symbol=SYMBOL,
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=9,
        slow_period=21,
        order_size=1,
        max_contracts=1,
        max_daily_loss=1000,
        max_trades_per_day=3,
        max_open_position=1,
        allowed_contracts=[CONTRACT_ID],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3600,
    )
    db.add_all([account, config])
    db.flush()
    return account, config


def _inject_topbot_signal(monkeypatch, *, candles: list[ProjectXMarketCandle], signal: SignalResult) -> None:
    def fake_fetch_candles_and_evaluate_strategy(db, *, user_id, config, client):
        assert str(config.strategy_type) == "topbot_adaptive"
        return candles, signal

    monkeypatch.setattr(
        bot_service_module,
        "fetch_candles_and_evaluate_strategy",
        fake_fetch_candles_and_evaluate_strategy,
    )
    monkeypatch.setattr(bot_service_module, "_is_inside_trading_session", lambda *_args, **_kwargs: True)


def test_topbot_live_buy_routes_projectx_market_order_with_brackets(db_session, monkeypatch):
    candles = _fresh_candles()
    signal = _topbot_signal("BUY", candles[-1].candle_timestamp)
    _inject_topbot_signal(monkeypatch, candles=candles, signal=signal)
    account, config = _add_account_and_config(db_session, execution_mode="live")
    client = RecordingProjectXClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_ID,
        config=config,
        account=account,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.decision.action == "BUY"
    assert result.order_attempt is not None
    assert result.order_attempt.status == "submitted"
    assert result.order_attempt.raw_request["stopLossBracket"] == {"ticks": 4, "type": 4}
    assert result.order_attempt.raw_request["takeProfitBracket"] == {"ticks": 8, "type": 1}
    assert client.place_order_calls == [
        {
            "account_id": ACCOUNT_ID,
            "contract_id": CONTRACT_ID,
            "order_type": 2,
            "side": 0,
            "size": 1,
            "custom_tag": result.order_attempt.raw_request["customTag"],
            "stop_loss_bracket": {"ticks": 4, "type": 4},
            "take_profit_bracket": {"ticks": 8, "type": 1},
        }
    ]


def test_topbot_live_sell_routes_projectx_market_order_with_brackets(db_session, monkeypatch):
    candles = _fresh_candles()
    signal = _topbot_signal("SELL", candles[-1].candle_timestamp)
    _inject_topbot_signal(monkeypatch, candles=candles, signal=signal)
    account, config = _add_account_and_config(db_session, execution_mode="live")
    client = RecordingProjectXClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_ID,
        config=config,
        account=account,
        client=client,
        dry_run=False,
        confirm_live_order_routing=True,
    )

    assert result.decision.action == "SELL"
    assert result.order_attempt is not None
    assert result.order_attempt.status == "submitted"
    assert client.place_order_calls == [
        {
            "account_id": ACCOUNT_ID,
            "contract_id": CONTRACT_ID,
            "order_type": 2,
            "side": 1,
            "size": 1,
            "custom_tag": result.order_attempt.raw_request["customTag"],
            "stop_loss_bracket": {"ticks": 4, "type": 4},
            "take_profit_bracket": {"ticks": 8, "type": 1},
        }
    ]


def test_topbot_dry_run_records_order_attempt_without_projectx_call(db_session, monkeypatch):
    candles = _fresh_candles()
    signal = _topbot_signal("BUY", candles[-1].candle_timestamp)
    _inject_topbot_signal(monkeypatch, candles=candles, signal=signal)
    account, config = _add_account_and_config(db_session, execution_mode="dry_run")
    client = RecordingProjectXClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_ID,
        config=config,
        account=account,
        client=client,
        dry_run=True,
        confirm_live_order_routing=False,
    )

    assert result.order_attempt is not None
    assert result.order_attempt.status == "dry_run"
    assert result.order_attempt.raw_response == {"dry_run": True, "message": "Order not sent to ProjectX."}
    assert result.order_attempt.raw_request["side"] == 0
    assert result.order_attempt.raw_request["stopLossBracket"] == {"ticks": 4, "type": 4}
    assert result.order_attempt.raw_request["takeProfitBracket"] == {"ticks": 8, "type": 1}
    assert client.place_order_calls == []


def test_topbot_live_without_confirmation_blocks_projectx_call(db_session, monkeypatch):
    candles = _fresh_candles()
    signal = _topbot_signal("BUY", candles[-1].candle_timestamp)
    _inject_topbot_signal(monkeypatch, candles=candles, signal=signal)
    account, config = _add_account_and_config(db_session, execution_mode="live")
    client = RecordingProjectXClient()

    result = evaluate_bot_config(
        db_session,
        user_id=USER_ID,
        config=config,
        account=account,
        client=client,
        dry_run=False,
        confirm_live_order_routing=False,
    )

    assert result.order_attempt is None
    assert {event.code for event in result.risk_events} == {"live_order_confirmation_missing"}
    assert client.place_order_calls == []
