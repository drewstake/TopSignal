"""Explicit maintenance-only retirement of mirror production, preserving fences.

The caller must stop application writers and the mirror task, verify matching
local/cloud contents and a fresh off-device restore, then supply their receipts.
No user records, outbox history, cloud data or credentials are deleted here.
"""
import re
from uuid import UUID
from psycopg import sql
from psycopg.pq import TransactionStatus


def retired(connection):
    if not connection.execute("SELECT to_regclass('topsignal_local.mirror_retirement')").fetchone()[0]:
        return False
    return bool(connection.execute('SELECT 1 FROM topsignal_local.mirror_retirement WHERE singleton').fetchone())


def retire_source(connection, *, authority_id, acknowledged_seq, reconciliation_sha256, backup_sha256, reader_roles=()):
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError('Mirror retirement requires an explicit maintenance transaction')
    if connection.info.host not in {'localhost', '127.0.0.1', '::1'}:
        raise RuntimeError('Mirror retirement requires local PostgreSQL')
    if type(acknowledged_seq) is not int or acknowledged_seq < 0:
        raise ValueError('Invalid final acknowledged sequence')
    connection.execute("SET LOCAL lock_timeout='5s'")
    connection.execute("SET LOCAL statement_timeout='30s'")
    if not all(re.fullmatch(r'[0-9a-f]{64}', value) for value in (reconciliation_sha256, backup_sha256)):
        raise ValueError('Verified reconciliation and off-device backup receipt hashes required')
    if retired(connection):
        raise RuntimeError('Mirror already retired; do not replay a maintenance transition')
    from .local_sync import SYNC_LOCK, table_keys
    connection.execute('SELECT pg_advisory_xact_lock(%s)', (SYNC_LOCK,))
    tables = table_keys(connection)
    for name in sorted(tables):
        connection.execute(sql.SQL('LOCK TABLE public.{} IN ACCESS EXCLUSIVE MODE').format(sql.Identifier(name)))
    authority = connection.execute('SELECT source_id,active FROM topsignal_local.authority WHERE singleton FOR UPDATE').fetchone()
    if not authority or UUID(str(authority[0])) != UUID(str(authority_id)) or authority[1] is not True:
        raise RuntimeError('Authority mismatch; no retirement changes applied')
    checkpoint = connection.execute('SELECT acknowledged_seq FROM topsignal_local.sync_status WHERE singleton FOR UPDATE').fetchone()
    if not checkpoint or checkpoint[0] != acknowledged_seq or connection.execute('SELECT 1 FROM topsignal_local.outbox LIMIT 1').fetchone():
        raise RuntimeError('Final mirror reconciliation is stale or outbox is not empty')
    for name in sorted(tables):
        triggers = dict(connection.execute("SELECT tgname,tgenabled FROM pg_trigger WHERE tgrelid=%s::regclass "
                                           "AND tgname IN ('topsignal_local_serialize','topsignal_local_capture')",
                                           ('public.' + name,)).fetchall())
        if triggers != {'topsignal_local_serialize': 'O', 'topsignal_local_capture': 'O'}:
            raise RuntimeError('Missing authority or mirror trigger; review schema before retirement')
    connection.execute('''CREATE TABLE topsignal_local.mirror_retirement (
        singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
        retired_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        authority_id uuid NOT NULL, acknowledged_seq bigint NOT NULL,
        reconciliation_sha256 text NOT NULL, backup_sha256 text NOT NULL);
        REVOKE ALL ON TABLE topsignal_local.mirror_retirement FROM PUBLIC;''')
    connection.execute('INSERT INTO topsignal_local.mirror_retirement(singleton,authority_id,acknowledged_seq,reconciliation_sha256,backup_sha256) '
                       'VALUES(true,%s,%s,%s,%s)', (authority_id, acknowledged_seq, reconciliation_sha256, backup_sha256))
    for role in reader_roles:
        connection.execute(sql.SQL('GRANT SELECT ON topsignal_local.mirror_retirement TO {}').format(sql.Identifier(role)))
    for name in sorted(tables):
        connection.execute(sql.SQL('ALTER TABLE public.{} DISABLE TRIGGER topsignal_local_capture').format(sql.Identifier(name)))
    return dict(tables=len(tables), acknowledged_seq=acknowledged_seq, authority_fences_preserved=True)
