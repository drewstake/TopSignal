"""Session liquidity-heatmap history for the Bot page.

Reads derived frames built from the independent depth recorder's committed
segments (see ``tools/depth_heatmap.py``). Storage stays local under
``TOPSIGNAL_DATABENTO_CACHE_DIR``; nothing is written to Supabase. The caller's
owner scope is derived from the authenticated identity, never from HTTP input.

The deriver runs in a separate background-priority child. It yields to replay
work and recorder backpressure instead of adding concurrent archive reads.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sqlite3
import sys
from threading import BoundedSemaphore, Event, Lock, Thread
from time import monotonic, time

from tools.depth_heatmap import read_heatmap
from .depth_history import history_source_path
from .probabilistic_shadow import cache_root, scope_hash

logger = logging.getLogger(__name__)
_slots = BoundedSemaphore(2)
_cache_lock = Lock()
_cache: OrderedDict = OrderedDict()
_CACHE_BYTES = 24 * 1024**2


def recordings_root() -> Path:
    return cache_root() / 'depth-recordings-v1'


def heatmap_cache_root() -> Path:
    return cache_root() / 'depth-heatmap-v1'


def account_heatmap(*, user_id, contract_id: str, data_live: bool, start_ms: int, end_ms: int, tier: int,
                    summary: bool = False) -> bytes:
    """Return the encoded JSON response. Completed windows are cached briefly in memory."""
    owner = scope_hash(str(user_id))
    query = dict(owner_hash=owner, contract_id=contract_id, data_live=data_live, start_ms=start_ms, end_ms=end_ms, tier=tier,
                 summary=summary)
    config = history_source_path()
    key = (owner, contract_id, data_live, start_ms, end_ms, tier, summary, str(cache_root()), config.exists())
    with _cache_lock:
        cached = _cache.get(key)
        # Short reuse avoids duplicate tab work; the live tail changes every sealed segment.
        if cached and monotonic() - cached[0] < 10:
            _cache.move_to_end(key)
            return _with_server_time(cached[1])
    # Do not spend a client's deadline queued behind another archive read.
    if not _slots.acquire(blocking=False):
        raise RuntimeError('Heatmap history is busy. Retrying shortly.')
    try:
        if config.exists():
            payload = _remote(config, query)
        else:
            result = read_heatmap(heatmap_cache_root(), query, deadline=monotonic() + 20)
            result.pop('server_time_ms', None)
            payload = json.dumps(result, separators=(',', ':'), allow_nan=False).encode()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        raise RuntimeError('Heatmap history is unavailable.') from exc
    finally:
        _slots.release()
    with _cache_lock:
        _cache[key] = (monotonic(), payload)
        _cache.move_to_end(key)
        while len(_cache) > 64 or sum(len(item[1]) for item in _cache.values()) > _CACHE_BYTES:
            _cache.popitem(last=False)
    return _with_server_time(payload)


def _with_server_time(payload: bytes) -> bytes:
    """Add the current server time to a (possibly cached) response object.

    Cached bodies never carry an old timestamp: a replayed stale time would skew
    the browser's live-depth clock.
    """
    if not payload.endswith(b'}'):
        raise RuntimeError('Heatmap history returned an invalid response.')
    stamp = b'"server_time_ms":%d' % int(time() * 1000)
    return payload[:-1] + (b',' if len(payload) > 2 else b'') + stamp + b'}'


def _remote(config_path: Path, query: dict) -> bytes:
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if not config.get('heatmap_reader_path') or not config.get('heatmap_cache_root'):
        # Same explicit boundary as the session order-flow reader: never upload
        # code or silently switch to a different data source.
        raise RuntimeError('Heatmap history reader is not installed on the recording host yet.')
    for field in ('python_path', 'heatmap_reader_path', 'heatmap_cache_root'):
        if not re.fullmatch(r'[A-Za-z]:/[A-Za-z0-9_./-]+', config.get(field, '')) or '..' in config[field]:
            raise RuntimeError('Heatmap history source configuration is invalid.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+', config.get('ssh_target', '')):
        raise RuntimeError('Heatmap history source configuration is invalid.')
    completed = subprocess.run([
        'ssh', '-T', '-i', config['identity_file'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=5', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', config['ssh_target'],
        config['python_path'], '-B', config['heatmap_reader_path'], 'read', '--cache', config['heatmap_cache_root'],
    ], input=json.dumps(query), text=True, encoding='utf-8', capture_output=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if completed.returncode or len(completed.stdout) > 48 * 1024**2:
        raise RuntimeError('Heatmap history is unavailable on the recording host.')
    result = json.loads(completed.stdout)
    if (result.get('schema') != 'liquidity-heatmap-history-v1' or result.get('contract_id') != query['contract_id']
            or result.get('data_live') is not query['data_live'] or result.get('tier') != query['tier']):
        raise RuntimeError('Heatmap history source returned a different market scope.')
    result['source'] = str(config.get('label', 'Recording host'))[:80]
    result.pop('server_time_ms', None)  # This backend's clock is the one the browser talks to.
    return json.dumps(result, separators=(',', ':'), allow_nan=False).encode()


class HeatmapDeriverSupervisor:
    """Keep one low-priority deriver child alive while this backend runs.

    ``TOPSIGNAL_HEATMAP_DERIVER``: ``auto`` (default) runs only when this backend
    reads local recordings (no remote history-source file) and the recording
    root exists; ``on`` requires that; ``off`` disables it. The child exits when
    this process closes its stdin, so it cannot outlive the backend.
    """

    def __init__(self, *, root: Path | None = None, cache: Path | None = None, mode: str | None = None):
        self.mode = (mode or os.getenv('TOPSIGNAL_HEATMAP_DERIVER', 'auto')).strip().lower()
        self.root = root
        self.cache = cache
        self.process: subprocess.Popen | None = None
        self.stop_event = Event()
        self.thread: Thread | None = None
        self.state = 'disabled'
        self._pressure_sent = None

    def enabled(self) -> bool:
        if self.mode not in {'auto', 'on', 'off'}:
            logger.error('heatmap_deriver_invalid_mode')
            return False
        if self.mode == 'off' or history_source_path().exists():
            return False
        return (self.root or recordings_root()).is_dir()

    def start(self) -> bool:
        if not self.enabled():
            return False
        self.root = self.root or recordings_root()
        self.cache = self.cache or heatmap_cache_root()
        self.thread = Thread(target=self._run, name='heatmap-deriver-supervisor', daemon=True)
        self.thread.start()
        return True

    def _spawn(self) -> subprocess.Popen:
        script = Path(__file__).resolve().parents[2] / 'tools' / 'depth_heatmap.py'
        # The child reports errors through its status table; unread pipes could block it.
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(script.parent))
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
        else:
            kwargs['preexec_fn'] = lambda: os.nice(10)
        self.cache.mkdir(parents=True, exist_ok=True)
        return subprocess.Popen([sys.executable, '-B', str(script), 'follow', '--root', str(self.root),
                                 '--cache', str(self.cache), '--parent-stdin'], **kwargs)

    def _run(self):
        delay = 5.0
        while not self.stop_event.is_set():
            started = monotonic()
            try:
                self.process = self._spawn()
                self._pressure_sent = None
                self._signal_pressure()
                self.state = 'running'
                logger.info('heatmap_deriver_started', extra={'pid': self.process.pid})
                while self.process.poll() is None and not self.stop_event.wait(2):
                    self._signal_pressure()
            except OSError as exc:
                logger.error('heatmap_deriver_spawn_failed', extra={'error': type(exc).__name__})
            if self.stop_event.is_set():
                break
            code = self.process.returncode if self.process else None
            self.state = 'restarting'
            logger.warning('heatmap_deriver_exited', extra={'returncode': code})
            delay = 5.0 if monotonic() - started > 300 else min(300.0, delay * 2)
            self.stop_event.wait(delay)
        self.state = 'stopped'

    def _signal_pressure(self):
        """Send only state changes; no archive reads or files on the live drive."""
        from .topbot_study_worker import ACTIVE, STATE_LOCK, recording_pressure
        try:
            with STATE_LOCK:
                replaying = bool(ACTIVE)
            paused = replaying or recording_pressure() is not None
        except Exception:
            paused = True  # Unknown recorder health must not start optional IO.
        if paused == self._pressure_sent:
            return
        try:
            self.process.stdin.write(b'pause\n' if paused else b'resume\n')
            self.process.stdin.flush()
            self._pressure_sent = paused
        except (OSError, ValueError, AttributeError):
            pass  # Shutdown may have closed stdin; EOF stops the child.

    def request_stop(self) -> None:
        """Signal the child without waiting (never raises), so trading shutdown is not delayed."""
        try:
            self.stop_event.set()
            process = self.process
            if process and process.poll() is None and process.stdin and not process.stdin.closed:
                process.stdin.close()
        except Exception:  # noqa: BLE001 - shutdown must continue regardless.
            logger.exception('heatmap_deriver_stop_signal_failed')

    def stop(self, timeout: float = 10.0) -> bool:
        """Never raises: it runs on the shutdown path before the trading worker stops."""
        try:
            self.stop_event.set()
            process = self.process
            if process and process.poll() is None:
                try:
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()  # The child stops after its current segment.
                    process.wait(timeout=timeout)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=5)
            if self.thread:
                self.thread.join(timeout=5)
            return True
        except Exception:  # noqa: BLE001 - shutdown must continue regardless.
            logger.exception('heatmap_deriver_stop_failed')
            return False
