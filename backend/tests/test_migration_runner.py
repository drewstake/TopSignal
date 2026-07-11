from __future__ import annotations

from pathlib import Path

import pytest

from tools import migrate_db


@pytest.fixture(autouse=True)
def clear_migration_database_url(monkeypatch):
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)


def test_database_dsn_converts_sqlalchemy_psycopg_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:pass@localhost:5432/topsignal?sslmode=require",
    )

    assert migrate_db._database_dsn() == (
        "postgresql://user:pass@localhost:5432/topsignal?sslmode=require"
    )


def test_database_dsn_rejects_non_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")

    with pytest.raises(RuntimeError, match="requires a PostgreSQL"):
        migrate_db._database_dsn()


def test_database_dsn_prefers_dedicated_migration_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@db.example.com/app")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://migrator@db.example.com/app")

    assert migrate_db._database_dsn() == "postgresql://migrator@db.example.com/app"


def test_database_dsn_rejects_supabase_transaction_pooler(monkeypatch):
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql://user:pass@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    )

    with pytest.raises(RuntimeError, match="transaction pooler"):
        migrate_db._database_dsn()


def test_migrations_are_discovered_in_filename_order():
    files = migrate_db._migration_files()

    assert files
    assert [path.name for path in files] == sorted(path.name for path in files)
    assert all(path.suffix == ".sql" for path in files)


def test_fresh_schema_contains_current_bot_safety_contract():
    schema = (migrate_db.REPO_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")

    required_fragments = [
        "create table if not exists bot_backtests",
        "last_evaluated_at timestamptz",
        "last_error text",
        "'duplicate_skip'",
        "execution_mode text not null default 'dry_run'",
        "create unique index if not exists uq_bot_runs_one_running_per_config",
        "create unique index if not exists uq_bot_order_attempts_idempotency_key",
        "is_archived boolean not null default false",
        "balance numeric(18,6)",
        "'submission_unknown'",
        "schema-20260710-v1",
        "order_size <= 10000 and order_size = trunc(order_size)",
        "bot_config_id bigint references bot_configs(id) on delete set null",
    ]
    for fragment in required_fragments:
        assert fragment in schema


def test_migration_checksums_are_sha256_hex():
    first = migrate_db._migration_files()[0]

    checksum = migrate_db._checksum(Path(first))

    assert len(checksum) == 64
    int(checksum, 16)


def test_latest_migration_is_audit_preservation_contract():
    assert migrate_db._migration_files()[-1].name == "20260710_preserve_bot_order_attempt_audit.sql"


def test_quantity_safety_migration_rejects_unsafe_legacy_rows_and_validates_constraints():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260710_enforce_bot_quantity_safety.sql"
    ).read_text(encoding="utf-8").lower()

    assert "unsafe bot contract quantities exist" in migration
    assert "order_size <> trunc(order_size)" in migration
    assert "max_contracts > 10000" in migration
    assert "max_open_position > 10000" in migration
    assert "not valid" not in migration


def test_adoption_manifest_covers_current_orm_safety_objects():
    columns, indexes = migrate_db._current_model_manifest()

    assert {"balance", "last_seen_at"} <= columns["accounts"]
    assert {"execution_mode", "idempotency_key", "bot_config_id"} <= columns["bot_order_attempts"]
    assert "uq_bot_order_attempts_idempotency_key" in indexes
    assert "uq_bot_runs_one_running_per_config" in indexes


def test_adoption_requires_explicit_backup_acknowledgement():
    with pytest.raises(RuntimeError, match="verified backup"):
        migrate_db.migration_status(check_only=False, adopt_current=True)


def test_adoption_index_parser_requires_exact_ordered_keys():
    exact = (
        "CREATE UNIQUE INDEX uq_test ON public.sample USING btree "
        "(user_id, bot_config_id, idempotency_key) WHERE (idempotency_key IS NOT NULL)"
    )
    superset = (
        "CREATE UNIQUE INDEX uq_test ON public.sample USING btree "
        "(user_id, bot_config_id, idempotency_key, created_at)"
    )

    assert migrate_db._ordered_key_columns(exact) == (
        "user_id",
        "bot_config_id",
        "idempotency_key",
    )
    assert migrate_db._ordered_key_columns(superset) != (
        "user_id",
        "bot_config_id",
        "idempotency_key",
    )


def test_migration_checksums_normalize_line_endings_and_utf8_bom(tmp_path):
    lf = tmp_path / "lf.sql"
    crlf = tmp_path / "crlf.sql"
    lf.write_bytes(b"select 1;\nselect 2;\n")
    crlf.write_bytes(b"\xef\xbb\xbfselect 1;\r\nselect 2;\r\n")

    assert migrate_db._checksum(lf) == migrate_db._checksum(crlf)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _BaselineConnection:
    def __init__(self, *, include_submission_unknown=True):
        self.include_submission_unknown = include_submission_unknown
        self.rolled_back = False

    def execute(self, statement, _params=None):
        query = str(statement)
        if "information_schema.columns" in query:
            rows = [
                (table_name, column_name)
                for table_name, columns in migrate_db.BASELINE_REQUIRED_COLUMNS.items()
                for column_name in columns
            ]
            return _Rows(rows)
        if "pg_indexes" in query:
            return _Rows([(name,) for name in migrate_db.BASELINE_REQUIRED_INDEXES])
        if "pg_constraint" in query:
            value = "CHECK status IN (submission_unknown)" if self.include_submission_unknown else "CHECK status IN (pending)"
            return _Rows([(value,)])
        if "topsignal_schema_baselines" in query:
            return _Rows([(1,)])
        if "pg_tables" in query:
            return _Rows([(table_name,) for table_name in migrate_db.BASELINE_REQUIRED_COLUMNS])
        if "select 1 from" in query.lower() or "SQL(" in query:
            return _Rows([])
        raise AssertionError(query)

    def rollback(self):
        self.rolled_back = True


def test_baseline_validation_accepts_current_safety_schema():
    connection = _BaselineConnection()

    migrate_db._validate_current_schema_for_baseline(connection)

    assert connection.rolled_back is True


def test_baseline_validation_rejects_outdated_order_status_constraint():
    connection = _BaselineConnection(include_submission_unknown=False)

    with pytest.raises(RuntimeError, match="submission_unknown"):
        migrate_db._validate_current_schema_for_baseline(connection)
