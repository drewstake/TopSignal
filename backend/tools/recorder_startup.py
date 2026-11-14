"""Protected local-only credential lookup for the independent market recorder.

No application database initializer, cloud environment or order client is loaded.
The administrator provisions a separate read-only PostgreSQL login and pins the
existing owner/configuration in the protected recorder config.
"""
from pathlib import Path
import os
import sys
from uuid import UUID

from cryptography.fernet import Fernet
from dotenv import dotenv_values
import psycopg
from sqlalchemy.engine import make_url


def configuration(config):
    env_path = Path(config.get('credential_env_file', ''))
    backend = Path(config['backend_root'])
    if not env_path.is_absolute() or not env_path.is_file() or not backend.is_absolute():
        raise RuntimeError('Explicit protected recorder credential environment required')
    values = dotenv_values(env_path, interpolate=False)
    if any(values.get(name) != 'false' for name in
           ('TOPSIGNAL_LIVE_EXECUTION_ENABLED', 'TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION')):
        raise RuntimeError('Paper-only environment required')
    if any(value for name, value in values.items() if name.startswith('SUPABASE_')):
        raise RuntimeError('Recorder environment must be independent of cloud configuration')
    url = make_url(values.get('DATABASE_URL', ''))
    if (url.drivername != 'postgresql+psycopg' or url.host not in {'127.0.0.1', 'localhost', '::1'}
            or not url.username or url.username != config.get('database_role')):
        raise RuntimeError('Recorder requires its dedicated loopback PostgreSQL role')
    owner = str(UUID(config['owner_id']))
    if UUID(owner).int == 0:
        raise RuntimeError('Existing recorder owner required')
    key = Fernet(values.get('CREDENTIALS_ENCRYPTION_KEY', '').encode('ascii'))
    cache = Path(values.get('TOPSIGNAL_DATABENTO_CACHE_DIR', ''))
    if not cache.is_absolute() or (cache / 'depth-recordings-v1').resolve() != Path(config['recording_root']).resolve():
        raise RuntimeError('Recording root must be under configured filesystem cache')
    return values, url, owner, key, backend.resolve(), cache.resolve()


def load_context(config):
    values, url, owner, key, backend, cache = configuration(config)
    sys.path.insert(0, str(backend))
    from app.database_security import validate_postgres_tls_url
    validate_postgres_tls_url(values['DATABASE_URL'])
    # Explicit connection parameters prevent URL or inherited environment routing.
    with psycopg.connect(host=url.host, port=url.port or 5432, dbname=url.database,
                         user=url.username, password=url.password, connect_timeout=10,
                         options='-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=2000') as db:
        role = db.execute('SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                          'FROM pg_roles WHERE rolname=current_user').fetchone()
        writable = db.execute("""SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public'
            AND (has_table_privilege(current_user, quote_ident(schemaname)||'.'||quote_ident(tablename),
                'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') OR
                 has_any_column_privilege(current_user, quote_ident(schemaname)||'.'||quote_ident(tablename),'INSERT,UPDATE')))""").fetchone()[0]
        memberships = db.execute('SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member='
                                 '(SELECT oid FROM pg_roles WHERE rolname=current_user))').fetchone()[0]
        if not role or role[0] != config['database_role'] or any(role[1:]) or writable or memberships:
            raise RuntimeError('Recorder credential role must be read-only and unprivileged')
        row = db.execute('SELECT execution_mode,contract_id FROM public.bot_configs WHERE id=%s AND user_id=%s',
                         (config['bot_config_id'], owner)).fetchone()
        if not row or row[0] != 'dry_run' or not row[1].startswith('CON.F.US.MNQ.'):
            raise RuntimeError('Owned paper MNQ configuration required')
        credentials = db.execute('SELECT username_encrypted,api_key_encrypted FROM public.provider_credentials '
                                 "WHERE user_id=%s AND provider='projectx'", (owner,)).fetchall()
        if len(credentials) != 1:
            raise RuntimeError('One owned provider credential required')
        username, api_key = (key.decrypt(value.encode('utf-8')).decode('utf-8') for value in credentials[0])
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['TOPSIGNAL_DATABENTO_CACHE_DIR'] = str(cache)
    for name in ('TOPSIGNAL_LIVE_EXECUTION_ENABLED', 'TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION'):
        os.environ[name] = 'false'
    from app.services.depth_capture import ReadOnlyMarketClient
    from app.services.probabilistic_shadow import scope_hash
    client = ReadOnlyMarketClient(base_url=values['PROJECTX_API_BASE_URL'], username=username,
                                  api_key=api_key, timeout_seconds=15)
    return client, scope_hash(owner), cache / 'depth-recordings-v1'
