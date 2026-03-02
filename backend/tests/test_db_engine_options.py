import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy.pool import NullPool

from app.db import _build_engine_options


def test_build_engine_options_disables_prepared_statements_for_supabase_pooler():
    options = _build_engine_options(
        "postgresql+psycopg://user:pass@aws-0-us-west-2.pooler.supabase.com:6543/postgres?sslmode=require"
    )

    assert options["pool_pre_ping"] is False
    assert options["poolclass"] is NullPool
    assert options["connect_args"]["prepare_threshold"] is None


def test_build_engine_options_keeps_default_pooling_for_direct_postgres():
    options = _build_engine_options("postgresql+psycopg://user:pass@db.example.com:5432/postgres")

    assert options["pool_pre_ping"] is True
    assert "poolclass" not in options
    assert "connect_args" not in options


def test_build_engine_options_keeps_default_pooling_for_sqlite():
    options = _build_engine_options("sqlite+pysqlite:///:memory:")

    assert options["pool_pre_ping"] is True
    assert "poolclass" not in options
    assert "connect_args" not in options
