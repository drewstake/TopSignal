"""Encrypted, snapshot-consistent PostgreSQL backup and isolated restore test.

No production overwrite, authority activation, cloud deletion, or credentials
on command lines. The backup key is Windows-user protected and kept separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database_security import validate_postgres_tls_url
from tools.storage_backup_crypto import load_key

RESEARCH = {'market_observations', 'projectx_market_candles', 'decision_research_snapshots'}


def connection_parameters(url):
    tls = validate_postgres_tls_url(url)
    parsed = make_url(url)
    if parsed.port == 6543:
        raise ValueError('Session/direct PostgreSQL required')
    return dict(host=parsed.host, port=parsed.port or 5432, dbname=parsed.database,
                user=parsed.username, password=parsed.password, connect_timeout=10, **tls)


def subprocess_env(parameters):
    environment = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    names = dict(host='PGHOST', port='PGPORT', dbname='PGDATABASE', user='PGUSER',
                 password='PGPASSWORD', sslmode='PGSSLMODE', sslrootcert='PGSSLROOTCERT',
                 gssencmode='PGGSSENCMODE', connect_timeout='PGCONNECT_TIMEOUT')
    environment.update({names[key]: str(value) for key, value in parameters.items() if value is not None})
    return environment


def run_pg(binary, arguments, parameters, data=None):
    result = subprocess.run([str(binary), *arguments], input=data, capture_output=True,
        env=subprocess_env(parameters), timeout=600,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('PostgreSQL backup/restore command failed')
    return result.stdout


def fingerprints(connection, *, progress=None):
    result = {}
    tables = connection.execute("""SELECT schemaname,tablename FROM pg_tables
        WHERE schemaname IN ('public','topsignal_local','topsignal_mirror') ORDER BY schemaname,tablename""").fetchall()
    for schema, name in tables:
        if name in RESEARCH or name.startswith('databento_') or (schema == 'topsignal_local' and name == 'authority'):
            continue
        table = sql.Identifier(schema, name)
        if progress:
            progress(schema + '.' + name)
        # JSONB's PostgreSQL representation preserves exact numeric values.
        keys = connection.execute('''SELECT a.attname FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord)
            JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
            WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.ord''', (schema + '.' + name,)).fetchall()
        if not keys:
            raise ValueError('Backup table requires a primary key')
        order = sql.SQL(',').join(sql.Identifier(k[0]) for k in keys)
        digest = hashlib.sha256()
        count = 0
        with connection.cursor(name='verify_' + uuid4().hex) as rows:
            rows.execute(sql.SQL('SELECT to_jsonb(t)::text AS value FROM {} t ORDER BY {}').format(table, order))
            for (value,) in rows:
                digest.update((value + '\n').encode())
                count += 1
        result[schema + '.' + name] = dict(rows=count, sha256=digest.hexdigest())
    return result


def verify_sequences(connection):
    columns = connection.execute('''SELECT table_schema,table_name,column_name,
        pg_get_serial_sequence(format('%I.%I',table_schema,table_name),column_name)
        FROM information_schema.columns WHERE table_schema='public'
        AND (column_default LIKE 'nextval(%' OR is_identity='YES')''').fetchall()
    for schema, table, column, sequence in columns:
        if not sequence or table in RESEARCH or table.startswith('databento_'):
            continue
        maximum = connection.execute(sql.SQL('SELECT max({}) FROM {}').format(
            sql.Identifier(column), sql.Identifier(schema, table))).fetchone()[0]
        if maximum is None:
            continue
        # Resolve the catalog OID instead of parsing quoted identifier strings.
        seq_schema, seq_name = connection.execute('''SELECT n.nspname,c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=%s::regclass''', (sequence,)).fetchone()
        value, called = connection.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier(seq_schema, seq_name))).fetchone()
        if value < maximum or (value == maximum and not called):
            raise ValueError('Restored sequence would reuse a stored identifier')


def backup(*, url, pg_bin, destination, key, credential_key=None, progress=None,
           runtime_environment=None, local_auth_path=None):
    destination.mkdir(parents=True, exist_ok=False)
    parameters = connection_parameters(url)
    with psycopg.connect(**parameters, prepare_threshold=None) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL timezone='UTC'")
        conn.execute("SET LOCAL statement_timeout='120s'")
        snapshot = conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
        manifest = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'database_bytes': conn.execute('SELECT pg_database_size(current_database())').fetchone()[0],
                    'tables': fingerprints(conn, progress=progress), 'authority_excluded': True,
                    'policy_roles': sorted({role for (roles,) in conn.execute(
                        "SELECT roles FROM pg_policies WHERE schemaname IN ('public','topsignal_local','topsignal_research','topsignal_mirror')")
                        for role in roles if role != 'public'})}
        arguments = ['--format=custom', '--no-owner', '--no-acl', '--schema=public',
                     '--schema=topsignal_local', '--schema=topsignal_research', '--schema=topsignal_mirror',
                     '--exclude-table=public.databento_*', '--exclude-table-data=topsignal_local.authority',
                     '--snapshot=' + snapshot]
        arguments += ['--exclude-table-data=public.' + name for name in sorted(RESEARCH)]
        dump = run_pg(pg_bin / 'pg_dump.exe', arguments, parameters)
    if not dump.startswith(b'PGDMP'):
        raise ValueError('Invalid PostgreSQL archive')
    encrypted = Fernet(key).encrypt(dump)
    (destination / 'execution.dump.fernet').write_bytes(encrypted)
    manifest.update(dump_sha256=hashlib.sha256(dump).hexdigest(), encrypted_sha256=hashlib.sha256(encrypted).hexdigest(),
                    encrypted_bytes=len(encrypted))
    if credential_key:
        # Separate artifact: the database alone cannot decrypt provider records.
        Fernet(credential_key.encode())
        secrets_blob = Fernet(key).encrypt(json.dumps({'CREDENTIALS_ENCRYPTION_KEY': credential_key}).encode())
        (destination / 'credential-key.fernet').write_bytes(secrets_blob)
        manifest['credential_key_encrypted_sha256'] = hashlib.sha256(secrets_blob).hexdigest()
    if runtime_environment is not None:
        blob = Fernet(key).encrypt(runtime_environment)
        (destination / 'runtime-env.fernet').write_bytes(blob)
        manifest['runtime_env_encrypted_sha256'] = hashlib.sha256(blob).hexdigest()
    if local_auth_path:
        from contextlib import closing
        with closing(sqlite3.connect(Path(local_auth_path).resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(':memory:')) as snapshot:
                source.backup(snapshot)
                if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Local authentication backup integrity failure')
                image = snapshot.serialize()
                # Online backup has incorporated every committed WAL page.
                # A standalone image must use rollback-journal header versions;
                # an in-memory restore cannot open an external WAL sidecar.
                if image[:16] != b'SQLite format 3\x00' or image[18:20] not in {b'\x01\x01', b'\x02\x02'}:
                    raise ValueError('Unsupported authentication SQLite image')
                image = image[:18] + b'\x01\x01' + image[20:]
                blob = Fernet(key).encrypt(image)
        (destination / 'local-auth.sqlite3.fernet').write_bytes(blob)
        manifest['local_auth_encrypted_sha256'] = hashlib.sha256(blob).hexdigest()
    # Authenticate the counts and hashes, as well as the dump itself.
    (destination / 'manifest.fernet').write_bytes(Fernet(key).encrypt(json.dumps(manifest, sort_keys=True).encode()))
    (destination / 'receipt.json').write_text(json.dumps(manifest, indent=2))
    return manifest


def decrypt_backup(directory, key):
    manifest = json.loads(Fernet(key).decrypt((directory / 'manifest.fernet').read_bytes()))
    encrypted = (directory / 'execution.dump.fernet').read_bytes()
    if hashlib.sha256(encrypted).hexdigest() != manifest['encrypted_sha256']:
        raise ValueError('Encrypted backup checksum mismatch')
    dump = Fernet(key).decrypt(encrypted)
    if hashlib.sha256(dump).hexdigest() != manifest['dump_sha256']:
        raise ValueError('Backup content checksum mismatch')
    if 'credential_key_encrypted_sha256' in manifest:
        blob = (directory / 'credential-key.fernet').read_bytes()
        if hashlib.sha256(blob).hexdigest() != manifest['credential_key_encrypted_sha256']:
            raise ValueError('Credential-key backup checksum mismatch')
        credential = json.loads(Fernet(key).decrypt(blob))['CREDENTIALS_ENCRYPTION_KEY']
        Fernet(credential.encode())
    for field, filename in [('runtime_env_encrypted_sha256', 'runtime-env.fernet'),
                            ('local_auth_encrypted_sha256', 'local-auth.sqlite3.fernet')]:
        if field in manifest:
            blob = (directory / filename).read_bytes()
            if hashlib.sha256(blob).hexdigest() != manifest[field]:
                raise ValueError('Runtime backup checksum mismatch')
            plaintext = Fernet(key).decrypt(blob)
            if filename.startswith('local-auth'):
                from contextlib import closing
                with closing(sqlite3.connect(':memory:')) as auth:
                    auth.deserialize(plaintext)
                    if auth.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Restored local authentication integrity failure')
    return manifest, dump


def verify_credentials(connection, directory, key, manifest):
    if not connection.execute("SELECT to_regclass('public.provider_credentials')").fetchone()[0]:
        return 0
    rows = connection.execute('SELECT username_encrypted,api_key_encrypted FROM public.provider_credentials').fetchall()
    if not rows:
        return 0
    if 'credential_key_encrypted_sha256' not in manifest:
        raise ValueError('Stored credentials require their separately backed-up encryption key')
    protected = (directory / 'credential-key.fernet').read_bytes()
    if hashlib.sha256(protected).hexdigest() != manifest['credential_key_encrypted_sha256']:
        raise ValueError('Credential-key backup checksum mismatch')
    credential_key = json.loads(Fernet(key).decrypt(protected))['CREDENTIALS_ENCRYPTION_KEY']
    cipher = Fernet(credential_key.encode())
    for values in rows:
        if not all(cipher.decrypt(value.encode()) for value in values):
            raise ValueError('Empty restored credential')
    return len(rows)


def restore(*, url, pg_bin, directory, key, database):
    if not database.startswith(('topsignal_audit_', 'topsignal_stage_')):
        raise ValueError('Restore requires a new isolated audit/staging database')
    parameters = connection_parameters(url)
    if parameters['host'] not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Restore is restricted to local PostgreSQL')
    manifest, dump = decrypt_backup(directory, key)
    with psycopg.connect(**{**parameters, 'dbname': 'postgres'}, autocommit=True) as admin:
        # pg_restore --no-acl still restores RLS policy role references. Preserve
        # those restrictions on a fresh recovery server without creating logins
        # or copying credentials/memberships. Existing roles are never modified.
        for role in manifest.get('policy_roles', []):
            if not admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
                                     'NOREPLICATION NOBYPASSRLS').format(sql.Identifier(role)))
        # No IF NOT EXISTS: an existing database is never overwritten.
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
        # Match the application/Supabase UTC contract for future connections,
        # not just the canonical timestamp formatting used during verification.
        admin.execute(sql.SQL("ALTER DATABASE {} SET timezone='UTC'").format(sql.Identifier(database)))
    parameters['dbname'] = database
    with psycopg.connect(**parameters) as fresh:
        # pg_dump's explicit schema selection includes CREATE SCHEMA public.
        # Only the empty schema in the database just created above is removed.
        fresh.execute('DROP SCHEMA public RESTRICT')
    run_pg(pg_bin / 'pg_restore.exe', ['--exit-on-error', '--single-transaction', '--no-owner', '--no-acl', '--dbname=' + database], parameters, dump)
    with psycopg.connect(**parameters) as conn:
        conn.execute("SET LOCAL timezone='UTC'")
        if fingerprints(conn) != manifest['tables']:
            raise ValueError('Restored row count/content mismatch')
        verify_sequences(conn)
        credentials = verify_credentials(conn, directory, key, manifest)
        if conn.execute("SELECT to_regclass('topsignal_local.authority')").fetchone()[0]:
            if conn.execute('SELECT count(*) FROM topsignal_local.authority').fetchone()[0]:
                raise ValueError('Restored execution authority must be absent')
    return dict(verified=True, verified_at=datetime.now(timezone.utc).isoformat(),
                backup_created_at=manifest['created_at'],
                authority_absent=True, database=database, tables=len(manifest['tables']),
                rows=sum(item['rows'] for item in manifest['tables'].values()),
                credential_rows_verified=credentials, dump_sha256=manifest['dump_sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--runtime-env-file', type=Path)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--database')
    args = parser.parse_args()
    environment = dotenv_values(args.env_file, interpolate=False)
    kwargs = dict(url=environment['DATABASE_URL'], pg_bin=args.pg_bin, key=load_key(args.key_file))
    if args.operation == 'backup':
        runtime_file = args.runtime_env_file or args.env_file
        runtime_values = dotenv_values(runtime_file, interpolate=False)
        result = backup(**kwargs, destination=args.directory, credential_key=runtime_values.get('CREDENTIALS_ENCRYPTION_KEY'),
                        runtime_environment=runtime_file.read_bytes(), local_auth_path=runtime_values.get('TOPSIGNAL_LOCAL_AUTH_DB'),
                        progress=lambda table: print(json.dumps({'checking_table': table}), flush=True))
    else:
        if not args.database:
            parser.error('--database required')
        result = restore(**kwargs, directory=args.directory, database=args.database)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
