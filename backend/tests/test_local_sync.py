"""Fault acceptance on disposable PostgreSQL only; never contacts a broker."""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url

from app.local_sync import install_source, install_target, install_cloud_fence, sync_once

URL = os.getenv('TOPSIGNAL_TEST_POSTGRES_URL', '')
pytestmark = pytest.mark.skipif(not URL, reason='Disposable PostgreSQL required')


@pytest.fixture
def databases():
    url = make_url(URL)
    assert url.database.startswith('topsignal_audit_')
    params = dict(host=url.host, port=url.port, user=url.username, password=url.password)
    names = ['topsignal_audit_sync_' + uuid4().hex[:12] for _ in range(2)]
    source_id = uuid4()
    with psycopg.connect(dbname='postgres', autocommit=True, **params) as admin:
        for name in names:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    def connect(index):
        return psycopg.connect(dbname=names[index], autocommit=True, **params)
    for i in range(2):
        with connect(i) as conn:
            conn.execute('''CREATE TABLE accounts(id bigint PRIMARY KEY, user_id uuid NOT NULL, value numeric(24,8));
                CREATE TABLE bot_order_attempts(id bigint PRIMARY KEY, account_id bigint REFERENCES accounts(id), idempotency_key text UNIQUE NOT NULL, status text NOT NULL);
                CREATE TABLE bot_runtime_leases(lease_name text PRIMARY KEY);
                CREATE TABLE market_observations(id bigint PRIMARY KEY);''')
            with conn.transaction():
                if i == 0:
                    install_source(conn, source_id=source_id)
                    conn.execute('UPDATE topsignal_local.authority SET active=true')
                else:
                    install_target(conn, source_id=source_id)
    yield connect
    with psycopg.connect(dbname='postgres', autocommit=True, **params) as admin:
        for name in names:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def seed(connection, key=1):
    with connection.transaction():
        connection.execute('INSERT INTO accounts VALUES(%s,%s,123.12345678)', (key, uuid4()))
        connection.execute("INSERT INTO bot_order_attempts VALUES(%s,%s,%s,'pending')", (key, key, 'intent-' + str(key)))


def test_mirror_retirement_is_atomic_stops_outbox_and_preserves_authority(databases, monkeypatch):
    from app.mirror_retirement import retire_source, retired
    from app.execution_storage import validate_authority
    from sqlalchemy import create_engine
    with databases(0) as source, databases(1) as target:
        identity = source.execute('SELECT source_id FROM topsignal_local.authority').fetchone()[0]
        params = dict(authority_id=identity, acknowledged_seq=0, reconciliation_sha256='a'*64, backup_sha256='b'*64)
        seed(source)
        with pytest.raises(RuntimeError, match='stale|not empty'):
            with source.transaction(): retire_source(source, **params)
        assert not retired(source)
        sync_once(source, target)
        params['acknowledged_seq'] = source.execute('SELECT acknowledged_seq FROM topsignal_local.sync_status').fetchone()[0]
        with source.transaction(): retire_source(source, **params)
        assert retired(source)
        seed(source, 2)
        assert source.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 0
        with pytest.raises(RuntimeError, match='retired'):
            sync_once(source, target)
        engine = create_engine(make_url(URL).set(database=source.info.dbname))
        monkeypatch.setenv('TOPSIGNAL_EXECUTION_STORAGE', 'local_postgres')
        monkeypatch.setenv('TOPSIGNAL_RESEARCH_STORAGE', 'local')
        monkeypatch.setenv('TOPSIGNAL_LOCAL_AUTHORITY_ID', str(identity))
        try:
            validate_authority(engine)
            source.execute('UPDATE topsignal_local.authority SET active=false')
            with pytest.raises(psycopg.errors.RaiseException, match='authority inactive'):
                seed(source, 3)
            with pytest.raises(RuntimeError, match='inactive'):
                validate_authority(engine)
        finally:
            engine.dispose()


def test_independent_recorder_uses_only_scoped_read_only_local_credentials(databases, tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from pathlib import Path
    from tools.recorder_startup import load_context
    from tools.provision_depth_recorder import provision
    from app.services.probabilistic_shadow import scope_hash
    role='topsignal_audit_depth_'+uuid4().hex[:12]
    owner=uuid4(); key=Fernet.generate_key(); cipher=Fernet(key)
    for name in ('PYTHON_DOTENV_DISABLED','TOPSIGNAL_DATABENTO_CACHE_DIR','TOPSIGNAL_LIVE_EXECUTION_ENABLED','TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION'):
        monkeypatch.setenv(name,os.getenv(name,''))
    with databases(0) as admin:
        try:
            admin.execute('CREATE TABLE bot_configs(id bigint PRIMARY KEY,user_id uuid,execution_mode text,contract_id text)')
            admin.execute('CREATE TABLE provider_credentials(user_id uuid,provider text,username_encrypted text,api_key_encrypted text)')
            admin.execute("INSERT INTO bot_configs VALUES(32,%s,'dry_run','CON.F.US.MNQ.Z26')",(owner,))
            admin.execute("INSERT INTO provider_credentials VALUES(%s,'projectx',%s,%s)",
                          (owner,cipher.encrypt(b'fixture-user').decode(),cipher.encrypt(b'fixture-key').decode()))
            admin.execute('ALTER TABLE bot_configs ENABLE ROW LEVEL SECURITY')
            admin.execute('ALTER TABLE provider_credentials ENABLE ROW LEVEL SECURITY')
            password = 'disposable-depth-fixture-password'
            with admin.transaction():
                provision(admin, role=role, password=password, owner_id=owner, bot_config_id=32)
            url=make_url(URL).set(database=admin.info.dbname,username=role,password=password)
            env=tmp_path/'recorder.env'
            env.write_text('DATABASE_URL='+url.render_as_string(hide_password=False)+'\nCREDENTIALS_ENCRYPTION_KEY='+key.decode()
                +'\nTOPSIGNAL_LIVE_EXECUTION_ENABLED=false\nTOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION=false\n'
                +'TOPSIGNAL_DATABENTO_CACHE_DIR='+tmp_path.as_posix()+'\nPROJECTX_API_BASE_URL=https://api.topstepx.com\n')
            config=dict(backend_root=str(Path(__file__).resolve().parents[1]),credential_env_file=str(env),
                database_role=role,owner_id=str(owner),bot_config_id=32,recording_root=str(tmp_path/'depth-recordings-v1'))
            client,scope,root=load_context(config)
            assert scope==scope_hash(str(owner)) and root==tmp_path/'depth-recordings-v1'
            admin.execute("INSERT INTO provider_credentials VALUES(%s,'projectx','other','other')",(uuid4(),))
            with psycopg.connect(host=url.host, port=url.port, dbname=url.database, user=role, password=password) as reader:
                assert reader.execute('SELECT user_id FROM provider_credentials').fetchall() == [(owner,)]
                with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                    reader.execute('DELETE FROM bot_configs')
            with pytest.raises(RuntimeError,match='permits authentication'):
                client._request_once('POST','/api/Order/place',json={})
            with pytest.raises(RuntimeError,match='Owned paper'):
                load_context(dict(config,owner_id=str(uuid4())))
            admin.execute(sql.SQL('GRANT INSERT ON bot_configs TO {}').format(sql.Identifier(role)))
            with pytest.raises(RuntimeError,match='read-only'):
                load_context(config)
        finally:
            admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_restored_fences_allow_only_intentionally_omitted_legacy_tables(databases):
    from tools.provision_execution_storage import LEGACY_TABLES, lock_offline_restore, replace_restored_cloud_fences
    with databases(0) as source:
        source_id = source.execute('SELECT source_id FROM topsignal_local.authority').fetchone()[0]
        tables = {row[0] for row in source.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
        with source.transaction():
            install_target(source, source_id=source_id)
            install_cloud_fence(source, sync_role='never_an_application_writer', tables=tables)
        with pytest.raises(RuntimeError, match='inactive'):
            with source.transaction():
                replace_restored_cloud_fences(source, cloud_tables=tables | LEGACY_TABLES, source_id=source_id)
        with pytest.raises(RuntimeError, match='active execution'):
            with source.transaction():
                lock_offline_restore(source)
        source.execute('UPDATE topsignal_local.authority SET active=false')
        with pytest.raises(RuntimeError, match='table set'):
            with source.transaction():
                replace_restored_cloud_fences(source, cloud_tables=tables | LEGACY_TABLES | {'missing_execution_table'}, source_id=source_id)
        assert source.execute("SELECT count(*) FROM pg_trigger WHERE tgname='topsignal_mirror_fence'").fetchone()[0] == len(tables)
        with source.transaction():
            lock_offline_restore(source)
            omitted = replace_restored_cloud_fences(source, cloud_tables=tables | LEGACY_TABLES, source_id=source_id)
        assert set(omitted) == LEGACY_TABLES
        with pytest.raises(psycopg.errors.RaiseException, match='inactive'):
            source.execute('UPDATE accounts SET id=id WHERE false')


def test_outage_and_restart_replay_after_cloud_commit_preserves_idempotency(databases):
    with databases(0) as src, databases(1) as dst:
        seed(src)
        # A disconnected cloud leaves both local state and outbox durable.
        dst.close()
        with pytest.raises(psycopg.OperationalError):
            sync_once(src, dst)
        assert src.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 2
    def crash():
        raise RuntimeError('process died after remote commit')
    with databases(0) as src, databases(1) as dst:
        with pytest.raises(RuntimeError, match='process died'):
            sync_once(src, dst, after_cloud_commit=crash)
        assert dst.execute('SELECT count(*) FROM bot_order_attempts').fetchone()[0] == 1
        assert src.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 2
    with databases(0) as src, databases(1) as dst:
        assert sync_once(src, dst) == 2
        assert sync_once(src, dst) == 0
        assert dst.execute('SELECT value::text FROM accounts').fetchone()[0] == '123.12345678'
        with pytest.raises(psycopg.errors.UniqueViolation):
            src.execute("INSERT INTO bot_order_attempts VALUES(2,1,'intent-1','pending')")
        assert src.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 0


def test_restored_schema_ledgers_are_readable_without_write_grants(databases):
    from tools.provision_execution_storage import grant_schema_ledger_reads
    role = 'audit_ledger_' + uuid4().hex[:12]
    with databases(0) as src:
        src.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        try:
            for name in ('topsignal_schema_baselines', 'topsignal_schema_migrations'):
                table = sql.Identifier(name)
                src.execute(sql.SQL('CREATE TABLE {}(version text PRIMARY KEY)').format(table))
                src.execute(sql.SQL("INSERT INTO {} VALUES('verified-fixture')").format(table))
                src.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(table))
            with src.transaction():
                grant_schema_ledger_reads(src, app_role=role)
            src.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
            try:
                for name in ('topsignal_schema_baselines', 'topsignal_schema_migrations'):
                    assert src.execute(sql.SQL('SELECT version FROM {}').format(sql.Identifier(name))).fetchall() == [('verified-fixture',)]
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        src.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(name)))
            finally:
                src.execute('RESET ROLE')
        finally:
            src.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            src.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_concurrent_writers_and_sync_workers_atomic_transactions(databases):
    def write(key):
        with databases(0) as src:
            seed(src, key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(1, 21)))
    def sync(_):
        with databases(0) as src, databases(1) as dst:
            return sync_once(src, dst)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for _ in range(20):
            list(pool.map(sync, range(4)))
    with databases(0) as src, databases(1) as dst:
        assert src.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 0
        assert dst.execute('SELECT count(*) FROM bot_order_attempts').fetchone()[0] == 20
        assert dst.execute('SELECT count(*) FROM accounts').fetchone()[0] == 20


def test_cloud_fence_rejects_stale_execution_and_lease_writes(databases):
    with databases(1) as dst:
        with pytest.raises(RuntimeError, match='explicit transaction'):
            install_cloud_fence(dst, sync_role='not_the_old_application', tables=['accounts'])
        with dst.transaction():
            install_cloud_fence(dst, sync_role='not_the_old_application',
                                tables=['accounts','bot_order_attempts','bot_runtime_leases','market_observations'])
        for statement in ["INSERT INTO bot_runtime_leases VALUES('worker')", 'TRUNCATE accounts CASCADE',
                          "INSERT INTO bot_order_attempts VALUES(1,1,'old','pending')",
                          'INSERT INTO market_observations VALUES(1)']:
            with pytest.raises(psycopg.errors.RaiseException, match='read-only mirror'):
                dst.execute(statement)


def test_logical_deletions_preserve_cloud_rows_and_authority_mismatch_blocks(databases):
    with databases(0) as src, databases(1) as dst:
        seed(src)
        sync_once(src, dst)
        src.execute('UPDATE accounts SET value=123456789012345.12345678')
        sync_once(src, dst)
        assert dst.execute('SELECT value::text FROM accounts').fetchone()[0] == '123456789012345.12345678'
        src.execute('DELETE FROM bot_order_attempts WHERE id=1')
        sync_once(src, dst)
        assert dst.execute('SELECT count(*) FROM bot_order_attempts').fetchone()[0] == 1
        assert dst.execute('SELECT deleted FROM topsignal_mirror.tombstones').fetchone()[0]
        src.execute("INSERT INTO bot_order_attempts VALUES(1,1,'intent-1','pending')")
        sync_once(src, dst)
        assert dst.execute('SELECT deleted FROM topsignal_mirror.tombstones').fetchone()[0] is False
        seed(src, 2)
        dst.execute('UPDATE topsignal_mirror.receipt SET source_id=%s', (uuid4(),))
        with pytest.raises(RuntimeError, match='authority mismatch'):
            sync_once(src, dst)
        assert src.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 2


def test_local_rollback_emits_no_event_and_inactive_authority_blocks(databases):
    with databases(0) as src:
        with pytest.raises(RuntimeError):
            with src.transaction():
                seed(src)
                raise RuntimeError('rollback')
        assert src.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 0
        src.execute('UPDATE topsignal_local.authority SET active=false')
        with pytest.raises(psycopg.errors.RaiseException, match='authority inactive'):
            seed(src)
        with pytest.raises(psycopg.errors.RaiseException, match='authority inactive'):
            src.execute("INSERT INTO bot_runtime_leases VALUES('stale-worker')")


def test_runtime_rejects_restored_authority_and_unmirrored_tables(databases, monkeypatch):
    from sqlalchemy import create_engine
    from app.execution_storage import validate_authority
    with databases(0) as src:
        authority = src.execute('SELECT source_id FROM topsignal_local.authority').fetchone()[0]
        engine = create_engine(make_url(URL).set(database=src.info.dbname))
        monkeypatch.setenv('TOPSIGNAL_EXECUTION_STORAGE', 'local_postgres')
        monkeypatch.setenv('TOPSIGNAL_RESEARCH_STORAGE', 'local')
        monkeypatch.setenv('TOPSIGNAL_LOCAL_AUTHORITY_ID', str(authority))
        try:
            validate_authority(engine)
            src.execute('CREATE TABLE newly_added_state(id bigint PRIMARY KEY)')
            with pytest.raises(RuntimeError, match='unfenced'):
                validate_authority(engine)
            src.execute('DELETE FROM topsignal_local.authority')
            with pytest.raises(RuntimeError, match='absent, inactive or mismatched'):
                validate_authority(engine)
        finally:
            engine.dispose()


def test_full_mirror_verification_handles_pending_deletes_and_detects_drift(databases):
    from tools.verify_local_mirror import verify
    with databases(0) as src, databases(1) as dst:
        seed(src)
        assert verify(src, dst)['reason'] == 'synchronization_pending'
        sync_once(src, dst)
        result = verify(src, dst)
        assert result['verified'] and result['rows'] == 2
        src.execute('DELETE FROM bot_order_attempts WHERE id=1')
        sync_once(src, dst)
        result = verify(src, dst)
        assert result['verified'] and result['rows'] == 1
        assert result['cloud_logical_deletions'] == 1
        # Drift without a corresponding receipt advance cannot masquerade as
        # successful reconciliation merely because the queue is empty.
        dst.execute('UPDATE accounts SET value=0 WHERE id=1')
        result = verify(src, dst)
        assert not result['verified'] and result['mismatched_tables'] == ['accounts']


def test_mirror_verification_preserves_snapshot_proof_during_concurrent_writer(databases, monkeypatch):
    import tools.verify_local_mirror as checker
    original = checker.record_hashes
    with databases(0) as src, databases(1) as dst:
        seed(src)
        sync_once(src, dst)
        def concurrent_write(connection, keys, *, mirror=False):
            result = original(connection, keys, mirror=mirror)
            if mirror:
                with databases(0) as writer:
                    seed(writer, 2)
            return result
        monkeypatch.setattr(checker, 'record_hashes', concurrent_write)
        result = checker.verify(src, dst)
        assert result['verified'] and result['checkpoint_advanced']
        assert result['rows'] == 2  # The fixed snapshots exclude the later write.
        assert result['latest_pending_events'] == 2
        assert result['verification_scope'] == 'matched_repeatable_read_checkpoint'
