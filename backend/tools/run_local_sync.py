"""Independent, restartable local PostgreSQL -> Supabase outbox consumer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.local_sync import sync_once
from app.mirror_retirement import retired
from tools.backup_execution_database import connection_parameters


def status_file(path, status):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(status, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def queue_status(source):
    count, oldest, size = source.execute('''SELECT count(*),min(created_at),
        pg_total_relation_size('topsignal_local.outbox') FROM topsignal_local.outbox''').fetchone()
    acknowledged, success = source.execute('SELECT acknowledged_seq,last_success_at FROM topsignal_local.sync_status WHERE singleton').fetchone()
    return dict(pending_events=count, oldest_pending_at=oldest.isoformat() if oldest else None,
                outbox_bytes=size, acknowledged_seq=acknowledged,
                last_success_at=success.isoformat() if success else None)


def run(config, *, once=False):
    source_parameters = connection_parameters(config['source_url'])
    target_parameters = connection_parameters(config['target_url'])
    if source_parameters['host'] not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Sync source must be local PostgreSQL')
    if target_parameters['host'] in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Production mirror must be the explicit remote target')
    receipt = Path(config['status_file'])
    if not receipt.is_absolute() or not receipt.parent.is_dir():
        raise ValueError('Private status directory required')
    stop = Path(config['stop_file'])
    if not stop.is_absolute():
        raise ValueError('Absolute stop file required')
    delay = 1
    while not stop.exists():
        state = dict(observed_at=datetime.now(timezone.utc).isoformat(), state='connecting')
        try:
            # Connections are exclusive to sync. Application pool availability
            # and cloud latency never hold application transactions open.
            with psycopg.connect(**source_parameters, autocommit=True, prepare_threshold=None) as source:
                source.execute("SET statement_timeout='30s'")
                source.execute("SET lock_timeout='5s'")
                if retired(source):
                    state.update(state='retired')
                    status_file(receipt, state)
                    return 0
                state.update(queue_status(source))
                with psycopg.connect(**target_parameters, autocommit=True, prepare_threshold=None) as target:
                    target.execute("SET statement_timeout='30s'")
                    target.execute("SET lock_timeout='5s'")
                    # Reconnect periodically so operator credential rotations
                    # and restart behavior have bounded transport lifetimes.
                    for _ in range(100):
                        if stop.exists():
                            break
                        applied = sync_once(source, target)
                        state.update(queue_status(source), state='caught_up' if not applied else 'syncing',
                                     observed_at=datetime.now(timezone.utc).isoformat())
                        status_file(receipt, state)
                        if not applied or once:
                            break
            delay = 1
        except Exception as error:
            state.update(state='retrying', error_type=type(error).__name__, retry_seconds=delay)
            status_file(receipt, state)
            if once:
                return 1
            delay = min(60, delay * 2)
        if once:
            return 0
        # The dedicated sync STOP never stops the trading worker or recorder.
        for _ in range(delay):
            if stop.exists():
                break
            time.sleep(1)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        raise SystemExit(run(json.loads(args.config.read_text()), once=args.once))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
