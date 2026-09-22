"""One-shot MNQ bracket orders with durable claims and broker readback."""
import math
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from time import monotonic

from sqlalchemy.orm import Session
from sqlalchemy import or_

from ..manual_order_schemas import ManualOrderTestIn
from ..models import BotConfig, BotOrderAttempt, BotRun
from .bot_execution_safety import live_execution_environment_enabled, running_under_tests
from .bot_service import (
    _ProviderMutationBlocked, _ProviderMutationFence, _require_owned_account,
    _require_projectx_trade_data_source, _submit_order_attempt,
)
from .projectx_accounts import serialize_account_main_mutation
from .projectx_client import ProjectXClient

MAX_STOP_RISK = 250.0


@contextmanager
def _serialize(db, user_id):
    with serialize_account_main_mutation(db, user_id=user_id):
        if db.get_bind().dialect.name == "sqlite":
            # Coordinate separate local API processes as well as in-process callers.
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield


def _require_enabled():
    if (not live_execution_environment_enabled() or running_under_tests() or
            os.getenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "").lower() not in {"true", "1", "yes", "on"}):
        raise ValueError("Live order routing is disabled by the server.")


def _owned(db, user_id, account_id):
    row = _require_owned_account(db, user_id=user_id, account_id=account_id, lock_for_update=True, refresh_from_database=True)
    _require_projectx_trade_data_source(row)
    if row.archived_at is not None:
        raise ValueError("Archived accounts cannot submit test orders.")
    return row


def _require_idle(db, user_id, account_id, *, own_attempt_id=None):
    if (db.query(BotConfig.id).filter(BotConfig.user_id == user_id, BotConfig.account_id == account_id,
                                    BotConfig.enabled.is_(True)).first() or
            db.query(BotRun.id).filter(BotRun.user_id == user_id, BotRun.account_id == account_id,
                                      BotRun.status == "running").first()):
        raise ValueError("Stop automation on this account before submitting manual test orders.")
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
    attempts = db.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == user_id,
        BotOrderAttempt.account_id == account_id, BotOrderAttempt.execution_mode == "live")
    for attempt in attempts.filter(or_(BotOrderAttempt.status.in_(["pending", "submission_unknown"]),
            (BotOrderAttempt.status == "submitted") & or_(BotOrderAttempt.updated_at >= cutoff,
                BotOrderAttempt.created_at >= cutoff, BotOrderAttempt.updated_at.is_(None)))).all():
        if attempt.id == own_attempt_id:
            continue
        timestamp = attempt.updated_at or attempt.created_at
        if timestamp is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if attempt.status in {"pending", "submission_unknown"} or timestamp is None or timestamp >= cutoff:
            raise ValueError("A previous order is pending, uncertain, or still settling. Check TopstepX before another test.")


def _contract(client):
    for row in client.search_contracts(search_text="MNQ", live=False):
        if row.get("active_contract") is True and re.fullmatch(r"CON\.F\.US\.MNQ\.[A-Z]\d{2}", str(row.get("id", ""))):
            for field in ("tick_size", "tick_value"):
                value = row.get(field)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                    raise ValueError("ProjectX did not return valid MNQ tick metadata.")
            return {key: row.get(key) for key in ("id", "name", "tick_size", "tick_value")}
    raise ValueError("No active MNQ contract is available from ProjectX.")


def _result(row):
    request = row.raw_request or {}
    return {"attempt_id": row.id, "request_id": row.correlation_id, "account_id": row.account_id,
        "contract_id": row.contract_id, "side": row.side, "quantity": int(row.size),
        "stop_loss_ticks": request.get("stopLossBracket", {}).get("ticks"),
        "take_profit_ticks": request.get("takeProfitBracket", {}).get("ticks"),
        "status": row.status, "provider_order_id": row.provider_order_id,
        "message": row.rejection_reason, "created_at": row.created_at}


def _require_same_request(existing, payload):
    if (existing.side != payload.side or int(existing.size) != payload.quantity or
            (existing.raw_request or {}).get("stopLossBracket", {}).get("ticks") != payload.stop_loss_ticks or
            (existing.raw_request or {}).get("takeProfitBracket", {}).get("ticks") != payload.take_profit_ticks):
        raise ValueError("This request ID was already used with different order settings.")


def _recover_existing(db, existing, client, user_id, account_id):
    if existing.status not in {"pending", "submission_unknown"}:
        return _result(existing)
    attempt_id, contract_id, side, size = existing.id, existing.contract_id, existing.side, int(existing.size)
    tag = (existing.raw_request or {}).get("customTag")
    start = existing.created_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    db.commit()
    # Recovery searches the original broker tag. It never resubmits an
    # ambiguous entry, even when the provider search returns no match.
    matches = [o for o in client.search_orders(account_id=account_id, start=start - timedelta(minutes=1))
               if o.get("custom_tag") == tag and o.get("account_id") == account_id and
               o.get("contract_id") == contract_id and
               o.get("raw_payload", {}).get("side") == (0 if side == "BUY" else 1) and
               o.get("raw_payload", {}).get("size") == size]
    with _serialize(db, user_id):
        row = db.query(BotOrderAttempt).filter(BotOrderAttempt.id == attempt_id,
            BotOrderAttempt.user_id == user_id, BotOrderAttempt.account_id == account_id).populate_existing().one()
        if row.status in {"pending", "submission_unknown"} and len(matches) == 1:
            order = matches[0]
            if order.get("order_id") and order.get("status") in {1, 2, 3, 4, 5}:
                row.provider_order_id = str(order["order_id"])
                row.status = "error" if order["status"] == 5 else "submitted"
                row.rejection_reason = "ProjectX reports the original order was rejected." if row.status == "error" else None
                row.raw_response = {"reconciled": True, "order": order.get("raw_payload")}
        db.commit()
        return _result(row)


def _find_request(db, user_id, account_id, request_id):
    return db.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == user_id,
        BotOrderAttempt.account_id == account_id,
        BotOrderAttempt.idempotency_key == f"manual-test:{request_id}").one_or_none()


def submit_manual_order_test(db: Session, *, user_id: str, account_id: int,
                             payload: ManualOrderTestIn, client: ProjectXClient):
    request_id = str(payload.request_id)
    _owned(db, user_id, account_id)
    existing = _find_request(db, user_id, account_id, request_id)
    if existing:
        _require_same_request(existing, payload)
        return _recover_existing(db, existing, client, user_id, account_id)
    _require_enabled()
    _require_idle(db, user_id, account_id)
    db.commit()

    contract = _contract(client)
    if payload.quantity * payload.stop_loss_ticks * contract["tick_value"] > MAX_STOP_RISK:
        raise ValueError("Manual tests allow at most $250 of planned stop risk, excluding fees and slippage.")
    provider_account = next((a for a in client.list_accounts(only_active_accounts=False) if a["id"] == account_id), None)
    if not provider_account or provider_account.get("can_trade") is not True or provider_account.get("is_visible") is not True:
        raise ValueError("ProjectX has not confirmed this account is active and tradable.")
    if provider_account.get("simulated") is not True:
        raise ValueError("Manual test orders require a provider-confirmed simulated account.")
    deadline = monotonic() + 10
    if client.search_open_positions(account_id=account_id) or client.search_open_orders(account_id=account_id):
        raise ValueError("This account already has a position or working orders. Close or cancel them before a new test.")
    with _serialize(db, user_id):
        row = _owned(db, user_id, account_id)
        existing = _find_request(db, user_id, account_id, request_id)
        if existing:
            _require_same_request(existing, payload)
            result = _result(existing)
            db.commit()
            return result
        _require_idle(db, user_id, account_id)
        row.provider_simulated = True
        row.provider_classification_observed_at = datetime.now(timezone.utc)
        row.can_trade = True
        row.is_visible = True
        row.account_state = "ACTIVE"
        db.flush()
        _ProviderMutationFence(db=db, token=None, user_id=user_id, account_id=account_id,
                               preflight_deadline=deadline).require()
        attempt = BotOrderAttempt(user_id=user_id, account_id=account_id, contract_id=contract["id"],
            execution_mode="live", correlation_id=request_id, idempotency_key=f"manual-test:{request_id}",
            side=payload.side, size=payload.quantity, order_type="market", status="pending",
            raw_request={"accountId": account_id, "contractId": contract["id"], "type": 2,
                "side": 0 if payload.side == "BUY" else 1, "size": payload.quantity,
                "customTag": f"ts-manual-{request_id}",
                "stopLossBracket": {"ticks": payload.stop_loss_ticks, "type": 4},
                "takeProfitBracket": {"ticks": payload.take_profit_ticks, "type": 1}})
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id
        db.commit()  # Durable pending claim precedes the broker request; crashes cannot silently retry.

    with _serialize(db, user_id):
        attempt = db.get(BotOrderAttempt, attempt_id)
        try:
            _require_enabled()
            _require_idle(db, user_id, account_id, own_attempt_id=attempt_id)
            _ProviderMutationFence(db=db, token=None, user_id=user_id, account_id=account_id,
                                   preflight_deadline=deadline).require()
        except (ValueError, _ProviderMutationBlocked) as exc:
            attempt.status = "blocked"
            attempt.rejection_reason = exc.block.message if isinstance(exc, _ProviderMutationBlocked) else str(exc)
        else:
            _submit_order_attempt(client=client, order_attempt=attempt)
        db.commit()
        return _result(attempt)


def _with_broker_lifecycle(result, history, positions, *, latest):
    """Keep the submission audit intact; report execution from matched broker orders."""
    order_id = result["provider_order_id"]
    if result["status"] != "submitted" or not order_id:
        return result
    matching = [o for o in history if o.get("account_id") == result["account_id"]
                and o.get("contract_id") == result["contract_id"]]
    entry = next((o for o in matching if str(o.get("order_id")) == str(order_id)), None)
    if entry is None:
        return result
    raw = entry.get("raw_payload") or {}
    if raw.get("side") != (0 if result["side"] == "BUY" else 1):
        return result
    state = {1: "working", 3: "cancelled", 4: "expired", 5: "rejected"}.get(entry.get("status"))
    filled = raw.get("fillVolume") or 0
    if isinstance(filled, (int, float)) and filled > 0:
        state = "filled" if filled >= result["quantity"] else "partially_filled"
        result["entry_fill_price"] = raw.get("filledPrice")
        children = [o.get("raw_payload") or {} for o in matching
                    if str((o.get("raw_payload") or {}).get("parentOrderId")) == str(order_id)
                    and (o.get("raw_payload") or {}).get("side") == (1 if result["side"] == "BUY" else 0)]
        exits = [o for o in children if isinstance(o.get("fillVolume"), (int, float)) and o["fillVolume"] > 0]
        exit_volume = sum(o["fillVolume"] for o in exits)
        if exit_volume >= result["quantity"] and filled >= result["quantity"]:
            types = {o.get("type") for o in exits}
            state = "take_profit_hit" if types == {1} else "stop_loss_hit" if types == {4} else "closed"
            if all(isinstance(o.get("filledPrice"), (int, float)) for o in exits):
                result["exit_fill_price"] = sum(o["filledPrice"] * o["fillVolume"] for o in exits) / exit_volume
        elif exit_volume:
            state = "partially_closed"
        elif latest and not any(p.get("contract_id") == result["contract_id"] for p in positions):
            state = "flat_exit_unconfirmed"
    if state:
        result["execution_status"] = state
    return result


def get_manual_order_test_state(db: Session, *, user_id: str, account_id: int, client: ProjectXClient):
    _owned(db, user_id, account_id)
    attempts = db.query(BotOrderAttempt).filter(BotOrderAttempt.user_id == user_id,
        BotOrderAttempt.account_id == account_id, BotOrderAttempt.idempotency_key.like("manual-test:%")) \
        .order_by(BotOrderAttempt.id.desc()).limit(10).all()
    recent = [_result(row) for row in attempts]
    db.commit()
    contract = _contract(client)
    positions = client.search_open_positions(account_id=account_id)
    orders = client.search_open_orders(account_id=account_id)
    if any(row.get("account_id") != account_id for row in [*positions, *orders]):
        raise ValueError("ProjectX returned data for a different account.")
    submitted = [a for a in recent if a["status"] == "submitted" and a["provider_order_id"]]
    if submitted:
        start = min(a["created_at"].replace(tzinfo=timezone.utc) if a["created_at"].tzinfo is None
                    else a["created_at"] for a in submitted)
        history = client.search_orders(account_id=account_id, start=start - timedelta(minutes=1))
        recent = [_with_broker_lifecycle(a, history, positions, latest=index == 0)
                  for index, a in enumerate(recent)]
    return {"account_id": account_id, "contract": contract, "max_stop_risk": MAX_STOP_RISK,
        "positions": [{k: p.get(k) for k in ("id", "contract_id", "type", "size", "average_price")} for p in positions],
        "orders": [{**{k: o.get(k) for k in ("order_id", "contract_id", "order_type", "side", "size", "parent_order_id")},
                    "limit_price": o.get("raw_payload", {}).get("limitPrice"),
                    "stop_price": o.get("raw_payload", {}).get("stopPrice")} for o in orders],
        "recent_attempts": recent}
