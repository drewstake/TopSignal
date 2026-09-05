from datetime import datetime, timedelta, timezone
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import AuthenticatedUser
from app.db import Base, get_db
from app.market_event_models import MarketEventSourceSnapshot, MarketEventVersion
from app import market_event_routes as routes
from app.services import market_event_providers as feeds
from app.services import market_events as service

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
USER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"


def request(app, method, path, body=None):
    url = urlsplit(path)
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
        scheme="http", path=url.path, raw_path=url.path.encode(), query_string=url.query.encode(),
        root_path="", headers=[(b"content-type", b"application/json")],
        client=("127.0.0.1", 1000), server=("testserver", 80))
    asyncio.run(app(scope, receive, send))
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[MarketEventVersion.__table__, MarketEventSourceSnapshot.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def calendar_event(**changes):
    return feeds._build_event("payroll", "Employment Situation", scheduled_at=NOW + timedelta(minutes=30),
                              importance="high", **changes)


def batch(event=None, complete=False):
    return feeds.FeedBatch([event or calendar_event()], NOW - timedelta(days=1), NOW + timedelta(days=8), complete)


def test_ics_dst_folded_title_future_schedule_has_no_actual():
    body = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:cpi\r\nDTSTART;TZID=America/New_York:20260714T083000\r\nSUMMARY:Consumer Price\r\n Index\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:winter\r\nDTSTART;TZID=America/New_York:20260114T083000\r\nSUMMARY:CPI\r\nEND:VEVENT\r\nEND:VCALENDAR"
    events = feeds.parse_bls(body, NOW).events
    assert events[0]["scheduled_at"].hour == 12
    assert events[1]["scheduled_at"].hour == 13
    assert events[0]["title"] == "Consumer PriceIndex"
    assert events[0]["importance"] == "high"
    assert events[0]["actual"] is None and events[0]["published_at"] is None


@pytest.mark.parametrize("stamp,code", [("20261101T013000", "ambiguous_local_time"), ("20260308T023000", "invalid_local_time")])
def test_ics_ambiguous_or_nonexistent_local_time_is_rejected(stamp, code):
    with pytest.raises(feeds.ProviderError, match=code):
        feeds._ical_datetime("DTSTART;TZID=America/New_York", stamp)


def test_rss_published_timestamp_is_distinct_and_markup_removed():
    body = b'<rss><channel><item><title>&lt;b&gt;Policy&lt;/b&gt;</title><guid>abc</guid><pubDate>Fri, 4 Sep 2026 11:00:00 GMT</pubDate><link>javascript:alert(1)</link></item></channel></rss>'
    event = feeds.parse_fed_news(body, NOW).events[0]
    assert event["title"] == "Policy" and event["url"] is None
    assert event["published_at"] == NOW - timedelta(hours=1)


@pytest.mark.parametrize("body", [b'<!DOCTYPE rss [<!ENTITY boom "x">]><rss/>', b'x' * (feeds.MAX_BYTES + 1)], ids=["entity", "oversized"])
def test_untrusted_xml_and_oversized_payloads_are_rejected(body):
    with pytest.raises(feeds.ProviderError):
        feeds.parse_fed_news(body, NOW)


def test_fomc_dates_include_shaded_rows_and_cross_month_without_invented_time():
    body = b'''<h4>2026 FOMC Meetings</h4><div class="fomc-meeting--shaded fomc-meeting__month">September</div><div class="fomc-meeting__date">15-16*</div><h4>2027 FOMC Meetings</h4><div class="fomc-meeting__month">Apr/May</div><div class="fomc-meeting__date">30-1</div>'''
    events = feeds.parse_fed_calendar(body, NOW).events
    assert len(events) == 2
    assert events[0]["time_precision"] == "date"
    assert events[0]["scheduled_at"] == datetime(2026, 9, 15, 4, tzinfo=UTC)
    assert events[0]["scheduled_end_at"] == datetime(2026, 9, 17, 4, tzinfo=UTC)
    assert events[1]["scheduled_end_at"] == datetime(2027, 5, 2, 4, tzinfo=UTC)


def test_changed_fomc_markup_fails_instead_of_silently_dropping_events():
    with pytest.raises(feeds.ProviderError, match="incomplete_fomc_calendar"):
        feeds.parse_fed_calendar(b'<h4>2026 FOMC Meetings</h4><div class="fomc-meeting__month">September</div><span>15-16</span>', NOW)


def test_te_zero_actual_utc_and_future_values_withheld():
    row = dict(CalendarId="1", Country="United States", Event="CPI", Date="2026-09-04T11:30:00", Actual=0, Forecast="1%", Previous="2%", Revised="1.9%", Importance=3)
    event = feeds.parse_trading_economics(json.dumps([row]).encode(), NOW, NOW - timedelta(days=1), NOW + timedelta(days=8)).events[0]
    assert event["actual"] == "0" and event["state"] == "released"
    row["Date"] = "2026-09-04T12:30:00"
    event = feeds.parse_trading_economics(json.dumps([row]).encode(), NOW, NOW, NOW + timedelta(days=8)).events[0]
    assert event["actual"] is None and event["revised"] is None and event["forecast"] == "1%"


def test_refresh_deduplicates_and_isolates_tenants(db):
    collector = lambda source, now: batch()
    result = service.refresh_market_events(db, user_id=USER, sources=["bls"], now=NOW, collector=collector)
    assert result["refresh"][0]["inserted_versions"] == 1
    service.refresh_market_events(db, user_id=USER, sources=["bls"], now=NOW + timedelta(minutes=2), collector=collector)
    assert db.query(MarketEventVersion).count() == 1
    assert service.list_market_events(db, user_id=OTHER, now=NOW)["events"] == []
    assert all(row["last_attempt_at"] is None for row in service.source_statuses(db, user_id=OTHER, as_of=NOW))


def test_first_seen_and_revisions_never_leak_into_earlier_asof(db):
    event = calendar_event()
    service.ingest_batch(db, user_id=USER, source="trading_economics", batch=batch(event), observed_at=NOW)
    event = {**event, "actual": "0.3%", "revised": "0.1%", "state": "released"}
    released = NOW + timedelta(hours=1)
    service.ingest_batch(db, user_id=USER, source="trading_economics", batch=batch(event), observed_at=released)
    db.commit()
    assert service.list_market_events(db, user_id=USER, as_of=NOW - timedelta(seconds=1), now=released)["events"] == []
    prior = service.list_market_events(db, user_id=USER, as_of=NOW + timedelta(minutes=45), now=released)["events"][0]
    assert prior["actual"] is None and prior["state"] == "awaiting_release"
    latest = service.list_market_events(db, user_id=USER, now=released)["events"][0]
    assert latest["actual"] == "0.3%" and latest["first_seen_at"] == NOW and latest["available_at"] == released
    assert "raw_fields" not in latest


def test_event_moved_outside_window_does_not_reappear_at_old_date(db):
    event = calendar_event()
    service.ingest_batch(db, user_id=USER, source="bls", batch=batch(event), observed_at=NOW)
    service.ingest_batch(db, user_id=USER, source="bls", batch=batch({**event, "scheduled_at": NOW + timedelta(days=5)}), observed_at=NOW + timedelta(minutes=2))
    db.commit()
    assert service.list_market_events(db, user_id=USER, start=NOW, end=NOW + timedelta(days=1), now=NOW + timedelta(minutes=2))["events"] == []


def test_future_publication_cannot_override_later_corrected_version(db):
    first = feeds._build_event("news", "Future timestamp", "news", published_at=NOW + timedelta(days=1))
    service.ingest_batch(db, user_id=USER, source="federal_reserve", batch=feeds.FeedBatch([first]), observed_at=NOW)
    assert service.list_market_events(db, user_id=USER, now=NOW)["events"] == []
    fixed = {**first, "published_at": NOW, "title": "Corrected timestamp"}
    service.ingest_batch(db, user_id=USER, source="federal_reserve", batch=feeds.FeedBatch([fixed]), observed_at=NOW + timedelta(minutes=1))
    db.commit()
    events = service.list_market_events(db, user_id=USER, start=NOW - timedelta(days=1), now=NOW + timedelta(days=2))["events"]
    assert len(events) == 1 and events[0]["title"] == "Corrected timestamp" and events[0]["first_seen_at"] == NOW


def test_risk_does_not_claim_low_from_partial_or_stale_coverage(db, monkeypatch):
    monkeypatch.setenv("TRADING_ECONOMICS_API_KEY", "test-only")
    result = service.refresh_market_events(db, user_id=USER, sources=["bls"], now=NOW,
        collector=lambda source, now: batch())
    assert result["risk"]["level"] == "high" and not result["risk"]["coverage_trusted"]
    later = NOW + timedelta(hours=2)
    assert service.list_market_events(db, user_id=USER, now=later)["risk"]["level"] == "unknown"
    result = service.refresh_market_events(db, user_id=USER, sources=["trading_economics"], now=later,
        collector=lambda source, now: batch(complete=True))
    assert result["risk"]["level"] == "low" and result["risk"]["coverage_trusted"]
    stale = service.list_market_events(db, user_id=USER, now=later + timedelta(minutes=16))
    assert stale["risk"]["level"] == "unknown"


def test_source_error_is_sanitized_and_does_not_erase_other_source(db):
    def collect(source, now):
        if source == "bls":
            raise feeds.ProviderError("https://host/?c=SECRET")
        return feeds.FeedBatch([feeds._build_event("news", "News", "news", published_at=NOW)])
    result = service.refresh_market_events(db, user_id=USER, sources=["bls", "federal_reserve"], now=NOW, collector=collect)
    assert "SECRET" not in json.dumps(result, default=str)
    assert len(result["events"]) == 1
    assert result["sources"][0]["error_code"] == "provider_failure"


def test_calendar_removal_is_versioned_without_retroactive_cancellation(db):
    original = batch()
    service.ingest_batch(db, user_id=USER, source="bls", batch=original, observed_at=NOW)
    removed = feeds.FeedBatch([], original.coverage_start, original.coverage_end)
    service.ingest_batch(db, user_id=USER, source="bls", batch=removed, observed_at=NOW + timedelta(minutes=2))
    db.commit()
    assert service.list_market_events(db, user_id=USER, now=NOW)["events"][0]["state"] == "scheduled"
    latest = service.list_market_events(db, user_id=USER, now=NOW + timedelta(minutes=2))
    assert latest["events"][0]["state"] == "cancelled" and latest["risk"]["level"] == "unknown"


def test_future_actual_never_autopublishes_when_clock_passes_release(db):
    event = calendar_event(actual="999", revised="998", state="released")
    service.ingest_batch(db, user_id=USER, source="trading_economics", batch=batch(event), observed_at=NOW)
    db.commit()
    later = service.list_market_events(db, user_id=USER, now=NOW + timedelta(hours=1))["events"][0]
    assert later["actual"] is None and later["revised"] is None and later["state"] == "awaiting_release"


def test_provider_status_cannot_backfill_historical_coverage(db, monkeypatch):
    monkeypatch.setenv("TRADING_ECONOMICS_API_KEY", "test-only")
    later = NOW + timedelta(hours=1)
    service.refresh_market_events(db, user_id=USER, sources=["trading_economics"], now=later,
        collector=lambda source, now: batch(complete=True))
    earlier = service.list_market_events(db, user_id=USER, as_of=NOW, now=later)
    assert earlier["risk"]["level"] == "unknown" and not earlier["risk"]["coverage_trusted"]
    assert earlier["events"] == []


def test_frozen_news_context_requires_first_observation_and_publication(db):
    later = NOW + timedelta(minutes=1)
    news = feeds._build_event("seen-later", "Actual headline", "news", published_at=NOW - timedelta(hours=1))
    future = feeds._build_event("future", "Future headline", "news", published_at=NOW + timedelta(hours=1))
    old = feeds._build_event("old", "Old headline", "news", published_at=NOW - timedelta(days=2))
    service.ingest_batch(db, user_id=USER, source="federal_reserve", batch=feeds.FeedBatch([news, future, old]), observed_at=later)
    db.commit()
    assert service.get_market_event_context(db, user_id=USER, as_of=NOW)["headlines"] == []
    context = service.get_market_event_context(db, user_id=USER, as_of=later)
    assert len(context["headlines"]) == 1
    assert context["headlines"][0]["title"] == "Actual headline"
    assert context["headlines"][0]["first_seen_at"] == later
    assert context["headlines"][0]["published_at"] == NOW - timedelta(hours=1)


def test_fetch_rejects_redirects_and_bounds_bytes_and_timeouts():
    class Response:
        status_code = 302
        def __enter__(self): return self
        def __exit__(self, *args): return False
    class Client:
        def get(self, url, **kwargs):
            assert url == feeds.BLS_URL
            assert kwargs["allow_redirects"] is False and kwargs["timeout"] == (5, 12)
            return Response()
    with pytest.raises(feeds.ProviderError, match="provider_http_302"):
        feeds.collect("bls", NOW, session=Client())


def test_routes_require_auth_and_reject_extra_sources_and_naive_asof(db, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(routes, "get_authenticated_user", lambda: None)
    assert request(app, "GET", "/api/market-events") == 401
    assert request(app, "POST", "/api/market-events/refresh", {}) == 401
    monkeypatch.setattr(routes, "get_authenticated_user", lambda: AuthenticatedUser(USER, None, {}))
    assert request(app, "GET", "/api/market-events?source=evil") == 422
    assert request(app, "GET", "/api/market-events?as_of=2026-09-04T10:00:00") == 422
    assert request(app, "POST", "/api/market-events/refresh", {"sources": ["evil"]}) == 422
    assert request(app, "POST", "/api/market-events/refresh", {"url": "http://localhost"}) == 422
    assert request(app, "GET", "/api/market-events") == 200
