"""Authenticated caller selects an owner; transport configuration is server-owned."""
import json
import os
from pathlib import Path
import re
import subprocess
from threading import BoundedSemaphore

from tools.read_depth_history import read_history
from .probabilistic_shadow import cache_root, scope_hash

_slots = BoundedSemaphore(2)


def history_source_path():
    return Path(__file__).resolve().parents[2] / 'storage/depth-history-source.json'


def account_history(*, user_id, start=None, end=None, scope='all'):
    query = dict(owner_hash=scope_hash(str(user_id)), start=start, end=end, scope=scope)
    if not _slots.acquire(blocking=False):
        raise RuntimeError('History is busy. Try again in a moment.')
    try:
        config_path = history_source_path()
        if not config_path.exists():
            return dict(read_history(cache_root() / 'depth-recordings-v1/totals', query), source='This backend')
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        # No HTTP input can select a host, identity, path, command or raw owner ID.
        # Restrict command words because Windows OpenSSH uses the remote shell.
        for key in ('python_path', 'reader_path', 'recording_root'):
            if not re.fullmatch(r'[A-Za-z]:/[A-Za-z0-9_./-]+', config[key]) or '..' in config[key]:
                raise RuntimeError('History source configuration is invalid.')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+', config['ssh_target']):
            raise RuntimeError('History source configuration is invalid.')
        result = subprocess.run([
            'ssh', '-T', '-i', config['identity_file'], '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
            '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', config['ssh_target'],
            config['python_path'], '-B', config['reader_path'], '--root', config['recording_root'],
        ], input=json.dumps(query), text=True, encoding='utf-8', capture_output=True, timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode or len(result.stdout) > 12 * 1024**2:
            raise RuntimeError('Host history is unavailable. Check the private host connection or choose a shorter range.')
        payload = json.loads(result.stdout)
        if 'points' not in payload or len(payload['points']) > 20000:
            raise RuntimeError('Host returned an invalid history response.')
        return dict(payload, source=str(config.get('label', 'Recording host'))[:80])
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError) as from_error:
        raise RuntimeError('Host history is unavailable. Check the private host connection.') from from_error
    finally:
        _slots.release()
