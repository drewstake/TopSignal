import logging
import os
from typing import Any, Literal

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Put it in backend/.env")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_LOCAL_SUPABASE_URLS = {
    "http://127.0.0.1:54321",
    "http://localhost:54321",
}
_SUPABASE_POOLER_SUFFIX = ".pooler.supabase.com"


def _uses_supabase_pooler(database_url: str) -> bool:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        return False
    host = (parsed.host or "").strip().strip("[]").lower()
    return host.endswith(_SUPABASE_POOLER_SUFFIX)


def _build_engine_options(database_url: str) -> dict[str, Any]:
    options: dict[str, Any] = {"pool_pre_ping": True}
    parsed = make_url(database_url)
    connect_args: dict[str, Any] = {}

    if parsed.get_backend_name() == "postgresql" and parsed.get_driver_name() == "psycopg":
        if _uses_supabase_pooler(database_url):
            # Supabase transaction pooler is incompatible with psycopg prepared statements.
            connect_args["prepare_threshold"] = None
            # Let Supabase own pooling to avoid stale connection/session state in app pool.
            options["poolclass"] = NullPool
            options["pool_pre_ping"] = False

    if connect_args:
        options["connect_args"] = connect_args
    return options

engine = create_engine(DATABASE_URL, **_build_engine_options(DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def resolve_database_host_mode() -> tuple[str, Literal["local", "cloud"]]:
    parsed = make_url(DATABASE_URL)
    backend_name = parsed.get_backend_name()
    if backend_name == "sqlite":
        return "sqlite", "local"

    host = (parsed.host or "").strip().strip("[]")
    if not host:
        return "unknown", "local"

    mode: Literal["local", "cloud"] = "local" if host.lower() in _LOCAL_HOSTS else "cloud"
    if parsed.port is None:
        return host, mode
    return f"{host}:{parsed.port}", mode


def guard_against_local_database_url() -> None:
    parsed = make_url(DATABASE_URL)
    if parsed.get_backend_name() == "sqlite":
        return

    host = (parsed.host or "").strip().strip("[]").lower()
    if host not in _LOCAL_HOSTS:
        return

    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:
        message = "You are in local DB mode; start Docker or switch DATABASE_URL to Supabase Cloud."
        logger.error("%s Current DATABASE_URL host: %s", message, host)
        raise RuntimeError(message) from exc


def resolve_supabase_mode() -> Literal["disabled", "local", "cloud"]:
    configured = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not configured:
        return "disabled"
    if configured in _LOCAL_SUPABASE_URLS:
        return "local"
    return "cloud"


def log_runtime_connection_targets() -> None:
    db_host, db_mode = resolve_database_host_mode()
    supabase_mode = resolve_supabase_mode()
    logger.info("Database target resolved: host=%s mode=%s", db_host, db_mode)
    logger.info("Supabase pooler detected: enabled=%s", _uses_supabase_pooler(DATABASE_URL))
    logger.info(
        "Supabase mode resolved from SUPABASE_URL: mode=%s (local mode only when SUPABASE_URL is 127.0.0.1:54321)",
        supabase_mode,
    )


def init_db():
    # Import models so SQLAlchemy can register all mapped tables before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_accounts_schema_compatibility()
    _ensure_journal_schema_compatibility()
    _ensure_multi_tenant_schema_compatibility()
    _ensure_default_instrument_metadata()


def _ensure_accounts_schema_compatibility() -> None:
    # Existing dev databases may predate account_state/is_main columns.
    # Apply a safe schema compatibility patch on startup for Postgres.
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "accounts" not in table_names:
            return

        column_names = {column["name"] for column in inspector.get_columns("accounts")}
        if "account_state" not in column_names:
            conn.execute(
                text(
                    "alter table accounts add column if not exists account_state text not null default 'ACTIVE'"
                )
            )
        if "can_trade" not in column_names:
            conn.execute(text("alter table accounts add column if not exists can_trade boolean"))
        if "is_visible" not in column_names:
            conn.execute(text("alter table accounts add column if not exists is_visible boolean"))
        if "first_seen_at" not in column_names:
            conn.execute(text("alter table accounts add column if not exists first_seen_at timestamptz"))
        if "last_seen_at" not in column_names:
            conn.execute(text("alter table accounts add column if not exists last_seen_at timestamptz"))
        if "last_missing_at" not in column_names:
            conn.execute(text("alter table accounts add column if not exists last_missing_at timestamptz"))
        if "is_main" not in column_names:
            conn.execute(text("alter table accounts add column if not exists is_main boolean not null default false"))

        conn.execute(text("update accounts set account_state = 'ACTIVE' where account_state is null"))
        conn.execute(text("update accounts set is_main = false where is_main is null"))
        conn.execute(text("create index if not exists idx_accounts_is_main on accounts (is_main)"))
        conn.execute(text("create index if not exists idx_accounts_account_state on accounts (account_state)"))


def _ensure_journal_schema_compatibility() -> None:
    # Existing dev databases may predate journal versioning/image support columns.
    # Apply a safe schema compatibility patch on startup for Postgres.
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "journal_entries" not in table_names:
            return

        column_names = {column["name"] for column in inspector.get_columns("journal_entries")}
        if "version" not in column_names:
            conn.execute(text("alter table journal_entries add column if not exists version integer"))
        if "stats_source" not in column_names:
            conn.execute(text("alter table journal_entries add column if not exists stats_source text"))
        if "stats_json" not in column_names:
            conn.execute(text("alter table journal_entries add column if not exists stats_json jsonb"))
        if "stats_pulled_at" not in column_names:
            conn.execute(text("alter table journal_entries add column if not exists stats_pulled_at timestamptz"))

        conn.execute(text("update journal_entries set version = 1 where version is null"))
        conn.execute(
            text(
                """
                alter table journal_entries
                  alter column version set default 1,
                  alter column version set not null
                """
            )
        )
        conn.execute(
            text(
                "create unique index if not exists uq_journal_entries_account_entry_date on journal_entries (account_id, entry_date)"
            )
        )
        conn.execute(
            text(
                """
                create index if not exists idx_journal_entries_account_archived_date_updated
                  on journal_entries (account_id, is_archived, entry_date desc, updated_at desc)
                """
            )
        )
        conn.execute(
            text(
                """
                create index if not exists idx_journal_entries_account_mood_date
                  on journal_entries (account_id, mood, entry_date desc)
                """
            )
        )
        conn.execute(
            text(
                """
                create table if not exists journal_entry_images (
                  id bigserial primary key,
                  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
                  journal_entry_id bigint not null references journal_entries(id) on delete cascade,
                  account_id bigint not null,
                  entry_date date not null,
                  filename text not null,
                  mime_type text not null,
                  byte_size integer not null,
                  width integer,
                  height integer,
                  created_at timestamptz not null default now()
                )
                """
            )
        )
        image_column_names = {column["name"] for column in inspect(conn).get_columns("journal_entry_images")}
        if "user_id" in image_column_names:
            conn.execute(
                text(
                    "create index if not exists idx_journal_entry_images_account_date on journal_entry_images (user_id, account_id, entry_date)"
                )
            )
            conn.execute(
                text(
                    "create index if not exists idx_journal_entry_images_journal_entry on journal_entry_images (user_id, journal_entry_id)"
                )
            )
        else:
            conn.execute(
                text(
                    "create index if not exists idx_journal_entry_images_account_date on journal_entry_images (account_id, entry_date)"
                )
            )
            conn.execute(
                text(
                    "create index if not exists idx_journal_entry_images_journal_entry on journal_entry_images (journal_entry_id)"
                )
            )


def _ensure_multi_tenant_schema_compatibility() -> None:
    if engine.dialect.name != "postgresql":
        return

    default_user_id = "00000000-0000-0000-0000-000000000000"
    table_names = [
        "accounts",
        "trades",
        "projectx_trade_events",
        "projectx_trade_day_syncs",
        "position_lifecycles",
        "journal_entries",
        "journal_entry_images",
        "expenses",
    ]

    with engine.begin() as conn:
        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())
        for table_name in table_names:
            if table_name not in existing_tables:
                continue

            conn.execute(
                text(
                    f"""
                    alter table {table_name}
                    add column if not exists user_id uuid not null
                    default '{default_user_id}'
                    """
                )
            )

        # Legacy schema created an unnamed unique constraint on (provider, external_id).
        # PostgreSQL auto-named it accounts_provider_external_id_key.
        conn.execute(text("alter table accounts drop constraint if exists accounts_provider_external_id_key"))
        conn.execute(text("drop index if exists accounts_provider_external_id_key"))
        conn.execute(text("alter table accounts drop constraint if exists uq_accounts_provider_external_id"))
        conn.execute(text("drop index if exists uq_accounts_provider_external_id"))
        conn.execute(
            text(
                """
                create unique index if not exists uq_accounts_provider_external_id
                on accounts (user_id, provider, external_id)
                """
            )
        )
        conn.execute(text("drop index if exists idx_accounts_is_main"))
        conn.execute(text("drop index if exists idx_accounts_account_state"))
        conn.execute(text("create index if not exists idx_accounts_is_main on accounts (user_id, is_main)"))
        conn.execute(text("create index if not exists idx_accounts_account_state on accounts (user_id, account_state)"))

        conn.execute(text("alter table projectx_trade_events drop constraint if exists uq_projectx_trade_events_account_source_trade"))
        conn.execute(text("alter table projectx_trade_events drop constraint if exists uq_projectx_trade_events_account_order_ts"))
        conn.execute(text("drop index if exists uq_projectx_trade_events_account_source_trade"))
        conn.execute(text("drop index if exists uq_projectx_trade_events_account_order_ts"))
        conn.execute(
            text(
                """
                create unique index if not exists uq_projectx_trade_events_account_source_trade
                on projectx_trade_events (user_id, account_id, source_trade_id)
                """
            )
        )
        conn.execute(
            text(
                """
                create unique index if not exists uq_projectx_trade_events_account_order_ts
                on projectx_trade_events (user_id, account_id, order_id, trade_timestamp)
                """
            )
        )

        conn.execute(text("alter table projectx_trade_day_syncs drop constraint if exists uq_projectx_trade_day_syncs_account_date"))
        conn.execute(text("drop index if exists uq_projectx_trade_day_syncs_account_date"))
        conn.execute(
            text(
                """
                create unique index if not exists uq_projectx_trade_day_syncs_account_date
                on projectx_trade_day_syncs (user_id, account_id, trade_date)
                """
            )
        )

        conn.execute(text("alter table journal_entries drop constraint if exists uq_journal_entries_account_entry_date"))
        conn.execute(text("drop index if exists uq_journal_entries_account_entry_date"))
        conn.execute(
            text(
                """
                create unique index if not exists uq_journal_entries_account_entry_date
                on journal_entries (user_id, account_id, entry_date)
                """
            )
        )
        conn.execute(text("drop index if exists idx_journal_entries_account_archived_date_updated"))
        conn.execute(text("drop index if exists idx_journal_entries_account_mood_date"))
        conn.execute(
            text(
                """
                create index if not exists idx_journal_entries_account_archived_date_updated
                on journal_entries (user_id, account_id, is_archived, entry_date desc, updated_at desc)
                """
            )
        )
        conn.execute(
            text(
                """
                create index if not exists idx_journal_entries_account_mood_date
                on journal_entries (user_id, account_id, mood, entry_date desc)
                """
            )
        )

        conn.execute(text("drop index if exists idx_journal_entry_images_account_date"))
        conn.execute(text("drop index if exists idx_journal_entry_images_journal_entry"))
        conn.execute(
            text(
                """
                create index if not exists idx_journal_entry_images_account_date
                on journal_entry_images (user_id, account_id, entry_date)
                """
            )
        )
        conn.execute(
            text(
                """
                create index if not exists idx_journal_entry_images_journal_entry
                on journal_entry_images (user_id, journal_entry_id)
                """
            )
        )

        conn.execute(text("drop index if exists idx_position_lifecycles_account_opened"))
        conn.execute(text("drop index if exists idx_position_lifecycles_contract_opened"))
        conn.execute(
            text(
                """
                create index if not exists idx_position_lifecycles_account_opened
                on position_lifecycles (user_id, account_id, opened_at desc)
                """
            )
        )
        conn.execute(
            text(
                """
                create index if not exists idx_position_lifecycles_contract_opened
                on position_lifecycles (user_id, contract_id, opened_at desc)
                """
            )
        )

        conn.execute(text("drop index if exists idx_expenses_expense_date"))
        conn.execute(text("drop index if exists idx_expenses_account_id"))
        conn.execute(text("drop index if exists idx_expenses_category"))
        conn.execute(text("drop index if exists uq_expenses_dedupe"))
        conn.execute(text("create index if not exists idx_expenses_expense_date on expenses (user_id, expense_date)"))
        conn.execute(text("create index if not exists idx_expenses_account_id on expenses (user_id, account_id)"))
        conn.execute(text("create index if not exists idx_expenses_category on expenses (user_id, category)"))
        conn.execute(
            text(
                """
                create unique index if not exists uq_expenses_dedupe
                on expenses (
                  user_id,
                  expense_date,
                  category,
                  coalesce(account_type, ''),
                  coalesce(plan_size, ''),
                  coalesce(account_id, 0),
                  amount_cents
                )
                """
            )
        )

        conn.execute(
            text(
                """
                create table if not exists provider_credentials (
                  id bigserial primary key,
                  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
                  provider text not null,
                  username_encrypted text not null,
                  api_key_encrypted text not null,
                  created_at timestamptz not null default now(),
                  updated_at timestamptz not null default now()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                create unique index if not exists uq_provider_credentials_user_provider
                on provider_credentials (user_id, provider)
                """
            )
        )
        conn.execute(
            text(
                """
                create index if not exists idx_provider_credentials_user_provider
                on provider_credentials (user_id, provider)
                """
            )
        )


def _ensure_default_instrument_metadata() -> None:
    from .services.instruments import DEFAULT_INSTRUMENT_SPECS

    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "instrument_metadata" not in table_names:
            return

        for symbol, spec in DEFAULT_INSTRUMENT_SPECS.items():
            conn.execute(
                text(
                    """
                    insert into instrument_metadata (symbol, tick_size, tick_value)
                    values (:symbol, :tick_size, :tick_value)
                    on conflict (symbol) do nothing
                    """
                ),
                {
                    "symbol": symbol,
                    "tick_size": spec.tick_size,
                    "tick_value": spec.tick_value,
                },
            )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
