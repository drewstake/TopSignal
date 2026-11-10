"""Bounded background archival of decision copies; never on the trading cycle.

The evaluation freezes its inputs in memory before the execution commit and
hands them here afterwards. One thread writes the immutable decision and its
commit confirmation in a single local transaction, retrying disk contention
with backoff. No application database session and no broker call is used.

The queue is memory only: a process crash between the execution commit and the
local write loses that copy. The application database decision row remains the
authoritative record; a missing copy is an absence, never an invented one.
"""
from collections import deque
from datetime import datetime, timezone
from threading import Condition, Thread
from time import monotonic

from .market_archive import Archive, enabled, failure, stamp
from .market_archive_quotes import diagnostic

MAX_PENDING = 256
MAX_ATTEMPTS = 8
MAX_AGE_SECONDS = 600
WRITE_TIMEOUT = 2.
RETRY_BASE_SECONDS = .5

_condition = Condition()
_pending = deque()
_thread = None
_closing = False
_busy = False
_generation = 0
_stats = {}


def _count(name, amount=1):
    _stats[name] = _stats.get(name, 0) + amount


def submit(prepared, *, committed):
    """Enqueue without I/O. False means the copy was refused and counted."""
    global _thread
    if prepared is None or not enabled():
        return False
    with _condition:
        _count('submitted')
        if _closing or len(_pending) >= MAX_PENDING:
            _count('dropped_shutdown' if _closing else 'dropped_queue_full')
            refused = True
        else:
            refused = False
            confirmed_at = stamp(datetime.now(timezone.utc)) if committed else None
            _pending.append([prepared, confirmed_at, 0, monotonic(), 0.])
            if _thread is None or not _thread.is_alive():
                _thread = Thread(target=_run, args=(_generation,), name='market-archive-decisions', daemon=True)
                _thread.start()
            _condition.notify_all()
    if refused:
        failure(prepared['owner'], 'decision_queue_refused', OverflowError('decision_queue'))
    return not refused


def _write(prepared, confirmed_at):
    Archive(prepared['owner'], write_timeout=WRITE_TIMEOUT).decision(prepared, confirmed_at=confirmed_at)


def _run(generation):
    global _busy
    while True:
        with _condition:
            while not _pending and not _closing and generation == _generation:
                _condition.wait(1.)
            if not _pending or generation != _generation:
                return
            item = _pending[0]
            wait = item[4]-monotonic()
            if wait > 0 and not _closing:
                _condition.wait(min(wait, 1.))
                continue
            _busy = True
        prepared, confirmed_at, attempts, began, _ = item
        started = monotonic()
        error = None
        try:
            _write(prepared, confirmed_at)
        except Exception as exc:
            error = exc
        seconds = round(monotonic()-started, 6)
        with _condition:
            if generation != _generation:
                return
            _busy = False
            if error is None:
                _pending.popleft()
                _count('written')
                _count('written_after_retry', 1 if attempts else 0)
                _stats.update(last_write_at=stamp(datetime.now(timezone.utc)), last_write_seconds=seconds,
                              max_write_seconds=max(seconds, _stats.get('max_write_seconds', 0)))
                terminal = False
            else:
                info = diagnostic(error)
                terminal = (not info['recoverable'] or attempts+1 >= MAX_ATTEMPTS
                            or monotonic()-began >= MAX_AGE_SECONDS or _closing)
                _stats.update(last_error={'at': stamp(datetime.now(timezone.utc)), **info})
                if terminal:
                    _pending.popleft()
                    _count('failed')
                else:
                    item[2] = attempts+1
                    item[4] = monotonic()+min(30., RETRY_BASE_SECONDS * 2**attempts)
                    _count('retries')
            _condition.notify_all()
        if error is not None:
            failure(prepared['owner'], 'decision_write' if terminal else 'decision_write_retry', error)


def health():
    with _condition:
        result = {k: v for k, v in _stats.items()}
        result.update(queued=len(_pending), queue_limit=MAX_PENDING, writing=_busy,
                      oldest_queued_seconds=round(monotonic()-_pending[0][3], 3) if _pending else None,
                      writer_alive=bool(_thread and _thread.is_alive()),
                      durability='memory queue; a crash before the local write loses that copy, not the execution record')
    return result


def drain(timeout=5):
    """Bounded final flush for shutdown. Unwritten copies are counted, not hidden."""
    global _closing
    deadline = monotonic()+max(0, min(10, timeout))
    with _condition:
        _closing = True
        _condition.notify_all()
        while (_pending or _busy) and monotonic() < deadline and _thread is not None and _thread.is_alive():
            _condition.wait(max(.01, min(.25, deadline-monotonic())))
        remaining = len(_pending)
        if remaining:
            _count('unwritten_at_shutdown', remaining)
        return remaining == 0 and not _busy


def reset():
    """Test isolation only."""
    global _closing, _thread, _busy, _generation
    with _condition:
        _generation += 1
        _pending.clear()
        _stats.clear()
        _closing = False
        _busy = False
        _thread = None
        _condition.notify_all()
