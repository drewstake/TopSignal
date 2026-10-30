"""Explicit read-only collection authorization, separate from bot enablement.

The existing worker lease schedules this; no additional worker or credentials.
One account per owner prevents duplicate user/market subscriptions for this scope.
"""
import json
import os
import re
from datetime import datetime, timezone
from threading import Lock

from .market_archive import enabled, stamp

_lock = Lock()
_health = {}


def collection_enabled():
    return enabled() and os.getenv('TOPSIGNAL_MARKET_COLLECTION_ENABLED', '').lower() in {'1', 'true', 'yes', 'on'}


def configured_scopes():
    if not collection_enabled():
        return []
    raw = os.getenv('TOPSIGNAL_MARKET_COLLECTION_SCOPES', '[]')
    if len(raw) > 8192:
        raise ValueError('Collection scope too large')
    rows = json.loads(raw)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        raise ValueError('One to four explicit collection scopes required')
    result, owners, contracts = [], set(), set()
    for row in rows:
        owner, account = str(row['owner']), int(row['account'])
        scope_id = str(row['id'])
        names = row['contracts']
        if (not owner or account <= 0 or not re.fullmatch(r'[a-z0-9_-]{1,64}', scope_id)
                or not isinstance(names, list) or len(names) != 1
                or owner in owners or len(set(names)) != len(names)):
            raise ValueError('Invalid or duplicate collection scope')
        for contract in names:
            if not re.fullmatch(r'CON\.F\.US\.MNQ\.[FGHJKMNQUVXZ]\d{2}', contract) or (owner, contract) in contracts:
                raise ValueError('Explicit MNQ delivery contract required')
            contracts.add((owner, contract))
        owners.add(owner)
        result.append(dict(id=scope_id, owner=owner, account=account, contracts=tuple(names)))
    return result


def heartbeat(owner, *, error=None, contracts=()):
    with _lock:
        _health[str(owner)] = dict(heartbeat_at=stamp(datetime.now(timezone.utc)), error=error, contracts=list(contracts))


def health(owner):
    try:
        scopes = [s for s in configured_scopes() if s['owner'] == str(owner)]
        error = None
    except Exception as exc:
        scopes, error = [], type(exc).__name__
    with _lock:
        runtime = dict(_health.get(str(owner), {}))
    age = (datetime.now(timezone.utc)-datetime.fromisoformat(runtime['heartbeat_at'])).total_seconds() if runtime.get('heartbeat_at') else None
    return dict(enabled=collection_enabled(), authorized=bool(scopes), scopes=scopes,
                running=bool(scopes and age is not None and age < 90 and not runtime.get('error')),
                error=error or runtime.get('error'), heartbeat_at=runtime.get('heartbeat_at'),
                scheduling='existing bot-worker lease; independent of BotRun status')
