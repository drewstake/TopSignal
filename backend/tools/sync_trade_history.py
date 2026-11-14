"""Explicit provider synchronization / persisted coverage policy (writes state).

Run in the authoritative backend environment, never against a cloud fallback.
Report generation is a different, offline command.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['status','policy','sync'])
    p.add_argument('--owner', required=True)
    p.add_argument('--start', type=date.fromisoformat)
    p.add_argument('--end', type=date.fromisoformat, help='Inclusive UTC date; sessions are filtered at report time')
    p.add_argument('--accounts', type=int, nargs='+')
    p.add_argument('--record-active', action='store_true')
    p.add_argument('--disable', action='store_true', help='Disable the history policy; recorder enrollments remain explicit')
    p.add_argument('--max-accounts', type=int, default=8)
    p.add_argument('--max-windows', type=int, default=20)
    p.add_argument('--refresh', action='store_true')
    a = p.parse_args()
    from app.services import trade_history_sync as sync
    if a.action == 'status':
        if not sync.path().exists():
            print(json.dumps({'state':'not_configured'})); return
        with sync.state() as db:
            print(json.dumps({t:[dict(r) for r in db.execute(f'SELECT * FROM {t} WHERE owner=?',(a.owner,))]
                              for t in ('policies','accounts','windows')}, indent=2)); return
    if not a.start:
        p.error('--start is required for policy/sync')
    if a.action == 'policy':
        sync.configure(a.owner,a.start.isoformat(),record_active=a.record_active,max_accounts=a.max_accounts,enabled=not a.disable)
        print(json.dumps({'state':'configured','owner':a.owner})); return
    if not a.accounts or not 1 <= a.max_windows <= 200:
        p.error('sync requires explicit --accounts and --max-windows 1..200')
    from app.db import SessionLocal
    from app.main import _projectx_client_for_user_without_open_transaction
    from app.services.historical_trade_excursions import recorder_lock
    end = a.end or datetime.now(timezone.utc).date()
    if end < a.start or end > datetime.now(timezone.utc).date():
        p.error('Invalid date range')
    with sync.state(write=True):
        pass
    with recorder_lock(sync.path().with_suffix('.lock')) as acquired:
        if not acquired:
            raise RuntimeError('History synchronizer busy; retry later')
        with SessionLocal() as db:
            client = _projectx_client_for_user_without_open_transaction(db,user_id=a.owner)
            client.timeout_seconds = min(client.timeout_seconds,15)
        discovered = {r['id'] for r in client.list_accounts(only_active_accounts=False)}
        count = 0
        for account in a.accounts:
            if account not in discovered:
                print(json.dumps({'account':account,'state':'unavailable','reason':'not_in_provider_discovery'}))
                continue
            for i in range((end-a.start).days+1):
                day = a.start+timedelta(days=i)
                with sync.state() as db:
                    old = db.execute('SELECT * FROM windows WHERE owner=? AND account=? AND day=?',(a.owner,account,day.isoformat())).fetchone()
                if old and not a.refresh and (old['state']=='response_covered' or (old['next_retry'] or 0)>datetime.now(timezone.utc).timestamp()):
                    continue
                if count >= a.max_windows:
                    print(json.dumps({'state':'bounded_stop','windows':count,'resume':'repeat same command'})); return
                print(json.dumps(sync.sync_day(SessionLocal,client,a.owner,account,day)),flush=True)
                count += 1


if __name__ == '__main__':
    main()
