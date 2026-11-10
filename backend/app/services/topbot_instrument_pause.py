"""Durable entry-only pauses after a definite temporary ProjectX rejection.

The rejected attempt is never retried. Recovery requires a new depth episode,
then the ordinary account, session, risk and fenced submission checks. Position
management and read-only provider probes remain independent of this pause.
"""
from datetime import datetime, timedelta, timezone

from .projectx_client import PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE


def snapshot(run):
    state = run.raw_state if run is not None and isinstance(run.raw_state, dict) else {}
    value = state.get("instrument_pause")
    return dict(value) if isinstance(value, dict) else None


def pause_after_rejection(run, attempt, *, now):
    response = attempt.raw_response or {}
    if (attempt.status != "error" or not isinstance(response, dict)
            or response.get("reason_code") != PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE):
        return False
    prior = snapshot(run) or {}
    count = min(int(prior.get("rejections", 0)) + 1, 4)
    delay = min(60 * 2 ** (count - 1), 300)
    state = dict(run.raw_state or {})
    state["instrument_pause"] = {
        "contract_id": attempt.contract_id,
        "reason_code": PROJECTX_ERROR_INSTRUMENT_NOT_TRADABLE,
        "rejected_at": now.isoformat(),
        "retry_at": (now + timedelta(seconds=delay)).isoformat(),
        "rejections": count,
        "order_attempt_id": attempt.id,
    }
    state["phase"] = "instrument_wait"
    run.raw_state = state
    return True


def entry_pause_reason(pause, *, contract_id, now, observation=None):
    if pause is None:
        return None
    try:
        retry_at = datetime.fromisoformat(pause["retry_at"])
        rejected_at = datetime.fromisoformat(pause["rejected_at"])
        if retry_at.tzinfo is None or rejected_at.tzinfo is None:
            raise ValueError("Missing timezone")
        if pause["contract_id"] != contract_id:
            return "instrument_pause_contract_changed"
        if now.astimezone(timezone.utc) < retry_at:
            return "instrument_not_tradable_retry_wait"
        if observation is not None:
            episode = datetime.fromisoformat(observation.get("episode_at") or "")
            if episode.tzinfo is None or episode <= rejected_at:
                return "instrument_not_tradable_waiting_for_fresh_setup"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "instrument_not_tradable_waiting_for_fresh_setup"
    return None


def clear_pause(run):
    if snapshot(run) is not None:
        state = dict(run.raw_state)
        state.pop("instrument_pause", None)
        run.raw_state = state
