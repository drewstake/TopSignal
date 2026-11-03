"""Offline replacement of copied cloud fences by inactive local authority gates."""
from psycopg import sql
from psycopg.pq import TransactionStatus


LEGACY_TABLES = frozenset({
    'databento_import_batches', 'databento_import_files', 'databento_instruments',
    'databento_ohlcv_1m', 'databento_roll_schedule',
})


def grant_schema_ledger_reads(connection, *, app_role):
    """Expose restored migration metadata to the backend without write access."""
    if connection.info.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('Ledger access requires local PostgreSQL')
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError('Ledger access requires an explicit transaction')
    connection.execute("SET LOCAL lock_timeout='5s'")
    for name in ('topsignal_schema_baselines', 'topsignal_schema_migrations'):
        if connection.execute('SELECT to_regclass(%s)', ('public.' + name,)).fetchone()[0]:
            table = sql.Identifier('public', name)
            role = sql.Identifier(app_role)
            connection.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(table, role))
            connection.execute(sql.SQL('CREATE POLICY topsignal_backend_ledger_read ON {} FOR SELECT TO {} USING(true)').format(table, role))


def lock_offline_restore(connection):
    """Acquire DDL locks before GRANT/policy changes can race autovacuum."""
    if connection.info.host not in {'127.0.0.1', 'localhost', '::1'} or not connection.info.dbname.startswith(('topsignal_stage_', 'topsignal_audit_')):
        raise RuntimeError('Offline restore database required')
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError('Offline restore locks require an explicit transaction')
    if connection.execute("SELECT to_regclass('topsignal_local.authority')").fetchone()[0]:
        if connection.execute('SELECT EXISTS(SELECT 1 FROM topsignal_local.authority WHERE active)').fetchone()[0]:
            raise RuntimeError('Cannot lock an active execution authority')
    tables = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    connection.execute("SET LOCAL lock_timeout='15s'")
    if tables:
        connection.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(
            sql.SQL(',').join(sql.Identifier('public', name) for name in tables)))


def replace_restored_cloud_fences(connection, *, cloud_tables, source_id):
    """Only the five intentionally excluded legacy tables may be absent."""
    if connection.info.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('Fence replacement requires local PostgreSQL')
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError('Fence replacement requires an explicit transaction')
    authority = connection.execute('SELECT source_id,active FROM topsignal_local.authority WHERE singleton FOR UPDATE').fetchone()
    if authority is None or str(authority[0]) != str(source_id) or authority[1]:
        raise RuntimeError('Matching inactive local authority required')
    tables = {row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    expected = set(cloud_tables)
    missing = expected - tables
    if missing - LEGACY_TABLES or tables - expected:
        raise RuntimeError('Restored application table set differs from frozen cloud')
    copied = {row[0] for row in connection.execute('''SELECT c.relname
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND t.tgname='topsignal_mirror_fence' AND t.tgenabled='O' ''')}
    if copied != tables:
        raise RuntimeError('Restored cloud fences are incomplete')
    for name in sorted(tables):
        connection.execute(sql.SQL('DROP TRIGGER topsignal_mirror_fence ON {}').format(sql.Identifier('public', name)))
    return sorted(missing)
