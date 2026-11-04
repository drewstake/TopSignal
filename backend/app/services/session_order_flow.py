"""Read-only market context. No dependency on strategy, orders, or bot decisions."""
from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import subprocess
from threading import Lock
from time import monotonic

from tools.read_session_flow import read_session_flow
from .depth_history import history_source_path
from .probabilistic_shadow import cache_root, scope_hash

_lock = Lock()
_cache = OrderedDict()


def account_session_flow(*, user_id, contract_id, data_live=False):
    config_path = history_source_path()
    revision = (config_path.stat().st_mtime_ns, config_path.stat().st_size) if config_path.exists() else None
    # One bounded reader at a time; callers retry rather than queue unbounded work.
    if not _lock.acquire(blocking=False):
        raise RuntimeError('Session order flow is being updated. Try again shortly.')
    try:
        minute = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
        key = (scope_hash(str(user_id)), contract_id, data_live, minute, revision, str(cache_root()))
        cached = _cache.get(key)
        if cached and monotonic()-cached[0] < cached[2]:
            return json.loads(cached[1])
        query = dict(owner_hash=key[0], contract_id=contract_id, data_live=data_live)
        if not config_path.exists():
            result = read_session_flow(cache_root()/'depth-recordings-v1', query)
            result['source'] = 'This backend'
        else:
            config = json.loads(config_path.read_text(encoding='utf-8-sig'))
            if not config.get('session_flow_reader_path'):
                raise RuntimeError('Session order-flow reader is not installed on the recording host yet.')
            for field in ('python_path', 'session_flow_reader_path', 'research_recording_root'):
                if not re.fullmatch(r'[A-Za-z]:/[A-Za-z0-9_./-]+', config.get(field, '')) or '..' in config[field]:
                    raise RuntimeError('Session order-flow source configuration is invalid.')
            if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+', config['ssh_target']):
                raise RuntimeError('Session order-flow source configuration is invalid.')
            completed = subprocess.run(['ssh', '-T', '-i', config['identity_file'], '-o', 'BatchMode=yes',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5', '-o', 'ForwardAgent=no',
                '-o', 'ClearAllForwardings=yes', config['ssh_target'], config['python_path'], '-B',
                config['session_flow_reader_path'], '--root', config['research_recording_root']],
                input=json.dumps(query), text=True, encoding='utf-8', capture_output=True, timeout=40,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if completed.returncode or len(completed.stdout) > 65536:
                raise RuntimeError('Session order-flow recordings are unavailable.')
            result = json.loads(completed.stdout)
            if (result.get('schema') != 'session-provider-flow-v1' or result.get('contract_id') != contract_id
                    or result.get('data_live') is not data_live):
                raise RuntimeError('Session order-flow source returned a different market scope.')
            result['source'] = str(config.get('label', 'Recording host'))[:80]
        encoded = json.dumps(result, allow_nan=False)
        _cache[key] = (monotonic(), encoded, 1 if result['coverage']['status'] == 'building' else 20)
        _cache.move_to_end(key)
        while len(_cache) > 16:
            _cache.popitem(last=False)
        return result
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        raise RuntimeError('Session order-flow recordings are unavailable.') from exc
    finally:
        _lock.release()
