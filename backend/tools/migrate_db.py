from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from psycopg import sql
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
LEDGER_TABLE = "topsignal_schema_migrations"
LOCK_NAME = "topsignal-schema-migrations-v1"
CURRENT_SCHEMA_BASELINE = "schema-20260830-v6"
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
# Populated pre-runner databases may satisfy the structural adoption manifest
# while retaining unsafe Supabase Data API ACLs. These idempotent security
# migrations must execute transactionally before adoption records any history.
ADOPTION_EXECUTED_MIGRATIONS = frozenset(
    {"20260830_harden_supabase_data_api.sql"}
)
BASELINE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "account_emergency_actions": {
        "id",
        "user_id",
        "account_id",
        "status",
        "confirmed_flat",
        "lease_owner_id",
        "lease_expires_at",
        "attempt_count",
        "request_payload",
        "result_payload",
        "completed_at",
    },
    "accounts": {
        "user_id",
        "external_id",
        "balance",
        "provider_simulated",
        "provider_classification_observed_at",
        "account_state",
        "can_trade",
        "trade_data_source",
    },
    "provider_credentials": {"user_id", "username_encrypted", "api_key_encrypted"},
    "journal_entries": {"user_id", "account_id", "version", "is_archived"},
    "expenses": {"user_id", "account_id", "source_id"},
    "expense_suppressions": {"user_id", "source", "account_id", "created_at"},
    "payouts": {"user_id", "amount_cents", "payout_date"},
    "trade_import_batches": {
        "user_id",
        "account_id",
        "account_row_id",
        "account_external_id",
        "source_file_name",
        "file_sha256",
        "total_rows",
        "inserted_rows",
        "duplicate_rows",
        "imported_at",
    },
    "trade_import_previews": {
        "token_hash",
        "user_id",
        "account_id",
        "account_row_id",
        "account_external_id",
        "normalized_manifest",
        "dedupe_snapshot",
        "status",
        "expires_at",
        "retention_until",
        "import_batch_id",
    },
    "projectx_trade_events": {
        "user_id",
        "account_id",
        "source_trade_id",
        "raw_payload",
        "commissions",
        "fee_scope",
        "trade_date",
        "entry_timestamp",
        "entry_price",
        "import_batch_id",
        "account_row_id",
        "account_external_id",
    },
    "bot_backtests": {"user_id", "input_fingerprint", "result_snapshot"},
    "bot_runs": {"last_evaluated_at", "last_error"},
    "bot_runtime_leases": {
        "lease_name",
        "owner_id",
        "heartbeat_at",
        "expires_at",
        "details",
    },
    "bot_decisions": {"correlation_id", "idempotency_key"},
    "bot_order_attempts": {"execution_mode", "correlation_id", "idempotency_key"},
    "topsignal_schema_baselines": {"version", "created_at"},
}
BASELINE_REQUIRED_INDEXES = {
    "uq_account_emergency_actions_one_pending",
    "uq_bot_order_attempts_idempotency_key",
    "uq_bot_runs_one_live_running_per_account",
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


@dataclass(frozen=True)
class _ColumnContract:
    postgres_type: str
    nullable: bool
    default_expression: str | None


@dataclass(frozen=True)
class _ForeignKeyContract:
    local_columns: tuple[str, ...]
    remote_schema: str
    remote_table: str
    remote_columns: tuple[str, ...]
    match_type: str
    on_delete: str
    on_update: str
    deferrable: bool
    initially_deferred: bool
    validated: bool


@dataclass(frozen=True)
class _CheckContract:
    name: str
    expression: str
    validated: bool
    no_inherit: bool


@dataclass(frozen=True)
class _SchemaContract:
    columns: dict[tuple[str, str], _ColumnContract]
    primary_keys: dict[str, tuple[str, ...]]
    foreign_keys: dict[str, tuple[_ForeignKeyContract, ...]]
    checks: dict[str, tuple[_CheckContract, ...]]


_FOREIGN_KEY_ACTIONS = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}

_FOREIGN_KEY_MATCH_TYPES = {
    "s": "SIMPLE",
    "f": "FULL",
    "p": "PARTIAL",
}


def _normalize_catalog_expression(
    value: object | None,
    *,
    postgres_type: str | None = None,
) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split())
    if normalized.lower() in {
        "current_timestamp",
        "transaction_timestamp()",
        "now()",
    }:
        return "<current_timestamp>"
    if postgres_type is not None:
        type_name = _normalize_postgres_type(postgres_type)
        numeric_literal = normalized
        quoted_numeric = re.fullmatch(
            r"'([+-]?\d+(?:\.\d+)?)'::(?:numeric|smallint|integer|bigint)",
            numeric_literal,
            flags=re.IGNORECASE,
        )
        cast_numeric = re.fullmatch(
            r"\(?([+-]?\d+(?:\.\d+)?)\)?::(?:numeric|smallint|integer|bigint)",
            numeric_literal,
            flags=re.IGNORECASE,
        )
        if quoted_numeric is not None:
            numeric_literal = quoted_numeric.group(1)
        elif cast_numeric is not None:
            numeric_literal = cast_numeric.group(1)
        if type_name.startswith(("numeric", "smallint", "integer", "bigint")):
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", numeric_literal):
                integer, separator, fraction = numeric_literal.partition(".")
                fraction = fraction.rstrip("0")
                return f"<numeric:{integer}{separator + fraction if fraction else ''}>"
        if type_name == "boolean":
            boolean_literal = re.fullmatch(
                r"'?((?:true)|(?:false))'?(?:::boolean)?",
                normalized,
                flags=re.IGNORECASE,
            )
            if boolean_literal is not None:
                return f"<boolean:{boolean_literal.group(1).lower()}>"
    return normalized


def _normalize_postgres_type(value: object) -> str:
    normalized = " ".join(str(value).strip().lower().split())
    return re.sub(r"\s*,\s*", ",", normalized)


def _strip_balanced_outer_parentheses(expression: str) -> str:
    value = expression.strip()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        quote = False
        wraps_whole_expression = True
        index = 0
        while index < len(value):
            char = value[index]
            if char == "'":
                if quote and index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                quote = not quote
            elif not quote:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0 and index != len(value) - 1:
                        wraps_whole_expression = False
                        break
            index += 1
        if quote or depth != 0 or not wraps_whole_expression:
            break
        value = value[1:-1].strip()
    return value


def _catalog_expression_conjuncts(expression: str) -> frozenset[str]:
    """Return exact top-level AND terms from PostgreSQL's deparsed expression.

    Fresh schema DDL sometimes combines multiple named model checks into one
    stronger inline CHECK. Comparing exact parsed terms (rather than substring
    fragments) accepts that equivalent form while still rejecting a weakened
    or rearranged predicate such as ``expected OR true``.
    """

    value = _strip_balanced_outer_parentheses(expression)
    terms: list[str] = []
    start = 0
    depth = 0
    quote = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            if quote and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            quote = not quote
            index += 1
            continue
        if quote:
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and value[index : index + 3].lower() == "and":
            before = value[index - 1] if index else " "
            after = value[index + 3] if index + 3 < len(value) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                terms.append(_strip_balanced_outer_parentheses(value[start:index]))
                start = index + 3
                index += 3
                continue
        index += 1
    terms.append(_strip_balanced_outer_parentheses(value[start:]))
    return frozenset(
        _normalize_check_term(term)
        for term in terms
        if term.strip()
    )


def _split_catalog_array_items(value: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            if quote and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            quote = not quote
        elif not quote:
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif char == "," and depth == 0:
                items.append(" ".join(value[start:index].strip().split()))
                start = index + 1
        index += 1
    items.append(" ".join(value[start:].strip().split()))
    return [item for item in items if item]


def _normalize_check_term(value: str) -> str:
    normalized = " ".join(_strip_balanced_outer_parentheses(value).split())
    membership = re.fullmatch(
        r"(.+?\s*=\s*ANY\s*\(ARRAY\[)(.*)(\]\))",
        normalized,
        flags=re.IGNORECASE,
    )
    if membership is not None:
        items = sorted(_split_catalog_array_items(membership.group(2)))
        return f"{membership.group(1)}{','.join(items)}{membership.group(3)}"

    trunc_match = re.fullmatch(
        r"([a-z_][a-z0-9_]*)\s*=\s*trunc\(\1\)",
        normalized,
        flags=re.IGNORECASE,
    )
    cast_match = re.fullmatch(
        r"([a-z_][a-z0-9_]*)\s*=\s*\(\(\1\)::bigint\)::numeric",
        normalized,
        flags=re.IGNORECASE,
    )
    whole_number_match = trunc_match or cast_match
    if whole_number_match is not None:
        return f"{whole_number_match.group(1).lower()} = <whole-number>"
    return normalized


def _load_schema_contract(connection: psycopg.Connection, *, schema_name: str) -> _SchemaContract:
    column_rows = connection.execute(
        """
        select relation.relname,
               attribute.attname,
               format_type(attribute.atttypid, attribute.atttypmod),
               not attribute.attnotnull,
               pg_get_expr(attribute_default.adbin, attribute_default.adrelid),
               attribute.attidentity,
               pg_get_serial_sequence(
                 format('%%I.%%I', namespace.nspname, relation.relname),
                 attribute.attname
               )
        from pg_class as relation
        join pg_namespace as namespace on namespace.oid = relation.relnamespace
        join pg_attribute as attribute on attribute.attrelid = relation.oid
        left join pg_attrdef as attribute_default
          on attribute_default.adrelid = relation.oid
         and attribute_default.adnum = attribute.attnum
        where namespace.nspname = %s
          and relation.relkind in ('r', 'p')
          and attribute.attnum > 0
          and not attribute.attisdropped
        order by relation.relname, attribute.attnum
        """,
        (schema_name,),
    ).fetchall()
    columns: dict[tuple[str, str], _ColumnContract] = {}
    for (
        table_name,
        column_name,
        postgres_type,
        nullable,
        default_expression,
        identity_kind,
        serial_sequence,
    ) in column_rows:
        generated = bool(identity_kind) or serial_sequence is not None
        columns[(str(table_name), str(column_name))] = _ColumnContract(
            postgres_type=_normalize_postgres_type(postgres_type),
            nullable=bool(nullable),
            default_expression=(
                "<generated>"
                if generated
                else _normalize_catalog_expression(
                    default_expression,
                    postgres_type=str(postgres_type),
                )
            ),
        )

    key_rows = connection.execute(
        """
        select source_relation.relname,
               constraint_row.contype,
               constraint_row.conname,
               array(
                 select source_attribute.attname
                 from unnest(constraint_row.conkey) with ordinality
                   as key_column(attnum, ordinal)
                 join pg_attribute as source_attribute
                   on source_attribute.attrelid = source_relation.oid
                  and source_attribute.attnum = key_column.attnum
                 order by key_column.ordinal
               ) as local_columns,
               target_namespace.nspname,
               target_relation.relname,
               case when constraint_row.contype = 'f' then array(
                 select target_attribute.attname
                 from unnest(constraint_row.confkey) with ordinality
                   as key_column(attnum, ordinal)
                 join pg_attribute as target_attribute
                   on target_attribute.attrelid = target_relation.oid
                  and target_attribute.attnum = key_column.attnum
                 order by key_column.ordinal
               ) else null end as remote_columns,
               constraint_row.confmatchtype,
               constraint_row.confdeltype,
               constraint_row.confupdtype,
               constraint_row.condeferrable,
               constraint_row.condeferred,
               constraint_row.convalidated
        from pg_constraint as constraint_row
        join pg_class as source_relation on source_relation.oid = constraint_row.conrelid
        join pg_namespace as source_namespace on source_namespace.oid = source_relation.relnamespace
        left join pg_class as target_relation on target_relation.oid = constraint_row.confrelid
        left join pg_namespace as target_namespace on target_namespace.oid = target_relation.relnamespace
        where source_namespace.nspname = %s
          and constraint_row.contype in ('p', 'f')
        order by source_relation.relname, constraint_row.conname
        """,
        (schema_name,),
    ).fetchall()
    primary_keys: dict[str, tuple[str, ...]] = {}
    foreign_keys_by_table: dict[str, list[_ForeignKeyContract]] = {}
    for (
        table_name,
        constraint_type,
        _constraint_name,
        local_columns,
        remote_schema,
        remote_table,
        remote_columns,
        match_type,
        delete_action,
        update_action,
        deferrable,
        initially_deferred,
        validated,
    ) in key_rows:
        table = str(table_name)
        local_key = tuple(str(column) for column in (local_columns or []))
        if constraint_type == "p":
            primary_keys[table] = local_key
            continue
        normalized_remote_schema = (
            "<application>" if str(remote_schema) == schema_name else str(remote_schema)
        )
        foreign_keys_by_table.setdefault(table, []).append(
            _ForeignKeyContract(
                local_columns=local_key,
                remote_schema=normalized_remote_schema,
                remote_table=str(remote_table),
                remote_columns=tuple(str(column) for column in (remote_columns or [])),
                match_type=_FOREIGN_KEY_MATCH_TYPES.get(str(match_type), str(match_type)),
                on_delete=_FOREIGN_KEY_ACTIONS.get(str(delete_action), str(delete_action)),
                on_update=_FOREIGN_KEY_ACTIONS.get(str(update_action), str(update_action)),
                deferrable=bool(deferrable),
                initially_deferred=bool(initially_deferred),
                validated=bool(validated),
            )
        )

    check_rows = connection.execute(
        """
        select relation.relname,
               constraint_row.conname,
               pg_get_expr(constraint_row.conbin, constraint_row.conrelid),
               constraint_row.convalidated,
               constraint_row.connoinherit
        from pg_constraint as constraint_row
        join pg_class as relation on relation.oid = constraint_row.conrelid
        join pg_namespace as namespace on namespace.oid = relation.relnamespace
        where namespace.nspname = %s
          and constraint_row.contype = 'c'
        order by relation.relname, constraint_row.conname
        """,
        (schema_name,),
    ).fetchall()
    checks_by_table: dict[str, list[_CheckContract]] = {}
    for table_name, constraint_name, expression, validated, no_inherit in check_rows:
        normalized_expression = _normalize_catalog_expression(expression)
        if normalized_expression is None:
            continue
        checks_by_table.setdefault(str(table_name), []).append(
            _CheckContract(
                name=str(constraint_name),
                expression=normalized_expression,
                validated=bool(validated),
                no_inherit=bool(no_inherit),
            )
        )

    return _SchemaContract(
        columns=columns,
        primary_keys=primary_keys,
        foreign_keys={
            table_name: tuple(sorted(rows, key=repr))
            for table_name, rows in foreign_keys_by_table.items()
        },
        checks={
            table_name: tuple(sorted(rows, key=lambda row: (row.name, row.expression)))
            for table_name, rows in checks_by_table.items()
        },
    )


def _create_expected_model_schema(
    connection: psycopg.Connection,
    *,
    schema_name: str,
) -> set[tuple[str, str]]:
    backend_dir = str(REPO_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import app.models  # noqa: F401
    from app.db import Base
    from sqlalchemy import CheckConstraint, JSON, MetaData
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    expected_metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in LEGACY_DATABENTO_TABLE_NAMES:
            continue
        table.to_metadata(expected_metadata, schema=schema_name)

    # Generic JSON keeps the ORM portable to SQLite, while TopSignal's
    # canonical PostgreSQL schema intentionally uses JSONB for every JSON model
    # column. Materialize that PostgreSQL-specific contract in the scratch DDL.
    for table in expected_metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSON) and not isinstance(column.type, postgresql.JSONB):
                column.type = postgresql.JSONB()

    expected_check_names = {
        (table.name, str(constraint.name))
        for table in expected_metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }

    connection.execute(
        sql.SQL("create schema {}").format(sql.Identifier(schema_name))
    )
    dialect = postgresql.dialect()
    for table in expected_metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        connection.execute(ddl, prepare=False)
    return expected_check_names


def _schema_contract_errors(
    *,
    expected: _SchemaContract,
    actual: _SchemaContract,
    expected_check_names: set[tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    expected_tables = sorted({table_name for table_name, _column_name in expected.columns})
    actual_tables = {table_name for table_name, _column_name in actual.columns}
    for table_name in expected_tables:
        if table_name not in actual_tables:
            errors.append(f"missing application table {table_name}")
            continue
        expected_columns = {
            column_name
            for candidate_table, column_name in expected.columns
            if candidate_table == table_name
        }
        actual_columns = {
            column_name
            for candidate_table, column_name in actual.columns
            if candidate_table == table_name
        }
        unexpected_columns = sorted(actual_columns - expected_columns)
        if unexpected_columns:
            errors.append(
                f"{table_name} has unexpected model-table columns: "
                f"{', '.join(unexpected_columns)}"
            )

    for key, expected_column in sorted(expected.columns.items()):
        actual_column = actual.columns.get(key)
        table_name, column_name = key
        if actual_column is None:
            errors.append(f"{table_name} missing column {column_name}")
            continue
        if actual_column.postgres_type != expected_column.postgres_type:
            errors.append(
                f"{table_name}.{column_name} has PostgreSQL type "
                f"{actual_column.postgres_type}; expected {expected_column.postgres_type}"
            )
        if actual_column.nullable != expected_column.nullable:
            expected_nullability = "NULL" if expected_column.nullable else "NOT NULL"
            errors.append(f"{table_name}.{column_name} must be {expected_nullability}")
        if actual_column.default_expression != expected_column.default_expression:
            errors.append(
                f"{table_name}.{column_name} has default "
                f"{actual_column.default_expression!r}; expected {expected_column.default_expression!r}"
            )

    for table_name, expected_primary_key in sorted(expected.primary_keys.items()):
        actual_primary_key = actual.primary_keys.get(table_name)
        if actual_primary_key != expected_primary_key:
            errors.append(
                f"{table_name} has primary key {actual_primary_key!r}; "
                f"expected {expected_primary_key!r}"
            )

    # Application tables are an exact model contract. Deliberately ignore
    # non-model tables (for example the migration ledger/baseline tables), but
    # reject extra columns and foreign keys on every table owned by the ORM: an
    # additive FK can still change write/delete behavior and MATCH semantics.
    for table_name in expected_tables:
        expected_foreign_keys = set(expected.foreign_keys.get(table_name, ()))
        actual_foreign_keys = set(actual.foreign_keys.get(table_name, ()))
        for expected_foreign_key in sorted(
            expected_foreign_keys - actual_foreign_keys,
            key=repr,
        ):
            errors.append(
                f"{table_name} is missing foreign key "
                f"{expected_foreign_key.local_columns!r} -> "
                f"{expected_foreign_key.remote_table}{expected_foreign_key.remote_columns!r} "
                f"MATCH {expected_foreign_key.match_type} "
                f"ON DELETE {expected_foreign_key.on_delete} "
                f"ON UPDATE {expected_foreign_key.on_update}"
            )
        for unexpected_foreign_key in sorted(
            actual_foreign_keys - expected_foreign_keys,
            key=repr,
        ):
            errors.append(
                f"{table_name} has unexpected foreign key "
                f"{unexpected_foreign_key.local_columns!r} -> "
                f"{unexpected_foreign_key.remote_table}{unexpected_foreign_key.remote_columns!r} "
                f"MATCH {unexpected_foreign_key.match_type} "
                f"ON DELETE {unexpected_foreign_key.on_delete} "
                f"ON UPDATE {unexpected_foreign_key.on_update}"
            )

    for table_name, expected_checks in sorted(expected.checks.items()):
        actual_checks = actual.checks.get(table_name, ())
        for expected_check in expected_checks:
            if (table_name, expected_check.name) not in expected_check_names:
                continue
            expected_terms = _catalog_expression_conjuncts(expected_check.expression)
            matching_check = next(
                (
                    actual_check
                    for actual_check in actual_checks
                    if expected_terms.issubset(
                        _catalog_expression_conjuncts(actual_check.expression)
                    )
                ),
                None,
            )
            if matching_check is None:
                errors.append(
                    f"{table_name} is missing structural CHECK {expected_check.name}: "
                    f"{expected_check.expression}"
                )
            elif not matching_check.validated or matching_check.no_inherit:
                errors.append(
                    f"{table_name} CHECK {expected_check.name} must be validated and inherited"
                )
    return errors


def _validate_current_schema_for_adoption(connection: psycopg.Connection) -> None:
    _expected_columns, expected_indexes = _current_model_manifest()
    expected_unique_constraints, expected_unique_indexes = _current_model_uniqueness_manifest()
    expected_schema_name = f"ts_adopt_expected_{uuid4().hex}"
    errors: list[str] = []
    try:
        expected_check_names = _create_expected_model_schema(
            connection,
            schema_name=expected_schema_name,
        )
        expected_contract = _load_schema_contract(
            connection,
            schema_name=expected_schema_name,
        )
        actual_contract = _load_schema_contract(connection, schema_name="public")
        errors.extend(
            _schema_contract_errors(
                expected=expected_contract,
                actual=actual_contract,
                expected_check_names=expected_check_names,
            )
        )

        # Keep the existing named-index and uniqueness validation in addition
        # to the catalog-level table contract above. Standalone indexes are not
        # emitted by CreateTable, and adopting without them can silently remove
        # idempotency or make critical per-user paths unusably slow.
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
    finally:
        # The model contract is materialized only so PostgreSQL itself parses
        # types/defaults/check expressions. It and every validation read are
        # rolled back before adoption can execute security DDL or ledger writes.
        connection.rollback()

    if errors:
        raise RuntimeError(
            "Cannot adopt an outdated or incomplete populated schema: " + "; ".join(errors)
        )


def _record_all_migrations(
    connection: psycopg.Connection,
    files: list[Path],
    *,
    execute_migrations: frozenset[str] = frozenset(),
) -> None:
    paths_by_name = {path.name: path for path in files}
    missing = sorted(execute_migrations - paths_by_name.keys())
    if missing:
        raise RuntimeError(
            "Required adoption security migrations are missing: " + ", ".join(missing)
        )

    with connection.transaction():
        for migration_name in sorted(execute_migrations):
            migration_sql = paths_by_name[migration_name].read_text(encoding="utf-8")
            connection.execute(migration_sql, prepare=False)
        # Baseline/adoption validation runs before the ledger exists. Create it
        # in the same transaction as the required security DDL and history so
        # any failure leaves neither a ledger artifact nor partial adoption.
        connection.execute(
            f"""
            create table if not exists {LEDGER_TABLE} (
              version text primary key,
              checksum_sha256 text not null,
              applied_at timestamptz not null default now()
            )
            """
        )
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
            applied = _read_applied(
                connection,
                create=not (check_only or baseline or adopt_current),
            )
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
                _record_all_migrations(
                    connection,
                    files,
                    execute_migrations=ADOPTION_EXECUTED_MIGRATIONS,
                )
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
