"""Explicit, bounded evidence capture. No provider calls or application writes.

Run using the deployed environment. PostgreSQL uses a repeatable-read read-only
transaction; each optional SQLite file uses an independent online backup. This
is not a cross-database atomic snapshot. Credentials are never exported.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

TABLES = ('accounts', 'projectx_trade_events', 'projectx_trade_day_syncs',
          'bot_order_attempts', 'bot_runs', 'bot_configs', 'journal_entries',
          'expenses', 'payouts', 'position_lifecycles')


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def capture(release, owner, output, *, max_sqlite_bytes=100_000_000):
    from dotenv import load_dotenv
    load_dotenv(release / 'backend' / '.env', override=True)
    sys.path.insert(0, str(release / 'backend'))
    from app.db import engine
    from app.services.probabilistic_shadow import cache_root
    from sqlalchemy import inspect, text
    from backup_market_cache import backup_sqlite
    root = cache_root().resolve()
    output = output.resolve()
    if not output.is_relative_to(root):
        raise ValueError('Evidence capture must be under TOPSIGNAL_DATABENTO_CACHE_DIR')
    if output.exists():
        raise FileExistsError('Use a unique evidence directory')
    owner_hash = hashlib.sha256(owner.encode()).hexdigest()
    sources = {
        'progress.sqlite3': root / 'trade-progress-v1/progress.sqlite3',
        'exit-results.sqlite3': root / 'topbot-exit-study-v1' / owner_hash / 'results.sqlite3',
        'exit-replay-jobs.sqlite3': root / 'topbot-exit-study-v1' / owner_hash / 'replay-jobs.sqlite3',
        'history-sync.sqlite3': root / 'trade-history-sync-v1/state.sqlite3',
    }
    planned = sum(p.stat().st_size + (Path(str(p)+'-wal').stat().st_size if Path(str(p)+'-wal').exists() else 0)
                  for p in sources.values() if p.exists())
    if planned > max_sqlite_bytes:
        raise ValueError('SQLite capture exceeds explicit size bound')
    if shutil.disk_usage(root).free < planned * 2 + 10_000_000_000:
        raise OSError('Capture free-space floor')
    output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    manifest = dict(schema=1, owner=owner, capture_started_at=started, source_release=str(release),
                    consistency='execution transaction; independently timed SQLite online snapshots',
                    planned_sqlite_bytes=planned, files={}, sources={}, exclusions=[])
    manifest['source_code_sha256'] = {name:digest(release/name) for name in (
        'backend/app/services/projectx_trades.py','backend/app/services/trade_progress.py',
        'backend/app/services/projectx_hubs.py','backend/app/services/topbot_study_worker.py',
        'backend/app/services/topbot_study.py','backend/app/services/topbot_study_store.py',
        'backend/app/services/topbot_depth.py') if (release/name).exists()}
    release_manifest = release / 'release-manifest.json'
    if release_manifest.exists():
        manifest['release_manifest_sha256'] = digest(release_manifest)
        # Deployment metadata, not private environment.
        shutil.copyfile(release_manifest, output / 'release-manifest.json')
    data = {'_snapshot_at': started, '_owner': owner}
    with engine.connect() as connection:
        if engine.dialect.name == 'postgresql':
            connection = connection.execution_options(isolation_level='REPEATABLE READ')
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout='20s'"))
            manifest['database_bytes'] = connection.execute(text('select pg_database_size(current_database())')).scalar()
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table in TABLES:
            if table not in tables or 'user_id' not in {c['name'] for c in inspector.get_columns(table)}:
                data[table] = []
                manifest['exclusions'].append({'source': table, 'state': 'unavailable_schema'})
                continue
            # Identifiers are the hardcoded allowlist above. Never dump credentials.
            rows = connection.execute(text(f'SELECT * FROM {table} WHERE user_id=:owner'), {'owner': owner}).mappings()
            data[table] = [dict(row) for row in rows]
            manifest['sources'][table] = {'rows': len(data[table]), 'scope': owner}
        connection.rollback()
    with gzip.open(output / 'execution.json.gz', 'wt', encoding='utf-8') as f:
        json.dump(data, f, default=str)
    for name, source in sources.items():
        before = datetime.now(timezone.utc).isoformat()
        if not source.exists():
            manifest['sources'][name] = {'state': 'no_store'}
            continue
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(output / name)) as dst:
                backup_sqlite(src, dst, timeout_seconds=90)
        manifest['sources'][name] = dict(state='captured', path=str(source), started_at=before,
                                         completed_at=datetime.now(timezone.utc).isoformat())
    for path in output.iterdir():
        if path.is_file():
            manifest['files'][path.name] = dict(bytes=path.stat().st_size, sha256=digest(path))
    manifest['capture_completed_at'] = datetime.now(timezone.utc).isoformat()
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=str), encoding='utf-8')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-root', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--owner', required=True)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--max-sqlite-bytes', type=int, default=100_000_000)
    a = p.parse_args()
    result = capture(a.release_root.resolve(), a.owner, a.output, max_sqlite_bytes=a.max_sqlite_bytes)
    print(json.dumps({'output': str(a.output), 'files': result['files'], 'database_bytes': result.get('database_bytes')}))


if __name__ == '__main__':
    main()
