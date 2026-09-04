from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


EvaluationStatus = Literal[
    "evaluated",
    "held",
    "risk_blocked",
    "duplicate_skipped",
    "dry_run_attempt",
    "submitted",
    "error",
]

TERMINAL_RUN_STATUSES = frozenset({"stopped", "blocked", "error"})
VALID_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "running": TERMINAL_RUN_STATUSES,
    "stopped": frozenset(),
    "blocked": frozenset(),
    "error": frozenset(),
}

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SECRET_VALUE_PATTERN = re.compile(
    r'''(?ix)
    ["']?
    (api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret|token|jwt)
    ["']?\s*[:=]\s*
    (?:
        "(?:\\.|[^"\\])*"
        | '(?:\\.|[^'\\])*'
        | bearer\s+[^\s,;}\]]+
        | [^\s,;}\]]+
    )
    '''
)
_BEARER_VALUE_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


class InvalidBotRunTransition(ValueError):
    pass


def new_correlation_id() -> str:
    """Return a request-scoped, non-secret identifier suitable for logs and audit rows."""

    return str(uuid4())


def effective_dry_run(*, requested_dry_run: bool | None) -> bool:
    """Live routing is opt-in per request; omission always resolves to dry-run."""

    return requested_dry_run is not False


def build_action_idempotency_key(
    *,
    user_id: str,
    bot_config_id: int,
    candle_timestamp: datetime,
    action: str,
    execution_mode: str,
) -> str:
    normalized_action = str(action).strip().upper()
    if normalized_action not in {"BUY", "SELL"}:
        raise ValueError("Only BUY and SELL signals can receive an actionable idempotency key.")
    normalized_mode = str(execution_mode).strip().lower()
    if normalized_mode not in {"dry_run", "live"}:
        raise ValueError("execution_mode must be dry_run or live")

    timestamp = _as_utc(candle_timestamp)
    canonical = "|".join(
        [
            "v1",
            str(user_id),
            str(int(bot_config_id)),
            timestamp.isoformat(timespec="microseconds"),
            normalized_action,
            normalized_mode,
        ]
    )
    return f"v1:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def provider_custom_tag(*, bot_config_id: int, idempotency_key: str) -> str:
    """Build a deterministic reconciliation tag without exposing user or account data."""

    digest = str(idempotency_key).removeprefix("v1:")
    return f"topsignal-{int(bot_config_id)}-{digest[:24]}"


def transition_bot_run(
    run: Any,
    target_status: str,
    *,
    reason: str | None = None,
    error: BaseException | str | None = None,
    now: datetime | None = None,
) -> Any:
    current_status = str(run.status)
    normalized_target = str(target_status).strip().lower()
    if normalized_target == current_status:
        return run
    if normalized_target not in VALID_RUN_TRANSITIONS.get(current_status, frozenset()):
        raise InvalidBotRunTransition(f"Invalid bot run transition: {current_status} -> {normalized_target}")

    changed_at = _as_utc(now or datetime.now(timezone.utc))
    run.status = normalized_target
    run.stopped_at = changed_at
    run.stop_reason = reason or f"transitioned_to_{normalized_target}"
    run.last_heartbeat_at = changed_at
    if normalized_target == "error":
        run.last_error = sanitize_error(error or reason or "Bot evaluation failed.")
    return run


def touch_bot_run(run: Any, *, candle_timestamp: datetime | None = None, now: datetime | None = None) -> Any:
    if str(run.status) != "running":
        raise InvalidBotRunTransition(f"Cannot heartbeat bot run in terminal state {run.status}.")
    changed_at = _as_utc(now or datetime.now(timezone.utc))
    run.last_heartbeat_at = changed_at
    run.last_evaluated_at = changed_at
    state = dict(run.raw_state) if isinstance(run.raw_state, dict) else {}
    if candle_timestamp is not None:
        state["last_closed_candle_at"] = _as_utc(candle_timestamp).isoformat()
    run.raw_state = state
    return run


def live_execution_environment_enabled() -> bool:
    return os.getenv("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "").strip().lower() in _TRUE_VALUES


def running_under_tests() -> bool:
    environment = os.getenv("TOPSIGNAL_ENV", "").strip().lower()
    return bool(os.getenv("PYTEST_CURRENT_TEST")) or environment in {"test", "testing"}


def sanitize_error(error: BaseException | str, *, max_length: int = 500) -> str:
    if isinstance(error, BaseException):
        message = f"{type(error).__name__}: {error}"
    else:
        message = str(error)
    redacted = _SECRET_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", message)
    redacted = _BEARER_VALUE_PATTERN.sub("Bearer [REDACTED]", redacted)
    compact = " ".join(redacted.split())
    return compact[:max_length] or "Bot evaluation failed."


def log_bot_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit allow-listed, structured JSON without strategy/provider payloads or credentials."""

    safe_fields = {
        key: value
        for key, value in fields.items()
        if key
        in {
            "user_id",
            "bot_config_id",
            "bot_run_id",
            "account_id",
            "correlation_id",
            "idempotency_key",
            "execution_mode",
            "action",
            "evaluation_status",
            "order_attempt_id",
            "duplicate_of_order_attempt_id",
            "error_type",
        }
        and value is not None
    }
    logger.info(
        "%s",
        json.dumps({"event": str(event), **safe_fields}, sort_keys=True, default=str),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
