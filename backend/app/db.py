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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
