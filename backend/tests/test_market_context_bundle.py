"""Independent checks of collected context and observed excursion boundaries."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.market_observation_models import MarketObservation
from app.models import PositionLifecycle, ProjectXMarketCandle, ProjectXTradeEvent
from app.services import market_context_bundle as bundle
from app.services.trade_excursions import attach_trade_excursions


USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
CONTRACT = "CON.F.US.MNQ.U26"
OTHER_CONTRACT = "CON.F.US.MNQ.Z26"
AS_OF = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    tables = [MarketObservation.__table__, ProjectXMarketCandle.__table__, PositionLifecycle.__table__, ProjectXTradeEvent.__table__]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        yield session
    engine.dispose()


def observation(db, *, event_type="quote", source=None, user=USER, contract=CONTRACT,
                provider_time=None, received=None, bid=None, ask=None, price=None, size=None, side=None):
    row = MarketObservation(user_id=user, contract_id=contract,
        source=source or ("projectx_gateway_trade" if event_type == "trade" else "projectx_gateway_depth"),
        event_type=event_type, provider_timestamp=provider_time or AS_OF,
        received_at=received or AS_OF, fingerprint=str(uuid4()), bid=bid, ask=ask,
        price=price, size=size, side=side, details={})
    db.add(row)
    db.flush()
    return row


def test_book_excludes_future_provider_and_receipt_times_other_user_and_contract(db):
    observation(db, bid=100, ask=100.25, received=AS_OF-timedelta(seconds=1), provider_time=AS_OF-timedelta(seconds=1))
    observation(db, bid=200, ask=200.25, provider_time=AS_OF+timedelta(seconds=1))
    observation(db, bid=300, ask=300.25, received=AS_OF+timedelta(seconds=1))
    observation(db, bid=400, ask=400.25, user=OTHER)
    observation(db, bid=500, ask=500.25, contract=OTHER_CONTRACT)
    result = bundle._book_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["bid"] == 100
    assert result["ask"] == 100.25
    assert result["status"] == "fresh"
    assert result["full_depth_verified"] is False


def test_book_age_uses_both_provider_and_receipt_timestamps(db):
    observation(db, bid=100, ask=100.25, provider_time=AS_OF-timedelta(minutes=1))
    result = bundle._book_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["status"] == "stale"
    assert result["age_seconds"] == 60


@pytest.mark.parametrize("bid,ask", [(None, 101), (100, None), (None, None), (102, 101)])
def test_invalid_latest_book_does_not_resurrect_older_quote(db, bid, ask):
    observation(db, bid=100, ask=100.25, received=AS_OF-timedelta(seconds=2), provider_time=AS_OF-timedelta(seconds=2))
    observation(db, bid=bid, ask=ask)
    result = bundle._book_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["status"] == "unavailable"
    assert "bid" not in result


def test_latest_quote_with_unknown_provider_time_invalidates_previous_book(db):
    observation(db, bid=100, ask=100.25, received=AS_OF-timedelta(seconds=2), provider_time=AS_OF-timedelta(seconds=2))
    latest = observation(db, bid=100, ask=101)
    latest.provider_timestamp = None
    db.flush()
    result = bundle._book_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["status"] == "unavailable"


@pytest.mark.parametrize("event_type", ["gap", "reset"])
def test_book_cannot_remain_fresh_after_later_gap_or_reset(db, event_type):
    observation(db, bid=100, ask=100.25, received=AS_OF-timedelta(seconds=2), provider_time=AS_OF-timedelta(seconds=2))
    observation(db, event_type=event_type, received=AS_OF-timedelta(seconds=1), provider_time=AS_OF-timedelta(seconds=1))
    result = bundle._book_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["status"] != "fresh"


def test_book_does_not_select_an_unrelated_source(db):
    observation(db, bid=100, ask=100.25, received=AS_OF-timedelta(seconds=1), provider_time=AS_OF-timedelta(seconds=1))
    observation(db, source="unrelated_feed", bid=200, ask=201)
    result = bundle._book_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["bid"] == 100


def test_partial_profile_uses_contiguous_value_area_around_poc_and_real_classification(db):
    for price, size in [(100, 10), (101, 25), (102, 40), (103, 20), (104, 5)]:
        observation(db, event_type="trade", price=price, size=size, side="buy" if price != 104 else None)
    result = bundle._profile_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["partial"] is True
    assert result["status"] == "partial"
    assert result["recorded_volume"] == 100
    assert result["poc"] == 102
    assert result["value_area_low"] == 101
    assert result["value_area_high"] == 103
    assert result["classification_coverage"] == .95
    assert result["cumulative_delta"] is None
    assert result["vwap"] == pytest.approx(101.85)


def test_profile_does_not_mix_contracts_users_sources_or_future_observations(db):
    observation(db, event_type="trade", price=100, size=10, side="buy")
    observation(db, event_type="trade", price=101, size=5, side="sell")
    observation(db, event_type="trade", price=200, size=1000, contract=OTHER_CONTRACT)
    observation(db, event_type="trade", price=300, size=1000, user=OTHER)
    observation(db, event_type="trade", price=400, size=1000, source="another_trade_feed")
    observation(db, event_type="trade", price=500, size=1000, provider_time=AS_OF+timedelta(seconds=1))
    observation(db, event_type="trade", price=600, size=1000, received=AS_OF+timedelta(seconds=1))
    observation(db, event_type="trade", price=700, size=1000, provider_time=AS_OF-timedelta(days=1))
    result = bundle._profile_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["recorded_volume"] == 15
    assert result["recorded_trade_count"] == 2
    assert result["classification_coverage"] == 1
    assert result["cumulative_delta"] == 5


def test_optional_read_error_rolls_back_savepoint_and_preserves_outer_transaction(db, monkeypatch):
    quote = observation(db, bid=100, ask=100.25)

    def unavailable(*args, **kwargs):
        db.execute(text("select * from deliberately_missing_optional_table"))

    monkeypatch.setattr(bundle, "get_market_event_context", unavailable)
    result = bundle.build_collected_context(db, user_id=USER, contract_id=CONTRACT, as_of=AS_OF)
    assert result["events"]["status"] == "unavailable"
    assert result["order_book"]["status"] == "fresh"
    assert db.is_active
    db.commit()
    assert db.get(MarketObservation, quote.id) is not None
    assert db.execute(text("select 1")).scalar() == 1


def test_bundle_passes_selected_mode_and_decision_cutoff_to_related_markets(db, monkeypatch):
    seen = {}

    class Result:
        def model_dump(self, *, mode):
            return {"live": seen["live"], "as_of": seen["as_of"].isoformat()}

    def context(_db, **kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr(bundle, "stored_market_context", context)
    monkeypatch.setattr(bundle, "get_market_event_context", lambda *args, **kwargs: {"news_risk": "unknown"})
    result = bundle.build_collected_context(db, user_id=USER, contract_id=CONTRACT, live=True, as_of=AS_OF)
    assert seen == {"user_id": USER, "live": True, "as_of": AS_OF, "collected_by_as_of": True}
    assert result["related_markets"]["live"] is True
    assert result["as_of"] == AS_OF.isoformat()


def test_lifecycle_initialized_zeros_without_observed_marks_are_not_trade_excursions(db):
    opened = AS_OF-timedelta(minutes=5)
    db.add(PositionLifecycle(user_id=USER, account_id=1, contract_id=CONTRACT, symbol="MNQ", side="LONG",
        opened_at=opened, closed_at=AS_OF, max_qty=1, mae_usd=0, mfe_usd=0,
        mae_timestamp=opened, mfe_timestamp=opened))
    closing = ProjectXTradeEvent(user_id=USER, account_id=1, contract_id=CONTRACT, symbol="MNQ",
        side="SELL", size=1, price=20000, pnl=10, fees=1, trade_timestamp=AS_OF, order_id="close")
    db.add(closing)
    db.flush()
    trade = dict(id=closing.id, account_id=1, contract_id=CONTRACT, size=1, entry_time=opened, exit_time=AS_OF)
    result = attach_trade_excursions(db, user_id=USER, account_id=1, trades=[trade])[0]
    assert result["mae"] is None
    assert result["mfe"] is None
    assert result["excursion_scope"] == "unavailable"


def test_decision_context_excludes_candles_collected_after_cutoff(db, monkeypatch):
    for minutes, price, fetched in [(2, 100, AS_OF-timedelta(seconds=30)), (1, 200, AS_OF+timedelta(seconds=1))]:
        db.add(ProjectXMarketCandle(user_id=USER, contract_id=CONTRACT, symbol="F.US.MNQ", live=False,
            unit="minute", unit_number=1, candle_timestamp=AS_OF-timedelta(minutes=minutes),
            open_price=price, high_price=price+1, low_price=price-1, close_price=price,
            volume=10, is_partial=False, source="projectx", fetched_at=fetched))
    db.flush()
    monkeypatch.setattr(bundle, "get_market_event_context", lambda *args, **kwargs: {"news_risk": "unknown"})
    result = bundle.build_collected_context(db, user_id=USER, contract_id=CONTRACT, live=False, as_of=AS_OF)
    mnq = next(item for item in result["related_markets"]["items"] if item["symbol"] == "MNQ")
    assert mnq["close"] == 100
