"""Restore a verified backup to a new isolated directory; check exact inputs/model.

Stdlib only: this never loads application credentials, connects to a provider or
starts a bot. Retain the restored evidence and receipt; no automatic deletion.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

from backup_market_cache import backup, verify


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def restore_check(source, output):
    verify(source)
    backup(source, output)
    samples = []
    for path in sorted(output.glob('market-archive/v1/*/archive.sqlite3')):
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            rows = db.execute('SELECT d.id,d.payload FROM decisions d JOIN decision_commits c ON c.id=d.id ORDER BY d.at DESC')
            for index, row in enumerate(rows):
                record = json.loads(zlib.decompress(row[1]))
                model = record['forecast'].get('artifact_sha256')
                if not model and (record.get('artifact_status') != 'no_model_used'
                                  or record['forecast'].get('forecasts')
                                  or record.get('action') in ('BUY', 'SELL')):
                    raise ValueError('Original model missing or corrupt')
                # Check the latest decision even when it legitimately used no
                # model, then find the most recent model-bearing decision too.
                if index and not model:
                    continue
                if digest(record['input_ids']) != record['input_sha256'] or len(record['input_ids']) != len(record['input_receipts']):
                    raise ValueError('Decision input list corrupt')
                for identity in record['input_ids']:
                    payload = db.execute('SELECT payload FROM bars WHERE id=?', (identity,)).fetchone()
                    if payload is None or digest(json.loads(zlib.decompress(payload[0]))) != identity:
                        raise ValueError('Original input missing or corrupt')
                if model:
                    artifact = db.execute('SELECT payload FROM artifacts WHERE id=?', (model,)).fetchone()
                    if artifact is None or digest(json.loads(zlib.decompress(artifact[0]))) != model:
                        raise ValueError('Original model missing or corrupt')
                samples.append(dict(owner_directory=path.parent.name, decision=row[0], inputs=len(record['input_ids']),
                                    input_sha256=record['input_sha256'], model_sha256=model))
                if model:
                    break
    if not any(sample['model_sha256'] for sample in samples):
        raise ValueError('No confirmed archived decision with original model to restore')
    return dict(verified=True, samples=samples, restored_to=str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(restore_check(args.source.resolve(), args.output.resolve())))
