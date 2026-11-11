"""Read-only recording health, reported separately from trading health.

Nothing here gates an evaluation, an order or readiness. A worker that is
trading normally can have unhealthy recording, and this report says so.
Every figure is an observation of this process and the local files it names;
it does not certify that any recording is complete.
"""
from datetime import datetime, timezone
import json
import shutil

from .probabilistic_shadow import cache_root, scope_hash

GIB = 1024**3
RECORDER_STALE_SECONDS = 60
QUEUE_AGE_SECONDS = 5
DURABLE_AGE_SECONDS = 30
REVIEW_WARNING_SECONDS = 7 * 86400


def _age(value, now):
    try:
        return (now-datetime.fromisoformat(str(value).replace('Z', '+00:00'))).total_seconds()
    except (TypeError, ValueError):
        return None


def _worker_evidence(owner, now, problems):
    from .topbot_depth import feed
    rows = []
    with feed.lock:
        streams = [(key[1], stream['state'].capture) for key, stream in feed.streams.items() if key[0] == str(owner)]
    for contract, capture in streams:
        status = capture.status() if capture is not None else {'state': 'unavailable'}
        # Durable progress, not a connected socket: how long since a batch actually
        # committed, and how many produced events are not yet durable.
        committed_age = _age(status.get('last_commit_completed_at'), now)
        status['durable_age_seconds'] = round(committed_age, 3) if committed_age is not None else None
        if 'produced_index' in status:
            status['uncommitted_events'] = status['produced_index']-status.get('durable_index', 0)
        rows.append({'contract_id': contract, **status})
        state = status['state']
        if (state == 'recording' and status.get('uncommitted_events', 0) > 0
                and (committed_age is None or committed_age > DURABLE_AGE_SECONDS)):
            problems.append(dict(code='worker_evidence_not_durable', contract_id=contract,
                                 seconds=status['durable_age_seconds'], uncommitted=status['uncommitted_events'],
                                 action='Events are arriving but no batch has committed recently; check the data disk.'))
        if state not in {'recording', 'closed'}:
            problems.append(dict(code='worker_evidence_'+state, contract_id=contract,
                                 category=status.get('error_category'), reason=status.get('blocked_reason'),
                                 action={'storage_blocked': 'Review evidence storage: budget, free space or the 30-day review boundary. Nothing is deleted automatically.',
                                         'retrying': 'Local storage is contended or failing; the writer is retrying.',
                                         'stalled': 'The writer has made no progress for five seconds; check the data disk.',
                                         'starting': 'The capture has not committed its first batch yet.'}.get(
                                             state, 'Inspect the recorder error category in the diagnostics log.')))
        if status.get('dropped_events'):
            problems.append(dict(code='worker_evidence_events_dropped', contract_id=contract,
                                 count=status['dropped_events'], last=status.get('last_drop'),
                                 action='This capture has index holes; replay cannot certify state after the first hole.'))
        if (status.get('oldest_queued_seconds') or 0) > QUEUE_AGE_SECONDS:
            problems.append(dict(code='worker_evidence_backlog', contract_id=contract,
                                 seconds=status['oldest_queued_seconds'], queued=status.get('queued_events'),
                                 action='Events are waiting longer than five seconds for a durable write.'))
        due = _age(status.get('storage', {}).get('review_due_at'), now)
        if due is not None and due > -REVIEW_WARNING_SECONDS and state != 'storage_blocked':
            problems.append(dict(code='worker_evidence_review_boundary_near', contract_id=contract,
                                 due_at=status['storage']['review_due_at'],
                                 action='Recording stops at the 30-day review boundary until older captures are reviewed and archived with a verified backup.'))
    return rows


def _depth_recorder(owner, root, now, problems):
    path = root / 'depth-recordings-v1' / 'recording-health.json'
    try:
        with path.open('rb') as stream:
            value = json.loads(stream.read(65537))
    except (OSError, ValueError):
        return {'state': 'not_reported'}
    if value.pop('owner_hash', None) != scope_hash(str(owner)):
        return {'state': 'not_reported'}
    age = _age(value.get('at'), now)
    value.update(age_seconds=age, stale=age is None or age > RECORDER_STALE_SECONDS)
    if value['stale'] or value.get('state') != 'recording':
        problems.append(dict(code='depth_recorder_not_recording', state=value.get('state'), reason=value.get('reason'),
                             age_seconds=age, action='The independent recorder is stopped, reconnecting or not reporting; history and charts have a gap until it resumes.'))
    if value.get('capacity_scan_stalled') or value.get('capacity_scan_failures'):
        problems.append(dict(code='depth_recorder_capacity_scan_slow', failures=value.get('capacity_scan_failures'),
                             age_seconds=value.get('capacity_scan_age_seconds'),
                             action='The storage walk is slow or failing; capture continues on conservative accounting until the stale limit.'))
    return value


def report(owner, *, now=None):
    now = now or datetime.now(timezone.utc)
    root = cache_root()
    problems = []
    result = dict(as_of=now.isoformat(), scope='recording only; trading health is reported by readiness and worker status',
                  storage_root=str(root))
    try:
        result['worker_evidence'] = _worker_evidence(owner, now, problems)
    except Exception as exc:
        result['worker_evidence'] = {'status_error': type(exc).__name__}
        problems.append(dict(code='worker_evidence_status_unavailable', action='Inspect the backend log.'))
    try:
        from .market_archive_decisions import health as decision_health
        decisions = result['decision_archive'] = decision_health()
        lost = sum(decisions.get(k, 0) for k in ('failed', 'dropped_queue_full', 'dropped_shutdown', 'unwritten_at_shutdown'))
        if lost:
            problems.append(dict(code='decision_copies_not_archived', count=lost, last_error=decisions.get('last_error'),
                                 action='Those decisions exist in the execution database without a local archive copy.'))
        if (decisions.get('oldest_queued_seconds') or 0) > 60:
            problems.append(dict(code='decision_archive_backlog', seconds=decisions['oldest_queued_seconds'],
                                 queued=decisions['queued'], action='The archive disk is contended; copies are waiting.'))
    except Exception as exc:
        result['decision_archive'] = {'status_error': type(exc).__name__}
    try:
        from .market_archive_events import health as event_health
        events = result['broker_events'] = event_health(owner)
        process = events.get('process', {})
        if process.get('critical_unrecoverable'):
            problems.append(dict(code='broker_events_lost', count=process['critical_unrecoverable'],
                                 action='Broker observations were lost before a durable local write.'))
        if events.get('backlog', 0) and events.get('retry_records', 0):
            problems.append(dict(code='broker_event_archive_backlog', backlog=events['backlog'],
                                 action='Durably spooled broker events are waiting for the archive writer.'))
        # The durable quote counters are cumulative for the life of the file; they
        # stay visible in this section. Only losses since this process started
        # are a current problem, so one past fault cannot mask today's health.
        if process.get('optional_quotes_dropped'):
            problems.append(dict(code='optional_quote_samples_dropped', count=process['optional_quotes_dropped'],
                                 action='Sampled quotes are optional evidence; losses are counted, not reconstructed.'))
    except Exception as exc:
        result['broker_events'] = {'status_error': type(exc).__name__}
    result['depth_recorder'] = _depth_recorder(owner, root, now, problems)
    from .market_archive import backup_status
    backup = result['backup'] = backup_status()
    if backup.get('stale'):
        problems.append(dict(code='off_device_backup_stale', status=backup.get('status'),
                             last_verified_at=backup.get('last_verified_at'), last_failure=backup.get('last_failure'),
                             action='No verified off-device copy within 36 hours; local recordings are exposed to a device failure.'))
    try:
        ancestor = root
        while not ancestor.exists():
            ancestor = ancestor.parent
        usage = shutil.disk_usage(ancestor)
        result['disk'] = dict(free_bytes=usage.free, total_bytes=usage.total)
        if usage.free < 10*GIB:
            problems.append(dict(code='recording_disk_low', free_bytes=usage.free,
                                 action='Below the 10 GiB floor recorders stop writing rather than delete data.'))
    except OSError as exc:
        result['disk'] = {'status_error': type(exc).__name__}
        problems.append(dict(code='recording_disk_unavailable', action='The recording volume cannot be read.'))
    result.update(recording_healthy=not problems, problems=problems,
                  limitations=['Counters reset when the backend restarts; durable files keep their own status.',
                               'Events never delivered by the provider or lost before receipt cannot be counted.',
                               'Healthy means no known fault now, not that any recording is complete.'])
    return result
