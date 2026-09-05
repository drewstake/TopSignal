import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

from fastapi import FastAPI
import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.models import ProjectXMarketCandle
from app import market_data_routes as routes
from app.services.market_data_context import stored_market_context
from app.services import public_market_context as public


UTC = timezone.utc
NOW = datetime(2026, 9, 4, 22, tzinfo=UTC)
USER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
START = date(2026, 9, 1)
END = date(2026, 9, 4)


def h15(*rows):
    return ('"Series Description","Two year","Ten year"\r\r\n'
        '"Unit:","Percent:_Per_Year","Percent:_Per_Year"\r\r\n'
        '"Multiplier:","1","1"\r\r\n'
        '"Time Period","RIFLGFCY02_N.B","RIFLGFCY10_N.B"\r\r\n' +
        "\r\r\n".join(rows)).encode()


def observations():
    return public.parse_h15(h15("2026-09-02,4.39,4.79", "2026-09-03,4.34,4.77"), start=START, end_exclusive=END)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[ProjectXMarketCandle.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def item(db, **kwargs):
    return next(row for row in stored_market_context(db, user_id=USER, as_of=NOW, **kwargs).items if row.symbol == "US10Y")


def test_h15_selects_exact_daily_ten_year_series_and_skips_missing_future_dates():
    rows = public.parse_h15(h15("2026-08-31,1,2", "2026-09-01,1,4.75", "2026-09-02,1,ND", "2026-09-04,1,99"),
                            start=START, end_exclusive=END)
    assert len(rows) == 1 and rows[0].day == START and rows[0].close == Decimal("4.75")
    assert rows[0].open == rows[0].high == rows[0].low == rows[0].close


@pytest.mark.parametrize("body", [
    h15("2026-09-01,1,NaN"), h15("2026-09-01,1,4.75", "2026-09-01,1,4.76"),
    h15("2026-09-01,1,4.75").replace(b"Percent:_Per_Year", b"Dollars"),
    h15("2026-09-01,1,4.75").replace(b"RIFLGFCY10_N.B", b"RIFLGFCY10_N.M"),
    h15("2026-09-01,1,4.7500001"), b"x" * (public.MAX_BYTES + 1),
], ids=["nonfinite", "conflict", "wrong_units", "wrong_series", "precision", "too_large"])
def test_bad_provider_data_is_rejected(body):
    with pytest.raises(public.PublicDataError):
        public.parse_h15(body, start=START, end_exclusive=END)


def test_vix_uses_real_ohlc_only_within_bounded_completed_dates():
    body = b"DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,0,0,0,17.24\n09/01/2026,14.95,16.80,14.95,16.34\n09/04/2026,1,2,1,2"
    rows = public.parse_vix(body, start=START, end_exclusive=END)
    assert len(rows) == 1 and rows[0].open == Decimal("14.95") and rows[0].close == Decimal("16.34")
    with pytest.raises(public.PublicDataError, match="invalid_ohlc"):
        public.parse_vix(body.replace(b"16.80", b"15.00"), start=START, end_exclusive=END)


def test_day_bounds_follow_new_york_dst():
    start, end = public.observation_bounds(date(2026, 3, 8))
    assert (end - start).total_seconds() == 23 * 3600 and start.hour == 5 and end.hour == 4
    start, end = public.observation_bounds(date(2026, 11, 1))
    assert (end - start).total_seconds() == 25 * 3600


def test_merge_preserves_first_collected_values_and_counts_revision_conflicts(db):
    original = observations()
    assert public.merge_observations(db, user_id=USER, symbol="US10Y", observations=original, collected_at=NOW) == (2, 0, 0)
    assert public.merge_observations(db, user_id=USER, symbol="US10Y", observations=original, collected_at=NOW + timedelta(hours=1)) == (0, 2, 0)
    revision = public.parse_h15(h15("2026-09-03,4.34,4.78"), start=START, end_exclusive=END)
    assert public.merge_observations(db, user_id=USER, symbol="US10Y", observations=revision, collected_at=NOW + timedelta(hours=2)) == (0, 0, 1)
    latest = db.query(ProjectXMarketCandle).order_by(ProjectXMarketCandle.candle_timestamp.desc()).first()
    assert latest.close_price == Decimal("4.77") and public._utc(latest.fetched_at) == NOW
    assert latest.raw_payload["published_at"] is None
    assert latest.raw_payload["storage_semantics"] == "yield_point_in_required_price_columns"


def test_public_context_never_backfills_before_collection_even_in_retrospective_mode(db):
    public.merge_observations(db, user_id=USER, symbol="US10Y", observations=observations(), collected_at=NOW)
    before = stored_market_context(db, user_id=USER, as_of=NOW - timedelta(seconds=1))
    assert next(row for row in before.items if row.symbol == "US10Y").status == "missing"
    current = item(db)
    assert current.close == 4.77 and current.previous_close == 4.79
    assert current.change_bps == pytest.approx(-2) and current.change_pct is None and current.volume is None
    assert current.value_unit == "percent_per_year" and current.observation_kind == "daily_yield_observation"
    assert current.data_mode == "public_daily" and current.available_at == NOW
    assert current.observation_date == date(2026, 9, 3)
    assert item(db, live=True).close == current.close


def test_public_context_age_tracks_observation_date_and_remains_tenant_scoped(db):
    old = public.parse_h15(h15("2026-09-01,4,4.70"), start=START, end_exclusive=END)
    public.merge_observations(db, user_id=USER, symbol="US10Y", observations=old, collected_at=NOW)
    public.merge_observations(db, user_id=OTHER, symbol="US10Y", observations=observations(), collected_at=NOW)
    current = item(db)
    assert current.close == 4.7 and current.status == "stale"
    assert current.age_seconds > 2 * 86400 and current.available_at == NOW
    status = public.public_market_status(db, user_id=USER).sources[0]
    assert status.stored_rows == 1 and status.latest_observation_date == START


def test_corrupt_provenance_and_incomplete_day_cannot_enter_context(db):
    public.merge_observations(db, user_id=USER, symbol="US10Y", observations=observations()[-1:], collected_at=NOW)
    row = db.query(ProjectXMarketCandle).first()
    row.raw_payload = {**row.raw_payload, "first_collected_at": (NOW - timedelta(days=5)).isoformat()}
    db.commit()
    assert item(db).status == "missing"
    with pytest.raises(public.PublicDataError, match="observation_day_not_complete"):
        public.merge_observations(db, user_id=USER, symbol="US10Y", observations=observations(), collected_at=NOW - timedelta(days=2))


def test_disabled_cboe_never_makes_network_request_and_status_is_explicit(db, monkeypatch):
    monkeypatch.delenv("CBOE_VIX_ENABLED", raising=False)
    monkeypatch.setattr(public, "_download", lambda *args, **kwargs: pytest.fail("disabled source downloaded"))
    result = public.refresh_public_market_context(db, user_id=USER, symbols=["VIX"], days=365)
    assert result.items[0].status == "disabled" and "CBOE_VIX_ENABLED" in result.items[0].detail
    status = public.public_market_status(db, user_id=USER).sources[1]
    assert status.enabled is False and status.status == "disabled" and "https://www.cboe.com/terms" in status.data_notice


def test_refresh_receipt_after_download_and_one_request_per_unique_symbol(db, monkeypatch):
    class Clock(datetime):
        current = NOW
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(public, "datetime", Clock)
    calls = []
    def download(symbol, *, start, end_exclusive):
        calls.append((symbol, start, end_exclusive))
        Clock.current += timedelta(seconds=5)
        return h15("2026-09-03,4.34,4.77")
    monkeypatch.setattr(public, "_download", download)
    result = public.refresh_public_market_context(db, user_id=USER, symbols=["US10Y", "US10Y"], days=365)
    assert len(calls) == 1 and (calls[0][2] - calls[0][1]).days == 365
    assert result.items[0].inserted_rows == 1
    stored = db.query(ProjectXMarketCandle).one()
    assert public._utc(stored.fetched_at) == NOW + timedelta(seconds=5)


def test_download_fixed_endpoint_no_redirects_and_sanitized_error(monkeypatch):
    def get(url, **kwargs):
        assert url == public.FED_URL and kwargs["params"]["series"] == public.FED_PACKAGE
        assert kwargs["params"]["from"] == "09/01/2026" and kwargs["params"]["to"] == "09/03/2026"
        assert kwargs["allow_redirects"] is False and sum(kwargs["timeout"]) <= 30
        raise requests.Timeout("SECRET RESPONSE SHOULD NEVER ESCAPE")
    monkeypatch.setattr(public.requests, "get", get)
    with pytest.raises(public.PublicDataError, match="^source_transport_error$"):
        public._download("US10Y", start=START, end_exclusive=END)


def request(app, method, path, body=None):
    sent = []
    consumed = False
    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"", "more_body": False}
    async def send(message):
        sent.append(message)
    scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method=method,
        scheme="http", path=path, raw_path=path.encode(), query_string=b"", root_path="",
        headers=[(b"content-type", b"application/json")], client=("127.0.0.1", 1000), server=("testserver", 80))
    asyncio.run(app(scope, receive, send))
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


@pytest.mark.parametrize("method,path", [("GET", "/api/market-data/public-status"), ("POST", "/api/market-data/refresh-public")])
def test_public_routes_require_authentication(db, monkeypatch, method, path):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(routes, "auth_required", lambda: True)
    monkeypatch.setattr(routes, "get_authenticated_user", lambda: None)
    monkeypatch.setattr(routes, "refresh_public_market_context", lambda **kwargs: pytest.fail("unauthenticated refresh"))
    assert request(app, method, path, {}) == 401


def test_public_refresh_routes_validate_bounds_and_share_mutation_lock(db, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[routes.market_data_user_id] = lambda: USER
    assert request(app, "POST", "/api/market-data/refresh-public", {"symbols": ["US10Y"], "days": 366}) == 422
    assert request(app, "POST", "/api/market-data/refresh-public", {"symbols": ["QQQ"]}) == 422
    assert request(app, "POST", "/api/market-data/refresh-public", {"url": "https://example.com"}) == 422
    monkeypatch.delenv("CBOE_VIX_ENABLED", raising=False)
    assert request(app, "POST", "/api/market-data/refresh-public", {"symbols": ["VIX"]}) == 200
    assert request(app, "GET", "/api/market-data/public-status") == 200
    assert routes._MUTATION_SLOT.acquire(blocking=False)
    try:
        assert request(app, "POST", "/api/market-data/refresh-public", {"symbols": ["VIX"]}) == 429
    finally:
        routes._MUTATION_SLOT.release()
