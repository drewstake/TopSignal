from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Request
from jwt import InvalidTokenError, PyJWKClient

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUTH_USER_CONTEXT: ContextVar["AuthenticatedUser | None"] = ContextVar("topsignal_auth_user", default=None)
_JWKS_CLIENT_CACHE: dict[str, PyJWKClient] = {}


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    claims: dict[str, Any]


class AuthError(RuntimeError):
    pass


def auth_required() -> bool:
    # If AUTH_REQUIRED is not explicitly set, default to requiring auth only
    # when Supabase auth is configured. This keeps local dev usable without
    # bearer tokens while preserving strict mode in deployed environments.
    if os.getenv("AUTH_REQUIRED") is not None:
        return _read_bool_env("AUTH_REQUIRED", True)
    return bool(os.getenv("SUPABASE_URL", "").strip())


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
    except InvalidTokenError as exc:
        raise AuthError("invalid_auth_header") from exc

    algorithm = str(header.get("alg") or "").upper()
    payload = _decode_jwt(token=token, algorithm=algorithm)
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError("invalid_token_subject")

    raw_email = payload.get("email")
    email = raw_email.strip() if isinstance(raw_email, str) and raw_email.strip() else None
    return AuthenticatedUser(user_id=subject.strip(), email=email, claims=payload)


def _decode_jwt(*, token: str, algorithm: str) -> dict[str, Any]:
    audience = os.getenv("SUPABASE_JWT_AUDIENCE", "").strip() or None
    issuer = _supabase_issuer()
    options = {"verify_aud": bool(audience)}

    if algorithm.startswith("HS"):
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
        except InvalidTokenError as exc:
            raise AuthError("invalid_token") from exc

    jwks_client = _jwks_client(_supabase_jwks_url())
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key=signing_key,
            algorithms=[algorithm],
            issuer=issuer,
            audience=audience,
            options=options,
        )
    except InvalidTokenError as exc:
        raise AuthError("invalid_token") from exc


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
    existing = _JWKS_CLIENT_CACHE.get(url)
    if existing is not None:
        return existing
    created = PyJWKClient(url)
    _JWKS_CLIENT_CACHE[url] = created
    return created


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
