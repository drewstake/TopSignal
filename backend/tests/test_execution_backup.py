import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
import psycopg
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url

from tools.storage_backup_crypto import save_key, load_key
from tools.backup_execution_database import backup, restore, decrypt_backup
from tools.run_execution_backup import run as publish_backup
from app.local_sync import install_source


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_backup_key_is_wrapped_and_roundtrips_without_overwrite(tmp_path):
    key = Fernet.generate_key()
    path = tmp_path / 'key.dpapi'
    save_key(path, key)
    assert key not in path.read_bytes()
    assert load_key(path) == key
    with pytest.raises(FileExistsError):
        save_key(path, Fernet.generate_key())


@pytest.mark.skipif(not os.getenv('TOPSIGNAL_TEST_PG_BIN') or not os.getenv('TOPSIGNAL_TEST_POSTGRES_URL'),
                    reason='Disposable PostgreSQL and binary directory required')
def test_encrypted_restore_preserves_content_sequences_and_requires_fresh_authority(tmp_path):
    url = make_url(os.environ['TOPSIGNAL_TEST_POSTGRES_URL'])
    assert url.database.startswith('topsignal_audit_')
    names = ['topsignal_audit_backup_' + uuid4().hex[:12] for _ in range(2)]
    parameters = dict(host=url.host, port=url.port, user=url.username, password=url.password)
    binary = Path(os.environ['TOPSIGNAL_TEST_PG_BIN'])
    with psycopg.connect(dbname='postgres', autocommit=True, **parameters) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(names[0])))
    try:
        with psycopg.connect(dbname=names[0], **parameters) as src:
            src.execute('CREATE TABLE accounts(id bigserial PRIMARY KEY, payload jsonb, amount numeric(24,8))')
            src.execute('CREATE TABLE market_observations(id bigint PRIMARY KEY)')
            install_source(src, source_id=uuid4())
            src.execute('UPDATE topsignal_local.authority SET active=true')
            src.execute('INSERT INTO accounts(payload,amount) VALUES(%s::jsonb,123.12345678)', ('{"private":"test-only-record"}',))
            src.execute('INSERT INTO market_observations VALUES(1)')
        key = Fernet.generate_key()
        archive = tmp_path / 'backup'
        auth_path = tmp_path / 'auth.sqlite3'
        with sqlite3.connect(auth_path) as auth:
            auth.execute('PRAGMA journal_mode=WAL')
            auth.execute('CREATE TABLE owner(id integer PRIMARY KEY, signing_key text)')
            auth.execute("INSERT INTO owner VALUES(1,'fixture-only-key')")
        backup(url=url.set(database=names[0]).render_as_string(hide_password=False), pg_bin=binary,
               destination=archive, key=key, credential_key=Fernet.generate_key().decode(),
               runtime_environment=b'AUTH_REQUIRED=true\n', local_auth_path=auth_path)
        assert b'test-only-record' not in (archive / 'execution.dump.fernet').read_bytes()
        if os.name == 'nt':
            # Exercise real Windows flush/rename and key custody, not mocks.
            # Only a complete encrypted archive may enter the cache pull root.
            for directory in ('staging', 'published', 'status'):
                (tmp_path / directory).mkdir()
            key_file = tmp_path / 'scheduled-key.dpapi'
            save_key(key_file, key)
            env_file = tmp_path / 'database.env'
            env_file.write_text('DATABASE_URL=' + url.set(database=names[0]).render_as_string(hide_password=False) + '\n')
            runtime_file = tmp_path / 'runtime.env'
            runtime_file.write_text('CREDENTIALS_ENCRYPTION_KEY=' + Fernet.generate_key().decode()
                                    + '\nTOPSIGNAL_LOCAL_AUTH_DB=' + auth_path.as_posix() + '\n')
            published = publish_backup(dict(staging_directory=str(tmp_path / 'staging'),
                publish_directory=str(tmp_path / 'published'), status_file=str(tmp_path / 'status/backup.json'),
                database_env_file=str(env_file), runtime_env_file=str(runtime_file),
                key_file=str(key_file), pg_bin=str(binary)))
            assert published['state'] == 'verified_and_published'
            assert not list((tmp_path / 'staging').iterdir())
            archive = Path(published['directory'])
            assert all(path.suffix in {'.fernet', '.json'} for path in archive.iterdir())
            assert decrypt_backup(archive, key)[0]['authority_excluded'] is True
        result = restore(url=url.render_as_string(hide_password=False), pg_bin=binary,
                         directory=archive, key=key, database=names[1])
        assert result['verified']
        with psycopg.connect(dbname=names[1], autocommit=True, **parameters) as dst:
            assert dst.execute('SHOW timezone').fetchone()[0] == 'UTC'
            assert dst.execute('SELECT count(*) FROM topsignal_local.authority').fetchone()[0] == 0
            assert dst.execute('SELECT count(*) FROM topsignal_local.outbox').fetchone()[0] == 1
            assert dst.execute('SELECT count(*) FROM market_observations').fetchone()[0] == 0
            assert dst.execute("SELECT nextval('accounts_id_seq')").fetchone()[0] == 2
            with pytest.raises(psycopg.errors.RaiseException, match='authority inactive'):
                dst.execute('INSERT INTO accounts(amount) VALUES(1)')
        with pytest.raises(psycopg.errors.DuplicateDatabase):
            restore(url=url.render_as_string(hide_password=False), pg_bin=binary,
                    directory=archive, key=key, database=names[1])
        path = archive / 'execution.dump.fernet'
        path.write_bytes(path.read_bytes()[:-10] + b'corruption')
        with pytest.raises(ValueError, match='checksum'):
            decrypt_backup(archive, key)
    finally:
        with psycopg.connect(dbname='postgres', autocommit=True, **parameters) as admin:
            for name in names:
                admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))
