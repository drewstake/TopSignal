from datetime import datetime, timezone
import json
import os
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services import system_logs as logs


def test_history_is_tenant_scoped_and_never_returns_raw_log_messages(tmp_path, monkeypatch):
    path = tmp_path / "bot-diagnostics.jsonl"
    monkeypatch.setattr(logs, "diagnostic_path", lambda: path)
    timestamp = datetime.now(timezone.utc).isoformat()
    rows = [
        dict(timestamp=timestamp, user_id="owner", event="projectx_feed_state", state="reconnecting", stream_kind="depth", message="secret-key"),
        dict(timestamp=timestamp, user_id="other", event="projectx_feed_state", state="unavailable", stream_kind="depth"),
        dict(timestamp=timestamp, event="projectx_feed_state", state="unavailable"),
        dict(timestamp=timestamp, user_id="owner", event="raw-secret", state="unavailable"),
        dict(timestamp=timestamp, user_id="owner", event="projectx_feed_state", state={"bad": "input"}),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + '\n{"partial":')
    (tmp_path / "supervisor.log").write_text(
        f"{timestamp} Starting TopSignal backend on 127.0.0.1:8000 and control UI on 127.0.0.1:4173.\n"
        f"{timestamp} credential failure secret-password\n"
        f"{timestamp} Liveness probe failed (1/3).\n"
    )
    result = logs.read_system_logs(user_id="owner")
    assert {row["event"] for row in result["entries"]} == {"host_start", "backend_probe_failed", "reconnecting"}
    assert "secret" not in json.dumps(result)
    assert "user_id" not in json.dumps(result)
    assert result["sources"] == {"host": "available", "market_feed": "available"}


def test_rotated_history_is_bounded_and_missing_sources_are_not_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "diagnostic_path", lambda: tmp_path / "bot-diagnostics.jsonl")
    empty = logs.read_system_logs(user_id="owner")
    assert empty["sources"]["host"] == "not_configured"
    assert empty["sources"]["market_feed"] == "not_configured"
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp} Liveness probe failed (1/3).\n"
    (tmp_path / "supervisor.log.1").write_text("x" * 1000 + "\n" + line)
    monkeypatch.setattr(logs, "SUPERVISOR_BYTES", 200)
    result = logs.read_system_logs(user_id="owner")
    assert result["scan_limited"]
    assert len(result["entries"]) == 1


def test_logs_route_requires_authentication_and_does_not_need_database(monkeypatch):
    from app import main
    from fastapi import Response
    from starlette.requests import Request
    import asyncio
    monkeypatch.setattr(main, "auth_required", lambda: True)
    request = Request({"type": "http", "method": "GET", "path": "/api/system/logs", "query_string": b"", "headers": []})
    async def next_handler(_request):
        raise AssertionError("Unauthenticated request reached log reader")
    response = asyncio.run(main.api_auth_middleware(request, next_handler))
    assert response.status_code == 401
    monkeypatch.setattr(logs, "read_system_logs", lambda **kwargs: {"entries": []})
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: "owner")
    monkeypatch.setattr(main, "_bot_worker_runtime", None)
    response = Response()
    result = main.get_system_logs(response, hours=24)
    assert response.headers["Cache-Control"] == "no-store"
    assert result["worker"]["healthy"] is False


def test_feed_state_logging_deduplicates_and_scopes_transitions(caplog):
    import logging
    from types import SimpleNamespace
    from app.services.projectx_order_book import ProjectXMarketDepthSession
    session = ProjectXMarketDepthSession.__new__(ProjectXMarketDepthSession)
    session._observation_user_id = "owner"
    session._subscribe_target = "SubscribeContractMarketDepth"
    session._state_for_market = lambda contract, state, **kwargs: {"state": state}
    session._broadcast_to_channel_locked = lambda *args: None
    channel = SimpleNamespace(last_state=None)
    with caplog.at_level(logging.INFO):
        session._publish_state_locked("MNQ", channel, "reconnecting")
        session._publish_state_locked("MNQ", channel, "reconnecting")
        session._publish_state_locked("MNQ", channel, "connected")
    events = [json.loads(record.getMessage()) for record in caplog.records if "projectx_feed_state" in record.getMessage()]
    assert [event["state"] for event in events] == ["reconnecting", "connected"]
    assert all(event["user_id"] == "owner" and event["stream_kind"] == "depth" for event in events)
