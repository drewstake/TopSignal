"""Bounded local diagnostics. Never serialize ORM objects or provider payloads."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import sys
import time
import traceback
from uuid import uuid4

from .production_logging import redact


PROCESS_SESSION_ID = str(uuid4())
# Explicit fields shared by event producers and the file formatter. Unknown
# extras (including request/response bodies, credentials and candles) are dropped.
SAFE_FIELDS = frozenset("""
user_id bot_config_id bot_run_id account_id correlation_id idempotency_key
execution_mode action evaluation_status order_attempt_id duplicate_of_order_attempt_id
error_type error_frames reason_code status_code phase stream_kind contract_id symbol strategy_type
config_hash decision_id candle_timestamp candle_close_timestamp candle_age_seconds
candle_count price quantity reason risk_codes order_status provider_order_id
duration_ms persistence worker_id state previous_state owns_lease provider_status
active_runs evaluated_runs retrying_runs unresolved_live_submissions last_error_code
last_cycle_completed_at last_success_at last_provider_success_at last_provider_check_at
retry_delay_seconds failure_count poll_seconds lease_ttl_seconds
stage self_ms calls failures db_queries db_query_ms db_connects db_connect_ms
""".split())


def _value(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        return redact(value)[:2000]
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value[:32] if not isinstance(item, (dict, list, tuple))]
    # No repr/str fallback: these can contain credentials or full candle arrays.
    return None


def safe_fields(fields):
    return {key: _value(value) for key, value in fields.items() if key in SAFE_FIELDS and value is not None}


def error_frames(error):
    """Locations only; never exception messages, source text or frame locals."""
    return [f"{os.path.basename(frame.f_code.co_filename)}:{line}:{frame.f_code.co_name}"
            for frame, line in traceback.walk_tb(error.__traceback__)][-12:]


def event(logger, name, *, level=logging.INFO, **fields):
    """Emit compatible JSON to ordinary logs and fields to the JSONL formatter."""
    payload = {"event": name, **safe_fields(fields)}
    logger.log(level, "%s", json.dumps(payload, allow_nan=False, sort_keys=True))


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        message = record.getMessage()
        payload = None
        if message.startswith("{"):
            try:
                candidate = json.loads(message)
                if isinstance(candidate, dict) and isinstance(candidate.get("event"), str):
                    payload = candidate
            except (ValueError, TypeError):
                pass
        result = {
            "schema_version": 1,
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "process_id": os.getpid(),
            "process_session_id": PROCESS_SESSION_ID,
            "event": redact(payload["event"] if payload else message)[:2000],
            **safe_fields(record.__dict__),
            **safe_fields(payload or {}),
        }
        if record.exc_info and record.exc_info[0]:
            # Locations are useful; exception text, source lines and locals can
            # contain SQL parameters or provider secrets, so never include them.
            result["error_type"] = record.exc_info[0].__name__
            result["frames"] = [
                {"file": os.path.basename(frame.filename), "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])[-12:]
            ]
        return json.dumps(result, ensure_ascii=True, allow_nan=False)


class DiagnosticFileHandler(RotatingFileHandler):
    """Keep I/O failures out of trading control flow without dumping raw records."""
    def handleError(self, record):
        now = time.monotonic()
        if now - getattr(self, "_last_failure_notice", float("-inf")) >= 60:
            self._last_failure_notice = now
            try:
                sys.stderr.write("TopSignal diagnostic_log_write_failed; check log permissions and free disk space.\n")
            except Exception:
                pass
