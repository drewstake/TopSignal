from __future__ import annotations

from dataclasses import replace
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
        "trade_data_source text not null default 'projectx'",
        "archived_at timestamptz",
        "accounts_archived_not_main_check",
        "'submission_unknown'",
        "schema-20260830-v6",
        "create table if not exists trade_import_batches",
        "create table if not exists trade_import_previews",
        "create table if not exists expense_suppressions",
        "fk_projectx_trade_events_owned_batch",
        "constraint uq_projectx_trade_events_account_source_trade",
        "constraint uq_projectx_trade_events_account_order_ts",
        "commissions numeric(18,6)",
        "fee_scope text not null default 'per_side'",
        "order_size <= 10000 and order_size = trunc(order_size)",
        "bot_config_id bigint references bot_configs(id) on delete set null",
        "('NQ', 0.25, 5.00)",
        "('ES', 0.25, 12.50)",
    ]
    for fragment in required_fragments:
        assert fragment in schema
    assert "create table if not exists databento_" not in schema.lower()


def test_migration_checksums_are_sha256_hex():
    first = migrate_db._migration_files()[0]

    checksum = migrate_db._checksum(Path(first))

    assert len(checksum) == 64
    int(checksum, 16)


def test_latest_migration_hardens_supabase_data_api():
    assert (
        migrate_db._migration_files()[-1].name
        == "20260830_harden_supabase_data_api.sql"
    )


def test_supabase_data_api_hardening_covers_existing_and_future_public_objects():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260830_harden_supabase_data_api.sql"
    ).read_text(encoding="utf-8").lower()
    schema = (migrate_db.REPO_ROOT / "db" / "schema.sql").read_text(encoding="utf-8").lower()

    for sql in (migration, schema):
        normalized_sql = " ".join(sql.split())
        assert "array['anon', 'authenticated']" in sql
        assert "revoke all privileges on all tables in schema public" in sql
        assert "revoke all privileges on all sequences in schema public" in sql
        assert "revoke execute on all functions in schema public" in sql
        assert "alter default privileges for role postgres in schema public" in sql
        assert "revoke execute on functions from public" in sql
        assert (
            "alter default privileges for role postgres "
            "revoke execute on functions from public"
        ) in normalized_sql
        assert (
            "alter default privileges for role postgres in schema public "
            "revoke execute on functions from public"
        ) in normalized_sql
        for object_type in ("tables", "sequences"):
            assert (
                f"alter default privileges revoke all privileges on {object_type} "
                "from public"
            ) in normalized_sql
            assert (
                "alter default privileges in schema public revoke all privileges "
                f"on {object_type} from public"
            ) in normalized_sql
            assert (
                f"alter default privileges for role postgres revoke all privileges on {object_type} "
                "from public"
            ) in normalized_sql
            assert (
                "alter default privileges for role postgres in schema public "
                f"revoke all privileges on {object_type} from public"
            ) in normalized_sql
        hardening_end = sql.rfind("$topsignal_data_api_hardening$;")
        marker_position = sql.rfind("values ('schema-20260830-v6')")
        assert 0 < marker_position < hardening_end
        assert "values ('schema-20260830-v6')" not in sql[hardening_end:]

    assert "auth.*" in migration
    assert "storage.*" in migration
    assert "values ('schema-20260830-v6')" in migration
    assert "enable row level security" not in migration
    assert "create policy" not in migration


def test_expense_suppressions_migration_is_user_scoped_and_non_destructive():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260729_add_expense_suppressions.sql"
    ).read_text(encoding="utf-8").lower()

    assert "primary key (user_id, source, account_id)" in migration
    assert "account_id > 0" in migration
    assert "drop table" not in migration
    assert "delete from" not in migration


def test_live_account_archiving_migration_preserves_history_and_main_integrity():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260725_live_account_archiving.sql"
    ).read_text(encoding="utf-8").lower()

    assert "add column if not exists archived_at timestamptz" in migration
    assert "archived_at is null or not is_main" in migration
    assert "drop table" not in migration
    assert "delete from" not in migration


def test_express_trade_data_source_repair_uses_narrow_provider_name_predicate():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260724_restore_express_trade_data_source.sql"
    ).read_text(encoding="utf-8").lower()

    assert "set trade_data_source = 'projectx'" in migration
    assert "where provider = 'projectx'" in migration
    assert "and trade_data_source = 'csv_import'" in migration
    assert "and name ilike 'express-%'" in migration


def test_account_trade_data_source_migration_is_backfilled_and_constrained():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260724_add_account_trade_data_source.sql"
    ).read_text(encoding="utf-8").lower()

    assert "set trade_data_source = 'projectx'" in migration
    assert "alter column trade_data_source set not null" in migration
    assert "trade_data_source in ('projectx','csv_import')" in migration


def test_emini_seed_migration_preserves_existing_instrument_overrides():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260711_seed_nq_es_instrument_metadata.sql"
    ).read_text(encoding="utf-8")

    assert "('NQ', 0.25, 5.00)" in migration
    assert "('ES', 0.25, 12.50)" in migration
    assert "on conflict (symbol) do nothing" in migration.lower()
    assert "do update" not in migration.lower()


def test_databento_migration_preserves_natural_keys_and_roll_provenance():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260711_add_databento_historical_market_data.sql"
    ).read_text(encoding="utf-8").lower()

    assert "unique (archive_sha256)" in migration
    assert "unique (batch_id, filename)" in migration
    assert "primary key (dataset, instrument_id, ts_event)" in migration
    assert "primary key (root_symbol, trading_date)" in migration
    assert "decision_session_date < trading_date" in migration
    assert "source_file_sha256 text not null" in migration
    assert (
        "20260711_add_databento_historical_market_data.sql"
        in migrate_db.RETIRED_NOOP_MIGRATIONS
    )


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


def test_trade_import_hardening_migration_fails_closed_and_enforces_ownership():
    migration = (
        migrate_db.REPO_ROOT
        / "db"
        / "migrations"
        / "20260725_harden_topstep_trade_imports.sql"
    ).read_text(encoding="utf-8").lower()

    assert "orphaned or mismatched trade import batches exist" in migration
    assert "cross-owner imported trade events exist" in migration
    assert "unsafe projectx trade quantities exist" in migration
    assert "create table if not exists trade_import_previews" in migration
    assert "fk_trade_import_batches_owned_account" in migration
    assert "fk_projectx_trade_events_owned_batch" in migration
    assert "size <> trunc(size)" in migration
    assert "not valid" not in migration


def test_adoption_manifest_covers_current_orm_safety_objects():
    columns, indexes = migrate_db._current_model_manifest()

    assert {"balance", "last_seen_at", "trade_data_source", "archived_at"} <= columns["accounts"]
    assert {"user_id", "source", "account_id", "created_at"} <= columns["expense_suppressions"]
    assert {"execution_mode", "idempotency_key", "bot_config_id"} <= columns["bot_order_attempts"]
    assert {"account_row_id", "account_external_id"} <= columns["trade_import_batches"]
    assert {"normalized_manifest", "dedupe_snapshot", "retention_until"} <= columns[
        "trade_import_previews"
    ]
    assert set(columns).isdisjoint(migrate_db.LEGACY_DATABENTO_TABLE_NAMES)
    assert "uq_bot_order_attempts_idempotency_key" in indexes
    assert "uq_bot_runs_one_running_per_config" in indexes
    assert "idx_accounts_user_archived" in indexes
    assert not any("databento" in name for name in indexes)


def test_adoption_requires_explicit_backup_acknowledgement():
    with pytest.raises(RuntimeError, match="verified backup"):
        migrate_db.migration_status(check_only=False, adopt_current=True)


def test_adoption_executes_security_migration_before_recording_history(tmp_path):
    historical = tmp_path / "20260101_historical.sql"
    security = tmp_path / "20260830_harden_supabase_data_api.sql"
    historical.write_text("select 'historical';", encoding="utf-8")
    security.write_text("select 'security-hardening';", encoding="utf-8")
    events = []

    class Transaction:
        def __enter__(self):
            events.append(("transaction", "begin"))

        def __exit__(self, exc_type, exc, traceback):
            events.append(("transaction", "rollback" if exc else "commit"))

    class Connection:
        def transaction(self):
            return Transaction()

        def execute(self, statement, params=None, **kwargs):
            events.append((str(statement), params, kwargs))

    migrate_db._record_all_migrations(
        Connection(),
        [historical, security],
        execute_migrations=migrate_db.ADOPTION_EXECUTED_MIGRATIONS,
    )

    assert events[0] == ("transaction", "begin")
    assert events[1] == ("select 'security-hardening';", None, {"prepare": False})
    assert "create table if not exists topsignal_schema_migrations" in events[2][0]
    assert "insert into topsignal_schema_migrations" in events[3][0]
    assert "insert into topsignal_schema_migrations" in events[4][0]
    assert events[-1] == ("transaction", "commit")


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


def _sample_adoption_contract() -> migrate_db._SchemaContract:
    return migrate_db._SchemaContract(
        columns={
            ("parents", "id"): migrate_db._ColumnContract(
                postgres_type="bigint",
                nullable=False,
                default_expression="<generated>",
            ),
            ("children", "id"): migrate_db._ColumnContract(
                postgres_type="bigint",
                nullable=False,
                default_expression="<generated>",
            ),
            ("children", "parent_id"): migrate_db._ColumnContract(
                postgres_type="bigint",
                nullable=False,
                default_expression=None,
            ),
            ("children", "quantity"): migrate_db._ColumnContract(
                postgres_type="numeric(18,6)",
                nullable=False,
                default_expression="<numeric:1>",
            ),
        },
        primary_keys={"parents": ("id",), "children": ("id",)},
        foreign_keys={
            "children": (
                migrate_db._ForeignKeyContract(
                    local_columns=("parent_id",),
                    remote_schema="<application>",
                    remote_table="parents",
                    remote_columns=("id",),
                    match_type="SIMPLE",
                    on_delete="CASCADE",
                    on_update="NO ACTION",
                    deferrable=False,
                    initially_deferred=False,
                    validated=True,
                ),
            )
        },
        checks={
            "children": (
                migrate_db._CheckContract(
                    name="children_quantity_positive_check",
                    expression="(quantity > (0)::numeric)",
                    validated=True,
                    no_inherit=False,
                ),
                migrate_db._CheckContract(
                    name="children_quantity_supported_check",
                    expression=(
                        "((quantity <= (10000)::numeric) AND "
                        "(quantity = trunc(quantity)))"
                    ),
                    validated=True,
                    no_inherit=False,
                ),
            )
        },
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("wrong_type", "PostgreSQL type text; expected numeric(18,6)"),
        ("dropped_not_null", "children.quantity must be NOT NULL"),
        ("wrong_default", "children.quantity has default None"),
        ("wrong_fk_action", "ON DELETE CASCADE"),
        ("wrong_fk_match", "MATCH SIMPLE"),
        ("unexpected_column", "unexpected model-table columns: legacy_flag"),
        ("unexpected_fk", "has unexpected foreign key"),
        ("weakened_check", "children_quantity_supported_check"),
    ],
)
def test_adoption_contract_rejects_structural_drift(mutation, expected_error):
    expected = _sample_adoption_contract()
    actual_columns = dict(expected.columns)
    actual_foreign_keys = dict(expected.foreign_keys)
    actual_checks = dict(expected.checks)

    if mutation == "wrong_type":
        actual_columns[("children", "quantity")] = replace(
            actual_columns[("children", "quantity")],
            postgres_type="text",
        )
    elif mutation == "dropped_not_null":
        actual_columns[("children", "quantity")] = replace(
            actual_columns[("children", "quantity")],
            nullable=True,
        )
    elif mutation == "wrong_default":
        actual_columns[("children", "quantity")] = replace(
            actual_columns[("children", "quantity")],
            default_expression=None,
        )
    elif mutation == "wrong_fk_action":
        actual_foreign_keys["children"] = (
            replace(actual_foreign_keys["children"][0], on_delete="RESTRICT"),
        )
    elif mutation == "wrong_fk_match":
        actual_foreign_keys["children"] = (
            replace(actual_foreign_keys["children"][0], match_type="FULL"),
        )
    elif mutation == "unexpected_column":
        actual_columns[("children", "legacy_flag")] = migrate_db._ColumnContract(
            postgres_type="boolean",
            nullable=True,
            default_expression=None,
        )
    elif mutation == "unexpected_fk":
        actual_foreign_keys["children"] = (
            *actual_foreign_keys["children"],
            replace(
                actual_foreign_keys["children"][0],
                local_columns=("quantity",),
            ),
        )
    elif mutation == "weakened_check":
        actual_checks["children"] = (
            actual_checks["children"][0],
            replace(
                actual_checks["children"][1],
                expression="(quantity <= (20000)::numeric)",
            ),
        )
    else:  # pragma: no cover - parametrization is exhaustive.
        raise AssertionError(mutation)

    actual = replace(
        expected,
        columns=actual_columns,
        foreign_keys=actual_foreign_keys,
        checks=actual_checks,
    )
    errors = migrate_db._schema_contract_errors(
        expected=expected,
        actual=actual,
        expected_check_names={
            ("children", "children_quantity_positive_check"),
            ("children", "children_quantity_supported_check"),
        },
    )

    assert any(expected_error in error for error in errors)


def test_adoption_contract_accepts_stronger_combined_structural_check():
    expected = _sample_adoption_contract()
    actual = replace(
        expected,
        checks={
            "children": (
                migrate_db._CheckContract(
                    name="children_quantity_check",
                    expression=(
                        "((quantity > (0)::numeric) AND "
                        "(quantity <= (10000)::numeric) AND "
                        "(quantity = trunc(quantity)))"
                    ),
                    validated=True,
                    no_inherit=False,
                ),
            )
        },
    )

    assert migrate_db._schema_contract_errors(
        expected=expected,
        actual=actual,
        expected_check_names={
            ("children", "children_quantity_positive_check"),
            ("children", "children_quantity_supported_check"),
        },
    ) == []


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
