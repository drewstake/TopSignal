import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, TimeoutError

from app.database_errors import install_database_error_handlers


@pytest.mark.parametrize("error", [
    TimeoutError("private connection details"),
    OperationalError("private SQL", {"password": "secret"}, RuntimeError("private database host")),
])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_database_failure_returns_readable_cors_response_without_retry(error, method, caplog):
    app = FastAPI()
    install_database_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:4173"])
    calls = []

    @app.api_route("/api/probe", methods=["GET", "POST"])
    def probe():
        calls.append(True)
        raise error

    response = TestClient(app).request(method, "/api/probe", headers={"Origin": "http://127.0.0.1:4173"})
    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:4173"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "5"
    assert response.json()["code"] == "database_unavailable"
    assert "temporarily unavailable" in response.json()["detail"]
    assert len(calls) == 1
    assert "private" not in response.text + caplog.text
    assert "secret" not in response.text + caplog.text


def test_database_error_does_not_grant_cors_to_untrusted_origin():
    app = FastAPI()
    install_database_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:4173"])

    @app.get("/api/probe")
    def probe():
        raise TimeoutError("unavailable")

    response = TestClient(app).get("/api/probe", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 503
    assert "access-control-allow-origin" not in response.headers
