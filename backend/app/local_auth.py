"""Opt-in, single-owner login for a loopback worker. Never provisions cloud users.

The store must be provisioned by an administrator and protected by filesystem
permissions. Passwords, setup codes and bearer tokens are never written to logs.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
from threading import BoundedSemaphore
import time
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .local_request_security import anonymous_request_error

KID = "topsignal-local-v1"
ISSUER = "urn:topsignal:local"
AUDIENCE = "topsignal-local"
SESSION_SECONDS = 12 * 60 * 60
_HASH_SLOTS = BoundedSemaphore(2)


def enabled() -> bool:
    return bool(os.getenv("TOPSIGNAL_LOCAL_AUTH_DB", "").strip())


@contextmanager
def connect():
    path = Path(os.environ.get("TOPSIGNAL_LOCAL_AUTH_DB", ""))
    if not enabled() or not path.is_absolute() or not path.is_file():
        raise RuntimeError("Local authentication store unavailable")
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def validate_configuration() -> None:
    if not enabled():
        return
    with connect() as db:
        owner = db.execute("SELECT * FROM owner WHERE id=1").fetchone()
        if (owner is None or UUID(owner["user_id"]).int == 0
                or len(owner["signing_key"]) < 32):
            raise RuntimeError("Invalid local authentication owner")


def provision(path: Path, user_id: str, email: str | None) -> str:
    """Admin-only initial provisioning; refuse to overwrite any existing store."""
    user_id = str(UUID(user_id))
    if UUID(user_id).int == 0 or not path.is_absolute():
        raise ValueError("An existing owner UUID and absolute store path are required")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental password/session resets.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    code = secrets.token_urlsafe(32)
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE owner (id INTEGER PRIMARY KEY CHECK(id=1), user_id TEXT NOT NULL,
                email TEXT, signing_key BLOB NOT NULL, salt BLOB, password_hash BLOB,
                setup_hash TEXT, setup_expires INTEGER, attempt_start INTEGER NOT NULL DEFAULT 0,
                attempt_count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE sessions (id TEXT PRIMARY KEY, expires INTEGER NOT NULL);
            CREATE TABLE events (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, kind TEXT NOT NULL);
        """)
        db.execute("INSERT INTO owner(id,user_id,email,signing_key,setup_hash,setup_expires) VALUES(1,?,?,?,?,?)",
                   (user_id, email, secrets.token_bytes(32), _digest(code), int(time.time()) + 86400))
        _event(db, "provisioned")
    db.close()
    return code


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _event(db, kind: str) -> None:
    db.execute("INSERT INTO events(at,kind) VALUES(?,?)", (int(time.time()), kind))


def _password_hash(password: str, salt: bytes) -> bytes:
    # OWASP scrypt baseline: N=2^17, r=8, p=1. Bound concurrent memory use.
    if not _HASH_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Local sign-in busy; try again shortly")
    try:
        return hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1,
                              maxmem=256 * 1024 * 1024)
    finally:
        _HASH_SLOTS.release()


def _reserve_attempt(db) -> sqlite3.Row:
    now = int(time.time())
    db.execute("BEGIN IMMEDIATE")
    owner = db.execute("SELECT * FROM owner WHERE id=1").fetchone()
    start = owner["attempt_start"]
    count = owner["attempt_count"]
    if now - start >= 300:
        start, count = now, 0
    if count >= 10:
        db.rollback()
        raise HTTPException(429, "Too many attempts; try again in five minutes", headers={"Retry-After": "300"})
    db.execute("UPDATE owner SET attempt_start=?,attempt_count=? WHERE id=1", (start, count + 1))
    _event(db, "sign_in_attempt")
    db.commit()
    return owner


def _session(db, owner) -> dict:
    now = int(time.time())
    jti = secrets.token_urlsafe(32)
    claims = dict(sub=owner["user_id"], email=owner["email"], iss=ISSUER,
                  aud=AUDIENCE, iat=now, exp=now + SESSION_SECONDS, jti=jti)
    token = jwt.encode(claims, owner["signing_key"], algorithm="HS256", headers={"kid": KID})
    # Only expired authentication sessions are pruned; audit events are retained.
    db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
    db.execute("INSERT INTO sessions(id,expires) VALUES(?,?)", (_digest(jti), claims["exp"]))
    db.execute("UPDATE owner SET attempt_count=0 WHERE id=1")
    _event(db, "signed_in")
    return dict(access_token=token, expires_at=claims["exp"], expires_in=SESSION_SECONDS,
                token_type="bearer", refresh_token="", user=dict(id=owner["user_id"], email=owner["email"]))


def verify(token: str) -> dict:
    from .auth import AuthError, AuthUnavailable
    try:
        with connect() as db:
            owner = db.execute("SELECT * FROM owner WHERE id=1").fetchone()
            claims = jwt.decode(token, owner["signing_key"], algorithms=["HS256"],
                                issuer=ISSUER, audience=AUDIENCE,
                                options={"require": ["sub", "iat", "exp", "jti", "iss", "aud"]})
            if (claims["sub"] != owner["user_id"] or not isinstance(claims["jti"], str)
                    or not db.execute("SELECT 1 FROM sessions WHERE id=? AND expires>?",
                                      (_digest(claims["jti"]), int(time.time()))).fetchone()):
                raise AuthError("invalid_local_session")
            # Owner identity comes from the protected store, never from user input.
            claims["email"] = owner["email"]
            return claims
    except AuthError:
        raise
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise AuthError("invalid_local_session") from exc
    except (sqlite3.Error, RuntimeError, OSError) as exc:
        raise AuthUnavailable("local_auth_unavailable") from exc


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)


class SetupInput(LoginInput):
    password: str = Field(min_length=14, max_length=128)
    setup_code: str = Field(min_length=1, max_length=128)


def make_router(origin_allowed) -> APIRouter:
    def boundary(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if anonymous_request_error(request, origin_allowed):
            raise HTTPException(403, "Local sign-in requires a trusted loopback connection")
        if not enabled():
            raise HTTPException(404, "Local sign-in is not enabled")

    router = APIRouter(prefix="/auth/local", dependencies=[Depends(boundary)])

    @router.get("/status")
    def status():
        with connect() as db:
            owner = db.execute("SELECT password_hash FROM owner WHERE id=1").fetchone()
            return dict(enabled=True, needs_setup=owner["password_hash"] is None)

    @router.post("/setup")
    def setup(body: SetupInput):
        with connect() as db:
            owner = _reserve_attempt(db)
            if (owner["password_hash"] is not None or not owner["setup_hash"]
                    or owner["setup_expires"] <= time.time()
                    or not hmac.compare_digest(owner["setup_hash"], _digest(body.setup_code))):
                raise HTTPException(401, "Invalid or expired setup code")
            salt = secrets.token_bytes(32)
            password_hash = _password_hash(body.password, salt)
            # Compare-and-set makes the one-time code safe against simultaneous setup.
            result = db.execute("UPDATE owner SET salt=?,password_hash=?,setup_hash=NULL,setup_expires=NULL "
                                "WHERE id=1 AND password_hash IS NULL AND setup_hash=?",
                                (salt, password_hash, owner["setup_hash"]))
            if result.rowcount != 1:
                raise HTTPException(401, "Invalid or expired setup code")
            _event(db, "password_created")
            return _session(db, owner)

    @router.post("/login")
    def login(body: LoginInput):
        with connect() as db:
            owner = _reserve_attempt(db)
            if owner["password_hash"] is None:
                raise HTTPException(401, "Invalid local credentials")
            actual = _password_hash(body.password, owner["salt"])
            if not hmac.compare_digest(actual, owner["password_hash"]):
                raise HTTPException(401, "Invalid local credentials")
            return _session(db, owner)

    def session_claims(request: Request):
        from .auth import AuthError, AuthUnavailable, extract_access_token
        try:
            return verify(extract_access_token(request) or "")
        except AuthUnavailable:
            raise HTTPException(503, "Local authentication unavailable") from None
        except AuthError:
            raise HTTPException(401, "Local session expired; sign in again") from None

    @router.get("/session")
    def session(claims=Depends(session_claims)):
        return dict(user=dict(id=claims["sub"], email=claims.get("email")), expires_at=claims["exp"])

    @router.post("/logout")
    def logout(claims=Depends(session_claims)):
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (_digest(claims["jti"]),))
            _event(db, "signed_out")
        return dict(ok=True)

    return router
