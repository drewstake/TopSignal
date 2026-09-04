from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import math
from threading import RLock
from typing import Any

from sqlalchemy import case, or_, text
from sqlalchemy.orm import Session

from ..auth import get_authenticated_user_id
from ..models import Account

ACCOUNT_PROVIDER = "projectx"

ACCOUNT_STATE_ACTIVE = "ACTIVE"
ACCOUNT_STATE_LOCKED_OUT = "LOCKED_OUT"
ACCOUNT_STATE_HIDDEN = "HIDDEN"
ACCOUNT_STATE_MISSING = "MISSING"
ACCOUNT_STATE_INACTIVE = {ACCOUNT_STATE_LOCKED_OUT, ACCOUNT_STATE_HIDDEN}
TRADE_DATA_SOURCE_PROJECTX = "projectx"
TRADE_DATA_SOURCE_CSV_IMPORT = "csv_import"
TRADE_DATA_SOURCES = {
    TRADE_DATA_SOURCE_PROJECTX,
    TRADE_DATA_SOURCE_CSV_IMPORT,
}
ACCOUNT_DISPLAY_NAME_MAX_LENGTH = 120
# Live CSV accounts still need a numeric key for authenticated API routes and
# relational ownership, but that key is an application detail rather than a
# provider identifier. Keep generated values inside JavaScript's safe integer
# range so clients can route requests without ever asking the user for an ID.
LOCAL_LIVE_ACCOUNT_ID_MIN = 8_000_000_000_000_000
LOCAL_LIVE_ACCOUNT_ID_MAX = 9_007_199_254_740_991
_ACCOUNT_MAIN_LOCK_STRIPES = tuple(RLock() for _ in range(256))


def is_generated_live_account_id(account_id: int) -> bool:
    return LOCAL_LIVE_ACCOUNT_ID_MIN <= account_id <= LOCAL_LIVE_ACCOUNT_ID_MAX


class AccountTradeDataSourceConflictError(Exception):
    def __init__(
        self,
        *,
        account_id: int,
        current_trade_data_source: str,
        requested_trade_data_source: str,
    ) -> None:
        super().__init__("account_trade_data_source_conflict")
        self.account_id = account_id
        self.current_trade_data_source = current_trade_data_source
        self.requested_trade_data_source = requested_trade_data_source


class LiveAccountArchivedError(Exception):
    def __init__(self, *, account_id: int) -> None:
        super().__init__("live_account_archived")
        self.account_id = account_id


class MainAccountReplacementRequiredError(Exception):
    def __init__(self, *, account_id: int) -> None:
        super().__init__("main_account_replacement_required")
        self.account_id = account_id


def _resolve_user_id(user_id: str | None) -> str:
    if user_id:
        return user_id
    return get_authenticated_user_id()


@contextmanager
def serialize_account_main_mutation(
    db: Session,
    *,
    user_id: str | None = None,
    provider: str = ACCOUNT_PROVIDER,
):
    """Serialize main-account decisions until the caller commits or rolls back.

    PostgreSQL uses a transaction-scoped advisory lock, which also coordinates
    separate API processes. SQLite uses a deterministic in-process lock stripe
    for local development and concurrency tests. Callers must end the database
    transaction inside this context so the lock covers the full mutation.
    """

    resolved_user_id = _resolve_user_id(user_id)
    lock_key_bytes = hashlib.blake2b(
        f"topsignal:account-main:{resolved_user_id}:{provider}".encode("utf-8"),
        digest_size=8,
    ).digest()
    signed_lock_key = int.from_bytes(lock_key_bytes, byteorder="big", signed=True)
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        db.execute(
            text("select pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": signed_lock_key},
        )
        yield
        return

    stripe = _ACCOUNT_MAIN_LOCK_STRIPES[signed_lock_key % len(_ACCOUNT_MAIN_LOCK_STRIPES)]
    with stripe:
        yield


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
        if (
            row is not None
            and row.trade_data_source == TRADE_DATA_SOURCE_CSV_IMPORT
        ):
            # An explicit local-data choice owns this external ID. Provider
            # discovery must neither overwrite it nor try to insert a duplicate.
            continue
        if row is None:
            row = Account(
                user_id=resolved_user_id,
                provider=ACCOUNT_PROVIDER,
                external_id=external_id,
                trade_data_source=TRADE_DATA_SOURCE_PROJECTX,
            )
            db.add(row)
            existing_by_external_id[external_id] = row

        row.name = payload["name"]
        if payload["balance"] is not None:
            row.balance = payload["balance"]
        row.account_state = payload["account_state"]
        row.can_trade = payload["can_trade"]
        row.is_visible = payload["is_visible"]
        if isinstance(payload["provider_simulated"], bool):
            row.provider_simulated = payload["provider_simulated"]
            row.provider_classification_observed_at = now
        row.last_seen_at = now
        if row.first_seen_at is None:
            row.first_seen_at = now
        if row.account_state != ACCOUNT_STATE_MISSING:
            row.last_missing_at = None

    missing_query = (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.trade_data_source == TRADE_DATA_SOURCE_PROJECTX)
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


def get_projectx_account_row(
    db: Session,
    account_id: int,
    *,
    user_id: str | None = None,
    lock_for_update: bool = False,
) -> Account | None:
    resolved_user_id = _resolve_user_id(user_id)
    query = (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.external_id == str(account_id))
    )
    if lock_for_update:
        query = query.with_for_update()
    return query.first()


def create_projectx_import_account(
    db: Session,
    *,
    name: str,
    starting_balance: float | None = None,
    user_id: str | None = None,
) -> Account:
    resolved_user_id = _resolve_user_id(user_id)
    normalized_name = _normalize_optional_text(name)
    if normalized_name is None:
        raise ValueError("Account name cannot be empty.")
    if len(normalized_name) > ACCOUNT_DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(
            f"Account name must be {ACCOUNT_DISPLAY_NAME_MAX_LENGTH} characters or fewer."
        )
    if _has_control_character(normalized_name):
        raise ValueError("Account name cannot contain control characters.")
    if starting_balance is not None and (
        not math.isfinite(starting_balance)
        or starting_balance <= 0
        or starting_balance > 1_000_000_000
    ):
        raise ValueError("Starting balance must be between 0 and 1,000,000,000.")

    # Treat a repeated name-only request as idempotent. The route serializes
    # this lookup and creation per user, including across PostgreSQL workers.
    existing_live_rows = (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.trade_data_source == TRADE_DATA_SOURCE_CSV_IMPORT)
        .order_by(Account.created_at.asc(), Account.id.asc())
        .all()
    )
    normalized_name_key = normalized_name.casefold()
    for existing in existing_live_rows:
        effective_name = _normalize_optional_text(existing.display_name) or _normalize_optional_text(
            existing.name
        )
        if effective_name is None or effective_name.casefold() != normalized_name_key:
            continue
        existing_account_id = account_id_from_external_id(existing.external_id)
        if existing_account_id is None:
            continue
        if existing.archived_at is not None:
            raise LiveAccountArchivedError(account_id=existing_account_id)
        if existing.balance is None and starting_balance is not None:
            existing.balance = starting_balance
        return existing

    external_id = str(_next_local_live_account_id(db, user_id=resolved_user_id))

    has_main_account = (
        db.query(Account.id)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.is_main.is_(True))
        .first()
        is not None
    )
    row = Account(
        user_id=resolved_user_id,
        provider=ACCOUNT_PROVIDER,
        external_id=external_id,
        name=normalized_name,
        trade_data_source=TRADE_DATA_SOURCE_CSV_IMPORT,
        account_state=ACCOUNT_STATE_ACTIVE,
        can_trade=None,
        is_visible=True,
        # For a local Live account, balance is the user-supplied opening
        # balance. The API adds the account's all-time net P&L when returning
        # the current balance.
        balance=starting_balance,
        is_main=not has_main_account,
    )
    db.add(row)
    db.flush()
    return row


def _next_local_live_account_id(db: Session, *, user_id: str) -> int:
    used_ids: set[int] = set()
    for (external_id,) in (
        db.query(Account.external_id)
        .filter(Account.user_id == user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .all()
    ):
        normalized = normalize_projectx_account_external_id(external_id)
        if normalized is not None:
            used_ids.add(int(normalized))

    candidate = LOCAL_LIVE_ACCOUNT_ID_MIN
    while candidate in used_ids and candidate <= LOCAL_LIVE_ACCOUNT_ID_MAX:
        candidate += 1
    if candidate > LOCAL_LIVE_ACCOUNT_ID_MAX:
        raise ValueError("No internal Live account keys are available.")
    return candidate


def set_projectx_account_trade_data_source(
    db: Session,
    account_id: int,
    trade_data_source: str,
    *,
    user_id: str | None = None,
) -> Account:
    if trade_data_source not in TRADE_DATA_SOURCES:
        raise ValueError("Unsupported trade data source.")

    target = get_projectx_account_row(db, account_id, user_id=user_id)
    if target is None:
        raise LookupError("projectx_account_not_found")

    if target.trade_data_source == trade_data_source:
        return target

    raise AccountTradeDataSourceConflictError(
        account_id=account_id,
        current_trade_data_source=target.trade_data_source,
        requested_trade_data_source=trade_data_source,
    )


def set_main_projectx_account(db: Session, account_id: int, *, user_id: str | None = None) -> None:
    resolved_user_id = _resolve_user_id(user_id)
    external_id = str(account_id)

    target = (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.external_id == external_id)
        .with_for_update()
        .first()
    )

    if target is None:
        raise LookupError("projectx_account_not_found")
    if target.archived_at is not None:
        raise LiveAccountArchivedError(account_id=account_id)

    (
        db.query(Account)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.is_main.is_(True))
        .filter(Account.external_id != external_id)
        .update({Account.is_main: False}, synchronize_session=False)
    )

    target.is_main = True


def archive_projectx_import_account(
    db: Session,
    account_id: int,
    *,
    user_id: str | None = None,
    replacement_account_id: int | None = None,
    now_utc: datetime | None = None,
) -> tuple[Account, Account | None]:
    resolved_user_id = _resolve_user_id(user_id)
    target = get_projectx_account_row(
        db,
        account_id,
        user_id=resolved_user_id,
        lock_for_update=True,
    )
    if target is None:
        raise LookupError("projectx_account_not_found")
    if target.trade_data_source != TRADE_DATA_SOURCE_CSV_IMPORT:
        raise ValueError("only_live_csv_accounts_can_be_archived")
    if target.archived_at is not None:
        return target, None

    replacement: Account | None = None
    if target.is_main:
        if replacement_account_id is not None:
            if replacement_account_id == account_id:
                raise ValueError("main_account_replacement_must_be_different")
            replacement = get_projectx_account_row(
                db,
                replacement_account_id,
                user_id=resolved_user_id,
                lock_for_update=True,
            )
            if replacement is None:
                raise ValueError("main_account_replacement_not_found")
            if replacement.archived_at is not None:
                raise ValueError("main_account_replacement_is_archived")
            if not _is_normal_account_candidate(replacement):
                raise ValueError("main_account_replacement_is_not_selectable")
        else:
            replacement = (
                db.query(Account)
                .filter(Account.user_id == resolved_user_id)
                .filter(Account.provider == ACCOUNT_PROVIDER)
                .filter(Account.external_id != str(account_id))
                .filter(Account.archived_at.is_(None))
                .filter(
                    or_(
                        Account.trade_data_source == TRADE_DATA_SOURCE_CSV_IMPORT,
                        Account.account_state.in_([ACCOUNT_STATE_ACTIVE, ACCOUNT_STATE_LOCKED_OUT]),
                    )
                )
                .order_by(
                    case((Account.trade_data_source == TRADE_DATA_SOURCE_CSV_IMPORT, 0), else_=1),
                    case((Account.account_state == ACCOUNT_STATE_ACTIVE, 0), else_=1),
                    Account.created_at.asc(),
                    Account.id.asc(),
                )
                .with_for_update()
                .first()
            )
        if replacement is None:
            raise MainAccountReplacementRequiredError(account_id=account_id)
        target.is_main = False
        replacement.is_main = True

    target.archived_at = _as_utc(now_utc or datetime.now(timezone.utc))
    return target, replacement


def unarchive_projectx_import_account(
    db: Session,
    account_id: int,
    *,
    user_id: str | None = None,
) -> Account:
    resolved_user_id = _resolve_user_id(user_id)
    target = get_projectx_account_row(
        db,
        account_id,
        user_id=resolved_user_id,
        lock_for_update=True,
    )
    if target is None:
        raise LookupError("projectx_account_not_found")
    if target.trade_data_source != TRADE_DATA_SOURCE_CSV_IMPORT:
        raise ValueError("only_live_csv_accounts_can_be_unarchived")
    if target.archived_at is None:
        return target

    target.archived_at = None
    has_main_account = (
        db.query(Account.id)
        .filter(Account.user_id == resolved_user_id)
        .filter(Account.provider == ACCOUNT_PROVIDER)
        .filter(Account.is_main.is_(True))
        .with_for_update()
        .first()
        is not None
    )
    if not has_main_account:
        target.is_main = True
    return target


def set_projectx_account_display_name(
    db: Session,
    account_id: int,
    display_name: str,
    *,
    user_id: str | None = None,
) -> Account:
    target = get_projectx_account_row(db, account_id, user_id=user_id)
    if target is None:
        raise LookupError("projectx_account_not_found")

    normalized_display_name = _normalize_optional_text(display_name)
    if normalized_display_name is None:
        raise ValueError("Account name cannot be empty.")
    if len(normalized_display_name) > ACCOUNT_DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(f"Account name must be {ACCOUNT_DISPLAY_NAME_MAX_LENGTH} characters or fewer.")
    if _has_control_character(normalized_display_name):
        raise ValueError("Account name cannot contain control characters.")

    provider_name = resolve_projectx_account_provider_name(target.name, account_id=account_id)
    target.display_name = None if normalized_display_name == provider_name else normalized_display_name
    return target


def should_include_account(
    row: Account,
    *,
    show_inactive: bool,
    show_missing: bool,
    include_archived: bool = False,
) -> bool:
    if row.archived_at is not None:
        return include_archived
    if row.trade_data_source == TRADE_DATA_SOURCE_CSV_IMPORT:
        return True
    if row.is_main:
        return True
    if row.account_state == ACCOUNT_STATE_ACTIVE:
        return True
    if row.account_state in ACCOUNT_STATE_INACTIVE:
        return show_inactive
    if row.account_state == ACCOUNT_STATE_MISSING:
        return show_missing
    return False


def _is_normal_account_candidate(row: Account) -> bool:
    if row.archived_at is not None:
        return False
    if row.trade_data_source == TRADE_DATA_SOURCE_CSV_IMPORT:
        return True
    return row.account_state in {ACCOUNT_STATE_ACTIVE, ACCOUNT_STATE_LOCKED_OUT}


def account_id_from_external_id(external_id: str) -> int | None:
    normalized = normalize_projectx_account_external_id(external_id)
    if normalized is None:
        return None
    return int(normalized)


def normalize_projectx_account_external_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        account_id = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        account_id = int(value)
    else:
        text = str(value).strip()
        if not text.isdigit():
            return None
        account_id = int(text)

    if account_id <= 0:
        return None
    return str(account_id)


def resolve_projectx_account_provider_name(name: Any, *, account_id: int) -> str:
    normalized = _normalize_optional_text(name)
    if normalized is not None:
        return normalized
    return f"Account {account_id}"


def resolve_projectx_account_effective_name(*, provider_name: str, display_name: Any) -> str:
    normalized_display_name = _normalize_optional_text(display_name)
    if normalized_display_name is not None:
        return normalized_display_name
    return provider_name


def _normalize_provider_account(payload: dict[str, Any]) -> dict[str, Any] | None:
    normalized_id = normalize_projectx_account_external_id(payload.get("id"))
    if normalized_id is None:
        return None

    can_trade_raw = payload.get("can_trade")
    can_trade = can_trade_raw if isinstance(can_trade_raw, bool) else None

    is_visible_raw = payload.get("is_visible")
    is_visible = is_visible_raw if isinstance(is_visible_raw, bool) else None

    simulated_raw = payload.get("simulated")
    provider_simulated = simulated_raw if isinstance(simulated_raw, bool) else None

    balance_raw = payload.get("balance")
    try:
        balance = float(balance_raw) if balance_raw is not None else None
    except (TypeError, ValueError, OverflowError):
        balance = None
    if balance is not None and not math.isfinite(balance):
        balance = None

    return {
        "external_id": normalized_id,
        "name": str(payload.get("name") or f"Account {normalized_id}"),
        "balance": balance,
        "can_trade": can_trade,
        "is_visible": is_visible,
        "provider_simulated": provider_simulated,
        "account_state": account_state_from_flags(can_trade=can_trade, is_visible=is_visible),
    }


def persist_projectx_account_classification(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    simulated: bool,
    observed_at: datetime | None = None,
) -> Account:
    """Persist one tenant-scoped, authoritative ProjectX account classification."""

    if not isinstance(simulated, bool):
        raise ValueError("provider account simulated classification must be boolean")
    row = get_projectx_account_row(
        db,
        int(account_id),
        user_id=user_id,
        lock_for_update=True,
    )
    if row is None:
        raise LookupError("projectx_account_not_found")
    if row.trade_data_source != TRADE_DATA_SOURCE_PROJECTX:
        raise ValueError("provider classification requires a ProjectX-backed account")
    row.provider_simulated = simulated
    row.provider_classification_observed_at = _as_utc(
        observed_at or datetime.now(timezone.utc)
    )
    return row


def invalidate_projectx_account_classification(
    db: Session,
    *,
    user_id: str,
    account_id: int,
) -> Account | None:
    """Fail closed immediately when the authoritative user stream disconnects."""

    row = get_projectx_account_row(
        db,
        int(account_id),
        user_id=user_id,
        lock_for_update=True,
    )
    if row is None:
        return None
    row.provider_classification_observed_at = None
    return row


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
