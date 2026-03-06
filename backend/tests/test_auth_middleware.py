import asyncio
import os

from fastapi import Response
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.auth import extract_access_token
from app.main import api_auth_middleware


def _build_request(
    *,
    method: str,
    path: str = "/api/accounts",
    origin: str | None = None,
    query_string: bytes = b"",
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if origin:
        headers.append((b"origin", origin.encode("latin1")))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_options_preflight_bypasses_auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="OPTIONS")
    observed = {"called": False}

    async def call_next(_: Request):
        observed["called"] = True
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert observed["called"] is True
    assert response.status_code == 204


def test_get_without_token_still_requires_auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET")

    async def call_next(_: Request):
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 401
    assert response.body == b'{"detail":"missing_bearer_token"}'


def test_missing_token_response_includes_cors_for_allowed_origin(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET", origin="http://localhost:5173")

    async def call_next(_: Request):
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_metrics_routes_require_auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET", path="/metrics/summary")

    async def call_next(_: Request):
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 401
    assert response.body == b'{"detail":"missing_bearer_token"}'


def test_query_string_bearer_tokens_are_rejected_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_QUERY_BEARER_TOKENS", raising=False)
    request = _build_request(method="GET", query_string=b"access_token=query-token")

    assert extract_access_token(request) is None


def test_query_string_bearer_tokens_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("ALLOW_QUERY_BEARER_TOKENS", "true")
    request = _build_request(method="GET", query_string=b"access_token=query-token")

    assert extract_access_token(request) == "query-token"
