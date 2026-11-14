import asyncio
import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event, Lock, current_thread

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Response
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.auth as auth_module
import app.main as main_module
from app.auth import (
    AuthError,
    AuthUnavailable,
    AuthenticatedUser,
    auth_required,
    authenticate_request_token,
    extract_access_token,
)
from app.main import _validate_runtime_security_configuration, api_auth_middleware


_TEST_HS256_SECRET = "topsignal-test-hs256-secret-32-bytes-minimum"
_TEST_HS512_SECRET = "topsignal-test-hs512-secret-with-at-least-sixty-four-bytes-for-rfc-7518"




def _build_request(
    *,
    method: str,
    path: str = "/api/accounts",
    origin: str | None = None,
    query_string: bytes = b"",
    request_id: str | None = None,
    access_token: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if origin:
        headers.append((b"origin", origin.encode("latin1")))
    if request_id:
        headers.append((b"x-request-id", request_id.encode("ascii")))
    if access_token:
        headers.append((b"authorization", f"Bearer {access_token}".encode("ascii")))

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


@pytest.mark.parametrize("detail", ["local_auth_unavailable", "auth_verification_busy"])
def test_local_verification_capacity_failures_return_503_with_retry_after(
    monkeypatch,
    detail,
):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET", access_token="temporarily-unverifiable")

    def unavailable(_token):
        raise AuthUnavailable(detail)

    async def call_next(_: Request):
        pytest.fail("unverified request must not reach the route")

    monkeypatch.setattr("app.main.authenticate_request_token", unavailable)
    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert json.loads(response.body)["detail"] == detail


def test_auth_defaults_to_required_when_configuration_is_missing(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)

    assert auth_required() is True


def test_anonymous_mode_requires_an_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")

    assert auth_required() is False


def test_cloud_runtime_rejects_disabled_auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setattr("app.main.resolve_database_host_mode", lambda: ("db.example.com", "cloud"))

    with pytest.raises(RuntimeError, match="AUTH_REQUIRED=false"):
        _validate_runtime_security_configuration()


def test_production_runtime_rejects_disabled_auth_even_when_local(monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_ENV", "production")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setattr("app.main.resolve_database_host_mode", lambda: ("127.0.0.1", "local"))

    with pytest.raises(RuntimeError, match="AUTH_REQUIRED=true"):
        _validate_runtime_security_configuration()


@pytest.mark.parametrize("name,value,reason", [
    ("ALLOW_QUERY_BEARER_TOKENS", "true", "ALLOW_QUERY_BEARER_TOKENS"),
    ("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", "typo", "ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS"),
    ("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY", "true", "ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY"),
    ("DATABASE_URL", "sqlite://", "DATABASE_URL"),
    ("CREDENTIALS_ENCRYPTION_KEY", "invalid-secret", "CREDENTIALS_ENCRYPTION_KEY"),
    ("TOPSIGNAL_DB_SCHEMA_INIT", "full", "TOPSIGNAL_DB_SCHEMA_INIT"),
])
def test_production_rejects_unsafe_configuration_before_network(monkeypatch, tmp_path, name, value, reason):
    from app.local_auth import provision
    auth_path = tmp_path / "auth.sqlite3"
    provision(auth_path, "11111111-1111-1111-1111-111111111111", None)
    monkeypatch.setenv("TOPSIGNAL_LOCAL_AUTH_DB", str(auth_path))
    import base64

    configured = {
        "TOPSIGNAL_ENV": "production", "AUTH_REQUIRED": "true",
        "DATABASE_URL": "postgresql+psycopg://test:test@localhost/test",
        "CREDENTIALS_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"0" * 32).decode(),
        "SUPABASE_URL": "https://example.invalid", "SUPABASE_JWT_AUDIENCE": "authenticated",
        "TOPSIGNAL_DB_SCHEMA_INIT": "skip", "ALLOW_QUERY_BEARER_TOKENS": "false",
        "ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS": "false", "ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY": "false",
    }
    for key, item in configured.items():
        monkeypatch.setenv(key, item)
    _validate_runtime_security_configuration()
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=reason) as raised:
        _validate_runtime_security_configuration()
    assert "invalid-secret" not in str(raised.value)


def test_cloud_runtime_rejects_shared_provider_credentials(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", "true")
    monkeypatch.setattr("app.main.resolve_database_host_mode", lambda: ("db.example.com", "cloud"))

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










































def test_auth_middleware_offloads_token_verification(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET", access_token="valid-token")
    observed = {"thread_name": "", "called": False}

    def fake_authenticate(token):
        assert token == "valid-token"
        observed["thread_name"] = current_thread().name
        return AuthenticatedUser(
            user_id="11111111-1111-1111-1111-111111111111",
            email=None,
            claims={},
        )

    async def call_next(_: Request):
        observed["called"] = True
        return Response(status_code=204)

    monkeypatch.setattr("app.main.authenticate_request_token", fake_authenticate)

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 204
    assert observed["called"] is True
    assert observed["thread_name"].startswith("topsignal-auth")


def test_auth_executor_admission_overflow_returns_503(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = _build_request(method="GET", access_token="queued-token")

    class FullAdmissionGate:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

    async def call_next(_: Request):
        pytest.fail("unverified request must not reach the route")

    monkeypatch.setattr("app.main._AUTH_VERIFICATION_SLOTS", FullAdmissionGate())
    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert json.loads(response.body)["detail"] == "auth_verification_busy"


def test_cancelled_auth_request_holds_admission_until_verifier_thread_exits(monkeypatch):
    verifier_started = Event()
    release_verifier = Event()
    slot_released = Event()

    class SingleTrackingSlot:
        def __init__(self):
            self._lock = Lock()
            self._occupied = False
            self.release_count = 0

        def acquire(self, *, blocking):
            assert blocking is False
            with self._lock:
                if self._occupied:
                    return False
                self._occupied = True
                return True

        def release(self):
            with self._lock:
                assert self._occupied is True
                self._occupied = False
                self.release_count += 1
            slot_released.set()

    slots = SingleTrackingSlot()

    def blocking_authenticate(token):
        verifier_started.set()
        assert release_verifier.wait(timeout=5)
        return AuthenticatedUser(
            user_id="11111111-1111-1111-1111-111111111111",
            email=None,
            claims={"token": token},
        )

    monkeypatch.setattr(main_module, "_AUTH_VERIFICATION_SLOTS", slots)
    monkeypatch.setattr(main_module, "authenticate_request_token", blocking_authenticate)

    async def wait_for_thread_event(event: Event) -> None:
        for _ in range(2_000):
            if event.is_set():
                return
            await asyncio.sleep(0.001)
        pytest.fail("timed out waiting for verifier thread")

    async def scenario():
        task = asyncio.create_task(main_module._authenticate_token_off_event_loop("first"))
        await wait_for_thread_event(verifier_started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert slots.release_count == 0
        with pytest.raises(AuthUnavailable, match="auth_verification_busy"):
            await main_module._authenticate_token_off_event_loop("overflow")

        release_verifier.set()
        await wait_for_thread_event(slot_released)
        result = await main_module._authenticate_token_off_event_loop("after-completion")
        return result

    result = asyncio.run(scenario())

    assert result.claims["token"] == "after-completion"
    assert slots.release_count == 2
