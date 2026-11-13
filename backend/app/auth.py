from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any
from uuid import UUID
import jwt
from fastapi import Request
from jwt import PyJWTError

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUTH_USER_CONTEXT: ContextVar["AuthenticatedUser | None"] = ContextVar("topsignal_auth_user", default=None)


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    claims: dict[str, Any]

class AuthError(RuntimeError):
    pass

class AuthUnavailable(AuthError):
    """Authentication could not finish because local verification is unavailable."""

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

def authenticate_request_token(token: str) -> AuthenticatedUser:
    """Accept only revocable sessions issued by the protected local owner store.

    Obsolete cloud settings and arbitrary key IDs never trigger a network fetch.
    """
    if not token:
        raise AuthError("missing_bearer_token")
    try:
        header = jwt.get_unverified_header(token)
    except (PyJWTError, TypeError, ValueError) as exc:
        raise AuthError("invalid_auth_header") from exc
    from .local_auth import KID, verify
    if header.get("kid") != KID or header.get("alg") != "HS256":
        raise AuthError("unsupported_token_issuer")
    payload = verify(token)
    return AuthenticatedUser(user_id=payload["sub"], email=payload.get("email"), claims=payload)
