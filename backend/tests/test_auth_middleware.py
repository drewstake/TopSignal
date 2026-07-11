import asyncio
import os

import jwt
import pytest
from fastapi import Response
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.auth import AuthError, auth_required, authenticate_request_token, extract_access_token
from app.main import _validate_runtime_security_configuration, api_auth_middleware


def _build_request(
    *,
    method: str,
    path: str = "/api/accounts",
    origin: str | None = None,
    query_string: bytes = b"",
    request_id: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if origin:
        headers.append((b"origin", origin.encode("latin1")))
    if request_id:
        headers.append((b"x-request-id", request_id.encode("ascii")))

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


def test_auth_defaults_to_required_when_configuration_is_missing(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    assert auth_required() is True


def test_anonymous_mode_requires_an_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")

    assert auth_required() is False


def test_cloud_runtime_rejects_disabled_auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setattr("app.main.resolve_database_host_mode", lambda: ("db.example.com", "cloud"))
    monkeypatch.setattr("app.main.resolve_supabase_mode", lambda: "cloud")

    with pytest.raises(RuntimeError, match="AUTH_REQUIRED=false"):
        _validate_runtime_security_configuration()


def test_cloud_runtime_rejects_shared_provider_credentials(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", "true")
    monkeypatch.setattr("app.main.resolve_database_host_mode", lambda: ("db.example.com", "cloud"))
    monkeypatch.setattr("app.main.resolve_supabase_mode", lambda: "cloud")

    with pytest.raises(RuntimeError, match="ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS=true"):
        _validate_runtime_security_configuration()


def test_get_without_token_still_requires_auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET")

    async def call_next(_: Request):
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 401
    assert response.body == b'{"detail":"missing_bearer_token"}'
    assert response.headers["x-request-id"]


def test_request_id_is_preserved_for_safe_client_value(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET", request_id="client-request-123")

    async def call_next(_: Request):
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert request.state.request_id == "client-request-123"
    assert response.headers["x-request-id"] == "client-request-123"


def test_unsafe_request_id_is_replaced(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET", request_id="x" * 129)

    async def call_next(_: Request):
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.headers["x-request-id"] != "x" * 129
    assert len(response.headers["x-request-id"]) == 36


def test_control_characters_in_request_id_are_replaced(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET")
    request.scope["headers"].append((b"x-request-id", b"unsafe\nvalue"))

    async def call_next(_: Request):
        return Response(status_code=204)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.headers["x-request-id"] != "unsafe\nvalue"
    assert len(response.headers["x-request-id"]) == 36


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


def test_authenticate_request_token_normalizes_uuid_subject(monkeypatch):
    issuer = "https://project-ref.supabase.co/auth/v1"
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_ISSUER", issuer)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "iss": issuer,
            "email": " user@example.com ",
        },
        "test-secret",
        algorithm="HS256",
    )

    user = authenticate_request_token(token)

    assert user.user_id == "11111111-1111-1111-1111-111111111111"
    assert user.email == "user@example.com"


def test_authenticate_request_token_rejects_non_uuid_subject(monkeypatch):
    issuer = "https://project-ref.supabase.co/auth/v1"
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_ISSUER", issuer)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "iss": issuer,
        },
        "test-secret",
        algorithm="HS256",
    )

    with pytest.raises(AuthError, match="invalid_token_subject"):
        authenticate_request_token(token)
