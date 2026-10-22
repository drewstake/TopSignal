import importlib.util
import json
import logging
import logging.config
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.bot_diagnostics import DiagnosticFileHandler, DiagnosticFormatter, event


def record(message="provider_failed", **extra):
    item = logging.LogRecord("app.bot_worker", logging.ERROR, __file__, 1, message, (), None)
    item.__dict__.update(extra)
    return item


def test_formatter_preserves_context_and_drops_payloads_and_secrets():
    item = record(bot_run_id=12, status_code=429, retry_delay_seconds=30,
                  reason="token=never-log-this https://user:pass@host/path?key=secret",
                  raw_response={"anything": "PRIVATE"}, api_key="PRIVATE",
                  price=float("nan"), risk_codes=["x"] * 200)
    text = DiagnosticFormatter().format(item)
    parsed = json.loads(text)
    assert parsed["bot_run_id"] == 12
    assert parsed["retry_delay_seconds"] == 30
    assert parsed["status_code"] == 429
    assert parsed["timestamp"].endswith("+00:00")
    assert parsed["process_session_id"]
    assert parsed["price"] is None
    assert len(parsed["risk_codes"]) == 32
    assert all(secret not in text for secret in ("never-log-this", "user:pass", "key=secret", "PRIVATE"))
    assert item.reason.startswith("token=never-log-this")  # Other handlers see the original.


def test_existing_json_events_are_flattened_and_single_line():
    payload = {"event": "bot_evaluation_completed", "correlation_id": "abc", "reason": "line one\nline two",
               "raw_request": "DO NOT COPY", "quantity": 1}
    text = DiagnosticFormatter().format(record(json.dumps(payload)))
    assert len(text.splitlines()) == 1
    parsed = json.loads(text)
    assert parsed["event"] == payload["event"]
    assert parsed["correlation_id"] == "abc"
    assert parsed["quantity"] == 1
    assert "DO NOT COPY" not in text


def test_exception_formatter_keeps_location_without_exception_text_or_locals():
    try:
        raise RuntimeError("unlabeled-private-provider-response")
    except RuntimeError:
        item = record(exc_info=sys.exc_info())
    parsed = json.loads(DiagnosticFormatter().format(item))
    assert parsed["error_type"] == "RuntimeError"
    assert parsed["frames"][-1]["file"] == Path(__file__).name
    assert "unlabeled-private" not in json.dumps(parsed)


def test_rotation_and_disk_failure_are_bounded_and_do_not_escape(tmp_path, monkeypatch, capsys):
    handler = DiagnosticFileHandler(tmp_path / "events.jsonl", maxBytes=800, backupCount=2)
    handler.setFormatter(DiagnosticFormatter())
    try:
        for _ in range(12):
            handler.emit(record(bot_run_id=12))
        files = list(tmp_path.glob("events.jsonl*"))
        assert len(files) == 3
        assert all(json.loads(line)["bot_run_id"] == 12 for path in files for line in path.read_text().splitlines())
        monkeypatch.setattr(handler, "shouldRollover", lambda _record: (_ for _ in ()).throw(OSError("PRIVATE")))
        handler.emit(record("PRIVATE"))
        handler.emit(record("PRIVATE"))
        output = capsys.readouterr().err
        assert output.count("diagnostic_log_write_failed") == 1
        assert "PRIVATE" not in output
    finally:
        handler.close()


def test_production_handler_works_with_existing_app_extra_fields(tmp_path):
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / "backend/logging.production.json").read_text())
    # Configure an isolated logger using the actual production handler/formatter.
    config["handlers"] = {"rotating_diagnostic_file": config["handlers"]["rotating_diagnostic_file"]}
    config["handlers"]["rotating_diagnostic_file"]["filename"] = str(tmp_path / "bot-diagnostics.jsonl")
    options = config["handlers"]["rotating_diagnostic_file"]
    assert options["maxBytes"] == 10 * 1024 * 1024
    assert options["backupCount"] == 30
    assert config["loggers"]["app"]["handlers"] == ["rotating_diagnostic_file"]
    # Avoid resetting pytest/root logging with a global dictConfig call.
    configurator = logging.config.DictConfigurator(config)
    configurator.config["formatters"]["diagnostic"] = DiagnosticFormatter()
    handler = configurator.configure_handler(options)
    logger = logging.Logger("isolated", logging.INFO)
    logger.addHandler(handler)
    try:
        logger.error("projectx_hub_dispatch_failed", extra={"stream_kind": "market", "reason_code": "dispatch_error"})
        event(logger, "bot_evaluation_completed", bot_run_id=1, correlation_id="corr", raw_response="PRIVATE")
    finally:
        handler.close()
    rows = [json.loads(line) for line in (tmp_path / "bot-diagnostics.jsonl").read_text().splitlines()]
    assert rows[0]["stream_kind"] == "market"
    assert rows[1]["correlation_id"] == "corr"
    assert "raw_response" not in rows[1]


def test_worker_health_logs_changes_immediately_and_steady_state_once_per_minute(monkeypatch, caplog):
    from app import bot_worker
    ticks = [100.0]
    monkeypatch.setattr(bot_worker, "monotonic", lambda: ticks[0])
    runtime = bot_worker.BotWorkerRuntime(session_factory=lambda: None, client_factory=lambda: None)
    caplog.set_level(logging.INFO, logger=bot_worker.logger.name)
    runtime._replace_snapshot(state="running", owns_lease=True)
    runtime._replace_snapshot(state="running")
    ticks[0] += 61
    runtime._replace_snapshot(state="running")
    runtime._replace_snapshot(provider_status="error", last_error_code="outage")
    rows = [json.loads(item.message) for item in caplog.records]
    assert [row["event"] for row in rows] == ["bot_worker_state_changed", "bot_worker_heartbeat", "bot_worker_state_changed"]
    assert rows[-1]["last_error_code"] == "outage"
    assert caplog.records[-1].levelno == logging.WARNING


def test_evaluation_failure_is_correlated_and_safe(monkeypatch, caplog):
    from app.services import bot_service
    from app.models import BotConfig
    config = BotConfig(id=123, account_id=12, strategy_type="sma_cross")
    def fail(*args, **kwargs):
        raise RuntimeError("unlabeled-private-provider-response")
    monkeypatch.setattr(bot_service, "_evaluate_bot_config_impl", fail)
    caplog.set_level(logging.INFO, logger=bot_service.logger.name)
    with pytest.raises(RuntimeError):
        bot_service.evaluate_bot_config(SimpleNamespace(), config=config, user_id="user", account=None,
                                        client=None, correlation_id="trace")
    rows = [json.loads(item.message) for item in caplog.records]
    assert rows[0]["event"] == "bot_evaluation_started"
    assert rows[-1]["event"] == "bot_evaluation_failed"
    assert rows[-1]["correlation_id"] == "trace"
    assert rows[-1]["duration_ms"] >= 0
    assert rows[-1]["error_frames"]
    assert "unlabeled-private" not in caplog.text


def test_summary_reads_rotations_filters_and_counts_truncated_lines(tmp_path):
    path = Path(__file__).resolve().parents[1] / "tools/summarize_bot_logs.py"
    spec = importlib.util.spec_from_file_location("summary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def row(name, **fields):
        return json.dumps({"event": name, "timestamp": "2026-10-21T12:00:00Z", "bot_run_id": 1, **fields}) + "\n"
    old, new = tmp_path / "bot-diagnostics.jsonl.1", tmp_path / "bot-diagnostics.jsonl"
    old.write_text(row("bot_evaluation_completed", evaluation_status="risk_blocked", risk_codes=["stale_data"]))
    new.write_text(row("bot_evaluation_timing", duration_ms=50) + row("bot_evaluation_timing", duration_ms=150)
                   + row("bot_evaluation_failed", level="ERROR", bot_run_id=2) + '{"truncated":')
    report = module.summarize([old, new], run_id=1)
    assert report["invalid_lines"] == 1
    assert report["records_matched"] == 3
    assert report["risk_blocks"] == {"stale_data": 1}
    assert report["timings"]["bot_evaluation_timing"] == {"count": 2, "mean_ms": 100, "max_ms": 150}
    assert report["errors"] == {}
