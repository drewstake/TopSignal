from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
LEDGER_TABLE = "topsignal_schema_migrations"
LOCK_NAME = "topsignal-schema-migrations-v1"
CURRENT_SCHEMA_BASELINE = "schema-20260711-v2"
LEGACY_DATABENTO_TABLE_NAMES = frozenset(
    {
        "databento_import_batches",
        "databento_import_files",
        "databento_instruments",
        "databento_ohlcv_1m",
        "databento_roll_schedule",
    }
)
# This historical migration remains checksum-tracked for installations that
# already applied it. New upgrades record it without executing its relational
# market-table DDL, so existing tables are preserved but never recreated.
RETIRED_NOOP_MIGRATIONS = frozenset(
    {"20260711_add_databento_historical_market_data.sql"}
)
BASELINE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "accounts": {"user_id", "external_id", "balance", "account_state", "can_trade"},
    "provider_credentials": {"user_id", "username_encrypted", "api_key_encrypted"},
    "journal_entries": {"user_id", "account_id", "version", "is_archived"},
    "expenses": {"user_id", "account_id", "source_id"},
    "payouts": {"user_id", "amount_cents", "payout_date"},
    "projectx_trade_events": {"user_id", "account_id", "source_trade_id", "raw_payload"},
    "bot_backtests": {"user_id", "input_fingerprint", "result_snapshot"},
    "bot_runs": {"last_evaluated_at", "last_error"},
    "bot_decisions": {"correlation_id", "idempotency_key"},
    "bot_order_attempts": {"execution_mode", "correlation_id", "idempotency_key"},
    "topsignal_schema_baselines": {"version", "created_at"},
}
BASELINE_REQUIRED_INDEXES = {
    "uq_bot_order_attempts_idempotency_key",
    "uq_bot_runs_one_running_per_config",
}


def _database_dsn() -> str:
    load_dotenv(REPO_ROOT / "backend" / ".env")
    value = os.getenv("MIGRATION_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("MIGRATION_DATABASE_URL or DATABASE_URL is required")
    if value.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + value.removeprefix("postgresql+psycopg://")
    elif value.startswith("postgresql://") or value.startswith("postgres://"):
        dsn = value
    else:
        raise RuntimeError("The migration runner requires a PostgreSQL database URL")

    parsed = urlparse(dsn)
    host = (parsed.hostname or "").lower()
    if host.endswith(".pooler.supabase.com") and parsed.port == 6543:
        raise RuntimeError(
            "Migrations require a direct or session-mode PostgreSQL URL; "
            "Supabase transaction pooler port 6543 cannot hold the advisory lock."
        )
    return dsn


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda path: path.name)


def _checksum(path: Path) -> str:
    # Git may check the same SQL out with CRLF on Windows and LF on Linux.
    # Hash canonical UTF-8/LF content so the migration identity is portable.
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    canonical = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _read_applied(connection: psycopg.Connection, *, create: bool) -> dict[str, str]:
    if create:
        connection.execute(
            f"""
            create table if not exists {LEDGER_TABLE} (
              version text primary key,
              checksum_sha256 text not null,
              applied_at timestamptz not null default now()
            )
            """
        )
        connection.commit()
    else:
        exists = connection.execute(
            "select to_regclass(%s)",
            (f"public.{LEDGER_TABLE}",),
        ).fetchone()
        connection.rollback()
        if not exists or exists[0] is None:
            return {}

    rows = connection.execute(
        f"select version, checksum_sha256 from {LEDGER_TABLE} order by version"
    ).fetchall()
    connection.rollback()
    return {str(version): str(checksum) for version, checksum in rows}


def _verify_known_checksums(files: list[Path], applied: dict[str, str]) -> None:
    file_names = {path.name for path in files}
    unknown = sorted(set(applied) - file_names)
    if unknown:
        raise RuntimeError(f"Migration ledger contains missing files: {', '.join(unknown)}")
    for path in files:
        recorded = applied.get(path.name)
        if recorded is not None and recorded != _checksum(path):
            raise RuntimeError(f"Applied migration checksum changed: {path.name}")


def _validate_current_schema_for_baseline(connection: psycopg.Connection) -> None:
    rows = connection.execute(
        "select table_name, column_name from information_schema.columns where table_schema = 'public'"
    ).fetchall()
    columns_by_table: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        columns_by_table.setdefault(str(table_name), set()).add(str(column_name))

    errors: list[str] = []
    for table_name, required_columns in BASELINE_REQUIRED_COLUMNS.items():
        missing = sorted(required_columns - columns_by_table.get(table_name, set()))
        if missing:
            errors.append(f"{table_name} missing columns: {', '.join(missing)}")

    index_rows = connection.execute(
        "select indexname from pg_indexes where schemaname = 'public'"
    ).fetchall()
    index_names = {str(row[0]) for row in index_rows}
    missing_indexes = sorted(BASELINE_REQUIRED_INDEXES - index_names)
    if missing_indexes:
        errors.append(f"missing indexes: {', '.join(missing_indexes)}")

    if "bot_order_attempts" in columns_by_table:
        constraint_rows = connection.execute(
            """
            select pg_get_constraintdef(oid)
            from pg_constraint
            where conrelid = 'public.bot_order_attempts'::regclass
              and contype = 'c'
            """
        ).fetchall()
        if not any("submission_unknown" in str(row[0]) for row in constraint_rows):
            errors.append("bot_order_attempts status constraint is missing submission_unknown")

    baseline_row = (
        connection.execute(
            "select 1 from topsignal_schema_baselines where version = %s",
            (CURRENT_SCHEMA_BASELINE,),
        ).fetchone()
        if "topsignal_schema_baselines" in columns_by_table
        else None
    )
    if baseline_row is None:
        errors.append(f"missing fresh-schema baseline marker {CURRENT_SCHEMA_BASELINE}")

    table_rows = connection.execute(
        "select tablename from pg_tables where schemaname = 'public'"
    ).fetchall()
    allowed_nonempty = {
        "instrument_metadata",
        "topsignal_schema_baselines",
        LEDGER_TABLE,
    }
    nonempty_tables: list[str] = []
    for row in table_rows:
        table_name = str(row[0])
        if table_name in allowed_nonempty:
            continue
        present = connection.execute(
            sql.SQL("select 1 from {} limit 1").format(sql.Identifier(table_name))
        ).fetchone()
        if present is not None:
            nonempty_tables.append(table_name)
    if nonempty_tables:
        errors.append(
            "fresh-schema baseline requires empty application tables; found data in: "
            + ", ".join(sorted(nonempty_tables))
        )

    connection.rollback()
    if errors:
        raise RuntimeError(
            "Cannot baseline an outdated or incomplete schema: " + "; ".join(errors)
        )


def _current_model_manifest() -> tuple[dict[str, set[str]], set[str]]:
    backend_dir = str(REPO_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import app.models  # noqa: F401
    from app.db import Base

    columns = {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.tables.values()
        if table.name not in LEGACY_DATABENTO_TABLE_NAMES
    }
    indexes = {
        index.name
        for table in Base.metadata.tables.values()
        if table.name not in LEGACY_DATABENTO_TABLE_NAMES
        for index in table.indexes
        if index.name
    }
    return columns, indexes


def _current_model_uniqueness_manifest() -> tuple[
    list[tuple[str, tuple[str, ...]]],
    dict[str, tuple[str, tuple[str, ...], bool]],
]:
    backend_dir = str(REPO_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import app.models  # noqa: F401
    from app.db import Base
    from sqlalchemy import UniqueConstraint

    constraints: list[tuple[str, tuple[str, ...]]] = []
    unique_indexes: dict[str, tuple[str, tuple[str, ...], bool]] = {}
    for table in Base.metadata.tables.values():
        if table.name in LEGACY_DATABENTO_TABLE_NAMES:
            continue
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                constraints.append(
                    (table.name, tuple(column.name for column in constraint.columns))
                )
        for index in table.indexes:
            if not index.unique or not index.name:
                continue
            has_predicate = index.dialect_options["postgresql"].get("where") is not None
            unique_indexes[index.name] = (
                table.name,
                tuple(column.name for column in index.columns),
                has_predicate,
            )
    return constraints, unique_indexes


def _ordered_key_columns(definition: str) -> tuple[str, ...]:
    normalized = definition.lower()
    match = re.search(
        r"(?:\bunique\s*|\busing\s+[a-z0-9_]+\s*)\(([^()]*)\)",
        normalized,
    )
    if match is None:
        return ()
    columns: list[str] = []
    for raw_column in match.group(1).split(","):
        token = raw_column.strip().split()[0].strip('"')
        columns.append(token)
    return tuple(columns)


def _validate_current_schema_for_adoption(connection: psycopg.Connection) -> None:
    expected_columns, expected_indexes = _current_model_manifest()
    expected_unique_constraints, expected_unique_indexes = _current_model_uniqueness_manifest()
    column_rows = connection.execute(
        """
        select table_name, column_name, is_nullable
        from information_schema.columns
        where table_schema = 'public'
        """
    ).fetchall()
    columns_by_table: dict[str, set[str]] = {}
    nullable_by_column: dict[tuple[str, str], bool] = {}
    for table_name, column_name, is_nullable in column_rows:
        table = str(table_name)
        column = str(column_name)
        columns_by_table.setdefault(table, set()).add(column)
        nullable_by_column[(table, column)] = str(is_nullable).upper() == "YES"

    errors: list[str] = []
    for table_name, required_columns in expected_columns.items():
        missing = sorted(required_columns - columns_by_table.get(table_name, set()))
        if missing:
            errors.append(f"{table_name} missing columns: {', '.join(missing)}")

    index_rows = connection.execute(
        "select tablename, indexname, indexdef from pg_indexes where schemaname = 'public'"
    ).fetchall()
    index_definitions = {str(row[1]): str(row[2]) for row in index_rows}
    unique_indexes_by_table: dict[str, list[str]] = {}
    for table_name, _index_name, definition in index_rows:
        definition_text = str(definition)
        if "create unique index" in definition_text.lower():
            unique_indexes_by_table.setdefault(str(table_name), []).append(definition_text)
    index_names = set(index_definitions)
    missing_indexes = sorted(expected_indexes - index_names)
    if missing_indexes:
        errors.append(f"missing model indexes: {', '.join(missing_indexes)}")
    for index_name, (_table_name, columns, has_predicate) in expected_unique_indexes.items():
        definition = index_definitions.get(index_name, "").lower()
        if not definition:
            continue
        if "create unique index" not in definition:
            errors.append(f"{index_name} must be UNIQUE")
        if index_name == "uq_expenses_dedupe":
            fragments = (
                "user_id",
                "expense_date",
                "category",
                "coalesce(account_type",
                "coalesce(plan_size",
                "coalesce(source_id",
                "coalesce(account_id",
                "amount_cents",
            )
            positions = [definition.find(fragment) for fragment in fragments]
            if any(position < 0 for position in positions) or positions != sorted(positions):
                errors.append(f"{index_name} has the wrong indexed expressions")
        elif _ordered_key_columns(definition) != tuple(column.lower() for column in columns):
            errors.append(f"{index_name} has the wrong indexed columns")
        if has_predicate and " where " not in definition:
            errors.append(f"{index_name} is missing its partial-index predicate")
        if (
            index_name == "uq_bot_order_attempts_idempotency_key"
            and "idempotency_key is not null" not in definition
        ):
            errors.append(f"{index_name} has the wrong partial-index predicate")
        if (
            index_name == "uq_bot_runs_one_running_per_config"
            and "status = 'running'" not in definition
        ):
            errors.append(f"{index_name} has the wrong partial-index predicate")

    constraint_rows = connection.execute(
        """
        select relation.relname, constraint_row.conname, pg_get_constraintdef(constraint_row.oid)
        from pg_constraint as constraint_row
        join pg_class as relation on relation.oid = constraint_row.conrelid
        join pg_namespace as namespace on namespace.oid = relation.relnamespace
        where namespace.nspname = 'public'
        """
    ).fetchall()
    definitions_by_table: dict[str, list[str]] = {}
    for table_name, _constraint_name, definition in constraint_rows:
        definitions_by_table.setdefault(str(table_name), []).append(str(definition))

    for table_name, columns in expected_unique_constraints:
        candidates = definitions_by_table.get(table_name, [])
        expected_key = tuple(column.lower() for column in columns)
        matching_constraint = any(
            "unique" in definition.lower()
            and _ordered_key_columns(definition) == expected_key
            for definition in candidates
        )
        matching_unique_index = any(
            _ordered_key_columns(definition) == expected_key
            for definition in unique_indexes_by_table.get(table_name, [])
        )
        if not matching_constraint and not matching_unique_index:
            errors.append(
                f"{table_name} is missing UNIQUE ({', '.join(columns)})"
            )

    attempt_definitions = " ".join(definitions_by_table.get("bot_order_attempts", [])).lower()
    config_definitions = " ".join(definitions_by_table.get("bot_configs", [])).lower()
    decision_definitions = " ".join(definitions_by_table.get("bot_decisions", [])).lower()
    if "submission_unknown" not in attempt_definitions:
        errors.append("bot_order_attempts status constraint is missing submission_unknown")
    if "foreign key (bot_config_id)" not in attempt_definitions or "on delete set null" not in attempt_definitions:
        errors.append("bot_order_attempts bot_config_id foreign key must use ON DELETE SET NULL")
    if not nullable_by_column.get(("bot_order_attempts", "bot_config_id"), False):
        errors.append("bot_order_attempts.bot_config_id must be nullable")
    if "topbot_adaptive" not in config_definitions:
        errors.append("bot_configs strategy constraint is not current")
    if "duplicate_skip" not in decision_definitions:
        errors.append("bot_decisions type constraint is not current")
    config_constraint_definitions = [
        definition.lower() for definition in definitions_by_table.get("bot_configs", [])
    ]
    for quantity_column in ("order_size", "max_contracts", "max_open_position"):
        if not any(
            quantity_column in definition
            and "10000" in definition
            and "trunc" in definition
            for definition in config_constraint_definitions
        ):
            errors.append(f"bot_configs is missing the current {quantity_column} safety constraint")

    if "bot_configs" in columns_by_table:
        invalid_quantity_count = connection.execute(
            """
            select count(*)
            from bot_configs
            where order_size <= 0 or order_size > 10000 or order_size <> trunc(order_size)
               or max_contracts <= 0 or max_contracts > 10000 or max_contracts <> trunc(max_contracts)
               or max_open_position <= 0 or max_open_position > 10000
               or max_open_position <> trunc(max_open_position)
            """
        ).fetchone()
        if invalid_quantity_count and int(invalid_quantity_count[0]) > 0:
            errors.append("bot_configs contains quantities that violate the current safety policy")

    connection.rollback()
    if errors:
        raise RuntimeError(
            "Cannot adopt an outdated or incomplete populated schema: " + "; ".join(errors)
        )


def _record_all_migrations(
    connection: psycopg.Connection,
    files: list[Path],
) -> None:
    with connection.transaction():
        for path in files:
            connection.execute(
                f"insert into {LEDGER_TABLE} (version, checksum_sha256) values (%s, %s)",
                (path.name, _checksum(path)),
            )


def migration_status(
    *,
    check_only: bool,
    baseline: bool = False,
    adopt_current: bool = False,
    acknowledge_populated_database: bool = False,
) -> int:
    files = _migration_files()
    if not files:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}")
    if adopt_current and not acknowledge_populated_database:
        raise RuntimeError(
            "--adopt-current requires --acknowledge-populated-database after a verified backup"
        )

    with psycopg.connect(_database_dsn()) as connection:
        connection.execute("select pg_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        connection.commit()
        try:
            applied = _read_applied(connection, create=not check_only)
            _verify_known_checksums(files, applied)
            pending = [path for path in files if path.name not in applied]

            if baseline:
                if applied:
                    raise RuntimeError("Cannot baseline a database with recorded migrations")
                _validate_current_schema_for_baseline(connection)
                _record_all_migrations(connection, files)
                print(f"Baselined current schema at {len(files)} migrations.")
                return 0

            if adopt_current:
                if applied:
                    raise RuntimeError("Cannot adopt a database with recorded migrations")
                _validate_current_schema_for_adoption(connection)
                _record_all_migrations(connection, files)
                print(f"Adopted validated populated schema at {len(files)} migrations.")
                return 0

            if check_only:
                if pending:
                    print("Pending migrations:")
                    for path in pending:
                        print(f"  {path.name}")
                    return 1
                print(f"Database is current ({len(applied)} migrations applied).")
                return 0

            for path in pending:
                checksum = _checksum(path)
                retired = path.name in RETIRED_NOOP_MIGRATIONS
                if retired:
                    print(f"Recording retired no-op {path.name}...")
                else:
                    print(f"Applying {path.name}...")
                with connection.transaction():
                    if not retired:
                        sql = path.read_text(encoding="utf-8")
                        connection.execute(sql, prepare=False)
                    connection.execute(
                        f"insert into {LEDGER_TABLE} (version, checksum_sha256) values (%s, %s)",
                        (path.name, checksum),
                    )

            print(f"Database is current ({len(files)} migrations applied).")
            return 0
        finally:
            connection.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
            connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply TopSignal PostgreSQL migrations safely.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Report pending or checksum-mismatched migrations without changing the database.",
    )
    mode.add_argument(
        "--baseline",
        action="store_true",
        help="Validate a schema created from db/schema.sql and record all current migrations without replaying them.",
    )
    mode.add_argument(
        "--adopt-current",
        action="store_true",
        help="Validate a populated pre-runner schema and record current migration history without replaying it.",
    )
    parser.add_argument(
        "--acknowledge-populated-database",
        action="store_true",
        help="Required acknowledgement that a verified backup exists before --adopt-current.",
    )
    args = parser.parse_args()
    try:
        return migration_status(
            check_only=args.check,
            baseline=args.baseline,
            adopt_current=args.adopt_current,
            acknowledge_populated_database=args.acknowledge_populated_database,
        )
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
