"""Explicit historical Journal export; never changes source rows or objects.

The artifact uses the separately protected execution-backup key. Verification
decrypts every object and checks record ownership, sizes and content hashes.
This maintenance tool is not an application feature or runtime dependency.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import zipfile

from cryptography.fernet import Fernet

MAX_TOTAL = 100 * 1024**2
MAX_OBJECT = 10 * 1024**2
MAX_RECORDS = 10000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def snapshot(connection):
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL timezone='UTC'")
        connection.execute("SET LOCAL statement_timeout='10s'")
        return {table: [row[0] for row in connection.execute(
            f'SELECT to_jsonb(r) FROM public.{table} r ORDER BY id LIMIT {MAX_RECORDS+1}')]
            for table in ('journal_entries', 'journal_entry_images')}


def validate_records(records):
    entries, images = records['journal_entries'], records['journal_entry_images']
    if len(entries) > MAX_RECORDS or len(images) > MAX_RECORDS:
        raise ValueError('Journal export requires capacity review')
    by_id = {r['id']: r for r in entries}
    if len(by_id) != len(entries) or len({r['id'] for r in images}) != len(images):
        raise ValueError('Duplicate journal identities')
    for row in images:
        entry = by_id.get(row['journal_entry_id'])
        if entry is None or any(row[k] != entry[k] for k in ('user_id', 'account_id', 'entry_date')):
            raise ValueError('Journal image ownership mismatch')
        if not isinstance(row['filename'], str) or not row['filename'] or not 0 < row['byte_size'] <= MAX_OBJECT:
            raise ValueError('Invalid journal image reference')
    if sum(r['byte_size'] for r in images) > MAX_TOTAL:
        raise ValueError('Journal export requires capacity review')


def archive(records, load_object, destination, key, *, source):
    validate_records(records)
    encoded = canonical(records)
    if len(encoded) > 20 * 1024**2:
        raise ValueError('Journal records require capacity review')
    output = io.BytesIO()
    manifest = dict(schema='retired-journal-v1', source=source,
        created_at=datetime.now(timezone.utc).isoformat(), historical_recovery_only=True,
        records_sha256=hashlib.sha256(encoded).hexdigest(),
        entry_count=len(records['journal_entries']), image_count=len(records['journal_entry_images']),
        owners=sorted({r['user_id'] for r in records['journal_entries']}), objects=[])
    seen = set()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr('records.json', encoded)
        for row in records['journal_entry_images']:
            data = load_object(row['filename'])
            if len(data) != row['byte_size'] or len(data) > MAX_OBJECT:
                raise ValueError('Journal object size mismatch')
            digest = hashlib.sha256(data).hexdigest()
            name = 'objects/' + digest
            if name not in seen:
                bundle.writestr(name, data)
                seen.add(name)
            manifest['objects'].append(dict(image_id=row['id'], user_id=row['user_id'],
                account_id=row['account_id'], original_key=row['filename'], file=name,
                bytes=len(data), sha256=digest, mime_type=row['mime_type']))
        bundle.writestr('manifest.json', canonical(manifest))
    if output.tell() > MAX_TOTAL + 21 * 1024**2:
        raise ValueError('Journal archive capacity exceeded')
    encrypted = Fernet(key).encrypt(output.getvalue())
    # Refuse overwrite, including failed attempts. Caller chooses a new path.
    with Path(destination).open('xb') as stream:
        stream.write(encrypted)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    return verify(destination, key)


def verify(path, key, *, restore_to=None):
    path = Path(path)
    if path.stat().st_size > 170 * 1024**2:
        raise ValueError('Oversized journal archive')
    encrypted = path.read_bytes()
    raw = Fernet(key).decrypt(encrypted)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        members = bundle.infolist()
        if (len(members) > MAX_RECORDS+2 or len({m.filename for m in members}) != len(members)
                or sum(m.file_size for m in members) > MAX_TOTAL+21*1024**2):
            raise ValueError('Invalid journal archive bounds')
        record_bytes = bundle.read('records.json')
        records = json.loads(record_bytes)
        validate_records(records)
        manifest = json.loads(bundle.read('manifest.json'))
        if (manifest['schema'] != 'retired-journal-v1' or not manifest['historical_recovery_only']
                or hashlib.sha256(record_bytes).hexdigest() != manifest['records_sha256']
                or manifest['entry_count'] != len(records['journal_entries'])
                or manifest['image_count'] != len(records['journal_entry_images'])):
            raise ValueError('Journal records verification failed')
        images = {r['id']: r for r in records['journal_entry_images']}
        verified = set()
        allowed = {'manifest.json', 'records.json'}
        total = 0
        for obj in manifest['objects']:
            row = images.get(obj['image_id'])
            if row is None or obj['image_id'] in verified or any(obj[a] != row[b] for a,b in (
                ('user_id','user_id'),('account_id','account_id'),('original_key','filename'),('bytes','byte_size'),('mime_type','mime_type'))):
                raise ValueError('Journal object reference mismatch')
            if obj['file'] != 'objects/'+obj['sha256'] or len(obj['sha256']) != 64 or any(c not in '0123456789abcdef' for c in obj['sha256']):
                raise ValueError('Unsafe journal archive path')
            data = bundle.read(obj['file'])
            if len(data) != obj['bytes'] or hashlib.sha256(data).hexdigest() != obj['sha256']:
                raise ValueError('Journal object hash mismatch')
            verified.add(obj['image_id']); allowed.add(obj['file']); total += len(data)
        if verified != set(images) or allowed != {m.filename for m in members}:
            raise ValueError('Journal archive is incomplete')
        if restore_to is not None:
            destination = Path(restore_to)
            destination.mkdir(parents=True, exist_ok=False)
            for name in sorted(allowed):
                target = destination/name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(bundle.read(name))
                if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(bundle.read(name)).digest():
                    raise ValueError('Journal restore write verification failed')
    return dict(schema='retired-journal-verification-v1', verified=True,
        verified_at=datetime.now(timezone.utc).isoformat(), archive_sha256=hashlib.sha256(encrypted).hexdigest(),
        encrypted_bytes=len(encrypted), entries=len(records['journal_entries']),
        image_references=len(images), unique_objects=len(allowed)-2, image_bytes=total,
        owners=len(manifest['owners']), records_sha256=manifest['records_sha256'],
        isolated_restore=restore_to is not None)


if __name__ == '__main__':
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.storage_backup_crypto import load_key
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', required=True, type=Path)
    parser.add_argument('--key-file', required=True, type=Path)
    parser.add_argument('--restore-to', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.verify, load_key(args.key_file), restore_to=args.restore_to)))
    except Exception as exc:
        print(json.dumps(dict(verified=False,error_type=type(exc).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
