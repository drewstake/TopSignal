"""Current host observations for the Health tab. Only read small producer receipts."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .market_archive import BACKUP_FRESH_SECONDS, backup_status
from .recording_health import DURABLE_AGE_SECONDS, RECORDER_STALE_SECONDS
from .trading_day import futures_session_is_open


@dataclass(frozen=True)
class Reading:
    status: str
    reason: str
    checked_at: datetime | None = None
    expires_at: datetime | None = None


def evidence(value, now, seconds, source):
    try:
        at = datetime.fromisoformat(value)
        if at.utcoffset() is None or at > now:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        return Reading('Unknown', f'No valid check time is available for {source}.')
    expires = at + timedelta(seconds=seconds)
    if now >= expires:
        return Reading('Unknown', f'The last check for {source} is too old to establish its current status.', at)
    return Reading('Healthy', '', at, expires)


def receipt(path):
    if path is not None:
        try:
            with path.open('rb') as stream:
                value = json.loads(stream.read(65537))
            if isinstance(value, dict):
                return value
        except (ValueError, OSError, RecursionError):
            pass
    return {}


def paused(path):
    # Both independent services poll their own operator-owned STOP file.
    try:
        return path is not None and path.with_name('STOP').is_file()
    except OSError:
        return False


def database(db, now):
    try:
        if db.get_bind().dialect.name != 'postgresql':
            return Reading('Unknown', 'This workspace uses a different database. PostgreSQL is not in use.', now)
        db.execute(text('select 1')).scalar_one()
    except SQLAlchemyError:
        return Reading('Unhealthy', 'PostgreSQL failed the read-only connection check.', now)
    return Reading('Healthy', 'PostgreSQL answered a read-only query.', now)


def recorder(depth, check, now):
    if check.status == 'Unknown':
        return check
    state = depth.get('state')
    faults = {
        'failed': 'The recorder reported a capture failure.',
        'reconnecting': 'The recorder lost its feed and is reconnecting.',
        'stopped': 'The independent recorder has stopped.',
        'storage_blocked': 'The recorder stopped because storage is blocked.',
    }
    if isinstance(state, str) and state in faults:
        return replace(check, status='Unhealthy', reason=faults[state])
    if state != 'recording':
        return replace(check, status='Unknown', reason='The recorder has not confirmed active capture.')
    storage = depth.get('storage')
    if not isinstance(storage, dict):
        return replace(check, status='Unknown', reason='The recorder did not report its storage status.')
    if storage.get('blocked_reason') or storage.get('scan_stalled'):
        return replace(check, status='Unhealthy', reason='The recorder reported a problem checking or writing storage.')
    detail = depth.get('recording')
    if isinstance(detail, dict):
        saved, received = detail.get('durable_event_index'), detail.get('received_events')
        if type(saved) is int and type(received) is int and 0 < saved <= received:
            if received > saved:
                write = evidence(detail.get('last_durable_at'), now, DURABLE_AGE_SECONDS, 'the last recording write')
                if write.status == 'Unknown':
                    if write.checked_at:
                        return replace(check, status='Unhealthy', reason='Events are waiting and the recorder has stopped saving them.')
                    return replace(check, status='Unknown', reason='Events are waiting and the last save time is unavailable.')
            return replace(check, reason='The independent recorder is capturing and has saved events to disk.')
    return replace(check, status='Unknown', reason='The recorder has not confirmed that events reached disk.')


def market_data(depth, check, now):
    if check.status == 'Unknown':
        return check
    if depth.get('state') == 'reconnecting':
        return replace(check, status='Unhealthy', reason='The market feed disconnected and is reconnecting.')
    if depth.get('state') != 'recording':
        return replace(check, status='Unknown', reason='The recorder cannot currently confirm the market feed.')
    if not futures_session_is_open(now, symbol='MNQ'):
        frame = evidence(depth.get('last_frame_at'), now, 45, 'the feed connection')
        return replace(frame, reason='The MNQ session is closed. The feed connection is responding.') if frame.status == 'Healthy' else frame
    detail = depth.get('recording')
    tick = evidence(detail.get('last_depth_at') if isinstance(detail, dict) else None, now,
                    RECORDER_STALE_SECONDS, 'MNQ market depth')
    return replace(tick, reason='The feed delivered a recent MNQ market depth update.') if tick.status == 'Healthy' else tick


def discord(data, now):
    check = evidence(data.get('at'), now, 90, 'the Discord reader')
    if check.status == 'Unknown':
        return check
    state = data.get('state')
    if state == 'watching':
        return replace(check, reason='The reader is watching for trade updates.')
    faults = {'attention': 'The reader flagged a delivery or connection problem.',
              'retrying': 'The reader encountered an error and is retrying.',
              'stopped': 'The Discord reader has stopped.'}
    if isinstance(state, str) and state in faults:
        return replace(check, status='Unhealthy', reason=faults[state])
    return replace(check, status='Unknown', reason='The reader did not report a recognized state.')


def backup(data, now, *, archive):
    state = data.get('status' if archive else 'state')
    if archive:
        at = data.get('last_attempt_at' if state == 'failed' else 'last_verified_at')
        source = 'the full off-device archive'
    else:
        at, source = data.get('observed_at'), 'the local execution backup'
    check = evidence(at, now, BACKUP_FRESH_SECONDS, source)
    if check.status == 'Unknown':
        if archive:
            return replace(check, reason=f'{check.reason} The recovery PC controls its schedule.')
        return check
    if state == 'failed':
        return replace(check, status='Unhealthy', reason=f'The latest attempt to create {source} failed.')
    if archive and state == 'verified':
        return replace(check, reason='The recovery PC verified a full off-device archive.')
    if not archive and state == 'verified_and_published':
        return replace(check, reason='The host verified and published its local execution database backup.')
    return replace(check, status='Unknown', reason='The backup job has not confirmed verification.')


def report(db, worker):
    # The launcher sets this path relative to the actual protected runtime root.
    path = os.getenv('TOPSIGNAL_DEPTH_RECORDER_STATUS')
    depth_path = Path(path) if path else None
    runtime = depth_path.parent.parent if depth_path else None
    discord_path = runtime / 'discord-topbot/status.json' if runtime else None
    depth = receipt(depth_path)
    reader = receipt(discord_path)
    execution = receipt(runtime / 'execution-backup/status.json' if runtime else None)
    archive = backup_status()
    now = datetime.now(timezone.utc)
    check = evidence(depth.get('at'), now, RECORDER_STALE_SECONDS, 'the independent recorder')
    if paused(depth_path):
        check = Reading('Unknown', 'The operator has requested a recorder pause.', now)
    if not worker['healthy']:
        worker_check = Reading('Unhealthy', 'The background worker is unavailable.', now)
    elif not worker['enabled']:
        worker_check = Reading('Unknown', 'The background worker is disabled by configuration.', now)
    else:
        worker_check = Reading('Healthy', 'The background worker is running.', now)
    return dict(as_of=now, components={
        'postgres': database(db, now),
        'worker': worker_check,
        'recorder': recorder(depth, check, now),
        'market': market_data(depth, check, now),
        'discord': Reading('Unknown', 'The operator has requested a Discord reader pause.', now)
                   if paused(discord_path) else discord(reader, now),
        'execution_backup': backup(execution, now, archive=False),
        'archive_backup': backup(archive, now, archive=True),
    })
