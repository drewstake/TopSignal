"""Bounded History/retrieveBars recovery scheduled by the existing lease owner.

The background thread only reads the provider and writes local files. It cannot
trade, arm runs, fit models or write to the application database.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
from time import monotonic

from .market_archive import (Archive, MINUTE, canonical, enabled, failure, get_state,
                             native_bar, put_bar, refresh_day, set_state, stamp, stream_key)
from .trading_day import trading_day_bounds_utc, trading_day_date

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='market-archive-fetch')
_lock = threading.Lock()
_pending = None
_next = {}


def request_collection(owner, account, config, contract, client, allowed):
    global _pending
    if not enabled() or not str(contract).startswith('CON.F.US.MNQ.') or not allowed():
        return
    key = (str(owner), str(contract))
    with _lock:
        if _pending is not None and not _pending.done():
            return
        if monotonic() < _next.get(key, 0):
            return
        _next[key] = monotonic() + 60
        # Bound process bookkeeping even if configurations change repeatedly.
        if len(_next) > 64:
            _next.clear()
            _next[key] = monotonic() + 60
        _pending = _pool.submit(_collect_safe, owner, account, config, contract, client, allowed)


def _collect_safe(owner, account, config, contract, client, allowed):
    try:
        if not allowed():
            return
        client = client() if callable(client) else client
        collect(owner, account, config, contract, client, allowed=allowed)
    except Exception as exc:
        failure(owner, 'collection', exc)
        try:
            with Archive(owner).transaction() as db:
                previous = get_state(db, 'collection:'+contract, {})
                set_state(db, 'collection:'+contract, {**previous, 'error_type': type(exc).__name__, 'failed_at': stamp(datetime.now(timezone.utc))})
        except Exception:
            pass


def collect(owner, account, config, contract, client, *, now=None, root=None, allowed=lambda: True):
    now = now or datetime.now(timezone.utc)
    # Give delayed delivery two minutes; overlap discovers subsequent corrections.
    cutoff = now.replace(second=0, microsecond=0) - 2*MINUTE
    archive = Archive(owner, root)
    stream = stream_key(contract)
    with archive.transaction() as db:
        prior_contract = get_state(db, 'config-contract:'+str(config))
        if prior_contract and prior_contract != contract:
            event = dict(kind='contract_roll', contract=contract, previous_contract=prior_contract,
                         config=config, received_at=stamp(now), policy='separate_delivery_streams; no splicing')
            from .market_archive import digest, pack
            db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)', (digest(event), event['kind'], stamp(now), contract, pack(event)))
        set_state(db, 'config-contract:'+str(config), contract)
        initial = trading_day_bounds_utc(trading_day_date(cutoff - timedelta(days=7)))[0]
        cursor = datetime.fromisoformat(get_state(db, 'cursor:'+contract, stamp(initial)))
        origin = get_state(db, 'origin:'+contract)
        if origin is None:
            set_state(db, 'origin:'+contract, stamp(cursor))
        retry = None
        for day, payload in db.execute('SELECT day,payload FROM coverage WHERE stream=? ORDER BY day', (stream,)):
            row = json.loads(payload)
            if row['missing'] and row.get('attempts', 0) < 3 and (not row.get('retry_at') or row['retry_at'] <= stamp(now)):
                retry = (day, row)
                break
    # One recent overlap plus one chronological catch-up OR old-gap recovery.
    windows = [(max(cursor - 20*MINUTE, cutoff - 20*MINUTE), cutoff, 'recent')]
    if cursor < cutoff - 20*MINUTE:
        windows.append((cursor, min(cursor + timedelta(days=1), cutoff), 'catchup'))
    elif retry:
        start, end = trading_day_bounds_utc(datetime.fromisoformat(retry[0]).date())
        windows.append((start, min(end + timedelta(microseconds=1), cutoff), 'recovery'))
    total = 0
    for start, end, mode in windows:
        if start >= end or not allowed():
            continue
        if mode == 'recovery':
            # Count even failed provider calls; no unbounded retry storm after restarts.
            with archive.transaction() as db:
                row = retry[1]
                row.update(attempts=row.get('attempts', 0)+1, retry_at=stamp(now+timedelta(minutes=15)))
                db.execute('UPDATE coverage SET payload=? WHERE stream=? AND day=?', (canonical(row), stream, retry[0]))
        bars = client.retrieve_bars(contract_id=contract, live=False, start=start, end=end,
                                    unit=2, unit_number=1, limit=1500, include_partial_bar=False)
        received = datetime.now(timezone.utc) if root is None else now
        if len(bars) >= 1500:
            raise ValueError('Unexpected truncated history; cursor retained')
        normalized = [native_bar(b, contract) for b in bars if start <= b['timestamp'] < end
                      and b['timestamp'] + MINUTE <= cutoff and not b.get('is_partial')]
        with archive.transaction() as db:
            for bar in normalized:
                put_bar(db, bar, stamp(received))
            # Verify the entire requested range, including wholly empty days.
            day = trading_day_date(start)
            last = trading_day_date(end-timedelta(microseconds=1))
            while day <= last:
                refresh_day(db, contract, day, cutoff, stamp(received))
                day += timedelta(days=1)
            if mode == 'catchup' or (mode == 'recent' and cursor >= cutoff-20*MINUTE):
                set_state(db, 'cursor:'+contract, stamp(end))
            set_state(db, 'collection:'+contract, dict(last_success=stamp(received), requested_start=stamp(start),
                      requested_end=stamp(end), returned=len(normalized), mode=mode,
                      live_requested=False, feed_status='unverified', account=account, config=config))
        total += len(normalized)
    if allowed():
        try:
            recover_broker_events(archive, account, contract, client, now)
        except Exception as exc:
            failure(owner, 'broker_recovery', exc)
            with archive.transaction() as db:
                set_state(db, 'broker_error:'+str(account), {'type': type(exc).__name__, 'at': stamp(now)})
    return total


def recover_broker_events(archive, account, contract, client, now):
    """One bounded day per 15 minutes, independent cursor; read-only provider APIs.

    History can restore reported order states/fills, never intermediate events
    lost during a disconnect. Hub connection gaps remain preserved evidence.
    """
    key = 'broker:'+str(account)+':'+contract
    with archive.transaction() as db:
        state = get_state(db, key, {})
        if state.get('next_attempt') and state['next_attempt'] > stamp(now):
            return
        start = datetime.fromisoformat(state.get('cursor', stamp(now-timedelta(days=1)))) - timedelta(minutes=15)
        end = min(start+timedelta(days=1), now)
        set_state(db, key, {**state, 'next_attempt': stamp(now+timedelta(minutes=15))})
    orders = client.search_orders(account_id=account, start=start, end=end)
    fills = client.fetch_trade_history(account, start, end, limit=2000, require_valid_collection=True)
    if len(orders) >= 2000 or len(fills) >= 2000:
        raise ValueError('Broker recovery truncated; cursor retained')
    from .market_archive_events import event_record
    events = []
    for kind, rows in (('GatewayUserOrder', orders), ('GatewayUserTrade', fills)):
        for row in rows:
            payload = row.get('raw_payload') or {}
            if payload.get('contractId') == contract and payload.get('accountId') == account:
                events.append(event_record(account, kind, payload, now))
    for offset in range(0, len(events), 256):
        archive.events(events[offset:offset+256])
    with archive.transaction() as db:
        set_state(db, key, {'cursor': stamp(end), 'last_success': stamp(now), 'next_attempt': stamp(now+timedelta(minutes=15)),
                            'orders': len(orders), 'fills': len(fills), 'meaning': 'broker historical snapshots; intermediate order states may be unavailable'})
        set_state(db, 'broker_error:'+str(account), None)


def drain(timeout=5):
    with _lock:
        task = _pending
    if task:
        try:
            task.result(timeout=timeout)
        except TimeoutError:
            return False
    return True
