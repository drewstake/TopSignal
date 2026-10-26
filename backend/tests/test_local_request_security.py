import pytest
from starlette.requests import Request

from app.local_request_security import anonymous_request_error
from app.models import Account
from test_audit_regressions import api, USER_A, headers


@pytest.fixture
def local_api(api, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DEFAULT_USER_ID", USER_A)
    monkeypatch.setattr("app.main._ALLOWED_ORIGINS", ["http://127.0.0.1:5174"])
    monkeypatch.setattr("app.main._ALLOW_ORIGIN_PATTERN", None)
    with api.session_factory() as db:
        db.add(Account(user_id=USER_A, provider="projectx", external_id="91001",
                       name="Local fixture", is_main=False, account_state="ACTIVE", is_visible=True))
        db.commit()
    return api


@pytest.mark.parametrize("extra", [
    {"origin": "http://untrusted.invalid", "sec-fetch-site": "cross-site"},
    {"origin": "null"}, {"origin": ""},
    {"origin": "http://127.0.0.1:9999"},
    {"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"},
    {"host": "rebound.invalid:8000", "sec-fetch-site": "same-origin"},
    {"host": "127.0.0.1.attacker.invalid"}, {"host": "127.0.0.1@attacker.invalid"},
])
def test_simple_foreign_posts_cannot_mutate_local_accounts(local_api, extra):
    response = local_api.post("/api/accounts/91001/main", headers={
        "host": "127.0.0.1:8000", "content-type": "application/x-www-form-urlencoded", **extra})
    assert response.status_code == 403
    with local_api.session_factory() as db:
        assert not db.query(Account).filter_by(external_id="91001").one().is_main


@pytest.mark.parametrize("request_headers", [
    {"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:5174", "sec-fetch-site": "same-site"},
    {"host": "localhost:8000"},  # Explicit supported no-Origin CLI contract.
    {"host": "[::1]:8000"},
])
def test_allowed_local_ui_and_cli_can_mutate_disposable_account(local_api, request_headers):
    response = local_api.post("/api/accounts/91001/main", headers=request_headers)
    assert response.status_code == 200
    with local_api.session_factory() as db:
        assert db.query(Account).filter_by(external_id="91001").one().is_main


def test_provider_refresh_get_is_not_a_cross_origin_escape(local_api):
    response = local_api.get("/api/accounts", params={"refresh_provider": "true"}, headers={
        "host": "127.0.0.1:8000", "origin": "http://untrusted.invalid"})
    assert response.status_code == 403  # No provider or credential lookup is reached.


def test_authenticated_mode_keeps_bearer_requirement(api):
    assert api.get("/api/expenses", headers={"host": "app.example.test"}).status_code == 401
    assert api.get("/api/expenses", headers={"host": "app.example.test", **headers()}).status_code == 200


@pytest.mark.parametrize("host_values,peer", [
    ([], "127.0.0.1"), (["localhost", "localhost"], "127.0.0.1"),
    (["127.0.0.1:8000"], "192.0.2.1"),
    (["127.0.0.1/path"], "127.0.0.1"), (["localhost:99999"], "127.0.0.1"),
])
def test_ambiguous_hosts_and_remote_peers_are_rejected(host_values, peer):
    request = Request({"type": "http", "headers": [(b"host", host.encode()) for host in host_values],
                       "client": (peer, 1)})
    assert anonymous_request_error(request, lambda _: True) is not None
