from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import BoundedSemaphore, Event, Lock
from time import monotonic
from typing import Any
from uuid import UUID

import jwt
from fastapi import Request
from jwt import (
    PyJWKClient,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWTError,
)

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUTH_USER_CONTEXT: ContextVar["AuthenticatedUser | None"] = ContextVar("topsignal_auth_user", default=None)
_JWKS_CLIENT_CACHE: dict[str, PyJWKClient] = {}
_JWKS_CACHE_LOCK = Lock()
_JWKS_REFRESH_FLIGHTS: dict[str, "_JwksRefreshFlight"] = {}
_JWKS_REFRESH_WAITER_SLOTS: dict[str, BoundedSemaphore] = {}
_UNKNOWN_KID_CACHE: dict[tuple[str, str], float] = {}
_UNKNOWN_KID_TTL_SECONDS = 30.0
_UNKNOWN_KID_CACHE_LIMIT = 256
_KNOWN_KID_CACHE: dict[tuple[str, str], float] = {}
_KNOWN_KID_CACHE_LIMIT = 64
_JWKS_SET_CACHE_LIFESPAN_SECONDS = 300.0
_KNOWN_KID_TTL_SECONDS = _JWKS_SET_CACHE_LIFESPAN_SECONDS
_JWKS_REFRESH_WAITER_LIMIT = 64
_JWKS_REFRESH_WAIT_SECONDS = 5.0
_UNKNOWN_KID_REFRESH_BACKOFF: dict[str, float] = {}
_UNKNOWN_KID_REFRESH_BACKOFF_SECONDS = 5.0
_JWKS_TRANSPORT_BACKOFF: dict[str, float] = {}
_JWKS_TRANSPORT_BACKOFF_SECONDS = 5.0
_DEFAULT_ALLOWED_JWT_ALGORITHMS = frozenset({"HS256", "RS256", "ES256"})


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    claims: dict[str, Any]


class AuthError(RuntimeError):
    pass


class AuthUnavailable(AuthError):
    """Authentication could not finish because a bounded JWKS wait filled or timed out."""


@dataclass
class _JwksRefreshFlight:
    key_id: str
    completed: Event
    unavailable_detail: str | None = None


def auth_required() -> bool:
    # Authentication is fail-closed. A deliberately anonymous local instance
    # must opt out explicitly with AUTH_REQUIRED=false; a missing or misspelled
    # deployment variable must never expose the default tenant.
    return _read_bool_env("AUTH_REQUIRED", True)


def bind_authenticated_user(user: AuthenticatedUser | None) -> Token:
    return _AUTH_USER_CONTEXT.set(user)


def reset_authenticated_user(token: Token) -> None:
    _AUTH_USER_CONTEXT.reset(token)


def get_authenticated_user() -> AuthenticatedUser | None:
    return _AUTH_USER_CONTEXT.get()


def get_authenticated_user_or_default() -> AuthenticatedUser:
    current = get_authenticated_user()
    if current is not None:
        return current
    return AuthenticatedUser(
        user_id=os.getenv("DEFAULT_USER_ID", DEFAULT_USER_ID),
        email=os.getenv("DEFAULT_USER_EMAIL"),
        claims={},
    )


def get_authenticated_user_id() -> str:
    return get_authenticated_user_or_default().user_id


def extract_access_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if auth_header:
        scheme, _, value = auth_header.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()

    if not _allow_query_bearer_tokens():
        return None

    query_token = request.query_params.get("access_token")
    if query_token:
        normalized = query_token.strip()
        if normalized:
            return normalized
    return None


def authenticate_request_token(token: str) -> AuthenticatedUser:
    if not token:
        raise AuthError("missing_bearer_token")

    try:
        header = jwt.get_unverified_header(token)
    except (PyJWTError, TypeError, ValueError) as exc:
        raise AuthError("invalid_auth_header") from exc

    algorithm = str(header.get("alg") or "").upper()
    if algorithm not in _allowed_jwt_algorithms():
        raise AuthError("invalid_token_algorithm")
    key_id = str(header.get("kid") or "").strip() or None
    payload = _decode_jwt(token=token, algorithm=algorithm, key_id=key_id)
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError("invalid_token_subject")
    user_id = _normalize_user_id(subject, error_detail="invalid_token_subject")

    raw_email = payload.get("email")
    email = raw_email.strip() if isinstance(raw_email, str) and raw_email.strip() else None
    return AuthenticatedUser(user_id=user_id, email=email, claims=payload)


def _decode_jwt(*, token: str, algorithm: str, key_id: str | None) -> dict[str, Any]:
    audience = os.getenv("SUPABASE_JWT_AUDIENCE", "").strip() or None
    issuer = _supabase_issuer()
    options = {"verify_aud": bool(audience)}

    if algorithm == "HS256":
        secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
        if not secret:
            raise AuthError("missing_supabase_jwt_secret")
        try:
            return jwt.decode(
                token,
                key=secret,
                algorithms=[algorithm],
                issuer=issuer,
                audience=audience,
                options=options,
            )
        except (PyJWTError, TypeError, ValueError) as exc:
            raise AuthError("invalid_token") from exc

    if key_id is None:
        raise AuthError("invalid_token_key_id")

    jwks_url = _supabase_jwks_url()
    jwks_client = _jwks_client(jwks_url)
    # A preceding unknown kid may already have refreshed the shared JWK set.
    # Learn its real keys before applying URL-wide backoff.
    _cache_known_kids_from_jwks_cache(jwks_url, jwks_client)
    known_key = _known_kid_is_cached(jwks_url, key_id)
    if not known_key and _jwks_transport_is_throttled(jwks_url):
        raise AuthUnavailable("jwks_unavailable")
    if not known_key and _unknown_kid_is_cached(jwks_url, key_id):
        raise AuthError("invalid_token_key_id")
    refresh_flight: _JwksRefreshFlight | None = None
    owns_refresh_flight = False
    if not known_key:
        if _unknown_kid_refresh_is_throttled(jwks_url):
            raise AuthUnavailable("jwks_refresh_backoff")
        refresh_flight, owns_refresh_flight = _start_or_join_jwks_refresh(
            jwks_url,
            key_id,
        )
        if not owns_refresh_flight:
            # Same-kid callers share the result, including a genuinely new
            # rotation key at cold start. A different kid cannot be classified
            # from this flight, so fail temporarily without another network
            # request or a definitive invalid-token response.
            if refresh_flight.key_id != key_id:
                raise AuthUnavailable("jwks_refresh_busy")
            waiter_slot = _jwks_refresh_waiter_slots(jwks_url)
            if not waiter_slot.acquire(blocking=False):
                raise AuthUnavailable("jwks_refresh_busy")
            try:
                completed = refresh_flight.completed.wait(_JWKS_REFRESH_WAIT_SECONDS)
            finally:
                waiter_slot.release()
            if not completed:
                raise AuthUnavailable("jwks_refresh_timeout")
            if refresh_flight.unavailable_detail is not None:
                raise AuthUnavailable(refresh_flight.unavailable_detail)
            _cache_known_kids_from_jwks_cache(jwks_url, jwks_client)
            if _unknown_kid_is_cached(jwks_url, key_id):
                raise AuthError("invalid_token_key_id")
            if not _known_kid_is_cached(jwks_url, key_id):
                raise AuthError("invalid_token_key_id")

    try:
        if owns_refresh_flight:
            if _unknown_kid_is_cached(jwks_url, key_id):
                raise AuthError("invalid_token_key_id")
            if _jwks_transport_is_throttled(jwks_url):
                _raise_auth_unavailable(
                    "jwks_unavailable",
                    refresh_flight=refresh_flight,
                )
            if (
                _unknown_kid_refresh_is_throttled(jwks_url)
                and not _known_kid_is_cached(jwks_url, key_id)
            ):
                _raise_auth_unavailable(
                    "jwks_refresh_backoff",
                    refresh_flight=refresh_flight,
                )
        try:
            # The header and kid were already parsed above. Looking up by kid
            # keeps a malformed token body from becoming the shared refresh
            # owner and poisoning valid callers waiting on the same flight.
            signing_key = jwks_client.get_signing_key(key_id).key
        except PyJWKClientConnectionError as exc:
            _cache_known_kids_from_jwks_cache(jwks_url, jwks_client)
            _throttle_jwks_transport_failure(jwks_url)
            _raise_auth_unavailable(
                "jwks_unavailable",
                refresh_flight=refresh_flight,
                cause=exc,
            )
        except PyJWKClientError as exc:
            # PyJWKClient refreshes once when a kid is absent. Preserve every
            # real kid from that successfully fetched set before throttling the
            # unknown one, so an attacker cannot lock out legitimate sessions
            # or a just-published rotation key during the backoff window.
            _cache_known_kids_from_jwks_cache(jwks_url, jwks_client)
            _cache_unknown_kid(jwks_url, key_id)
            _throttle_unknown_kid_refresh(jwks_url)
            raise AuthError("invalid_token_key_id") from exc
        except PyJWTError as exc:
            _throttle_unknown_kid_refresh(jwks_url)
            raise AuthError("invalid_token") from exc
        except (OSError, TimeoutError) as exc:
            _throttle_jwks_transport_failure(jwks_url)
            _raise_auth_unavailable(
                "jwks_unavailable",
                refresh_flight=refresh_flight,
                cause=exc,
            )
        except (TypeError, ValueError) as exc:
            _throttle_unknown_kid_refresh(jwks_url)
            raise AuthError("invalid_token") from exc

        _cache_known_kids_from_jwks_cache(jwks_url, jwks_client)
        _cache_known_kid(
            jwks_url,
            key_id,
            expires_at=_known_kid_expiry_from_jwks_cache(jwks_client),
        )

        try:
            payload = jwt.decode(
                token,
                key=signing_key,
                algorithms=[algorithm],
                issuer=issuer,
                audience=audience,
                options=options,
            )
        except (PyJWTError, TypeError, ValueError) as exc:
            raise AuthError("invalid_token") from exc
        return payload
    finally:
        if owns_refresh_flight and refresh_flight is not None:
            _complete_jwks_refresh(jwks_url, refresh_flight)


def _supabase_url() -> str:
    value = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not value:
        raise AuthError("missing_supabase_url")
    return value


def _supabase_jwks_url() -> str:
    value = os.getenv("SUPABASE_JWKS_URL", "").strip()
    if value:
        return value
    return f"{_supabase_url()}/auth/v1/.well-known/jwks.json"


def _supabase_issuer() -> str:
    value = os.getenv("SUPABASE_JWT_ISSUER", "").strip()
    if value:
        return value
    return f"{_supabase_url()}/auth/v1"


def _jwks_client(url: str) -> PyJWKClient:
    with _JWKS_CACHE_LOCK:
        existing = _JWKS_CLIENT_CACHE.get(url)
        if existing is not None:
            return existing
        created = PyJWKClient(
            url,
            # PyJWT's optional per-key LRU has no TTL. Enabling it would keep
            # removed/revoked signing keys valid for the process lifetime.
            cache_keys=False,
            cache_jwk_set=True,
            lifespan=_JWKS_SET_CACHE_LIFESPAN_SECONDS,
            timeout=5,
        )
        _JWKS_CLIENT_CACHE[url] = created
        return created


def _start_or_join_jwks_refresh(
    jwks_url: str,
    key_id: str,
) -> tuple[_JwksRefreshFlight, bool]:
    with _JWKS_CACHE_LOCK:
        existing = _JWKS_REFRESH_FLIGHTS.get(jwks_url)
        if existing is not None:
            return existing, False
        created = _JwksRefreshFlight(key_id=key_id, completed=Event())
        _JWKS_REFRESH_FLIGHTS[jwks_url] = created
        return created, True


def _complete_jwks_refresh(jwks_url: str, flight: _JwksRefreshFlight) -> None:
    with _JWKS_CACHE_LOCK:
        flight.completed.set()
        if _JWKS_REFRESH_FLIGHTS.get(jwks_url) is flight:
            _JWKS_REFRESH_FLIGHTS.pop(jwks_url, None)


def _raise_auth_unavailable(
    detail: str,
    *,
    refresh_flight: _JwksRefreshFlight | None,
    cause: BaseException | None = None,
) -> None:
    if refresh_flight is not None:
        refresh_flight.unavailable_detail = detail
    if cause is not None:
        raise AuthUnavailable(detail) from cause
    raise AuthUnavailable(detail)


def _jwks_refresh_waiter_slots(jwks_url: str) -> BoundedSemaphore:
    with _JWKS_CACHE_LOCK:
        existing = _JWKS_REFRESH_WAITER_SLOTS.get(jwks_url)
        if existing is not None:
            return existing
        created = BoundedSemaphore(_JWKS_REFRESH_WAITER_LIMIT)
        _JWKS_REFRESH_WAITER_SLOTS[jwks_url] = created
        return created


def _allowed_jwt_algorithms() -> frozenset[str]:
    raw = os.getenv("SUPABASE_JWT_ALGORITHMS", "").strip()
    if not raw:
        return _DEFAULT_ALLOWED_JWT_ALGORITHMS
    configured = frozenset(part.strip().upper() for part in raw.split(",") if part.strip())
    # Configuration may narrow the supported set, never expand it to an
    # algorithm the application has not explicitly reviewed.
    return configured & _DEFAULT_ALLOWED_JWT_ALGORITHMS


def _unknown_kid_is_cached(jwks_url: str, key_id: str) -> bool:
    now = monotonic()
    cache_key = (jwks_url, key_id)
    with _JWKS_CACHE_LOCK:
        expires_at = _UNKNOWN_KID_CACHE.get(cache_key)
        if expires_at is None:
            return False
        if expires_at <= now:
            _UNKNOWN_KID_CACHE.pop(cache_key, None)
            return False
        return True


def _cache_unknown_kid(jwks_url: str, key_id: str) -> None:
    now = monotonic()
    with _JWKS_CACHE_LOCK:
        expired = [cache_key for cache_key, expires_at in _UNKNOWN_KID_CACHE.items() if expires_at <= now]
        for cache_key in expired:
            _UNKNOWN_KID_CACHE.pop(cache_key, None)
        if len(_UNKNOWN_KID_CACHE) >= _UNKNOWN_KID_CACHE_LIMIT:
            oldest_key = min(_UNKNOWN_KID_CACHE, key=_UNKNOWN_KID_CACHE.get)
            _UNKNOWN_KID_CACHE.pop(oldest_key, None)
        _UNKNOWN_KID_CACHE[(jwks_url, key_id)] = now + _UNKNOWN_KID_TTL_SECONDS


def _known_kid_is_cached(jwks_url: str, key_id: str) -> bool:
    now = monotonic()
    cache_key = (jwks_url, key_id)
    with _JWKS_CACHE_LOCK:
        expires_at = _KNOWN_KID_CACHE.get(cache_key)
        if expires_at is None:
            return False
        if expires_at <= now:
            _KNOWN_KID_CACHE.pop(cache_key, None)
            return False
        return True


def _cache_known_kid(
    jwks_url: str,
    key_id: str,
    *,
    expires_at: float | None = None,
) -> None:
    now = monotonic()
    cache_key = (jwks_url, key_id)
    bounded_expiry = min(
        expires_at if expires_at is not None else now + _KNOWN_KID_TTL_SECONDS,
        now + _KNOWN_KID_TTL_SECONDS,
    )
    if bounded_expiry <= now:
        return
    with _JWKS_CACHE_LOCK:
        expired = [
            known_cache_key
            for known_cache_key, known_expires_at in _KNOWN_KID_CACHE.items()
            if known_expires_at <= now
        ]
        for known_cache_key in expired:
            _KNOWN_KID_CACHE.pop(known_cache_key, None)
        if cache_key not in _KNOWN_KID_CACHE and len(_KNOWN_KID_CACHE) >= _KNOWN_KID_CACHE_LIMIT:
            oldest_key = min(_KNOWN_KID_CACHE, key=_KNOWN_KID_CACHE.get)
            _KNOWN_KID_CACHE.pop(oldest_key, None)
        _KNOWN_KID_CACHE[cache_key] = bounded_expiry
        _UNKNOWN_KID_CACHE.pop(cache_key, None)


def _known_kid_expiry_from_jwks_cache(jwks_client: PyJWKClient) -> float | None:
    """Return the current set's expiry so hints never outlive key material."""

    jwk_set_cache = getattr(jwks_client, "jwk_set_cache", None)
    cached_set = getattr(jwk_set_cache, "jwk_set_with_timestamp", None)
    get_timestamp = getattr(cached_set, "get_timestamp", None)
    lifespan = getattr(jwk_set_cache, "lifespan", None)
    if not callable(get_timestamp) or not isinstance(lifespan, (int, float)):
        return None
    try:
        return float(get_timestamp()) + float(lifespan)
    except (TypeError, ValueError):
        return None


def _cache_known_kids_from_jwks_cache(jwks_url: str, jwks_client: PyJWKClient) -> None:
    jwk_set_cache = getattr(jwks_client, "jwk_set_cache", None)
    if jwk_set_cache is None:
        return
    try:
        cached_payload = jwk_set_cache.get()
    except Exception:
        return
    if not isinstance(cached_payload, dict):
        return
    raw_keys = cached_payload.get("keys")
    if not isinstance(raw_keys, list):
        return
    expires_at = _known_kid_expiry_from_jwks_cache(jwks_client)
    for raw_key in raw_keys:
        if not isinstance(raw_key, dict):
            continue
        key_id = str(raw_key.get("kid") or "").strip()
        if key_id:
            _cache_known_kid(jwks_url, key_id, expires_at=expires_at)


def _unknown_kid_refresh_is_throttled(jwks_url: str) -> bool:
    now = monotonic()
    with _JWKS_CACHE_LOCK:
        not_before = _UNKNOWN_KID_REFRESH_BACKOFF.get(jwks_url)
        if not_before is None:
            return False
        if not_before <= now:
            _UNKNOWN_KID_REFRESH_BACKOFF.pop(jwks_url, None)
            return False
        return True


def _throttle_unknown_kid_refresh(jwks_url: str) -> None:
    with _JWKS_CACHE_LOCK:
        _UNKNOWN_KID_REFRESH_BACKOFF[jwks_url] = monotonic() + _UNKNOWN_KID_REFRESH_BACKOFF_SECONDS


def _jwks_transport_is_throttled(jwks_url: str) -> bool:
    now = monotonic()
    with _JWKS_CACHE_LOCK:
        not_before = _JWKS_TRANSPORT_BACKOFF.get(jwks_url)
        if not_before is None:
            return False
        if not_before <= now:
            _JWKS_TRANSPORT_BACKOFF.pop(jwks_url, None)
            return False
        return True


def _throttle_jwks_transport_failure(jwks_url: str) -> None:
    now = monotonic()
    with _JWKS_CACHE_LOCK:
        _JWKS_TRANSPORT_BACKOFF[jwks_url] = now + _JWKS_TRANSPORT_BACKOFF_SECONDS
        # Never-known random kids still fail closed without triggering another
        # network attempt during the same provider outage.
        _UNKNOWN_KID_REFRESH_BACKOFF[jwks_url] = now + _UNKNOWN_KID_REFRESH_BACKOFF_SECONDS


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _allow_query_bearer_tokens() -> bool:
    return _read_bool_env("ALLOW_QUERY_BEARER_TOKENS", False)


def _normalize_user_id(value: str, *, error_detail: str) -> str:
    try:
        return str(UUID(value.strip()))
    except (AttributeError, ValueError) as exc:
        raise AuthError(error_detail) from exc
