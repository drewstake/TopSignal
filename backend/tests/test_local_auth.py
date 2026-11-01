import os
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import sqlite3
import time
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import local_auth
from app.auth import AuthError, AuthUnavailable, authenticate_request_token

OWNER = "31cafcac-77cc-422c-a153-93e572e48c29"
PASSWORD = "a unique test passphrase 2026"


@pytest.fixture
def local(tmp_path, monkeypatch):
    path = tmp_path / "auth.sqlite3"
    code = local_auth.provision(path, OWNER, "owner@example.test")
    monkeypatch.setenv("TOPSIGNAL_LOCAL_AUTH_DB", str(path))
    app = FastAPI()
    app.include_router(local_auth.make_router(lambda origin: origin == "http://127.0.0.1:4173"))
    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 1234)) as client:
        yield client, code, path


def setup(local):
    client, code, _ = local
    response = client.post("/auth/local/setup", json={"password": PASSWORD, "setup_code": code})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_owner_identity_reload_login_logout_and_no_plaintext(local):
    client, code, path = local
    assert client.get("/auth/local/status").json()["needs_setup"] is True
    token = setup(local)
    assert authenticate_request_token(token).user_id == OWNER
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/local/session", headers=headers).json()["user"]["id"] == OWNER
    assert client.get("/auth/local/status").json()["needs_setup"] is False
    assert client.post("/auth/local/setup", json={"password": PASSWORD, "setup_code": code}).status_code == 401
    assert client.post("/auth/local/login", json={"password": "incorrect"}).status_code == 401
    assert client.post("/auth/local/login", json={"password": PASSWORD}).status_code == 200
    assert client.post("/auth/local/logout", headers=headers).status_code == 200
    assert client.get("/auth/local/session", headers=headers).status_code == 401
    with pytest.raises(AuthError): authenticate_request_token(token)
    raw = path.read_bytes()
    assert PASSWORD.encode() not in raw and code.encode() not in raw and token.encode() not in raw
    with sqlite3.connect(path) as db:
        events = [row[0] for row in db.execute("SELECT kind FROM events")]
    assert "password_created" in events and "signed_out" in events


@pytest.mark.parametrize("changes", [
    {"sub": "a55cb810-a510-4887-8232-53f8dbbe75db"},
    {"exp": 1}, {"iss": "supabase"}, {"aud": "authenticated"}, {"jti": "unknown"},
])
def test_rejects_invalid_claims_even_with_valid_signature(local, changes):
    token = setup(local)
    with local_auth.connect() as db:
        key = db.execute("SELECT signing_key FROM owner").fetchone()[0]
    claims = jwt.decode(token, options={"verify_signature": False})
    claims.update(changes)
    forged = jwt.encode(claims, key, algorithm="HS256", headers={"kid": local_auth.KID})
    with pytest.raises(AuthError): authenticate_request_token(forged)


def test_no_supabase_key_confusion_or_disabled_fallback(local, monkeypatch):
    token = setup(local)
    claims = jwt.decode(token, options={"verify_signature": False})
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "different-secret-that-is-at-least-32-bytes")
    forged = jwt.encode(claims, os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256", headers={"kid": local_auth.KID})
    with pytest.raises(AuthError): authenticate_request_token(forged)
    monkeypatch.delenv("TOPSIGNAL_LOCAL_AUTH_DB")
    with pytest.raises(AuthUnavailable): authenticate_request_token(token)
    assert local[0].get("/auth/local/status").status_code == 404


@pytest.mark.parametrize("headers", [
    {"Origin": "https://hostile.example"}, {"Origin": "null"},
    {"Host": "hostile.example"}, {"Sec-Fetch-Site": "cross-site"},
    {"Host": "hostile.example", "X-Forwarded-Host": "localhost"},
])
def test_login_rejects_untrusted_browser_requests(local, headers):
    assert local[0].get("/auth/local/status", headers=headers).status_code == 403
    assert local[0].post("/auth/local/login", headers=headers, json={"password": PASSWORD}).status_code == 403


def test_setup_limits_and_rate_limit_persist(local):
    client, code, path = local
    assert client.post("/auth/local/setup", json={"password": "short", "setup_code": code}).status_code == 422
    assert client.post("/auth/local/setup", json={"password": PASSWORD, "setup_code": code, "user_id": OWNER}).status_code == 422
    for _ in range(10):
        assert client.post("/auth/local/setup", json={"password": PASSWORD, "setup_code": "wrong"}).status_code == 401
    assert client.post("/auth/local/setup", json={"password": PASSWORD, "setup_code": code}).status_code == 429
    with sqlite3.connect(path) as db:
        db.execute("UPDATE owner SET attempt_start=?", (int(time.time()) - 301,))
    assert setup(local)


def test_expired_setup_and_no_overwrite(local):
    client, code, path = local
    with pytest.raises(FileExistsError): local_auth.provision(path, OWNER, None)
    with sqlite3.connect(path) as db: db.execute("UPDATE owner SET setup_expires=1")
    assert client.post("/auth/local/setup", json={"password": PASSWORD, "setup_code": code}).status_code == 401


def test_non_loopback_peer_rejected(local):
    app = FastAPI()
    app.include_router(local_auth.make_router(lambda origin: True))
    with TestClient(app, base_url="http://localhost", client=("192.0.2.1", 1234)) as client:
        assert client.get("/auth/local/status").status_code == 403


def test_real_api_middleware_preserves_owner_and_requires_auth(local, monkeypatch):
    from app.main import app
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    token = setup(local)
    # No lifespan: this tests the real middleware without starting a worker.
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 1234))
    assert client.get("/api/auth/me").status_code == 401
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == OWNER
    client.close()
