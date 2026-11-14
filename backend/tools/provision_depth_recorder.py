"""Maintenance-only provisioning of a new owner-scoped local recorder login.

Requires an administrator transaction and an already protected recorder env/config.
Never modifies an existing role, broker configuration or recording enrollment.
"""
import argparse
import json
from pathlib import Path
import re
import sys
from uuid import UUID

from dotenv import dotenv_values
import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.backup_execution_database import connection_parameters
from tools.recorder_startup import configuration


def provision(connection, *, role, password, owner_id, bot_config_id):
    if connection.info.host not in {'localhost', '127.0.0.1', '::1'}:
        raise RuntimeError('Recorder provisioning requires loopback PostgreSQL')
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError('Explicit maintenance transaction required')
    if not re.fullmatch(r'topsignal_(?:audit_)?depth_[a-z0-9_]{1,36}', role) or len(password) < 24:
        raise ValueError('Dedicated role and strong protected password required')
    owner = str(UUID(str(owner_id)))
    if UUID(owner).int == 0 or type(bot_config_id) is not int or bot_config_id <= 0:
        raise ValueError('Existing owner and paper configuration required')
    connection.execute("SET LOCAL lock_timeout='5s'")
    connection.execute("SET LOCAL statement_timeout='30s'")
    if connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
        raise RuntimeError('Recorder role already exists; never overwrite credentials')
    row = connection.execute('SELECT execution_mode,contract_id FROM public.bot_configs WHERE id=%s AND user_id=%s',
                             (bot_config_id, owner)).fetchone()
    if not row or row[0] != 'dry_run' or not row[1].startswith('CON.F.US.MNQ.'):
        raise RuntimeError('Owned paper MNQ configuration required')
    for table in ('bot_configs', 'provider_credentials'):
        if connection.execute('SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass',
                              ('public.'+table,)).fetchone() != (True,):
            raise RuntimeError('Recorder credential tables must have row security enabled')
    identity = sql.Identifier(role)
    connection.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
                               'NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 PASSWORD {}').format(identity, sql.Literal(password)))
    connection.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(identity))
    connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(connection.info.dbname), identity))
    connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(identity))
    for table, columns, extra in (
        ('bot_configs', ('id','user_id','execution_mode','contract_id'), sql.SQL('id={}').format(sql.Literal(bot_config_id))),
        ('provider_credentials', ('user_id','provider','username_encrypted','api_key_encrypted'), sql.SQL("provider='projectx'")),
    ):
        connection.execute(sql.SQL('GRANT SELECT ({}) ON public.{} TO {}').format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table), identity))
        connection.execute(sql.SQL('CREATE POLICY {} ON public.{} FOR SELECT TO {} USING(user_id={}::uuid AND {})').format(
            sql.Identifier(role+'_read'), sql.Identifier(table), identity, sql.Literal(owner), extra))
    return dict(role=role, owner_id=owner, bot_config_id=bot_config_id, read_only=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-env', type=Path, required=True)
    parser.add_argument('--recorder-config', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.receipt.exists():
            raise ValueError('Receipt already exists')
        config = json.loads(args.recorder_config.read_text(encoding='utf-8-sig'))
        _, url, owner, _, _, _ = configuration(config)
        admin = dotenv_values(args.admin_env, interpolate=False)
        parameters = connection_parameters(admin['MIGRATION_DATABASE_URL'])
        if parameters['dbname'] != url.database or parameters['port'] != (url.port or 5432):
            raise ValueError('Recorder and administrator database targets differ')
        with psycopg.connect(**parameters, autocommit=True) as db, db.transaction():
            result = provision(db, role=url.username, password=url.password, owner_id=owner,
                               bot_config_id=config['bot_config_id'])
        with args.receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({'provisioned': False, 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
