"""Adversarial HTTP regressions with disposable databases and signing keys."""
from datetime import datetime, timezone
import asyncio

import json
from urllib.parse import urlencode
from types import SimpleNamespace
import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main
from app.db import Base, get_db
from app.services.projectx_metrics import TradeMetricSample, compute_trade_summary

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
SECRET = "audit-fixture-signing-key-never-use-in-production"


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.delenv("SUPABASE_JWT_ISSUER", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def session():
        with factory() as db:
            yield db

    main.app.dependency_overrides[get_db] = session
    class Client:
        def __getattr__(self, method):
            def request(path, **kwargs):
                async def run():
                    body = json.dumps(kwargs["json"]).encode() if "json" in kwargs else b""
                    request_headers = {"content-type": "application/json", **kwargs.get("headers", {})}
                    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                             "http_version": "1.1", "method": method.upper(), "scheme": "http",
                             "path": path, "raw_path": path.encode(),
                             "query_string": urlencode(kwargs.get("params", {})).encode(),
                             "headers": [(k.lower().encode(), v.encode()) for k, v in request_headers.items()],
                             "client": ("127.0.0.1", 12345), "server": ("audit.test", 80)}
                    messages = []
                    delivered = False
                    async def receive():
                        nonlocal delivered
                        if not delivered:
                            delivered = True
                            return {"type": "http.request", "body": body, "more_body": False}
                        await asyncio.Event().wait()
                    async def send(message):
                        messages.append(message)
                    try:
                        await main.app(scope, receive, send)
                    except Exception:
                        if not any(m.get("status") == 500 for m in messages):
                            raise
                    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
                    data = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
                    return SimpleNamespace(status_code=status, text=data.decode(), json=lambda: json.loads(data))
                return asyncio.run(run())
            return request
    client = Client()
    client.session_factory = factory
    yield client
    main.app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def headers(user=USER_A, **claims):
    return {"Authorization": "Bearer " + jwt.encode(
        {"sub": user, "iss": "http://127.0.0.1:54321/auth/v1", "exp": 4102444800, **claims}, SECRET, algorithm="HS256")}


def expense(**changes):
    return {"expense_date": "2026-09-22", "amount_cents": 10000,
            "category": "other", **changes}


def test_expired_and_missing_auth_rejected(api):
    assert api.get("/api/expenses").status_code == 401
    assert api.get("/api/expenses", headers=headers(exp=1)).status_code == 401
    assert api.get("/api/expenses", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_cross_tenant_expense_read_edit_delete_blocked(api):
    created = api.post("/api/expenses", json=expense(), headers=headers())
    assert created.status_code == 201
    row_id = created.json()["id"]
    other = headers(USER_B)
    assert api.get("/api/expenses", headers=other).json()["items"] == []
    assert api.patch(f"/api/expenses/{row_id}", json={"amount_cents": 1}, headers=other).status_code == 404
    assert api.delete(f"/api/expenses/{row_id}", headers=other).status_code == 404
    assert api.get("/api/expenses", headers=headers()).json()["items"][0]["amount_cents"] == 10000


@pytest.mark.parametrize("changes", [
    {"amount_cents": 2**31}, {"amount_cents": -1},
    {"amount": "NaN"}, {"expense_date": "2026-02-30"}, {"category": "invalid"},
])
def test_invalid_expenses_rejected(api, changes):
    assert api.post("/api/expenses", json=expense(**changes), headers=headers()).status_code in (400, 422)


@pytest.mark.parametrize("path,params", [
    ("/api/expenses", {"limit": 501}),
    ("/api/expenses", {"offset": -1}),
    ("/api/expenses", {"start_date": "2026-09-22", "end_date": "2026-09-21"}),
])
def test_normal_query_boundaries_rejected(api, path, params):
    assert api.get(path, params=params, headers=headers()).status_code in (400, 422)


@pytest.mark.parametrize("params", [{"offset": 2**100}, {"account_id": 2**100}])
def test_oversized_query_integer_is_client_error(api, params):
    assert api.get("/api/expenses", params=params, headers=headers()).status_code in (400, 422)


def test_mixed_timezone_filters_do_not_500(api):
    response = api.get("/api/expenses/totals", params={
        "range": "all_time", "start_created_at": "2026-01-01T00:00:00",
        "end_created_at": "2026-01-02T00:00:00Z"}, headers=headers())
    assert response.status_code in (200, 400, 422)


def test_minimum_date_does_not_500(api):
    response = api.get("/api/expenses/financial-summary", params={"as_of_date": "0001-01-01"}, headers=headers())
    assert response.status_code in (200, 400, 422)


@pytest.mark.parametrize("path,payload", [
    ("/api/expenses", expense(currency="EUR")),
    ("/api/payouts", {"payout_date": "2026-09-22", "amount_cents": 10000, "currency": "EUR"}),
])
def test_usd_only_dashboard_rejects_unsupported_currency(api, path, payload):
    assert api.post(path, json=payload, headers=headers()).status_code in (400, 422)


@pytest.mark.parametrize("model,path", [
    ("expense", "/api/expenses/totals"),
    ("expense", "/api/expenses/financial-summary"),
    ("payout", "/api/payouts/totals"),
    ("payout", "/api/expenses/financial-summary"),
])
def test_legacy_non_usd_records_block_totals_without_modification(api, model, path):
    from app.models import Expense, Payout
    with api.session_factory() as db:
        row = (Expense(user_id=USER_A, expense_date=datetime(2026, 9, 22).date(),
                       amount_cents=10000, category="other", currency="EUR", provider="topstep")
               if model == "expense" else
               Payout(user_id=USER_A, payout_date=datetime(2026, 9, 22).date(),
                      amount_cents=10000, currency="EUR"))
        db.add(row)
        db.commit()
    params = {"range": "all_time"} if path.endswith("expenses/totals") else {}
    response = api.get(path, params=params, headers=headers())
    assert response.status_code == 409
    assert "non-USD" in response.text
    # An unrelated tenant's valid totals must remain available.
    assert api.get(path, params=params, headers=headers(USER_B)).status_code == 200
    with api.session_factory() as db:
        cls = Expense if model == "expense" else Payout
        assert db.query(cls).one().currency == "EUR"


def test_winning_only_profit_factor_is_not_zero():
    summary = compute_trade_summary([
        TradeMetricSample(timestamp=datetime(2026, 7, 2, 14, tzinfo=timezone.utc), pnl=202.50, fees=3.72)
    ])
    assert summary["profit_factor"] is None
    assert summary["win_count"] == 1 and summary["loss_count"] == 0

@pytest.mark.parametrize("params", [
    {"start_created_at": "2026-01-01T01:00:00+01:00", "end_created_at": "2026-01-01T00:00:00Z"},
    {"start_created_at": "2026-11-01T01:30:00-04:00", "end_created_at": "2026-11-01T01:30:00-05:00"},
    {"start_created_at": "2026-01-01T00:00:00Z", "end_created_at": "2026-01-02T00:00:00"},
])
def test_timezone_filters_accept_equal_instants_dst_and_naive_utc(api, params):
    response = api.get("/api/expenses/totals", params={"range": "all_time", **params}, headers=headers())
    assert response.status_code == 200


def test_timezone_filters_compare_instants_not_wall_clock(api):
    response = api.get("/api/expenses/totals", params={"range": "all_time",
        "start_created_at": "2026-01-01T01:00:00Z", "end_created_at": "2026-01-01T01:30:00+02:00"}, headers=headers())
    assert response.status_code == 400


@pytest.mark.parametrize("path,params", [
    ("/api/payouts", {"offset": 2**100}),
    ("/api/accounts/1/summary", {"start": "0001-01-01T00:00:00Z"}),
    ("/api/accounts/1/pnl-calendar", {"end": "9999-12-31T00:00:00Z"}),
    ("/api/expenses/financial-summary", {"as_of_date": "9999-12-31"}),
])
def test_related_route_bounds_fail_before_database_or_date_arithmetic(api, path, params):
    assert api.get(path, params=params, headers=headers()).status_code in (400, 422)


@pytest.mark.parametrize("day", ["0002-01-01", "9998-12-31", "2024-02-29"])
def test_supported_financial_date_boundaries(api, day):
    assert api.get("/api/expenses/financial-summary", params={"as_of_date": day}, headers=headers()).status_code == 200


@pytest.mark.parametrize("pnls,expected,wins,losses", [
    ([], None, 0, 0), ([0], None, 0, 0), ([10], None, 1, 0), ([-10], 0, 0, 1), ([20, -10], 2, 1, 1),
])
def test_profit_factor_states_are_json_safe(pnls, expected, wins, losses):
    summary = compute_trade_summary([TradeMetricSample(timestamp=datetime(2026, 7, 2, tzinfo=timezone.utc), pnl=pnl, fees=0, commissions=0) for pnl in pnls])
    assert summary["profit_factor"] == expected
    assert (summary["win_count"], summary["loss_count"]) == (wins, losses)
    json.dumps(summary, allow_nan=False)


def test_no_gross_losses_is_explicit_even_when_fees_make_a_net_loss():
    summary = compute_trade_summary([TradeMetricSample(
        timestamp=datetime(2026, 7, 2, tzinfo=timezone.utc), pnl=1, fees=2, commissions=0)])
    assert summary["profit_factor"] is None
    assert summary["profit_factor_no_losses"] is True
    assert summary["loss_count"] == 1
    assert summary["net_pnl"] == -1


def test_legacy_summary_serializes_undefined_profit_factor_as_null(api):
    response = api.get("/metrics/summary", headers=headers())
    assert response.status_code == 200
    assert response.json()["profit_factor"] is None
