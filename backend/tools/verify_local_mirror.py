"""Read-only reconciliation of matching execution/mirror snapshot checkpoints.

Later concurrent writes are reported separately without blocking trading.
Logical cloud deletions are excluded, but no records are deleted by this tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import psycopg
from psycopg import sql
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.local_sync import table_keys
from tools.backup_execution_database import connection_parameters


def source_checkpoint(connection):
    row = connection.execute('''SELECT a.source_id,a.active,s.acknowledged_seq,
        (SELECT count(*) FROM topsignal_local.outbox)
        FROM topsignal_local.authority a CROSS JOIN topsignal_local.sync_status s
        WHERE a.singleton AND s.singleton''').fetchone()
    if row is None or not row[1]:
        raise RuntimeError('Active local authority required')
    return row[0], row[2], row[3]


def target_checkpoint(connection):
    row = connection.execute('SELECT source_id,acknowledged_seq FROM topsignal_mirror.receipt WHERE singleton').fetchone()
    if row is None:
        raise RuntimeError('Mirror receipt missing')
    return row


def record_hashes(connection, keys, *, mirror=False):
    result = {}
    for name, primary_keys in sorted(keys.items()):
        table = sql.Identifier('public', name)
        predicate = sql.SQL('')
        parameters = ()
        if mirror:
            key_expression = sql.SQL(',').join(
                sql.SQL('{},r.{}').format(sql.Literal(key), sql.Identifier(key))
                for key in primary_keys)
            predicate = sql.SQL('''WHERE NOT EXISTS (
                SELECT 1 FROM topsignal_mirror.tombstones d WHERE d.table_name=%s
                AND d.deleted AND d.row_key=jsonb_build_object({}))''').format(key_expression)
            parameters = (name,)
        query = sql.SQL('SELECT to_jsonb(r)::text FROM {} r {} ORDER BY {}').format(
            table, predicate, sql.SQL(',').join(sql.SQL('r.{}').format(sql.Identifier(key)) for key in primary_keys))
        digest = hashlib.sha256()
        count = 0
        with connection.cursor(name='reconcile_records') as rows:
            rows.execute(query, parameters)
            for (value,) in rows:
                digest.update((value + '\n').encode())
                count += 1
        result[name] = dict(rows=count, sha256=digest.hexdigest())
    return result


def verify(source, target):
    """Connections must be idle/autocommit; no DML, DDL or advisory locks."""
    with source.transaction(), target.transaction():
        for connection in (source, target):
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            connection.execute("SET LOCAL timezone='UTC'")
            connection.execute("SET LOCAL statement_timeout='30s'")
            connection.execute("SET LOCAL lock_timeout='1s'")
        initial = source_checkpoint(source)
        remote_initial = target_checkpoint(target)
        if initial[0] != remote_initial[0]:
            raise RuntimeError('Mirror authority mismatch')
        if initial[2] or initial[1] != remote_initial[1]:
            return dict(verified=False, reason='synchronization_pending', pending_events=initial[2])
        keys = table_keys(source)
        if keys != table_keys(target):
            return dict(verified=False, reason='schema_keys_differ')
        local_hashes = record_hashes(source, keys)
        remote_hashes = record_hashes(target, keys, mirror=True)
        tombstones = target.execute('SELECT count(*) FROM topsignal_mirror.tombstones WHERE deleted').fetchone()[0]
    # Both repeatable-read snapshots represent the same acknowledged prefix:
    # the source had no pending events and the target receipt equalled it.
    # Later commits cannot alter either snapshot. Report subsequent progress
    # separately instead of requiring a quiet worker for the whole hash scan.
    with source.transaction(), target.transaction():
        for connection in (source, target):
            connection.execute('SET TRANSACTION READ ONLY')
        latest_source = source_checkpoint(source)
        latest_target = target_checkpoint(target)
        if latest_source[0] != initial[0] or latest_target[0] != remote_initial[0]:
            return dict(verified=False, reason='authority_changed_retry')
    mismatches = [name for name in keys if local_hashes[name] != remote_hashes[name]]
    return dict(verified=not mismatches, reason='content_mismatch' if mismatches else 'stable_checkpoint_matches',
                source_id=str(initial[0]), acknowledged_seq=initial[1],
                verification_scope='matched_repeatable_read_checkpoint',
                checkpoint_advanced=latest_source != initial or latest_target != remote_initial,
                latest_pending_events=latest_source[2],
                latest_source_acknowledged_seq=latest_source[1],
                latest_target_acknowledged_seq=latest_target[1],
                tables=len(keys), rows=sum(value['rows'] for value in local_hashes.values()),
                cloud_logical_deletions=tombstones, mismatched_tables=mismatches,
                table_hashes=local_hashes, observed_at=datetime.now(timezone.utc).isoformat())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-env', type=Path, required=True, help='Dedicated local read-only backup role')
    parser.add_argument('--sync-config', type=Path, required=True, help='Private config containing restricted target_url')
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    try:
        source_parameters = connection_parameters(dotenv_values(args.source_env)['DATABASE_URL'])
        if source_parameters['host'] not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('Reconciliation source must be local')
        target_parameters = connection_parameters(json.loads(args.sync_config.read_text())['target_url'])
        with psycopg.connect(**source_parameters, autocommit=True, prepare_threshold=None) as source:
            with psycopg.connect(**target_parameters, autocommit=True, prepare_threshold=None) as target:
                result = verify(source, target)
        with args.receipt.open('x') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({name: value for name, value in result.items() if name != 'table_hashes'}))
        raise SystemExit(0 if result['verified'] else 2)
    except Exception as error:
        print(json.dumps({'verified': False, 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
