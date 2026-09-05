import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.auth import AuthenticatedUser, bind_authenticated_user, reset_authenticated_user
from app.db import Base, get_db
from app.market_data_models import LocalCapture
from app.market_data_routes import router
from app.models import ProjectXMarketCandle
from app.services import market_data_inventory as inventory_module
from app.services.market_data_context import refresh_market_context, stored_market_context
from app.services.market_data_inventory import CaptureIntegrityError, aggregate_complete_minutes, database_streams, import_local_history, load_verified_capture, materialize_capture_timeframes


USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
STAMP = datetime(2026, 7, 10, 20, 20, tzinfo=timezone.utc)
CONTRACT = "CON.F.US.MNQ.U26"


def request(app, method, path, *, body=None, params=None):
    """Exercise the ASGI router without an optional HTTP client dependency."""
    if params:
        path += "?" + urlencode(params)
    url = urlsplit(path)
    payload = json.dumps(body).encode() if body is not None else b""
    events = []
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(event):
        events.append(event)

    scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method=method,
        scheme="http", path=url.path, raw_path=url.path.encode(), query_string=url.query.encode(),
        root_path="", headers=[(b"content-type", b"application/json")],
        client=("127.0.0.1", 1000), server=("testserver", 80))
    asyncio.run(app(scope, receive, send))
    status = next(event["status"] for event in events if event["type"] == "http.response.start")
    raw = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
    return SimpleNamespace(status_code=status, json=lambda: json.loads(raw))


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[ProjectXMarketCandle.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def candle(db, *, user=USER, timestamp=STAMP, live=False, partial=False, close=100, unit="minute", number=1, contract=CONTRACT, source="projectx"):
    row = ProjectXMarketCandle(user_id=user, contract_id=contract, symbol="F.US.MNQ", live=live,
        unit=unit, unit_number=number, candle_timestamp=timestamp, open_price=close,
        high_price=close + 1, low_price=close - 1, close_price=close, volume=10,
        is_partial=partial, source=source, fetched_at=STAMP + timedelta(days=1))
    db.add(row)
    db.commit()
    return row


def fixture_capture(tmp_path, *, invalid_bar=False):
    bars = [dict(t=(STAMP + timedelta(minutes=index)).isoformat(), o=100, h=101, l=99, c=100, v=10) for index in range(2)]
    if invalid_bar:
        bars[1]["h"] = 98
    name = "history.json"
    end = STAMP + timedelta(minutes=2)
    payload = dict(request=dict(contractId=CONTRACT, startTime=STAMP.isoformat(), endTime=end.isoformat(),
        live=False, unit=2, unitNumber=1, includePartialBar=False), response=dict(bars=bars))
    raw = json.dumps(payload).encode()
    (tmp_path / name).write_bytes(raw)
    manifest = dict(contract_id=CONTRACT, coverage=dict(total_rows=2, first_utc=STAMP.isoformat(), last_utc=(end - timedelta(minutes=1)).isoformat()),
        files={name: dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())},
        windows=[dict(file=name, rows=2, start_utc=STAMP.isoformat(), end_exclusive_utc=end.isoformat())],
        finished_at=(end + timedelta(hours=1)).isoformat())
    raw_manifest = json.dumps(manifest).encode()
    (tmp_path / "manifest.json").write_bytes(raw_manifest)
    return LocalCapture("test-capture", tmp_path, hashlib.sha256(raw_manifest).hexdigest(), CONTRACT, "MNQ", False, 2)


def test_inventory_scopes_user_and_preserves_source_mode_timeframe(db):
    candle(db)
    candle(db, timestamp=STAMP + timedelta(minutes=1), partial=True)
    candle(db, live=True)
    candle(db, number=5)
    candle(db, user=OTHER)
    rows = database_streams(db, user_id=USER)
    assert len(rows) == 3
    assert sum(row.rows for row in rows) == 4
    assert sum(row.complete_rows for row in rows) == 3
    assert {row.root_symbol for row in rows} == {"MNQ"}
    assert all(row.first_timestamp.tzinfo is not None for row in rows)


def test_import_is_idempotent_and_does_not_use_other_tenant_rows(db, tmp_path):
    capture = fixture_capture(tmp_path)
    candle(db, user=OTHER)
    first = import_local_history(db, user_id=USER, capture=capture)
    second = import_local_history(db, user_id=USER, capture=capture)
    assert (first.inserted_rows, first.unchanged_rows, first.conflicting_rows) == (2, 0, 0)
    assert (second.inserted_rows, second.unchanged_rows, second.conflicting_rows) == (0, 2, 0)
    assert db.query(ProjectXMarketCandle).count() == 3
    imported = db.query(ProjectXMarketCandle).filter_by(user_id=USER).first()
    assert imported.source == "projectx"
    assert imported.live is False
    assert imported.raw_payload["_topsignal_provenance"]["research_exposure"] == "previously_evaluated"


def test_import_preserves_conflicting_price_and_source(db, tmp_path):
    capture = fixture_capture(tmp_path)
    old = candle(db, close=200, source="different-source")
    outcome = import_local_history(db, user_id=USER, capture=capture)
    db.refresh(old)
    assert (outcome.inserted_rows, outcome.conflicting_rows) == (1, 1)
    assert old.close_price == Decimal("200")
    assert old.source == "different-source"


def test_import_validates_entire_capture_before_any_write(db, tmp_path):
    capture = fixture_capture(tmp_path, invalid_bar=True)
    with pytest.raises(CaptureIntegrityError, match="source_bar_invalid"):
        import_local_history(db, user_id=USER, capture=capture)
    assert db.query(ProjectXMarketCandle).count() == 0


def test_import_rejects_corrupted_manifest_and_file(db, tmp_path):
    capture = fixture_capture(tmp_path)
    path = tmp_path / "history.json"
    path.write_bytes(path.read_bytes().replace(b"100", b"101", 1))
    with pytest.raises(CaptureIntegrityError, match="source_file_hash_mismatch"):
        import_local_history(db, user_id=USER, capture=capture)
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(CaptureIntegrityError, match="source_manifest_hash_mismatch"):
        import_local_history(db, user_id=USER, capture=capture)
    assert db.query(ProjectXMarketCandle).count() == 0


def test_context_uses_only_closed_same_contract_source_mode_and_user(db):
    candle(db, timestamp=STAMP - timedelta(minutes=1), close=99)
    candle(db, timestamp=STAMP, close=100)
    candle(db, timestamp=STAMP + timedelta(minutes=1), close=200)
    candle(db, timestamp=STAMP, number=5, close=300)
    candle(db, timestamp=STAMP, live=True, close=400)
    candle(db, timestamp=STAMP, user=OTHER, close=500)
    context = stored_market_context(db, user_id=USER, as_of=STAMP + timedelta(minutes=1))
    mnq = context.items[0]
    assert mnq.symbol == "MNQ"
    assert mnq.close == 100
    assert mnq.previous_close == 99
    assert mnq.change_pct == pytest.approx((100 / 99 - 1) * 100)
    assert mnq.available_at == STAMP + timedelta(minutes=1)
    assert mnq.change_period_seconds == 60
    assert mnq.status == "fresh"
    assert "NQ" in context.missing_symbols
    assert "VIX" in context.missing_symbols


def test_context_reports_stale_and_refuses_future_asof(db):
    candle(db)
    response = stored_market_context(db, user_id=USER, as_of=STAMP + timedelta(hours=1))
    assert response.items[0].status == "stale"
    with pytest.raises(ValueError, match="as_of_must_not_be_in_the_future"):
        stored_market_context(db, user_id=USER, as_of=datetime.now(timezone.utc) + timedelta(hours=1))


def test_router_auth_fails_closed_without_bound_user(db, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    token = bind_authenticated_user(None)
    try:
        assert request(app, "GET", "/api/market-data/inventory").status_code == 401
        assert request(app, "POST", "/api/market-data/import-local-history").status_code == 401
        assert request(app, "POST", "/api/market-data/refresh", body={}).status_code == 401
    finally:
        reset_authenticated_user(token)


def test_router_uses_bound_user_not_user_query(db, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    candle(db)
    candle(db, user=OTHER, close=500)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    token = bind_authenticated_user(AuthenticatedUser(USER, None, {}))
    try:
        response = request(app, "GET", "/api/market-data/context", params={"user_id": OTHER, "as_of": (STAMP + timedelta(minutes=1)).isoformat()})
        assert response.status_code == 200
        assert response.json()["items"][0]["close"] == 100
        assert request(app, "POST", "/api/market-data/refresh", body={"symbols": ["NVDA"]}).status_code == 422
        assert request(app, "POST", "/api/market-data/refresh", body={"days": 11}).status_code == 422
    finally:
        reset_authenticated_user(token)


def test_bounded_refresh_uses_active_exact_symbol_and_no_orders(db):
    calls = []
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    class ReadOnlyClient:
        def search_contracts(self, *, search_text, live):
            calls.append(("search", search_text, live))
            return [dict(id=CONTRACT, symbol_id="F.US.MNQ", active_contract=True),
                    dict(id="CON.F.US.NQ.U26", symbol_id="F.US.NQ", active_contract=True)]

        def retrieve_bars(self, **kwargs):
            calls.append(("history", kwargs))
            assert kwargs["contract_id"] == CONTRACT
            return [dict(timestamp=now - timedelta(minutes=1), open=100, high=101, low=99, close=100, volume=10, is_partial=False),
                    dict(timestamp=now, open=200, high=201, low=199, close=200, volume=10, is_partial=False)]

    response = refresh_market_context(db, user_id=USER, client=ReadOnlyClient(), symbols=["MNQ"], days=3, live=False)
    assert response.items[0].inserted_rows == 1
    assert response.items[0].received_rows == 1
    assert len(calls) == 2
    assert calls[1][1]["limit"] == 20_000
    assert db.query(ProjectXMarketCandle).filter_by(user_id=USER).count() == 1


def test_archive_inventory_exposes_only_manifest_dates_and_rejects_escape(tmp_path):
    (tmp_path / "current.json").write_text(json.dumps(dict(version_dir="../outside", series={})))
    response = inventory_module.archive_inventory(tmp_path)
    assert response.status == "invalid"
    assert response.series == []


def test_fixed_workspace_capture_hashes_and_all_rows_when_installed():
    if not inventory_module.LOCAL_MNQ_CAPTURE.directory.is_dir():
        pytest.skip("Optional workspace capture is not installed")
    manifest, bars = load_verified_capture()
    assert len(bars) == 55240
    assert bars[0]["timestamp"] == STAMP
    assert bars[-1]["timestamp"] == datetime(2026, 9, 4, 20, 59, tzinfo=timezone.utc)


def test_strict_resampling_skips_missing_minutes_and_session_misalignment():
    bars = [dict(timestamp=STAMP + timedelta(minutes=index), open=Decimal(100 + index), high=Decimal(101 + index),
        low=Decimal(99 + index), close=Decimal(100 + index), volume=Decimal(10), is_partial=False) for index in range(25)]
    complete = aggregate_complete_minutes(bars, root_symbol="MNQ", unit="minute", unit_number=5, closed_by=STAMP + timedelta(hours=1))
    assert len(complete) == 5
    assert complete[0]["open"] == 100
    assert complete[0]["close"] == 104
    assert complete[0]["high"] == 105
    assert complete[0]["volume"] == 50
    missing = aggregate_complete_minutes(bars[:2] + bars[3:], root_symbol="MNQ", unit="minute", unit_number=5, closed_by=STAMP + timedelta(hours=1))
    assert len(missing) == 4
    assert missing[0]["timestamp"] == STAMP + timedelta(minutes=5)
    quarter_hours = aggregate_complete_minutes(bars, root_symbol="MNQ", unit="minute", unit_number=15, closed_by=STAMP + timedelta(hours=1))
    assert len(quarter_hours) == 1
    assert quarter_hours[0]["timestamp"].minute == 30
    assert aggregate_complete_minutes(bars, root_symbol="MNQ", unit="hour", unit_number=1, closed_by=STAMP + timedelta(hours=1)) == []


def test_materialization_skips_aggregate_if_source_minute_conflicts(db, tmp_path):
    capture = fixture_capture(tmp_path)
    bars = [dict(timestamp=STAMP + timedelta(minutes=index), open=Decimal(100), high=Decimal(101),
        low=Decimal(99), close=Decimal(100), volume=Decimal(10), is_partial=False) for index in range(10)]
    candle(db, timestamp=STAMP + timedelta(minutes=1), close=200)
    outcomes = materialize_capture_timeframes(db, user_id=USER, capture=capture, bars=bars,
        fetched_at=STAMP + timedelta(hours=1), provenance={"capture_id": "test"})
    db.commit()
    assert outcomes[0].conflicting_rows == 1
    assert outcomes[1].timeframe == "5m"
    assert outcomes[1].inserted_rows == 1
    derived = db.query(ProjectXMarketCandle).filter_by(unit="minute", unit_number=5).one()
    assert derived.candle_timestamp.replace(tzinfo=timezone.utc) == STAMP + timedelta(minutes=5)
    assert derived.source == "projectx"
    assert derived.raw_payload["_topsignal_provenance"]["derived_from"] == "verified_1m"


def test_recent_gap_check_is_bounded_and_scoped(db):
    candle(db)
    candle(db, timestamp=STAMP + timedelta(minutes=2))
    candle(db, timestamp=STAMP + timedelta(minutes=3), partial=True)
    candle(db, user=OTHER, timestamp=STAMP + timedelta(minutes=1))
    stream = database_streams(db, user_id=USER, include_recent_gaps=True)[0]
    assert stream.recent_gap_check.expected_open_minutes == 4
    assert stream.recent_gap_check.observed_open_minutes == 2
    assert stream.recent_gap_check.missing_open_minutes == 2


@pytest.mark.parametrize("symbol,native,micro", [("NQ", "ENQ", "MNQ"), ("ES", "EP", "MES")])
def test_refresh_resolves_native_emini_aliases_without_micro_collisions(db, symbol, native, micro):
    actual_contract = f"CON.F.US.{native}.U26"
    observed = []

    class Client:
        def search_contracts(self, **kwargs):
            return [dict(id=f"CON.F.US.{micro}.U26", symbol_id=f"F.US.{micro}", active_contract=True),
                    dict(id=actual_contract, symbol_id=f"F.US.{native}", active_contract=True)]

        def retrieve_bars(self, **kwargs):
            observed.append(kwargs["contract_id"])
            return [dict(timestamp=kwargs["end"]-timedelta(minutes=1), open=100, high=101, low=99, close=100, volume=10, is_partial=False)]

    response = refresh_market_context(db, user_id=USER, client=Client(), symbols=[symbol], days=1, live=False)
    assert response.items[0].status == "updated"
    assert observed == [actual_contract]
    stream = database_streams(db, user_id=USER)[0]
    assert stream.root_symbol == symbol
    assert stream.contract_id == actual_contract
    assert stream.symbol == f"F.US.{native}"
    context = stored_market_context(db, user_id=USER)
    assert next(item for item in context.items if item.symbol == symbol).contract_id == actual_contract
