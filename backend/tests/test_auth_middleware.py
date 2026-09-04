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


@pytest.fixture(autouse=True)
def clear_jwks_process_caches():
    cache_names = (
        "_JWKS_CLIENT_CACHE",
        "_JWKS_REFRESH_FLIGHTS",
        "_JWKS_REFRESH_WAITER_SLOTS",
        "_UNKNOWN_KID_CACHE",
        "_KNOWN_KID_CACHE",
        "_UNKNOWN_KID_REFRESH_BACKOFF",
        "_JWKS_TRANSPORT_BACKOFF",
    )
    for cache_name in cache_names:
        getattr(auth_module, cache_name).clear()
    yield
    for cache_name in cache_names:
        getattr(auth_module, cache_name).clear()


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


@pytest.mark.parametrize("detail", ["jwks_refresh_busy", "jwks_refresh_timeout"])
def test_jwks_refresh_capacity_failures_return_503_with_retry_after(
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


def test_production_runtime_rejects_disabled_auth_even_when_local(monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_ENV", "production")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setattr("app.main.resolve_database_host_mode", lambda: ("127.0.0.1", "local"))
    monkeypatch.setattr("app.main.resolve_supabase_mode", lambda: "local")

    with pytest.raises(RuntimeError, match="AUTH_REQUIRED=true"):
        _validate_runtime_security_configuration()


@pytest.mark.parametrize("name,value,reason", [
    ("ALLOW_QUERY_BEARER_TOKENS", "true", "ALLOW_QUERY_BEARER_TOKENS"),
    ("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", "typo", "ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS"),
    ("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY", "true", "ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY"),
    ("DATABASE_URL", "sqlite://", "DATABASE_URL"),
    ("CREDENTIALS_ENCRYPTION_KEY", "invalid-secret", "CREDENTIALS_ENCRYPTION_KEY"),
    ("SUPABASE_URL", "http://remote.invalid", "SUPABASE_URL"),
    ("SUPABASE_JWT_AUDIENCE", "", "SUPABASE_JWT_AUDIENCE"),
    ("TOPSIGNAL_DB_SCHEMA_INIT", "full", "TOPSIGNAL_DB_SCHEMA_INIT"),
])
def test_production_rejects_unsafe_configuration_before_network(monkeypatch, name, value, reason):
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
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _TEST_HS256_SECRET)
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "iss": issuer,
            "email": " user@example.com ",
        },
        _TEST_HS256_SECRET,
        algorithm="HS256",
    )

    user = authenticate_request_token(token)

    assert user.user_id == "11111111-1111-1111-1111-111111111111"
    assert user.email == "user@example.com"


def test_authenticate_request_token_rejects_non_uuid_subject(monkeypatch):
    issuer = "https://project-ref.supabase.co/auth/v1"
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_ISSUER", issuer)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _TEST_HS256_SECRET)
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "iss": issuer,
        },
        _TEST_HS256_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AuthError, match="invalid_token_subject"):
        authenticate_request_token(token)


def test_authenticate_request_token_rejects_unreviewed_algorithm_before_key_lookup(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    token = jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111"},
        _TEST_HS512_SECRET,
        algorithm="HS512",
    )
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: pytest.fail("JWKS must not be queried"))

    with pytest.raises(AuthError, match="invalid_token_algorithm"):
        authenticate_request_token(token)


def test_jwks_lookup_failure_is_401_safe_and_unknown_kid_is_negative_cached(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()
    calls = 0

    class MissingKeyClient:
        def get_signing_key(self, _key_id):
            nonlocal calls
            calls += 1
            raise jwt.PyJWKClientError("Unable to find a signing key that matches: attacker-kid")

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: MissingKeyClient())

    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    token = (
        f"{encode({'alg': 'RS256', 'kid': 'attacker-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )

    with pytest.raises(AuthError, match="invalid_token_key_id"):
        authenticate_request_token(token)
    with pytest.raises(AuthError, match="invalid_token_key_id"):
        authenticate_request_token(token)

    assert calls == 1


def test_unknown_kid_churn_is_globally_throttled_without_blocking_known_keys(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()
    calls = 0

    class MissingKeyClient:
        def get_signing_key(self, _key_id):
            nonlocal calls
            calls += 1
            raise jwt.PyJWKClientError("unknown kid")

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: MissingKeyClient())

    def token_for(key_id: str) -> str:
        def encode(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

        return (
            f"{encode({'alg': 'RS256', 'kid': key_id})}."
            f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
        )

    with pytest.raises(AuthError, match="invalid_token_key_id"):
        authenticate_request_token(token_for("random-kid-1"))
    with pytest.raises(AuthUnavailable, match="jwks_refresh_backoff"):
        authenticate_request_token(token_for("random-kid-2"))

    # A successfully seen key remains eligible through the backoff, so normal
    # sessions continue while random unknown-kid churn is throttled.
    auth_module._cache_known_kid(
        auth_module._supabase_jwks_url(),
        "known-kid",
    )
    with pytest.raises(AuthError, match="invalid_token_key_id"):
        authenticate_request_token(token_for("known-kid"))

    assert calls == 2


def test_expired_known_kid_hint_rejoins_singleflight_and_negative_cache(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()
    clock = [1_000.0]
    monkeypatch.setattr(auth_module, "monotonic", lambda: clock[0])
    calls = 0

    class RetiredKeyClient:
        def get_signing_key(self, _key_id):
            nonlocal calls
            calls += 1
            raise jwt.PyJWKClientError("retired kid")

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: RetiredKeyClient())
    jwks_url = auth_module._supabase_jwks_url()
    auth_module._cache_known_kid(jwks_url, "retired-kid")
    clock[0] += auth_module._KNOWN_KID_TTL_SECONDS + 1

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'retired-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )

    for _ in range(3):
        with pytest.raises(AuthError, match="invalid_token_key_id"):
            authenticate_request_token(token)

    assert calls == 1
    assert (jwks_url, "retired-kid") not in auth_module._KNOWN_KID_CACHE
    assert (jwks_url, "retired-kid") in auth_module._UNKNOWN_KID_CACHE


@pytest.mark.parametrize("seed_expired_known_hint", [False, True])
def test_concurrent_valid_tokens_share_one_refresh_on_cold_or_expired_cache(
    monkeypatch,
    seed_expired_known_hint,
):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    if seed_expired_known_hint:
        auth_module._cache_known_kid(jwks_url, "active-kid")
        auth_module._KNOWN_KID_CACHE[(jwks_url, "active-kid")] = auth_module.monotonic() - 1
    provider_started = Event()
    release_provider = Event()
    all_waiters_joined = Event()
    call_lock = Lock()
    lookup_calls = 0
    provider_refreshes = 0

    class SigningKey:
        key = object()

    class RefreshingClient:
        def get_signing_key(self, _key_id):
            nonlocal lookup_calls, provider_refreshes
            with call_lock:
                lookup_calls += 1
                call_number = lookup_calls
            if call_number == 1:
                provider_refreshes += 1
                provider_started.set()
                assert release_provider.wait(timeout=2)
            return SigningKey()

    class ObservableWaiterSlots:
        def __init__(self):
            self._slots = BoundedSemaphore(64)
            self._lock = Lock()
            self._joined = 0

        def acquire(self, *, blocking):
            assert blocking is False
            acquired = self._slots.acquire(blocking=False)
            if acquired:
                with self._lock:
                    self._joined += 1
                    if self._joined == 19:
                        all_waiters_joined.set()
            return acquired

        def release(self):
            self._slots.release()

    client = RefreshingClient()
    waiter_slots = ObservableWaiterSlots()
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: client)
    monkeypatch.setattr(
        auth_module,
        "_jwks_refresh_waiter_slots",
        lambda _url: waiter_slots,
    )
    monkeypatch.setattr(
        auth_module.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "11111111-1111-1111-1111-111111111111",
        },
    )

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'active-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )

    with ThreadPoolExecutor(max_workers=20) as executor:
        first = executor.submit(authenticate_request_token, token)
        assert provider_started.wait(timeout=2)
        waiters = [
            executor.submit(authenticate_request_token, token)
            for _ in range(19)
        ]
        assert all_waiters_joined.wait(timeout=2)
        release_provider.set()
        users = [first.result(timeout=2)] + [future.result(timeout=2) for future in waiters]

    assert {user.user_id for user in users} == {
        "11111111-1111-1111-1111-111111111111",
    }
    assert len(users) == 20
    assert lookup_calls == 20
    assert provider_refreshes == 1


def test_concurrent_retired_kid_burst_shares_one_failed_refresh(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    auth_module._cache_known_kid(jwks_url, "retired-kid")
    auth_module._KNOWN_KID_CACHE[(jwks_url, "retired-kid")] = auth_module.monotonic() - 1
    provider_started = Event()
    release_provider = Event()
    all_waiters_joined = Event()
    calls = 0

    class RetiredKeyClient:
        def get_signing_key(self, _key_id):
            nonlocal calls
            calls += 1
            provider_started.set()
            assert release_provider.wait(timeout=2)
            raise jwt.PyJWKClientError("retired kid")

    class ObservableWaiterSlots:
        def __init__(self):
            self._slots = BoundedSemaphore(64)
            self._lock = Lock()
            self._joined = 0

        def acquire(self, *, blocking):
            assert blocking is False
            acquired = self._slots.acquire(blocking=False)
            if acquired:
                with self._lock:
                    self._joined += 1
                    if self._joined == 19:
                        all_waiters_joined.set()
            return acquired

        def release(self):
            self._slots.release()

    client = RetiredKeyClient()
    waiter_slots = ObservableWaiterSlots()
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: client)
    monkeypatch.setattr(
        auth_module,
        "_jwks_refresh_waiter_slots",
        lambda _url: waiter_slots,
    )

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'retired-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )

    with ThreadPoolExecutor(max_workers=20) as executor:
        first = executor.submit(authenticate_request_token, token)
        assert provider_started.wait(timeout=2)
        waiters = [
            executor.submit(authenticate_request_token, token)
            for _ in range(19)
        ]
        assert all_waiters_joined.wait(timeout=2)
        release_provider.set()
        futures = [first, *waiters]
        for future in futures:
            with pytest.raises(AuthError, match="invalid_token_key_id"):
                future.result(timeout=2)

    assert calls == 1
    assert (jwks_url, "retired-kid") in auth_module._UNKNOWN_KID_CACHE


def test_jwks_transport_failure_is_replayed_to_joined_waiter(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    provider_started = Event()
    release_provider = Event()
    waiter_joined = Event()
    calls = 0

    class OfflineClient:
        def get_signing_key(self, _key_id):
            nonlocal calls
            calls += 1
            provider_started.set()
            assert release_provider.wait(timeout=2)
            raise jwt.PyJWKClientConnectionError("provider offline")

    class ObservableWaiterSlots:
        def acquire(self, *, blocking):
            assert blocking is False
            waiter_joined.set()
            return True

        def release(self):
            return None

    client = OfflineClient()
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: client)
    monkeypatch.setattr(
        auth_module,
        "_jwks_refresh_waiter_slots",
        lambda _url: ObservableWaiterSlots(),
    )

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'active-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(authenticate_request_token, token)
        assert provider_started.wait(timeout=2)
        waiter = executor.submit(authenticate_request_token, token)
        assert waiter_joined.wait(timeout=2)
        release_provider.set()
        for future in (owner, waiter):
            with pytest.raises(AuthUnavailable, match="jwks_unavailable"):
                future.result(timeout=2)

    assert calls == 1


def test_owner_replays_transport_backoff_race_to_same_kid_waiter(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    flight_created = Event()
    allow_owner = Event()
    waiter_joined = Event()
    original_start = auth_module._start_or_join_jwks_refresh
    lookup_called = False

    def pause_flight_owner(url, key_id):
        flight, is_owner = original_start(url, key_id)
        if is_owner:
            flight_created.set()
            assert allow_owner.wait(timeout=2)
        return flight, is_owner

    class UnexpectedLookupClient:
        def get_signing_key(self, _key_id):
            nonlocal lookup_called
            lookup_called = True
            pytest.fail("backed-off flight owner must not query JWKS")

    class ObservableWaiterSlots:
        def acquire(self, *, blocking):
            assert blocking is False
            waiter_joined.set()
            return True

        def release(self):
            return None

    monkeypatch.setattr(auth_module, "_start_or_join_jwks_refresh", pause_flight_owner)
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: UnexpectedLookupClient())
    monkeypatch.setattr(
        auth_module,
        "_jwks_refresh_waiter_slots",
        lambda _url: ObservableWaiterSlots(),
    )

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'new-active-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(authenticate_request_token, token)
        assert flight_created.wait(timeout=2)
        waiter = executor.submit(authenticate_request_token, token)
        assert waiter_joined.wait(timeout=2)
        auth_module._throttle_jwks_transport_failure(jwks_url)
        allow_owner.set()
        for future in (owner, waiter):
            with pytest.raises(AuthUnavailable, match="jwks_unavailable"):
                future.result(timeout=2)

    assert lookup_called is False


@pytest.mark.parametrize("previously_known", [False, True])
def test_all_asymmetric_kids_during_transport_backoff_are_unavailable(
    monkeypatch,
    previously_known,
):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    if previously_known:
        auth_module._cache_known_kid(jwks_url, "active-kid")
        auth_module._KNOWN_KID_CACHE[(jwks_url, "active-kid")] = auth_module.monotonic() - 1
    auth_module._throttle_jwks_transport_failure(jwks_url)

    class CachedClient:
        def get_signing_key(self, _key_id):
            pytest.fail("transport backoff must not query JWKS")

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: CachedClient())

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'active-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )
    with pytest.raises(AuthUnavailable, match="jwks_unavailable"):
        authenticate_request_token(token)


def test_malformed_refresh_owner_does_not_poison_valid_waiter(monkeypatch):
    issuer = "https://project-ref.supabase.co/auth/v1"
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_ISSUER", issuer)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    auth_module._cache_known_kid(jwks_url, "active-kid")
    auth_module._KNOWN_KID_CACHE[(jwks_url, "active-kid")] = auth_module.monotonic() - 1
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider_started = Event()
    release_provider = Event()
    waiter_joined = Event()
    calls = 0

    class SigningKey:
        key = private_key.public_key()

    class RefreshingClient:
        def get_signing_key(self, _key_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                provider_started.set()
                assert release_provider.wait(timeout=2)
            return SigningKey()

    class ObservableWaiterSlots:
        def acquire(self, *, blocking):
            assert blocking is False
            waiter_joined.set()
            return True

        def release(self):
            return None

    client = RefreshingClient()
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: client)
    monkeypatch.setattr(
        auth_module,
        "_jwks_refresh_waiter_slots",
        lambda _url: ObservableWaiterSlots(),
    )

    def encode_bytes(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    header = encode_bytes(json.dumps({"alg": "RS256", "kid": "active-kid"}).encode())
    malformed_token = f"{header}.{encode_bytes(b'not-json')}.AA"
    valid_token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "iss": issuer,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "active-kid"},
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(authenticate_request_token, malformed_token)
        assert provider_started.wait(timeout=2)
        waiter = executor.submit(authenticate_request_token, valid_token)
        assert waiter_joined.wait(timeout=2)
        release_provider.set()
        with pytest.raises(AuthError, match="invalid_token"):
            owner.result(timeout=2)
        user = waiter.result(timeout=2)

    assert user.user_id == "11111111-1111-1111-1111-111111111111"
    assert calls == 2
    assert (jwks_url, "active-kid") not in auth_module._UNKNOWN_KID_CACHE


def test_expired_known_kid_refresh_wait_timeout_is_unavailable(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    auth_module._cache_known_kid(jwks_url, "active-kid")
    auth_module._KNOWN_KID_CACHE[(jwks_url, "active-kid")] = auth_module.monotonic() - 1
    refresh_flight, is_owner = auth_module._start_or_join_jwks_refresh(
        jwks_url,
        "active-kid",
    )
    assert is_owner is True
    monkeypatch.setattr(auth_module, "_JWKS_REFRESH_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: object())

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'active-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )
    try:
        with pytest.raises(AuthUnavailable, match="jwks_refresh_timeout"):
            authenticate_request_token(token)
    finally:
        auth_module._complete_jwks_refresh(jwks_url, refresh_flight)


def test_expired_known_kid_refresh_wait_overflow_is_unavailable(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    auth_module._cache_known_kid(jwks_url, "active-kid")
    auth_module._KNOWN_KID_CACHE[(jwks_url, "active-kid")] = auth_module.monotonic() - 1
    refresh_flight, is_owner = auth_module._start_or_join_jwks_refresh(
        jwks_url,
        "active-kid",
    )
    assert is_owner is True
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: object())

    class FullWaiterSlots:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

    monkeypatch.setattr(
        auth_module,
        "_jwks_refresh_waiter_slots",
        lambda _url: FullWaiterSlots(),
    )

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'active-kid'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )
    try:
        with pytest.raises(AuthUnavailable, match="jwks_refresh_busy"):
            authenticate_request_token(token)
    finally:
        auth_module._complete_jwks_refresh(jwks_url, refresh_flight)


def test_different_unknown_kid_during_refresh_returns_temporary_unavailable(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()
    jwks_url = auth_module._supabase_jwks_url()
    refresh_flight, is_owner = auth_module._start_or_join_jwks_refresh(
        jwks_url,
        "owner-unknown",
    )
    assert is_owner is True
    called = False

    class UnexpectedLookupClient:
        def get_signing_key(self, _key_id):
            nonlocal called
            called = True
            pytest.fail("a concurrent unknown key must not start another JWKS lookup")

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: UnexpectedLookupClient())

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'parallel-unknown'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )
    try:
        with pytest.raises(AuthUnavailable, match="jwks_refresh_busy"):
            authenticate_request_token(token)
    finally:
        auth_module._complete_jwks_refresh(jwks_url, refresh_flight)

    assert called is False


def test_malformed_jwks_payload_is_converted_to_auth_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()

    class MalformedJwksClient:
        def get_signing_key(self, _key_id):
            raise ValueError("invalid JWKS JSON")

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: MalformedJwksClient())

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'malformed-jwks'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )

    with pytest.raises(AuthError, match="invalid_token"):
        authenticate_request_token(token)


def test_malformed_asymmetric_payload_returns_401_from_middleware(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()
    public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()

    class SigningKey:
        key = public_key

    class CachedKeyClient:
        def get_signing_key(self, _key_id):
            return SigningKey()

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: CachedKeyClient())

    def encode_bytes(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    header = encode_bytes(json.dumps({"alg": "RS256", "kid": "malformed"}).encode())
    token = f"{header}.{encode_bytes(b'not-json')}.AA"
    request = _build_request(method="GET", access_token=token)

    async def call_next(_: Request):
        pytest.fail("malformed token must not reach the route")

    response = asyncio.run(api_auth_middleware(request, call_next))

    assert response.status_code == 401
    assert response.body == b'{"detail":"invalid_token"}'


@pytest.mark.parametrize(
    "decode_error",
    [TypeError("unsupported key shape"), ValueError("bad key")],
)
def test_malformed_signing_key_decode_failure_is_converted_to_auth_error(
    monkeypatch,
    decode_error,
):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()

    class SigningKey:
        key = object()

    class MalformedSigningKeyClient:
        def get_signing_key(self, _key_id):
            return SigningKey()

    monkeypatch.setattr(
        auth_module,
        "_jwks_client",
        lambda _url: MalformedSigningKeyClient(),
    )

    def raise_decode_error(*_args, **_kwargs):
        raise decode_error

    monkeypatch.setattr(auth_module.jwt, "decode", raise_decode_error)

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = (
        f"{encode({'alg': 'RS256', 'kid': 'malformed-key'})}."
        f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
    )

    with pytest.raises(AuthError, match="invalid_token"):
        authenticate_request_token(token)


def test_unknown_kid_backoff_does_not_reject_a_key_present_in_cached_jwks(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()
    calls: list[str] = []

    class CachedSet:
        def get(self):
            return {"keys": [{"kid": "legitimate-rotation-key"}]}

    class SigningKey:
        key = object()

    class RotatingClient:
        jwk_set_cache = CachedSet()

        def get_signing_key(self, key_id):
            calls.append(key_id)
            if key_id == "bogus-kid":
                raise jwt.PyJWKClientError("unknown kid")
            return SigningKey()

    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: RotatingClient())
    monkeypatch.setattr(
        auth_module.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "11111111-1111-1111-1111-111111111111",
            "email": "user@example.com",
        },
    )

    def token_for(key_id: str) -> str:
        def encode(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

        return (
            f"{encode({'alg': 'RS256', 'kid': key_id})}."
            f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
        )

    with pytest.raises(AuthError, match="invalid_token_key_id"):
        authenticate_request_token(token_for("bogus-kid"))

    user = authenticate_request_token(token_for("legitimate-rotation-key"))

    assert user.user_id == "11111111-1111-1111-1111-111111111111"
    assert calls == ["bogus-kid", "legitimate-rotation-key"]


def test_removed_signing_key_is_rejected_after_jwks_set_expiry(monkeypatch):
    issuer = "https://project-ref.supabase.co/auth/v1"
    jwks_url = f"{issuer}/.well-known/revocation-test-jwks.json"
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_JWKS_URL", jwks_url)
    monkeypatch.setenv("SUPABASE_JWT_ISSUER", issuer)
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    auth_module._JWKS_CLIENT_CACHE.pop(jwks_url, None)
    auth_module._UNKNOWN_KID_CACHE.clear()
    auth_module._KNOWN_KID_CACHE.clear()
    auth_module._UNKNOWN_KID_REFRESH_BACKOFF.clear()

    old_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_jwk(private_key, key_id):
        value = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
        value.update({"kid": key_id, "use": "sig", "alg": "RS256"})
        return value

    published_keys = [public_jwk(old_private_key, "retired-kid")]
    fetches: list[list[str]] = []

    def fake_fetch_data(client):
        payload = {"keys": [dict(key) for key in published_keys]}
        fetches.append([str(key["kid"]) for key in published_keys])
        assert client.jwk_set_cache is not None
        client.jwk_set_cache.put(payload)
        return payload

    monkeypatch.setattr(auth_module.PyJWKClient, "fetch_data", fake_fetch_data)
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "iss": issuer,
        },
        old_private_key,
        algorithm="RS256",
        headers={"kid": "retired-kid"},
    )

    assert authenticate_request_token(token).user_id == "11111111-1111-1111-1111-111111111111"

    client = auth_module._JWKS_CLIENT_CACHE[jwks_url]
    assert client.jwk_set_cache is not None
    cached_set = client.jwk_set_cache.jwk_set_with_timestamp
    assert cached_set is not None
    cached_set.timestamp -= auth_module._JWKS_SET_CACHE_LIFESPAN_SECONDS + 1
    auth_module._KNOWN_KID_CACHE[(jwks_url, "retired-kid")] = auth_module.monotonic() - 1
    published_keys[:] = [public_jwk(new_private_key, "replacement-kid")]

    with pytest.raises(AuthError, match="invalid_token_key_id"):
        authenticate_request_token(token)

    assert fetches == [
        ["retired-kid"],
        ["replacement-kid"],
        ["replacement-kid"],
    ]


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


def test_large_same_kid_burst_does_not_starve_shared_executor_or_known_key(
    monkeypatch,
):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ALGORITHMS", raising=False)
    jwks_url = auth_module._supabase_jwks_url()
    auth_module._cache_known_kid(jwks_url, "known-kid")
    provider_started = Event()
    release_provider = Event()
    more_than_default_pool_waiting = Event()
    call_lock = Lock()
    lookup_calls = 0

    class SigningKey:
        key = object()

    class RefreshingClient:
        def get_signing_key(self, key_id):
            nonlocal lookup_calls
            with call_lock:
                lookup_calls += 1
                call_number = lookup_calls
            if key_id == "cold-kid" and call_number == 1:
                provider_started.set()
                assert release_provider.wait(timeout=5)
            return SigningKey()

    class ObservableWaiterSlots:
        def __init__(self):
            self._slots = BoundedSemaphore(64)
            self._lock = Lock()
            self._joined = 0

        def acquire(self, *, blocking):
            assert blocking is False
            acquired = self._slots.acquire(blocking=False)
            if acquired:
                with self._lock:
                    self._joined += 1
                    if self._joined >= 33:
                        more_than_default_pool_waiting.set()
            return acquired

        def release(self):
            self._slots.release()

    client = RefreshingClient()
    waiter_slots = ObservableWaiterSlots()
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _url: client)
    monkeypatch.setattr(
        auth_module,
        "_jwks_refresh_waiter_slots",
        lambda _url: waiter_slots,
    )
    monkeypatch.setattr(
        auth_module.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "11111111-1111-1111-1111-111111111111",
        },
    )

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    def token_for(key_id):
        return (
            f"{encode({'alg': 'RS256', 'kid': key_id})}."
            f"{encode({'sub': '11111111-1111-1111-1111-111111111111'})}.AA"
        )

    async def call_next(_: Request):
        return Response(status_code=204)

    async def wait_until_set(event: Event) -> None:
        for _ in range(2_000):
            if event.is_set():
                return
            await asyncio.sleep(0.001)
        pytest.fail("timed out waiting for auth burst")

    async def scenario():
        cold_tasks = [
            asyncio.create_task(
                api_auth_middleware(
                    _build_request(method="GET", access_token=token_for("cold-kid")),
                    call_next,
                )
            )
            for _ in range(40)
        ]
        try:
            await wait_until_set(provider_started)
            await wait_until_set(more_than_default_pool_waiting)
            shared_result = await asyncio.wait_for(
                asyncio.to_thread(lambda: "shared-executor-ok"),
                timeout=1,
            )
            known_response = await asyncio.wait_for(
                api_auth_middleware(
                    _build_request(method="GET", access_token=token_for("known-kid")),
                    call_next,
                ),
                timeout=1,
            )
        finally:
            release_provider.set()
        cold_responses = await asyncio.wait_for(
            asyncio.gather(*cold_tasks),
            timeout=5,
        )
        return shared_result, known_response, cold_responses

    shared_result, known_response, cold_responses = asyncio.run(scenario())

    assert shared_result == "shared-executor-ok"
    assert known_response.status_code == 204
    assert {response.status_code for response in cold_responses} == {204}
