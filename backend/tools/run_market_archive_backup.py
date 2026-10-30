"""Scheduled pull to the already authorized off-device computer over private SSH.

No credential discovery, billing, deletion or retention cleanup. All attempts,
including failed staging directories, remain. Configuration is a private local
JSON file. SSH host keys must already be pinned and keys must work unattended.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
from uuid import uuid4

from backup_market_cache import verify, checksum, filesystem_path
from check_market_archive_restore import restore_check


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def quote(value):
    return "'"+str(value).replace("'", "''")+"'"


def run(args, *, timeout=1200):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Do not persist arbitrary command output or credential-bearing paths.
        raise RuntimeError('Backup subprocess failed: '+Path(args[0]).name)
    return result.stdout


def ssh_options(config):
    return ['-i', config['ssh_key'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2']


def remote(config, script, *, timeout=1200):
    encoded = base64.b64encode(("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue';\n"+script).encode('utf-16-le')).decode()
    return run(['ssh', *ssh_options(config), config['ssh_host'], 'powershell', '-NoProfile',
                '-NonInteractive', '-EncodedCommand', encoded], timeout=timeout)


def publish(config, status):
    # A failed publish leaves the previous success to age naturally on the host.
    encoded = base64.b64encode(json.dumps(status).encode()).decode()
    target = quote(config['remote_receipt'])
    remote(config, f"$p={target}; $tmp=$p+'.'+[guid]::NewGuid().ToString()+'.tmp'; "
           f"[IO.File]::WriteAllBytes($tmp,[Convert]::FromBase64String('{encoded}')); "
           "if(Test-Path -LiteralPath $p){[IO.File]::Replace($tmp,$p,$p+'.'+[guid]::NewGuid().ToString()+'.previous')}else{[IO.File]::Move($tmp,$p)}", timeout=45)


def unpack_backup(archive_path, destination, *, max_bytes):
    """Extract regular snapshot files only, with explicit path/size bounds."""
    destination = filesystem_path(destination)
    if destination.exists():
        raise ValueError('Restore destination already exists')
    with tarfile.open(filesystem_path(archive_path), 'r:') as archive:
        members = archive.getmembers()
        names = set()
        total = 0
        for member in members:
            name = member.name
            target = (destination/name).resolve()
            if (not member.isfile() or '\\' in name or ':' in name or name.startswith('/')
                    or '..' in name.split('/') or not target.is_relative_to(destination)
                    or str(target).casefold() in names):
                raise ValueError('Invalid snapshot archive member')
            names.add(str(target).casefold())
            total += member.size
            if total > max_bytes:
                raise ValueError('Snapshot extraction budget exceeded')
        destination.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = destination/member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
    return verify(destination)


def attempt(config, *, previous=None, force_restore=False):
    root = Path(config['destination']).resolve()
    if not root.is_dir():
        raise OSError('Authorized backup destination unavailable')
    previous = previous or {}
    identity = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    limit = int(config.get('capacity_limit_bytes', 250_000_000_000))
    used = sum(p.stat().st_size for p in filesystem_path(root).rglob('*') if p.is_file())
    if used >= limit or shutil.disk_usage(root).free < 5_000_000_000:
        raise OSError('Off-device capacity budget reached; no automatic deletion')
    stage = config['remote_staging'].rstrip('/')+'/'+identity
    # Bound staging and require space for a new full snapshot before starting.
    script = f'''$source={quote(config['remote_source'])}; $base={quote(config['remote_staging'])}
$members=@(Get-ChildItem -LiteralPath $source -File -Recurse)
$bytes=($members | Measure-Object Length -Sum).Sum + $members.Count*4096 + 1MB
$used=0; if(Test-Path -LiteralPath $base){{$used=(Get-ChildItem -LiteralPath $base -File -Recurse | Measure-Object Length -Sum).Sum}}
if(($used+2*$bytes) -gt {limit}){{throw 'Staging capacity budget reached'}}
if((Get-Volume -DriveLetter D).SizeRemaining -lt (2*$bytes+5GB)){{throw 'Staging free space floor'}}
$bytes | ConvertTo-Json -Compress
'''
    estimated = int(json.loads(remote(config, script, timeout=60)))
    if used+estimated*3 > limit or shutil.disk_usage(root).free < estimated*3+5_000_000_000:
        raise OSError('Backup and restore capacity insufficient')
    remote(config, f"& {quote(config['remote_python'])} {quote(config['remote_helper'])} --source {quote(config['remote_source'])} --output {quote(stage)}; if($LASTEXITCODE -ne 0){{throw 'Snapshot failed'}}")
    backups = root/'backups'
    backups.mkdir(exist_ok=True)
    destination = backups/identity
    transport = json.loads(remote(config, f"& {quote(config['remote_python'])} {quote(config['remote_helper'])} --pack {quote(stage)} --output {quote(stage+'.tar')}; if($LASTEXITCODE -ne 0){{throw 'Packing failed'}}"))
    if transport['bytes'] > estimated:
        raise OSError('Snapshot grew beyond estimated transport budget')
    local_archive = backups/(identity+'.tar')
    run(['scp', *ssh_options(config), config['ssh_host']+':'+stage+'.tar', str(local_archive)])
    if local_archive.stat().st_size != transport['bytes'] or checksum(local_archive) != transport['sha256']:
        raise ValueError('Transport archive checksum mismatch')
    manifest = unpack_backup(local_archive, destination, max_bytes=estimated)
    result = dict(previous, schema=1, last_attempt_at=utc(), last_verified_at=utc(), status='verified',
                  destination=str(destination), schedule=config.get('schedule'),
                  dependency='Development computer powered on, drews signed in, private SSH connected; no browser required',
                  verified_files=len(manifest['files']), verified_bytes=sum(f['bytes'] for f in manifest['files'].values()),
                  last_failure=None)
    last_restore = previous.get('last_restore_at')
    due = not last_restore or (datetime.now(timezone.utc)-datetime.fromisoformat(last_restore)).total_seconds() >= 7*86400
    if force_restore or due:
        result['restore'] = restore_check(destination, root/'restore-checks'/identity)
        result['last_restore_at'] = utc()
    return result


def execute(config, *, force_restore=False, sleep=time.sleep):
    root = Path(config['destination']).resolve()
    # The state file lives beside the private config, so destination failure can
    # still be reported when a removable/network destination is unavailable.
    state_path = Path(config['state_file'])
    previous = json.loads(state_path.read_text(encoding='utf-8')) if state_path.is_file() else {}
    status = previous
    for number in range(1, 4):
        started = utc()
        try:
            status = attempt(config, previous=status, force_restore=force_restore)
            status['attempt'] = number
        except Exception as exc:
            status = dict(status, schema=1, status='failed', last_attempt_at=started, attempt=number,
                          last_failure=dict(at=utc(), type=type(exc).__name__), schedule=config.get('schedule'))
        atomic_json(state_path, status)
        atomic_json(state_path.parent/'backup-receipts'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]+'.json'), status)
        for publish_attempt in range(3):
            try:
                outgoing = {k: v for k, v in status.items() if k != 'publish_failure'}
                publish(config, outgoing)
            except Exception as exc:
                status['publish_failure'] = dict(at=utc(), type=type(exc).__name__)
                atomic_json(state_path, status)
                if publish_attempt < 2:
                    sleep(30*(publish_attempt+1))
            else:
                status.pop('publish_failure', None)
                atomic_json(state_path, status)
                break
        if status['status'] == 'verified':
            if status.get('publish_failure'):
                raise RuntimeError('Backup verified but host status publishing failed after three attempts')
            return status
        if number < 3:
            sleep(30*number)
    raise RuntimeError('Off-device backup failed after three attempts; see private backup status')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--restore-check', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    # OS-owned lock is released after a crash. Scheduler also rejects overlap.
    import msvcrt
    with args.config.with_suffix('.lock').open('a+b') as lock:
        lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        result = execute(config, force_restore=args.restore_check)
    print(json.dumps({k: result.get(k) for k in ('status', 'last_verified_at', 'last_restore_at', 'verified_files', 'verified_bytes')}))


if __name__ == '__main__':
    main()
