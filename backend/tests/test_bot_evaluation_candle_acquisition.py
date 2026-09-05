import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import BotConfig, ProjectXMarketCandle
from app.services import bot_candle_acquisition, bot_service
from app.services.bot_service import SignalResult, _is_contract_allowed, fetch_and_store_candles


USER_ID = "00000000-0000-0000-0000-000000000000"
SAVED_CONTRACT = "CON.F.US.MNQ.M26"
ACTIVE_CONTRACT = "CON.F.US.MNQ.U26"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def _config(*, allowed_contracts=None) -> BotConfig:
    return BotConfig(
        contract_id=SAVED_CONTRACT,
        symbol="F.US.MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        allowed_contracts=allowed_contracts or [],
    )


def _bar(timestamp: datetime, close: float) -> dict:
    return {
        "timestamp": timestamp,
        "open": close - 0.25,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": 100,
    }


def test_evaluation_rolls_expired_contract_to_active_symbol_and_widens_intraday_window(db_session):
    now = datetime.now(timezone.utc)
    bars = [_bar(now - timedelta(minutes=5 * (30 - index)), 30_000 + index) for index in range(30)]

    class StubClient:
        def __init__(self):
            self.search_texts = []
            self.history_contract_ids = []
            self.requested_start = None

        def search_contracts(self, *, search_text, live):
            assert live is False
            self.search_texts.append(search_text)
            if search_text == "F.US.MNQ":
                return []
            assert search_text == "MNQ"
            return [
                {
                    "id": ACTIVE_CONTRACT,
                    "active_contract": True,
                    "symbol_id": "F.US.MNQ",
                }
            ]

        def retrieve_bars(self, **kwargs):
            self.history_contract_ids.append(kwargs["contract_id"])
            self.requested_start = kwargs["start"]
            return bars

    client = StubClient()
    rows = fetch_and_store_candles(
        db_session,
        user_id=USER_ID,
        config=_config(),
        client=client,
    )

    assert client.search_texts == ["F.US.MNQ", "MNQ"]
    assert client.history_contract_ids == [ACTIVE_CONTRACT]
    assert client.requested_start <= now - timedelta(days=6, hours=23)
    assert len(rows) == 25
    assert {row.contract_id for row in rows} == {ACTIVE_CONTRACT}
    assert float(rows[-1].close_price) == 30_029


def test_evaluation_keeps_active_contract_cache_when_provider_returns_empty(db_session):
    now = datetime.now(timezone.utc)
    for index in range(25):
        close = 30_000 + index
        db_session.add(
            ProjectXMarketCandle(
                user_id=USER_ID,
                contract_id=ACTIVE_CONTRACT,
                symbol="F.US.MNQ",
                live=False,
                unit="minute",
                unit_number=5,
                candle_timestamp=now - timedelta(minutes=5 * (25 - index)),
                open_price=close - 0.25,
                high_price=close + 0.5,
                low_price=close - 0.5,
                close_price=close,
                volume=100,
                is_partial=False,
            )
        )
    db_session.flush()

    class StubClient:
        def search_contracts(self, *, search_text, live):
            assert search_text == "F.US.MNQ"
            assert live is False
            return [{"id": ACTIVE_CONTRACT, "active_contract": True, "symbol_id": "F.US.MNQ"}]

        def retrieve_bars(self, **kwargs):
            assert kwargs["contract_id"] == ACTIVE_CONTRACT
            return []

    rows = fetch_and_store_candles(
        db_session,
        user_id=USER_ID,
        config=_config(),
        client=StubClient(),
    )

    assert len(rows) == 25
    assert {row.contract_id for row in rows} == {ACTIVE_CONTRACT}
    assert float(rows[-1].close_price) == 30_024


def test_rollover_contract_must_match_actual_execution_allowlist():
    config = _config(allowed_contracts=[SAVED_CONTRACT])

    assert _is_contract_allowed(config, contract_id=ACTIVE_CONTRACT, symbol="F.US.MNQ") is False

    config.allowed_contracts = ["F.US.MNQ"]
    assert _is_contract_allowed(config, contract_id=ACTIVE_CONTRACT, symbol="F.US.MNQ") is True
