import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Put it in backend/.env")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    # Import models so SQLAlchemy can register all mapped tables before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_accounts_schema_compatibility()
    _ensure_journal_schema_compatibility()
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
