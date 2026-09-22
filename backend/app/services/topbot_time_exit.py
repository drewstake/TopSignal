"""Durable, contract-scoped exits for experimental Practice orders.

The obligation is attached to the entry audit before submission. It survives
stopping/restarting a run and does not depend on candles or model availability.
The deadline starts at submission preparation (conservatively before the fill).
"""
from datetime import datetime, timedelta, timezone

from ..models import BotOrderAttempt, BotRiskEvent
from .bot_execution_safety import live_execution_environment_enabled


def pending_exits(db):
    return db.query(BotOrderAttempt).filter(
        BotOrderAttempt.execution_mode == "live",
        BotOrderAttempt.raw_request["timeExit"]["status"].as_string() == "pending",
    )


def exit_plan(now=None):
    now = now or datetime.now(timezone.utc)
    return {"status": "pending", "deadline": (now + timedelta(minutes=15)).isoformat(),
            "horizon_minutes": 15, "attempts": 0}


def process_time_exit(db, *, entry_id, client, worker_lease_token, now=None):
    from . import bot_service as service

    now = service._as_utc(now or datetime.now(timezone.utc))
    candidate = pending_exits(db).filter(BotOrderAttempt.id == entry_id).one_or_none()
    if candidate is None:
        return True
    # Match the evaluator's config/account/audit lock order. A stopped config
    # still owns its outstanding exit; it need not be re-armed to reduce risk.
    service._require_bot_config(db, user_id=candidate.user_id,
                                bot_config_id=candidate.bot_config_id, lock_for_update=True)
    account = service._require_owned_account(
        db, user_id=candidate.user_id, account_id=candidate.account_id,
        lock_for_update=True, refresh_from_database=True,
    )
    entry = pending_exits(db).filter(BotOrderAttempt.id == entry_id).populate_existing().with_for_update().one_or_none()
    if entry is None:
        return True
    request = dict(entry.raw_request)
    plan = dict(request["timeExit"])

    def save():
        request["timeExit"] = plan
        entry.raw_request = dict(request)
        db.flush()

    # A rejected/blocked entry never created exposure. Unknown submissions must
    # be reconciled by the existing tagged-order reconciler before any close.
    if entry.status in {"rejected", "risk_blocked", "blocked", "cancelled", "error"}:
        plan.update(status="complete", outcome="entry_not_submitted")
        save()
        return True
    if entry.status != "submitted":
        return False
    deadline = service._as_utc(datetime.fromisoformat(plan["deadline"]))
    if now < deadline:
        return False
    if plan.get("retry_at") and now < service._as_utc(datetime.fromisoformat(plan["retry_at"])):
        return False
    if not live_execution_environment_enabled() or worker_lease_token is None:
        return False

    audit = service._BrokerActionAuditAttempt(raw_request={})
    prior_error = plan.get("last_error")
    plan.update(attempts=int(plan.get("attempts", 0)) + 1, last_attempt_at=now.isoformat())
    try:
        # Refresh only the owned account's authoritative classification. Never
        # infer Practice eligibility from its name or from a requested mode.
        accounts = client.list_accounts(only_active_accounts=False)
        provider = next((row for row in accounts if row.get("id") == entry.account_id), None)
        if provider is None:
            raise ValueError("Time exit requires verified simulated Practice classification.")
        simulated = provider.get("simulated")
        if isinstance(simulated, bool):
            account.provider_simulated = simulated
            account.provider_classification_observed_at = datetime.now(timezone.utc)
        elif service._fresh_cached_account_automation_eligibility(account) is not True:
            raise ValueError("Time exit requires a fresh Practice-account classification.")
        if simulated is False:
            raise ValueError("Time exit is blocked for non-simulated accounts.")
        if provider.get("can_trade") is not True or provider.get("status") != "ACTIVE":
            raise ValueError("ProjectX has not confirmed this account is active and tradable.")
        quantity = service._provider_contract_position_qty(
            client.search_open_positions(account_id=entry.account_id),
            account_id=entry.account_id, contract_id=entry.contract_id,
        )
        expected_sign = 1 if entry.side == "BUY" else -1
        if quantity * expected_sign < 0 or abs(quantity) > float(entry.size):
            raise ValueError("Position differs from the bot entry; manual reconciliation is required.")
        db.flush()
        fence = service._ProviderMutationFence(
            db=db, token=worker_lease_token, user_id=entry.user_id, account_id=entry.account_id,
        )
        fence.require()
        block = service._execute_verified_reduce_only_flatten(
            client=client, account_id=entry.account_id, contract_id=entry.contract_id,
            order_attempt=audit, before_provider_mutation=fence.require,
        )
        plan["audit"] = audit.raw_response
        if block is None:
            plan.update(status="complete", outcome="verified_flat", completed_at=now.isoformat())
            plan.pop("last_error", None)
            plan.pop("retry_at", None)
        else:
            plan["last_error"] = block.code
    except Exception as exc:
        # Leave the durable obligation pending and block new entries. Retry
        # re-reads broker state; it never blindly repeats an entry/close order.
        plan["last_error"] = service.sanitize_error(exc, max_length=180)
        plan["audit"] = audit.raw_response
    if plan.get("last_error"):
        plan["retry_at"] = (now + timedelta(seconds=min(300, 5 * 2 ** min(plan["attempts"], 6)))).isoformat()
    if plan.get("last_error") and prior_error is None:
        db.add(BotRiskEvent(
            user_id=entry.user_id, bot_config_id=entry.bot_config_id,
            bot_run_id=entry.bot_run_id, account_id=entry.account_id, severity="critical",
            code="mathematical_time_exit_pending",
            message="The 15-minute exit is not confirmed. New entries are blocked; check the position in ProjectX.",
            raw_payload={"entry_order_attempt_id": entry.id, "deadline": plan["deadline"],
                         "reason": plan["last_error"]},
        ))
    save()
    return plan["status"] == "complete"
