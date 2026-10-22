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
    assert options["connect_args"]["connect_timeout"] == 10
    assert options["connect_args"]["keepalives_idle"] == 10
    assert options["pool_timeout"] == 10
    assert options["hide_parameters"] is True


def test_build_engine_options_keeps_default_pooling_for_sqlite():
    options = _build_engine_options("sqlite+pysqlite:///:memory:")

    assert options["pool_pre_ping"] is True
    assert "poolclass" not in options
    assert "connect_args" not in options


def test_session_pooler_reuses_a_small_bounded_pool_and_still_disables_preparation():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool
    url = 'postgresql+psycopg://user:pass@aws-0-us-west-2.pooler.supabase.com:5432/postgres'
    options = _build_engine_options(url)
    assert options['pool_size'] == 3 and options['max_overflow'] == 2
    assert options['pool_pre_ping'] is True
    assert options['pool_recycle'] == 300 and options['pool_timeout'] == 10
    assert options['connect_args']['prepare_threshold'] is None
    engine = create_engine(url, **options)
    assert isinstance(engine.pool, QueuePool)
    engine.dispose()


def test_transaction_safety_timeouts_share_one_round_trip_and_remain_local():
    from app.db import _bound_database_transaction
    from types import SimpleNamespace
    calls = []
    connection = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'), exec_driver_sql=calls.append)
    _bound_database_transaction(None, SimpleNamespace(nested=False), connection)
    assert len(calls) == 1
    for name, value in [('statement_timeout', '30s'), ('lock_timeout', '5s'), ('idle_in_transaction_session_timeout', '60s')]:
        assert f"set_config('{name}', '{value}', true)" in calls[0]
    _bound_database_transaction(None, SimpleNamespace(nested=True), connection)
    assert len(calls) == 1


def test_pooler_has_bounded_connect_but_no_queuepool_only_options():
    from sqlalchemy import create_engine
    url = "postgresql+psycopg://user:pass@aws-0-us-west-2.pooler.supabase.com:6543/postgres"
    engine = create_engine(url, **_build_engine_options(url))
    assert isinstance(engine.pool, NullPool)
    assert engine.hide_parameters is True
    assert _build_engine_options(url)["connect_args"]["connect_timeout"] == 10
    engine.dispose()


def test_sql_errors_do_not_expose_bound_parameters():
    import pytest
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError
    engine = create_engine("sqlite://", **_build_engine_options("sqlite://"))
    with engine.connect() as connection:
        with pytest.raises(SQLAlchemyError) as raised:
            connection.execute(text("select * from nonexistent where password = :secret"), {"secret": "private-credential"})
        assert "private-credential" not in str(raised.value)
    engine.dispose()


def test_invalid_database_configuration_is_safe_before_logging_starts():
    import subprocess
    import sys
    env = dict(os.environ, DATABASE_URL="postgresql+psycopg://user@localhost:private-password/db", PYTHON_DOTENV_DISABLED="1")
    result = subprocess.run([sys.executable, "-c", "import app.db"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "Invalid DATABASE_URL" in result.stderr
    assert "private-password" not in result.stderr
