"""Publish immutable encrypted execution backups for the existing archive pull.

The task uses a dedicated read-only PostgreSQL role and a protected backup key.
Incomplete backups remain outside the cache root; nothing is pruned here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.backup_execution_database import backup, connection_parameters, decrypt_backup
from tools.run_local_sync import status_file
from tools.storage_backup_crypto import load_key
from tools.runtime_configuration import runtime_environment_file


def run(config):
    staging = Path(config['staging_directory'])
    published = Path(config['publish_directory'])
    status = Path(config['status_file'])
    if not all(path.is_absolute() and path.is_dir() for path in (staging, published, status.parent)):
        raise ValueError('Existing absolute backup directories required')
    if staging == published or staging in published.parents or published in staging.parents:
        raise ValueError('Staging and published backups must be separate')
    if staging.drive.lower() != published.drive.lower():
        raise ValueError('Atomic backup publication requires the same volume')
    environment = dotenv_values(config['database_env_file'], interpolate=False)
    if connection_parameters(environment['DATABASE_URL'])['host'] not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Scheduled execution backup requires local PostgreSQL')
    runtime_file = runtime_environment_file(config)
    runtime = dotenv_values(runtime_file, interpolate=False)
    # Losing this key would make stored broker credentials unrecoverable.
    if not runtime.get('CREDENTIALS_ENCRYPTION_KEY') or not runtime.get('TOPSIGNAL_LOCAL_AUTH_DB'):
        raise ValueError('Execution backup requires credential key and local sign-in state')
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:12]
    destination = staging / identifier
    key = load_key(Path(config['key_file']))
    result = backup(url=environment['DATABASE_URL'], pg_bin=Path(config['pg_bin']),
                    destination=destination, key=key,
                    credential_key=runtime['CREDENTIALS_ENCRYPTION_KEY'],
                    runtime_environment=runtime_file.read_bytes(),
                    local_auth_path=runtime['TOPSIGNAL_LOCAL_AUTH_DB'])
    verified, _ = decrypt_backup(destination, key)
    if verified != result or verified['authority_excluded'] is not True:
        raise ValueError('Backup verification failed')
    if runtime_environment_file(config) != runtime_file:
        raise ValueError('Runtime release changed during backup; retry after activation completes')
    for path in destination.iterdir():
        with path.open('r+b') as stream:
            os.fsync(stream.fileno())
    # rename fails on collision; do not replace an existing immutable backup.
    destination.rename(published / identifier)
    receipt = dict(state='verified_and_published', observed_at=datetime.now(timezone.utc).isoformat(),
                   runtime_environment_file=str(runtime_file),
                   directory=str(published / identifier), dump_sha256=result['dump_sha256'],
                   tables=len(result['tables']), rows=sum(row['rows'] for row in result['tables'].values()),
                   encrypted_bytes=result['encrypted_bytes'], off_device_verification='pending_archive_pull')
    status_file(status, receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    try:
        configuration = json.loads(args.config.read_text())
        print(json.dumps(run(configuration)))
    except Exception as error:
        failure = dict(state='failed', error_type=type(error).__name__,
                       observed_at=datetime.now(timezone.utc).isoformat())
        if 'configuration' in locals():
            status_file(Path(configuration['status_file']), failure)
        print(json.dumps(failure), file=sys.stderr)
        raise SystemExit(1) from None
