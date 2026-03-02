from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..auth import get_authenticated_user_id
from ..models import Account

ACCOUNT_PROVIDER = "projectx"

ACCOUNT_STATE_ACTIVE = "ACTIVE"
ACCOUNT_STATE_LOCKED_OUT = "LOCKED_OUT"
ACCOUNT_STATE_HIDDEN = "HIDDEN"
ACCOUNT_STATE_MISSING = "MISSING"
ACCOUNT_STATE_INACTIVE = {ACCOUNT_STATE_LOCKED_OUT, ACCOUNT_STATE_HIDDEN}


def _resolve_user_id(user_id: str | None) -> str:
    if user_id:
        return user_id
    return get_authenticated_user_id()


def account_state_from_flags(*, can_trade: bool | None, is_visible: bool | None) -> str:
    if is_visible is False:
        return ACCOUNT_STATE_HIDDEN
    if can_trade is False:
        return ACCOUNT_STATE_LOCKED_OUT
    return ACCOUNT_STATE_ACTIVE


def sync_projectx_accounts(
    db: Session,
    provider_accounts: list[dict[str, Any]],
    *,
    user_id: str | None = None,
    now_utc: datetime | None = None,
    missing_buffer: timedelta = timedelta(minutes=5),
) -> None:
    resolved_user_id = _resolve_user_id(user_id)
    now = _as_utc(now_utc or datetime.now(timezone.utc))
    normalized_rows = [_normalize_provider_account(row) for row in provider_accounts]
    normalized_rows = [row for row in normalized_rows if row is not None]

    seen_external_ids = {row["external_id"] for row in normalized_rows}
    existing_by_external_id: dict[str, Account] = {}
    if seen_external_ids:
        rows = (
            db.query(Account)
            .filter(Account.user_id == resolved_user_id)
            .filter(Account.provider == ACCOUNT_PROVIDER)
            .filter(Account.external_id.in_(sorted(seen_external_ids)))
            .all()
        )
        existing_by_external_id = {row.external_id: row for row in rows}

    for payload in normalized_rows:
        external_id = payload["external_id"]
        row = existing_by_external_id.get(external_id)
        if row is None:
            row = Account(
                user_id=resolved_user_id,
                provider=ACCOUNT_PROVIDER,
                external_id=external_id,
            )
            db.add(row)
            existing_by_external_id[external_id] = row

        row.name = payload["name"]
        row.account_state = payload["account_state"]
        row.can_trade = payload["can_trade"]
        row.is_visible = payload["is_visible"]
        row.last_seen_at = now
        if row.first_seen_at is None:
            row.first_seen_at = now
        if row.account_state != ACCOUNT_STATE_MISSING:
            row.last_missing_at = None

    missing_query = (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
    )
    if seen_external_ids:
        missing_query = missing_query.filter(~Account.external_id.in_(sorted(seen_external_ids)))

    for row in missing_query.all():
        if row.last_seen_at is None:
            continue
        if (now - _as_utc(row.last_seen_at)) <= missing_buffer:
            continue
        if row.account_state != ACCOUNT_STATE_MISSING:
            row.account_state = ACCOUNT_STATE_MISSING
            row.last_missing_at = now


def get_projectx_account_rows(db: Session, *, user_id: str | None = None) -> list[Account]:
    resolved_user_id = _resolve_user_id(user_id)
    return (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .order_by(Account.is_main.desc(), Account.external_id.asc())
        .all()
    )


def get_projectx_account_row(db: Session, account_id: int, *, user_id: str | None = None) -> Account | None:
    resolved_user_id = _resolve_user_id(user_id)
    return (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.external_id == str(account_id))
        .first()
    )


def set_main_projectx_account(db: Session, account_id: int, *, user_id: str | None = None) -> None:
    resolved_user_id = _resolve_user_id(user_id)
    external_id = str(account_id)

    target = (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.external_id == external_id)
        .first()
    )

    if target is None:
        target = Account(
            user_id=resolved_user_id,
            provider=ACCOUNT_PROVIDER,
            external_id=external_id,
            name=f"Account {account_id}",
            account_state=ACCOUNT_STATE_MISSING,
        )
        db.add(target)

    (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.is_main.is_(True))
        .update({Account.is_main: False}, synchronize_session=False)
    )

    target.is_main = True


def should_include_account(
    row: Account,
    *,
    show_inactive: bool,
    show_missing: bool,
) -> bool:
    if row.is_main:
        return True
    if row.account_state == ACCOUNT_STATE_ACTIVE:
        return True
    if row.account_state in ACCOUNT_STATE_INACTIVE:
        return show_inactive
    if row.account_state == ACCOUNT_STATE_MISSING:
        return show_missing
    return False


def account_id_from_external_id(external_id: str) -> int | None:
    try:
        value = int(external_id)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def _normalize_provider_account(payload: dict[str, Any]) -> dict[str, Any] | None:
    account_id = payload.get("id")
    try:
        normalized_id = str(int(account_id))
    except (TypeError, ValueError):
        return None

    can_trade_raw = payload.get("can_trade")
    can_trade = can_trade_raw if isinstance(can_trade_raw, bool) else None

    is_visible_raw = payload.get("is_visible")
    is_visible = is_visible_raw if isinstance(is_visible_raw, bool) else None

    return {
        "external_id": normalized_id,
        "name": str(payload.get("name") or f"Account {normalized_id}"),
        "can_trade": can_trade,
        "is_visible": is_visible,
        "account_state": account_state_from_flags(can_trade=can_trade, is_visible=is_visible),
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
