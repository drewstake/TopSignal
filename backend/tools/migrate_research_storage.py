"""Read-only cloud export, checked local restore, and full-content verification.

Never deletes source records, changes application settings, or activates a store.
Copy the export off device and restore/verify it there before activation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def canonical(value):
    def encode(item):
        if isinstance(item, datetime):
            return item.replace(tzinfo=timezone.utc).isoformat() if item.tzinfo is None else item.astimezone(timezone.utc).isoformat()
        if isinstance(item, Decimal):
            return format(item, 'f')
        if isinstance(item, UUID):
            return str(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=encode, allow_nan=False)


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def models():
    from app.models import ProjectXMarketCandle
    from app.market_observation_models import MarketObservation, DecisionResearchSnapshot
    return {m.__tablename__: m for m in (MarketObservation, ProjectXMarketCandle, DecisionResearchSnapshot)}


def export(source, destination: Path):
    from sqlalchemy import inspect, select, text, func
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'tables': {}}
    with source.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        conn.execute(text("SET LOCAL statement_timeout='120s'"))
        manifest['database_bytes'] = conn.scalar(text('select pg_database_size(current_database())'))
        for name, model in models().items():
            table = model.__table__
            columns = {column['name'] for column in inspect(conn).get_columns(name)}
            if columns != set(table.columns.keys()):
                raise ValueError('Source/model column mismatch: ' + name)
            expected = conn.scalar(select(func.count()).select_from(table))
            path = destination / (name + '.jsonl.gz')
            content = hashlib.sha256()
            count = 0
            with gzip.open(path, 'wb') as output:
                rows = conn.execution_options(stream_results=True).execute(select(table).order_by(table.c.id))
                for row in rows.mappings():
                    line = (canonical(dict(row)) + '\n').encode()
                    output.write(line)
                    content.update(line)
                    count += 1
            if count != expected:
                raise ValueError('Export count mismatch')
            manifest['tables'][name] = dict(rows=count, file=path.name, bytes=path.stat().st_size,
                sha256=checksum(path), content_sha256=content.hexdigest(),
                relation_bytes=conn.scalar(text('select pg_total_relation_size(:name)'), {'name': name}))
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    verify_export(destination)
    return manifest


def verify_export(root: Path):
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('schema') != 1 or set(manifest['tables']) != set(models()):
        raise ValueError('Unsupported research manifest')
    for name, entry in manifest['tables'].items():
        if entry['file'] != name + '.jsonl.gz':
            raise ValueError('Invalid archive member')
        path = root / entry['file']
        if path.stat().st_size != entry['bytes'] or checksum(path) != entry['sha256']:
            raise ValueError('Archive checksum mismatch: ' + name)
        content = hashlib.sha256()
        count = 0
        with gzip.open(path, 'rb') as stream:
            for line in stream:
                row = json.loads(line)
                if set(row) != set(models()[name].__table__.columns.keys()):
                    raise ValueError('Archive row schema mismatch')
                content.update(line)
                count += 1
        if count != entry['rows'] or content.hexdigest() != entry['content_sha256']:
            raise ValueError('Archive content mismatch: ' + name)
    return manifest


def restore(root: Path, target: Path):
    from sqlalchemy import DateTime, Numeric
    from app.research_storage import initialize_store
    manifest = verify_export(root)
    engine = initialize_store(target)
    try:
        with engine.begin() as conn:
            for name, model in models().items():
                table = model.__table__
                batch = []
                with gzip.open(root / manifest['tables'][name]['file'], 'rt', encoding='utf-8') as stream:
                    for line in stream:
                        row = json.loads(line)
                        for column in table.columns:
                            if row[column.name] is not None:
                                if isinstance(column.type, DateTime):
                                    row[column.name] = datetime.fromisoformat(row[column.name])
                                elif isinstance(column.type, Numeric):
                                    row[column.name] = Decimal(row[column.name])
                        batch.append(row)
                        if len(batch) == 500:
                            conn.execute(table.insert(), batch)
                            batch = []
                    if batch:
                        conn.execute(table.insert(), batch)
            conn.exec_driver_sql('ANALYZE')
        return verify_restore(root, engine)
    finally:
        engine.dispose()


def verify_restore(root: Path, engine):
    from sqlalchemy import select
    manifest = verify_export(root)
    result = {}
    with engine.connect() as conn:
        if conn.exec_driver_sql('PRAGMA integrity_check').scalar_one() != 'ok':
            raise ValueError('SQLite integrity check failed')
        for name, model in models().items():
            digest = hashlib.sha256()
            count = 0
            for row in conn.execute(select(model.__table__).order_by(model.id)).mappings():
                digest.update((canonical(dict(row)) + '\n').encode())
                count += 1
            expected = manifest['tables'][name]
            if count != expected['rows'] or digest.hexdigest() != expected['content_sha256']:
                raise ValueError('Restored content/count mismatch: ' + name)
            result[name] = dict(rows=count, content_sha256=digest.hexdigest())
    return {'verified': True, 'tables': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['export', 'verify', 'restore'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--source-env', type=Path)
    parser.add_argument('--target', type=Path)
    args = parser.parse_args()
    # Importing ORM metadata must never create/connect an application engine.
    os.environ['DATABASE_URL'] = 'sqlite://'
    os.environ['TOPSIGNAL_ENV'] = 'test'
    os.environ['TOPSIGNAL_RESEARCH_STORAGE'] = 'application'
    if args.operation == 'export':
        from dotenv import dotenv_values
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        from app.database_security import validate_postgres_tls_url
        if not args.source_env:
            parser.error('--source-env required')
        url = dotenv_values(args.source_env)['DATABASE_URL']
        connect_args = validate_postgres_tls_url(url)
        connect_args.update(connect_timeout=10, prepare_threshold=None)
        source = create_engine(url, poolclass=NullPool, connect_args=connect_args, hide_parameters=True)
        try:
            result = export(source, args.directory)
        finally:
            source.dispose()
    elif args.operation == 'verify':
        result = verify_export(args.directory)
    else:
        if not args.target:
            parser.error('--target required')
        result = restore(args.directory, args.target)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Driver exceptions may include source payloads or credentials.
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
