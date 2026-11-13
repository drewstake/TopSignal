"""Check, then explicitly retire mirror production in a permitted maintenance window.

Stop application writers, drain the consumer, stop/disable its task, then run
this tool. It performs fresh read-only reconciliation before the atomic local
transition. It never deletes cloud data, outbox history, roles or recovery files.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from dotenv import dotenv_values
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.mirror_retirement import retire_source
from tools.backup_execution_database import connection_parameters
from tools.verify_local_mirror import verify


def checked_backup(path, *, now=None):
    path = Path(path)
    raw = path.read_bytes()
    result = json.loads(raw)
    if not all(result.get(key) is True for key in
               ('verified','authority_absent','auth_issuance','auth_restart','auth_revocation')):
        raise ValueError('Verified off-device database and auth restore required')
    if result.get('auth_network_calls') != 0 or not str(result.get('database','')).startswith('topsignal_audit_'):
        raise ValueError('Cloud-independent isolated restore required')
    for name in ('verified_at','backup_created_at'):
        verified = datetime.fromisoformat(result[name])
        age = ((now or datetime.now(timezone.utc)) - verified).total_seconds()
        if not 0 <= age <= 7200:
            raise ValueError('Fresh backup and off-device restore required (within two hours)')
    files = result.get('encrypted_files', {})
    expected = {'execution.dump.fernet','manifest.fernet','credential-key.fernet',
                'local-auth.sqlite3.fernet','runtime-env.fernet'}
    if set(files) != expected or not re.fullmatch(r'[0-9a-f]{64}', result.get('dump_sha256','')):
        raise ValueError('Complete encrypted recovery bundle required')
    for name, digest in files.items():
        if hashlib.sha256((path.parent/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Recovery bundle differs from verified off-device copy')
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check','retire'))
    parser.add_argument('--admin-env', required=True, type=Path)
    parser.add_argument('--sync-config', required=True, type=Path)
    parser.add_argument('--backup-receipt', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--reader-role', action='append', required=True)
    args = parser.parse_args()
    try:
        if args.receipt.exists():
            raise ValueError('Receipt already exists')
        backup_digest = checked_backup(args.backup_receipt)
        source = connection_parameters(dotenv_values(args.admin_env, interpolate=False)['MIGRATION_DATABASE_URL'])
        config = json.loads(args.sync_config.read_text(encoding='utf-8-sig'))
        target = connection_parameters(config['target_url'])
        if source['host'] not in {'127.0.0.1','localhost','::1'} or target['host'] in {'127.0.0.1','localhost','::1'}:
            raise ValueError('Explicit local authority and remote mirror required')
        with psycopg.connect(**source, autocommit=True) as local:
            with psycopg.connect(**target, autocommit=True) as cloud:
                reconciliation = verify(local, cloud)
            if (not reconciliation['verified'] or reconciliation['checkpoint_advanced']
                    or reconciliation['latest_pending_events']):
                raise RuntimeError('Final mirror snapshot is not quiet and reconciled')
            digest = hashlib.sha256(json.dumps(reconciliation, sort_keys=True).encode()).hexdigest()
            result = dict(command=args.command, reconciliation=reconciliation,
                          reconciliation_sha256=digest, backup_sha256=backup_digest)
            if args.command == 'retire':
                with local.transaction():
                    result['retirement'] = retire_source(local, authority_id=reconciliation['source_id'],
                        acknowledged_seq=reconciliation['acknowledged_seq'], reconciliation_sha256=digest,
                        backup_sha256=backup_digest, reader_roles=args.reader_role)
            result['observed_at'] = datetime.now(timezone.utc).isoformat()
        with args.receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps(dict(command=args.command, verified=True, receipt=str(args.receipt))))
    except Exception as error:
        print(json.dumps({'verified':False,'error_type':type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
