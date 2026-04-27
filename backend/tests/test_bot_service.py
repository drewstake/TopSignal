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
from app.bot_schemas import BotConfigCreateIn, BotConfigUpdateIn
from app.services.bot_service import (
    create_bot_config,
    delete_bot_config,
    evaluate_bot_config,
    evaluate_sma_cross,
    fetch_and_store_market_candles,
    list_market_candles,
    update_bot_config,
)
from app.services.projectx_client import ProjectXClientError
import app.main as main_module


def _dt(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def _make_candle(timestamp: datetime, close: float, **overrides) -> ProjectXMarketCandle:
    values = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "contract_id": "CON.F.US.MNQ.M26",
        "symbol": "MNQ",
        "live": False,
        "unit": "minute",
        "unit_number": 5,
        "candle_timestamp": timestamp,
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "volume": 1,
        "is_partial": False,
    }
    values.update(overrides)
    return ProjectXMarketCandle(**values)


def _as_test_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _make_bot_create_payload(name: str = "Test Bot") -> BotConfigCreateIn:
    return BotConfigCreateIn(
        name=name,
        account_id=9001,
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=2,
        slow_period=3,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=3,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3600,
    )


def test_sma_cross_generates_buy_signal_on_latest_closed_candle():
    candles = [
        _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10),
        _make_candle(datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc), 10),
        _make_candle(datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc), 9),
        _make_candle(datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc), 20),
    ]

    signal = evaluate_sma_cross(candles, fast_period=2, slow_period=3)

    assert signal.action == "BUY"
    assert "SMA crossover" in signal.reason
    assert signal.price == 20.0


def test_list_market_candles_returns_cached_rows_sorted_with_limit():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        db.add_all(
            [
                _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10),
                _make_candle(datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc), 11),
                _make_candle(datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc), 12),
                _make_candle(
                    datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc),
                    13,
                    is_partial=True,
                ),
            ]
        )
        db.commit()

        rows = list_market_candles(
            db,
            user_id=user_id,
            contract_id="CON.F.US.MNQ.M26",
            live=False,
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 20, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=2,
            include_partial_bar=False,
        )

        assert [float(row.close_price) for row in rows] == [11.0, 12.0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_create_bot_config_rejects_duplicate_name_for_user():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [Account.__table__, BotConfig.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        db.add(
            Account(
                user_id=user_id,
                provider="projectx",
                external_id="9001",
                name="Practice 9001",
                account_state="ACTIVE",
                can_trade=True,
                is_visible=True,
            )
        )
        db.flush()
        create_bot_config(db, user_id=user_id, payload=_make_bot_create_payload(name="Opening Drive"))
        db.flush()

        with pytest.raises(ValueError, match="already exists"):
            create_bot_config(db, user_id=user_id, payload=_make_bot_create_payload(name=" opening drive "))
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_update_bot_config_rejects_duplicate_name_for_user():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [BotConfig.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        first = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Opening Drive",
            enabled=False,
            execution_mode="dry_run",
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=2,
            slow_period=3,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3600,
        )
        second = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Reversal",
            enabled=False,
            execution_mode="dry_run",
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=2,
            slow_period=3,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3600,
        )
        db.add_all([first, second])
        db.flush()

        with pytest.raises(ValueError, match="already exists"):
            update_bot_config(
                db,
                user_id=user_id,
                bot_config_id=second.id,
                payload=BotConfigUpdateIn(name=" opening drive "),
            )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_delete_bot_config_removes_activity_rows():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        BotConfig.__table__,
        BotRun.__table__,
        BotDecision.__table__,
        BotOrderAttempt.__table__,
        BotRiskEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        config = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Delete Me",
            enabled=False,
            execution_mode="dry_run",
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=2,
            slow_period=3,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3600,
        )
        db.add(config)
        db.flush()
        run = BotRun(user_id=user_id, bot_config_id=config.id, account_id=9001, dry_run=True, status="stopped")
        db.add(run)
        db.flush()
        decision = BotDecision(
            user_id=user_id,
            bot_config_id=config.id,
            bot_run_id=run.id,
            account_id=9001,
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            decision_type="signal",
            action="HOLD",
            reason="test",
        )
        db.add(decision)
        db.flush()
        db.add(
            BotOrderAttempt(
                user_id=user_id,
                bot_config_id=config.id,
                bot_run_id=run.id,
                bot_decision_id=decision.id,
                account_id=9001,
                contract_id="CON.F.US.MNQ.M26",
                side="BUY",
                order_type="market",
                size=1,
                status="dry_run",
            )
        )
        db.add(
            BotRiskEvent(
                user_id=user_id,
                bot_config_id=config.id,
                bot_run_id=run.id,
                account_id=9001,
                severity="info",
                code="test",
                message="test",
            )
        )
        db.commit()

        delete_bot_config(db, user_id=user_id, bot_config_id=config.id)
        db.commit()

        assert db.query(BotConfig).count() == 0
        assert db.query(BotRun).count() == 0
        assert db.query(BotDecision).count() == 0
        assert db.query(BotOrderAttempt).count() == 0
        assert db.query(BotRiskEvent).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_candles_endpoint_returns_local_cache_without_provider_call(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )

    try:
        db.add(_make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10))
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert len(payload) == 1
        assert payload[0]["close"] == 10.0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_fetches_only_missing_closed_tail(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
                    "open": 11,
                    "high": 12,
                    "low": 10,
                    "close": 11,
                    "volume": 1,
                }
            ]

    client = StubClient()
    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: client)

    try:
        db.add(_make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10))
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert [row["close"] for row in payload] == [10.0, 11.0]
        assert len(client.calls) == 1
        assert client.calls[0]["start"] == datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc)
        assert client.calls[0]["end"] == datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_resolves_legacy_symbol_to_cached_contract(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def search_contracts(self, *, search_text, live):
            assert search_text == "MNQ"
            assert live is False
            return [
                {
                    "id": "CON.F.US.MNQ.M26",
                    "name": "MNQM6",
                    "active_contract": True,
                    "symbol_id": "F.US.MNQ",
                }
            ]

        def retrieve_bars(self, **_kwargs):
            raise AssertionError("cached resolved candles should avoid provider history")

    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: StubClient())

    try:
        db.add(
            _make_candle(
                datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                10,
                contract_id="CON.F.US.MNQ.M26",
                symbol="F.US.MNQ",
            )
        )
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="MNQ",
            symbol="MNQ",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert len(payload) == 1
        assert payload[0]["contract_id"] == "CON.F.US.MNQ.M26"
        assert payload[0]["symbol"] == "F.US.MNQ"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_returns_cached_rows_when_refresh_provider_fails(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            raise ProjectXClientError("ProjectX request timed out.", status_code=504)

    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: StubClient())

    try:
        db.add(_make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10))
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=True,
            db=db,
        )

        assert len(payload) == 1
        assert payload[0]["close"] == 10.0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_refresh_replaces_cached_window(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                    "open": 11,
                    "high": 12,
                    "low": 10,
                    "close": 11,
                    "volume": 1,
                },
                {
                    "timestamp": datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
                    "open": 13,
                    "high": 14,
                    "low": 12,
                    "close": 13,
                    "volume": 1,
                },
            ]

    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: StubClient())

    try:
        db.add_all(
            [
                _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10),
                _make_candle(datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc), 999),
            ]
        )
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=True,
            db=db,
        )

        assert [row["timestamp"] for row in payload] == [
            datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
        ]
        assert [row["close"] for row in payload] == [11.0, 13.0]

        cached_rows = list_market_candles(
            db,
            user_id=user_id,
            contract_id="CON.F.US.MNQ.M26",
            live=False,
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
        )
        assert [_as_test_utc(row.candle_timestamp) for row in cached_rows] == [
            datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
        ]
        assert [row.close_price for row in cached_rows] == [11.0, 13.0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_fetch_market_candles_resolves_legacy_symbol_before_history_call():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def search_contracts(self, *, search_text, live):
            assert search_text == "MNQ"
            assert live is False
            return [
                {
                    "id": "CON.F.US.MNQ.M26",
                    "name": "MNQM6",
                    "active_contract": True,
                    "symbol_id": "F.US.MNQ",
                }
            ]

        def retrieve_bars(self, **kwargs):
            assert kwargs["contract_id"] == "CON.F.US.MNQ.M26"
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            ]

    try:
        rows = fetch_and_store_market_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            client=StubClient(),
            contract_id="MNQ",
            symbol="MNQ",
            live=False,
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
        )

        assert len(rows) == 1
        assert rows[0].contract_id == "CON.F.US.MNQ.M26"
        assert rows[0].symbol == "F.US.MNQ"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_dry_run_evaluation_logs_order_attempt_without_submitting():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Account.__table__,
        ProjectXMarketCandle.__table__,
        BotConfig.__table__,
        BotRun.__table__,
        BotDecision.__table__,
        BotOrderAttempt.__table__,
        BotRiskEvent.__table__,
        ProjectXTradeEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        place_order_called = False

        def retrieve_bars(self, **_kwargs):
            return [
                {"timestamp": _dt(3), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(2), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(1), "open": 9, "high": 9, "low": 9, "close": 9, "volume": 1},
                {"timestamp": _dt(0), "open": 20, "high": 20, "low": 20, "close": 20, "volume": 1},
            ]

        def place_order(self, **_kwargs):
            self.place_order_called = True
            raise AssertionError("dry-run evaluation should not place provider orders")

    try:
        account = Account(
            user_id="00000000-0000-0000-0000-000000000000",
            provider="projectx",
            external_id="9001",
            name="Practice 9001",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        db.add(account)
        config = BotConfig(
            user_id="00000000-0000-0000-0000-000000000000",
            account_id=9001,
            name="Test Bot",
            enabled=True,
            execution_mode="dry_run",
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=2,
            slow_period=3,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3600,
        )
        db.add(config)
        db.flush()
        run = BotRun(
            user_id="00000000-0000-0000-0000-000000000000",
            bot_config_id=config.id,
            account_id=9001,
            dry_run=True,
            status="running",
        )
        db.add(run)
        db.flush()
        client = StubClient()

        result = evaluate_bot_config(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            account=account,
            client=client,
            run=run,
            dry_run=True,
        )
        db.commit()

        assert result.decision.action == "BUY"
        assert result.order_attempt is not None
        assert result.order_attempt.status == "dry_run"
        assert client.place_order_called is False
        assert db.query(BotDecision).count() == 1
        assert db.query(BotOrderAttempt).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def test_live_evaluation_is_blocked_without_explicit_confirmation():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Account.__table__,
        ProjectXMarketCandle.__table__,
        BotConfig.__table__,
        BotRun.__table__,
        BotDecision.__table__,
        BotOrderAttempt.__table__,
        BotRiskEvent.__table__,
        ProjectXTradeEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            return [
                {"timestamp": _dt(3), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(2), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(1), "open": 9, "high": 9, "low": 9, "close": 9, "volume": 1},
                {"timestamp": _dt(0), "open": 20, "high": 20, "low": 20, "close": 20, "volume": 1},
            ]

        def place_order(self, **_kwargs):
            raise AssertionError("risk gate should block provider order submission")

    try:
        account = Account(
            user_id="00000000-0000-0000-0000-000000000000",
            provider="projectx",
            external_id="9002",
            name="Practice 9002",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        config = BotConfig(
            user_id="00000000-0000-0000-0000-000000000000",
            account_id=9002,
            name="Live Block Test",
            enabled=True,
            execution_mode="live",
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=2,
            slow_period=3,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3600,
        )
        db.add_all([account, config])
        db.flush()

        result = evaluate_bot_config(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            account=account,
            client=StubClient(),
            dry_run=False,
            confirm_live_order_routing=False,
        )
        db.commit()

        assert result.order_attempt is None
        assert {event.code for event in result.risk_events} == {"live_order_confirmation_missing"}
        assert db.query(BotRiskEvent).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()
