"""Sample BBO, retain broker lifecycle events, batch filesystem writes.

Critical callbacks commit to a separate local FULL spool before acknowledgement.
Optional sampled quotes use their own bounded lossy queue. No depth subscription.
"""
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
import math
import logging
import hashlib
from uuid import uuid4

from .market_archive import Archive, canonical, enabled, failure, stamp
from .market_archive_spool import MAX_EVENT_BYTES, Spool
from .market_archive_quotes import QuoteSpool, diagnostic, MAX_EVENT_BYTES as QUOTE_BYTES, MAX_ATTEMPTS, MAX_AGE_SECONDS

_queue = Queue(maxsize=4096)
_lock = Lock()
_thread = None
_quote_thread = None
_closing = False
_last_sample = {}
_critical = Queue(maxsize=1024)
_stop = Event()
_owners = set()
_stats = {}
_process_id = str(uuid4())
_receipt_sequence = 0

QUOTE_FIELDS = ('bestBid', 'bestAsk', 'timestamp', 'lastUpdated', 'symbol', 'lastPrice')
BROKER_FIELDS = ('id', 'accountId', 'contractId', 'creationTimestamp', 'updateTimestamp',
                 'timestamp', 'status', 'type', 'side', 'size', 'limitPrice', 'stopPrice',
                 'fillVolume', 'filledPrice', 'customTag', 'price', 'profitAndLoss', 'fees',
                 'voided', 'orderId', 'averagePrice', 'simulated', 'reason', 'stream')


def event_record(account, kind, payload, now):
    quote = kind == 'GatewayQuote'
    event = dict(kind='bbo_sample' if quote else kind, provider='projectx', contract=payload.get('contractId'),
                 account=account, received_at=stamp(now), feed_status='unverified',
                 payload={k: payload[k] for k in (QUOTE_FIELDS if quote else BROKER_FIELDS) if k in payload},
                 execution_evidence='market_quote' if quote else 'broker_reported; account simulation classification must be joined; not a TopBot paper fill')
    if quote:
        bid, ask = payload.get('bestBid'), payload.get('bestAsk')
        event['quality'] = 'paired_bbo' if all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0 for v in (bid, ask)) and bid <= ask else 'unusable_bbo'
    return event


def _metric(owner, name, amount=1):
    with _lock:
        state = _stats.setdefault(str(owner), {})
        state[name] = state.get(name, 0) + amount


def _quote_summary(owner, *, force=False):
    """Bounded cumulative loss diagnostics survive normal shutdown in existing logs."""
    with _lock:
        stats = _stats.setdefault(str(owner), {})
        now = monotonic()
        if not force and now-stats.get('_quote_summary_at', -60) < 30:
            return
        stats['_quote_summary_at'] = now
        counts = {k: v for k,v in stats.items() if k.startswith('optional_quotes_')}
    logging.getLogger(__name__).warning('optional_quote_counters process=%s scope=%s counts=%s',
        _process_id, hashlib.sha256(str(owner).encode()).hexdigest()[:16], counts)


def resume(owner):
    """Called by the lease owner even with no bot runs or incoming hub events."""
    global _thread, _quote_thread
    with _lock:
        if _closing:
            return
        _owners.add(str(owner))
        if _thread is None or not _thread.is_alive():
            _stop.clear()
            _thread = Thread(target=_run, name='market-archive-events', daemon=True)
            _thread.start()
        if _quote_thread is None or not _quote_thread.is_alive():
            _quote_thread = Thread(target=_run_quotes, name='market-archive-quotes', daemon=True)
            _quote_thread.start()


def capture(owner, account, kind, payload, contracts, *, now=None):
    global _receipt_sequence
    if not enabled():
        return
    owner = str(owner)
    now = now or datetime.now(timezone.utc)
    contract = str(payload.get('contractId') or '')
    if kind != 'GatewayUserAccount' and contract not in contracts:
        return
    quote = kind == 'GatewayQuote'
    if not quote and payload.get('accountId') != account:
        return
    if quote:
        _metric(owner, 'optional_quotes_received')
        if _closing:
            _metric(owner, 'optional_quotes_dropped')
            _metric(owner, 'optional_quotes_shutdown_dropped')
            _quote_summary(owner)
            return
        with _lock:
            key = (owner, contract)
            if monotonic() - _last_sample.get(key, -10) < 5:
                state = _stats.setdefault(owner, {})
                state['optional_quotes_sampled_out'] = state.get('optional_quotes_sampled_out', 0)+1
                return
            _last_sample[key] = monotonic()
            if len(_last_sample) > 64:
                _last_sample.clear()
        try:
            if any(not isinstance(payload[k], (str, int, float, type(None)))
                   or isinstance(payload[k], str) and len(payload[k]) > 256 for k in QUOTE_FIELDS if k in payload):
                raise ValueError('quote_field_size_or_type')
            event = event_record(account, kind, payload, now)
            with _lock:
                _receipt_sequence += 1
                event['sample_id'] = f'{_process_id}:{_receipt_sequence}'
            if len(canonical(event).encode()) > QUOTE_BYTES:
                raise ValueError('quote_oversize')
            _queue.put_nowait((owner, event, 0, monotonic(), 0))
            _metric(owner, 'optional_quotes_queued')
        except (Full, ValueError, TypeError) as exc:
            _metric(owner, 'optional_quotes_dropped')
            _metric(owner, 'optional_quotes_queue_or_size_dropped')
            failure(owner, 'optional_quote_admission_lost', exc)
            _quote_summary(owner)
    else:
        event = event_record(account, kind, payload, now)
        with _lock:
            _receipt_sequence += 1
            event['capture'] = dict(process_id=_process_id, receipt_sequence=_receipt_sequence)
        _metric(owner, 'critical_received')
        # This callback runs on the existing hub thread, never the trading loop.
        # A short FULL transaction commits before acknowledging durable capture.
        try:
            Spool(owner).enqueue(event)
        except Exception as exc:
            failure(owner, 'critical_spool_write', exc)
            _metric(owner, 'spool_write_failures')
            try:
                if len(canonical(event).encode()) > MAX_EVENT_BYTES:
                    raise Full()
                _critical.put_nowait((owner, event))
                _metric(owner, 'critical_memory_queued')
            except Full:
                _metric(owner, 'critical_unrecoverable')
                failure(owner, 'critical_unrecoverable', exc)
    resume(owner)


def _iteration():
    # Never dequeue fallback evidence until its durable spool commit succeeds.
    with _critical.mutex:
        pending = list(_critical.queue)[:128]
    for owner, event in pending:
        try:
            Spool(owner).enqueue(event)
        except Exception as exc:
            failure(owner, 'critical_memory_retry', exc)
            _metric(owner, 'memory_retry_attempts')
            break
        _critical.get_nowait()
        _critical.task_done()
        _metric(owner, 'critical_memory_recovered')
    with _lock:
        owners = tuple(_owners)
    for owner in owners:
        try:
            Spool(owner).flush()
        except Exception as exc:
            failure(owner, 'critical_archive_retry', exc)


def _quote_iteration():
    # Quotes have a dedicated consumer and separate disk spool. No quote retry
    # sleeps or I/O run on the critical-event consumer or trading evaluation loop.
    for _ in range(128):
        if _stop.is_set(): break
        with _queue.mutex:
            head = _queue.queue[0] if _queue.queue else None
        if head is None: break
        owner, event, attempts, began, next_at = head
        now = monotonic()
        if now < next_at: break
        if attempts:
            _metric(owner, 'optional_quotes_memory_retried')
        terminal = now-began >= MAX_AGE_SECONDS or attempts >= MAX_ATTEMPTS
        try:
            if not terminal:
                QuoteSpool(owner).enqueue(event)
        except Exception as exc:
            terminal = not diagnostic(exc)['recoverable'] or attempts+1 >= MAX_ATTEMPTS
            failure(owner, 'optional_quote_spool_write', exc)
            if not terminal:
                with _queue.mutex:
                    _queue.queue[0] = (owner, event, attempts+1, began, now+min(30, 2**(attempts+1)))
                break
        _queue.get_nowait()
        _queue.task_done()
        if terminal:
            _metric(owner, 'optional_quotes_dropped')
            _metric(owner, 'optional_quotes_spool_unrecoverable')
            _quote_summary(owner)
    with _lock:
        owners = tuple(_owners)
    for owner in owners:
        if _stop.is_set(): break
        try:
            QuoteSpool(owner).flush()
        except Exception as exc:
            failure(owner, 'optional_quote_archive_write', exc)


def _run_quotes():
    while not _stop.is_set():
        _quote_iteration()
        _stop.wait(.5)


def _run():
    while not _stop.is_set():
        _iteration()
        # Disk faults in the fallback queue retry at most once per five seconds;
        # persisted archive failures have exponential backoff capped at 5 min.
        _stop.wait(5 if not _critical.empty() else .5)
    # Preserve a final critical-only attempt, inside the joined worker so the
    # caller's shutdown deadline remains bounded. Quotes never join this pass.
    _iteration()


def health(owner, root=None):
    with _lock:
        metrics = dict(_stats.get(str(owner), {}))
    with _critical.mutex:
        metrics['memory_backlog'] = sum(o == str(owner) for o, _ in _critical.queue)
    try:
        result = Spool(owner, root).status()
    except Exception as exc:
        result = {'status_error': type(exc).__name__}
    try:
        result['quotes'] = QuoteSpool(owner, root).status()
    except Exception as exc:
        result['quotes'] = {'status_error': diagnostic(exc)}
    with _queue.mutex:
        metrics['quote_memory_backlog'] = sum(row[0] == str(owner) for row in _queue.queue)
    result.update(process=metrics, memory_limit=1024, quote_queue_limit=4096,
                  caveat='Process counters reset on restart; uncommitted memory and pre-callback network events can be lost. Historical reconciliation cannot restore every intermediate event.')
    return result


def drain(timeout=5):
    global _closing
    _closing = True
    deadline = monotonic()+max(0, min(5, timeout))
    _stop.set()
    with _lock:
        threads = (_thread, _quote_thread)
    for thread in threads:
        if thread: thread.join(max(0, deadline-monotonic()))
    if any(thread and thread.is_alive() for thread in threads):
        with _queue.mutex:
            pending = [row[0] for row in _queue.queue]
        for owner in set(pending):
            _metric(owner, 'optional_quotes_shutdown_at_risk', pending.count(owner))
            _quote_summary(owner, force=True)
        return False
    # Durable spools survive. Explicitly account for volatile optional losses;
    # never perform an unbounded synchronous final flush on service shutdown.
    while True:
        try:
            owner, *_ = _queue.get_nowait()
        except Empty:
            break
        _queue.task_done()
        _metric(owner, 'optional_quotes_dropped')
        _metric(owner, 'optional_quotes_shutdown_dropped')
    with _lock:
        owners = tuple(_owners)
    for owner in owners:
        _quote_summary(owner, force=True)
    return _critical.empty()
