import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import Account, BotConfig, BotRun
from app.services import bot_log_reader as reader


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index, user, account in [(1, "a", 101), (2, "b", 101), (3, "a", 202)]:
            session.add(Account(user_id=user, external_id=str(account), provider="projectx", name="Fixture"))
            session.add(BotConfig(id=index, user_id=user, account_id=account, name=f"Fixture {index}", contract_id="MNQ"))
            session.add(BotRun(id=index, user_id=user, account_id=account, bot_config_id=index, status="running", dry_run=True))
        session.commit()
        yield session
    engine.dispose()


def row(**fields):
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "level": "INFO", "event": "bot_evaluation_completed", **fields}


def write_logs(tmp_path, monkeypatch, rows):
    path = tmp_path / "bot-diagnostics.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(reader, "diagnostic_path", lambda: path)
    return path


def test_tenant_and_account_isolation_even_for_shared_broker_id(db, tmp_path, monkeypatch):
    write_logs(tmp_path, monkeypatch, [
        row(bot_config_id=1, bot_run_id=1, account_id=101, correlation_id="own"),
        row(bot_run_id=1, event="bot_worker_run_failed", level="ERROR"),
        row(user_id="a", account_id=101, event="projectx_trade_sync_failed"),
        row(bot_config_id=2, account_id=101, reason="foreign config"),
        row(bot_run_id=2, reason="foreign run"),
        row(user_id="b", account_id=101, reason="foreign explicit owner"),
        row(bot_config_id=1, user_id="b", account_id=101, reason="conflicting owner"),
        row(bot_config_id=3, account_id=202, reason="other account"),
        row(bot_config_id=1, account_id=202, reason="conflicting account"),
        row(account_id=101, reason="unproven ownership"),
        row(event="bot_worker_heartbeat", active_runs=99),
    ])
    result = reader.read_bot_logs(db, user_id="a", account_id=101)
    assert len(result["entries"]) == 3
    assert result["summary"]["errors"] == 1
    assert all("user_id" not in item for item in result["entries"])
    assert "foreign" not in json.dumps(result)
    assert "other account" not in json.dumps(result)
    assert "heartbeat" not in json.dumps(result)


def test_filters_summary_and_detail_limit_use_same_scoped_records(db, tmp_path, monkeypatch):
    path = write_logs(tmp_path, monkeypatch, [
        row(bot_run_id=1, evaluation_status="risk_blocked", risk_codes=["stale_data"], correlation_id="trace"),
        row(bot_run_id=1, event="bot_evaluation_timing", duration_ms=10, correlation_id="trace"),
        row(bot_run_id=1, event="bot_evaluation_timing", duration_ms=30, correlation_id="trace"),
        row(bot_run_id=1, timestamp=(datetime.now(timezone.utc) - timedelta(days=9)).isoformat(), correlation_id="trace"),
    ])
    path.with_name(path.name + ".1").write_text(json.dumps(row(bot_run_id=1, level="ERROR", event="bot_evaluation_failed", correlation_id="trace")))
    result = reader.read_bot_logs(db, user_id="a", account_id=101, run_id=1, search="trace", limit=1)
    assert result["summary"]["matched_events"] == 4
    assert len(result["entries"]) == 1 and result["has_more"]
    assert result["summary"]["risk_blocks"] == {"stale_data": 1}
    assert result["summary"]["timings"]["bot_evaluation_timing"] == {"count": 2, "mean_ms": 20, "max_ms": 30}
    assert reader.read_bot_logs(db, user_id="a", account_id=101, level="ERROR")["summary"]["matched_events"] == 1
    assert reader.read_bot_logs(db, user_id="a", account_id=101, run_id=2)["entries"] == []


def test_public_output_is_allowlisted_and_redacted_again(db, tmp_path, monkeypatch):
    write_logs(tmp_path, monkeypatch, [row(bot_run_id=1, raw_response="SECRET", token="SECRET",
        reason="api_key=SECRET", error_frames=["worker.py:1:run"], frames=[{"locals": "SECRET"}]),
        row(bot_run_id=1, event="Freeform SECRET event text")])
    result = reader.read_bot_logs(db, user_id="a", account_id=101)
    assert "SECRET" not in json.dumps(result)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["error_frames"] == ["worker.py:1:run"]


def test_missing_files_malformed_lines_and_scan_cap(db, tmp_path, monkeypatch):
    path = write_logs(tmp_path, monkeypatch, [])
    path.unlink()
    assert reader.read_bot_logs(db, user_id="a", account_id=101)["status"] == "not_configured"
    path.write_bytes(b"broken\n" + (json.dumps(row(bot_run_id=1)) + "\n").encode() * 20 + b'{"incomplete":')
    monkeypatch.setattr(reader, "MAX_SCAN_BYTES", 600)
    result = reader.read_bot_logs(db, user_id="a", account_id=101)
    assert result["scan_limited"] is True
    assert 0 < len(result["entries"]) < 20


def test_read_permission_error_is_visible_without_server_path(db, tmp_path, monkeypatch):
    write_logs(tmp_path, monkeypatch, [])
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("private-server-path")))
    result = reader.read_bot_logs(db, user_id="a", account_id=101)
    assert result["status"] == "read_error"
    assert "private-server-path" not in json.dumps(result)


def test_route_checks_account_ownership_before_reading_and_disables_caching(db, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: "b")
    calls = []
    monkeypatch.setattr(reader, "read_bot_logs", lambda *_args, **kwargs: calls.append(kwargs) or {})
    with pytest.raises(HTTPException) as failure:
        main.get_account_bot_logs(202, Response(), db=db)
    assert failure.value.status_code == 404
    assert calls == []
    response = Response()
    main.get_account_bot_logs(101, response, db=db, hours=24, level=None, run_id=None, search="", limit=100)
    assert calls[0]["user_id"] == "b"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("query", ["hours=0", "hours=169", "limit=201", "limit=0", "run_id=-1", "level=ALL", "search=" + "x" * 101])
def test_http_query_limits_reject_unbounded_reads(db, monkeypatch, query):
    from fastapi import FastAPI
    import app.main as main
    application = FastAPI()
    application.get("/api/accounts/{account_id}/bot-logs")(main.get_account_bot_logs)
    application.dependency_overrides[main.get_db] = lambda: db
    monkeypatch.setattr(reader, "read_bot_logs", lambda *_args, **_kwargs: pytest.fail("invalid filters must not scan files"))
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/api/accounts/101/bot-logs", "query_string": query.encode(), "headers": [],
             "client": ("127.0.0.1", 123), "server": ("test", 80)}
    asyncio.run(application(scope, receive, send))
    assert next(message["status"] for message in messages if message["type"] == "http.response.start") == 422


def test_logs_endpoint_requires_authentication(monkeypatch):
    from starlette.requests import Request
    import app.main as main
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    request = Request({"type": "http", "method": "GET", "path": "/api/accounts/101/bot-logs",
                       "query_string": b"", "headers": []})
    async def next_handler(_request):
        pytest.fail("unauthenticated request must never reach the log reader")
    assert asyncio.run(main.api_auth_middleware(request, next_handler)).status_code == 401
