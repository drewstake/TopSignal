"""Durable, one-way PostgreSQL mirror. Never dispatches provider operations.

The source transaction and its outbox commit together. Cloud acknowledgement is
committed before local acknowledgement; replay after a crash is harmless. There
is no automatic failover, bidirectional merge, or cloud execution lease.
"""
from __future__ import annotations

import json
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

EXCLUDED_TABLES = frozenset({
    'market_observations', 'projectx_market_candles', 'decision_research_snapshots',
    'bot_runtime_leases', 'topsignal_schema_baselines', 'topsignal_schema_migrations',
})
SYNC_LOCK = 78439120361102


def _require_provisioning_transaction(connection):
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError('Storage provisioning requires an explicit transaction')


def eligible(name):
    return name not in EXCLUDED_TABLES and not name.startswith(('databento_', 'topsignal_local_', 'trade_progress_'))


def table_keys(connection):
    rows = connection.execute("""
        SELECT t.relname, array_agg(a.attname ORDER BY k.ordinality)
        FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_index i ON i.indrelid=t.oid AND i.indisprimary
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ordinality)
        JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
        WHERE n.nspname='public' AND t.relkind='r'
        GROUP BY t.relname ORDER BY t.relname
    """).fetchall()
    result = {name: keys for name, keys in rows if eligible(name)}
    tables = {row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'") if eligible(row[0])}
    if tables != set(result):
        raise RuntimeError('Every mirrored table requires a primary key')
    return result


def install_source(connection, *, source_id):
    """Explicit offline provisioning; caller must stop all application writers."""
    _require_provisioning_transaction(connection)
    keys = table_keys(connection)
    if connection.execute("SELECT 1 FROM pg_constraint WHERE connamespace='public'::regnamespace AND condeferrable LIMIT 1").fetchone():
        raise RuntimeError('Review existing deferred constraints before installing commit-order capture')
    connection.execute("""
        CREATE SCHEMA topsignal_local;
        REVOKE ALL ON SCHEMA topsignal_local FROM PUBLIC;
        CREATE TABLE topsignal_local.authority (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            source_id uuid NOT NULL, active boolean NOT NULL DEFAULT false
        );
        CREATE TABLE topsignal_local.outbox (
            seq bigserial PRIMARY KEY, transaction_id bigint NOT NULL,
            table_name text NOT NULL, operation text NOT NULL CHECK(operation IN ('INSERT','UPDATE','DELETE')),
            row_data jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ON topsignal_local.outbox(transaction_id,seq);
        CREATE TABLE topsignal_local.sync_status (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            acknowledged_seq bigint NOT NULL DEFAULT 0, last_success_at timestamptz
        );
        INSERT INTO topsignal_local.sync_status(singleton) VALUES(true);
        CREATE FUNCTION topsignal_local.serialize_writes() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            IF (SELECT active FROM topsignal_local.authority WHERE singleton) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'Local execution authority inactive';
            END IF;
            RETURN NULL;
        END $$;
        CREATE FUNCTION topsignal_local.capture_change() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            -- Deferred until commit: never retain this global ordering lock
            -- during broker I/O or ordinary application row-lock acquisition.
            PERFORM pg_advisory_xact_lock(78439120361101);
            INSERT INTO topsignal_local.outbox(transaction_id,table_name,operation,row_data)
            VALUES(txid_current(),TG_TABLE_NAME,TG_OP,
                   CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END);
            RETURN NULL;
        END $$;
        REVOKE ALL ON FUNCTION topsignal_local.serialize_writes() FROM PUBLIC;
        REVOKE ALL ON FUNCTION topsignal_local.capture_change() FROM PUBLIC;
        CREATE FUNCTION topsignal_local.require_authority() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            IF (SELECT active FROM topsignal_local.authority WHERE singleton) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'Local execution authority inactive';
            END IF;
            RETURN NULL;
        END $$;
        REVOKE ALL ON FUNCTION topsignal_local.require_authority() FROM PUBLIC;
    """)
    connection.execute('INSERT INTO topsignal_local.authority(source_id) VALUES(%s)', (source_id,))
    for name in keys:
        connection.execute(sql.SQL('CREATE TRIGGER topsignal_local_serialize BEFORE INSERT OR UPDATE OR DELETE ON public.{} FOR EACH STATEMENT EXECUTE FUNCTION topsignal_local.serialize_writes()').format(sql.Identifier(name)))
        connection.execute(sql.SQL('CREATE CONSTRAINT TRIGGER topsignal_local_capture AFTER INSERT OR UPDATE OR DELETE ON public.{} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION topsignal_local.capture_change()').format(sql.Identifier(name)))
    if connection.execute("SELECT to_regclass('public.bot_runtime_leases')").fetchone()[0]:
        connection.execute('CREATE TRIGGER topsignal_local_authority BEFORE INSERT OR UPDATE OR DELETE ON public.bot_runtime_leases FOR EACH STATEMENT EXECUTE FUNCTION topsignal_local.require_authority()')
    return keys


def install_target(connection, *, source_id):
    _require_provisioning_transaction(connection)
    connection.execute("""
        CREATE SCHEMA topsignal_mirror;
        REVOKE ALL ON SCHEMA topsignal_mirror FROM PUBLIC;
        CREATE TABLE topsignal_mirror.receipt (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            source_id uuid NOT NULL, acknowledged_seq bigint NOT NULL DEFAULT 0,
            applied_at timestamptz
        );
        CREATE TABLE topsignal_mirror.tombstones (
            table_name text NOT NULL, row_key jsonb NOT NULL,
            deleted boolean NOT NULL, changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(table_name,row_key)
        );
    """)
    connection.execute('INSERT INTO topsignal_mirror.receipt(source_id) VALUES(%s)', (source_id,))


def install_cloud_fence(connection, *, sync_role, tables):
    """Deny stale applications writes, even if their old worker lease expires.

    All app tables, including leases and local-only research, must be fenced.
    The dedicated sync role is permitted only on mirrored tables via grants.
    """
    _require_provisioning_transaction(connection)
    connection.execute(sql.SQL("""
        CREATE FUNCTION topsignal_mirror.deny_old_writers() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF current_user <> {} THEN
                RAISE EXCEPTION 'Cloud is a read-only mirror; local authority required';
            END IF;
            RETURN NULL;
        END $$;
        REVOKE ALL ON FUNCTION topsignal_mirror.deny_old_writers() FROM PUBLIC;
    """).format(sql.Literal(sync_role)))
    for name in tables:
        connection.execute(sql.SQL('CREATE TRIGGER topsignal_mirror_fence BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.{} FOR EACH STATEMENT EXECUTE FUNCTION topsignal_mirror.deny_old_writers()').format(sql.Identifier(name)))


def _apply_row(target, name, operation, row, primary_keys):
    if not eligible(name) or name not in primary_keys:
        raise ValueError('Unregistered mirror table')
    # Keep PostgreSQL's exact JSON numeric representation through transport.
    # Decoding payload decimals through Python floats would lose money precision.
    raw_json = row if isinstance(row, str) else json.dumps(row)
    row = json.loads(raw_json)
    payload = Jsonb(raw_json, dumps=lambda value: value)
    keys = primary_keys[name]
    if not all(key in row and row[key] is not None for key in keys):
        raise ValueError('Missing mirror key')
    table = sql.Identifier('public', name)
    columns = list(row)
    # jsonb_populate_record uses the target's actual types, preserving numeric,
    # timestamp, UUID and JSON precision without ad-hoc Python conversions.
    if operation == 'DELETE':
        # Preserve cloud user/audit rows until a separate cleanup is approved.
        # Restores must apply these logical deletions before enabling execution.
        target.execute('''INSERT INTO topsignal_mirror.tombstones(table_name,row_key,deleted)
            VALUES(%s,%s,true) ON CONFLICT(table_name,row_key) DO UPDATE
            SET deleted=true,changed_at=clock_timestamp()''',
            (name, Jsonb({key: row[key] for key in keys})))
    elif operation in {'INSERT', 'UPDATE'}:
        assignments = sql.SQL(',').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(k), sql.Identifier(k)) for k in columns if k not in keys)
        conflict = sql.SQL('DO UPDATE SET {}').format(assignments) if len(columns) > len(keys) else sql.SQL('DO NOTHING')
        target.execute(sql.SQL('INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM jsonb_populate_record(NULL::{}, %s) r ON CONFLICT ({}) {}').format(
            table, sql.SQL(',').join(map(sql.Identifier, columns)),
            sql.SQL(',').join(sql.SQL('r.{}').format(sql.Identifier(k)) for k in columns),
            table, sql.SQL(',').join(map(sql.Identifier, keys)), conflict), (payload,))
        target.execute('UPDATE topsignal_mirror.tombstones SET deleted=false,changed_at=clock_timestamp() WHERE table_name=%s AND row_key=%s',
                       (name, Jsonb({key: row[key] for key in keys})))
    else:
        raise ValueError('Invalid mirror operation')


def sync_once(source, target, *, after_cloud_commit=None):
    """Apply one entire committed transaction, or return zero when caught up.

    Connections must be idle; this method owns both transactions. A source row
    lock serializes sync workers, separately from the application's write lock.
    Exceptions leave unacknowledged events durable for retry.
    """
    with source.transaction():
        if not source.execute('SELECT pg_try_advisory_xact_lock(%s)', (SYNC_LOCK,)).fetchone()[0]:
            return 0
        source_id, active = source.execute('SELECT source_id,active FROM topsignal_local.authority WHERE singleton').fetchone()
        if not active:
            raise RuntimeError('Source authority inactive')
        first = source.execute('SELECT transaction_id FROM topsignal_local.outbox ORDER BY seq LIMIT 1').fetchone()
        if first is None:
            return 0
        events = source.execute('SELECT seq,table_name,operation,row_data::text FROM topsignal_local.outbox WHERE transaction_id=%s ORDER BY seq', first).fetchall()
        maximum = events[-1][0]
        with target.transaction():
            receipt = target.execute('SELECT source_id,acknowledged_seq FROM topsignal_mirror.receipt WHERE singleton FOR UPDATE').fetchone()
            if receipt is None or receipt[0] != source_id:
                raise RuntimeError('Mirror authority mismatch')
            if receipt[1] < maximum:
                if receipt[1] >= events[0][0]:
                    raise RuntimeError('Partial transaction acknowledgement')
                keys = table_keys(target)
                for _, name, operation, row in events:
                    _apply_row(target, name, operation, row, keys)
                target.execute('UPDATE topsignal_mirror.receipt SET acknowledged_seq=%s, applied_at=clock_timestamp() WHERE singleton', (maximum,))
        if after_cloud_commit:
            after_cloud_commit()
        source.execute('DELETE FROM topsignal_local.outbox WHERE transaction_id=%s', first)
        source.execute('UPDATE topsignal_local.sync_status SET acknowledged_seq=greatest(acknowledged_seq,%s),last_success_at=clock_timestamp() WHERE singleton', (maximum,))
        return len(events)
