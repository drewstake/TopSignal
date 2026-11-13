"""Authenticated caller selects an owner; transport configuration is server-owned."""
import json
import gzip
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
import os
from pathlib import Path
import re
import subprocess
from threading import BoundedSemaphore, Lock
from time import monotonic

from tools.read_depth_history import read_history
from .probabilistic_shadow import cache_root, scope_hash

_slots = BoundedSemaphore(1)
_cache_lock = Lock()
_cache = OrderedDict()
_pending = {}
READ_SECONDS = 20


def history_source_path():
    return Path(__file__).resolve().parents[2] / 'storage/depth-history-source.json'


def _deliver(payload, representation):
    if representation == 'gzip':
        return payload
    raw = gzip.decompress(payload)
    return raw if representation == 'json' else json.loads(raw)


def account_history(*, user_id, start=None, end=None, scope='all', window='latest', representation='object'):
    if representation not in {'object', 'json', 'gzip'}:
        raise ValueError('Invalid history response representation')
    # Share duplicate tab requests. Cache only bounded compressed JSON in memory,
    # scoped to the authenticated owner and server-controlled source revision.
    config = history_source_path()
    revision = (config.stat().st_mtime_ns, config.stat().st_size) if config.exists() else None
    key = (scope_hash(str(user_id)), start, end, scope, window, str(config), revision, str(cache_root()))
    with _cache_lock:
        cached = _cache.get(key)
        if cached and monotonic() - cached[0] < 30:
            _cache.move_to_end(key)
            return _deliver(cached[1], representation)
        future = _pending.get(key)
        leader = future is None
        if leader:
            future = _pending[key] = Future()
    if not leader:
        try:
            return _deliver(future.result(timeout=READ_SECONDS + 7), representation)
        except TimeoutError as exc:
            raise RuntimeError('History is still preparing. Retry shortly or choose a shorter range.') from exc
    try:
        result = _read_account_history(user_id=user_id, start=start, end=end, scope=scope, window=window)
        payload = gzip.compress(json.dumps(result, separators=(',', ':'), allow_nan=False).encode(),
                                compresslevel=1)
        with _cache_lock:
            _cache[key] = (monotonic(), payload)
            _cache.move_to_end(key)
            while len(_cache) > 4 or sum(len(item[1]) for item in _cache.values()) > 24 * 1024**2:
                _cache.popitem(last=False)
        future.set_result(payload)
        return _deliver(payload, representation)
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        with _cache_lock:
            _pending.pop(key, None)


def _read_account_history(*, user_id, start=None, end=None, scope='all', window='latest'):
    query = dict(owner_hash=scope_hash(str(user_id)), start=start, end=end, scope=scope, window=window)
    if not _slots.acquire(blocking=False):
        raise RuntimeError('History is busy. Try again in a moment.')
    try:
        config_path = history_source_path()
        if not config_path.exists():
            return dict(read_history(cache_root() / 'depth-recordings-v1/totals', query,
                deadline=monotonic()+READ_SECONDS,
                cache_path=cache_root() / 'depth-history-cache-v1/history.sqlite3'), source='This backend')
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
        if result.returncode or len(result.stdout) > 128 * 1024**2:
            raise RuntimeError('Host history is unavailable. Check the private host connection or choose a shorter range.')
        payload = json.loads(result.stdout)
        if 'points' not in payload or len(payload['points']) > 100000:
            raise RuntimeError('Host returned an invalid history response.')
        return dict(payload, source=str(config.get('label', 'Recording host'))[:80])
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError) as from_error:
        raise RuntimeError('Host history is unavailable. Check the private host connection.') from from_error
    finally:
        _slots.release()
