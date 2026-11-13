"""Collect provider history into local evidence without changing execution rows.

Useful before a pending schema migration. Explicit owned accounts (or all owned
provider accounts) are checked against fresh provider discovery. Successful
windows and sanitized failures persist; re-run to resume. No broker orders.
"""
import argparse
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time


def collect(args):
    from dotenv import load_dotenv
    load_dotenv(args.release_root/'backend/.env',override=True)
    sys.path.insert(0,str(args.release_root/'backend'))
    from app.db import SessionLocal
    from app.models import Account
    from app.main import _projectx_client_for_user_without_open_transaction
    from app.services.probabilistic_shadow import cache_root
    root=cache_root().resolve()
    output=args.output.resolve()
    if not output.is_relative_to(root): raise ValueError('Collection must be under backend filesystem cache')
    if shutil.disk_usage(root).free<10_000_000_000+args.max_bytes: raise OSError('Collection free-space floor')
    output.mkdir(parents=True,exist_ok=True)
    with SessionLocal() as db:
        owned={int(a.external_id) for a in db.query(Account).filter(Account.user_id==args.owner,Account.provider=='projectx',Account.trade_data_source=='projectx').all() if a.external_id.isdigit()}
        client=_projectx_client_for_user_without_open_transaction(db,user_id=args.owner)
    client.timeout_seconds=15
    discovered={r['id']:r for r in client.list_accounts(only_active_accounts=False)}
    selected=sorted(owned if args.all_owned else set(args.accounts or []))
    if not set(selected)<=owned: raise ValueError('Account not owned')
    request=dict(owner=args.owner,start=args.start,end=args.end,accounts=selected)
    config=output/'request.json'
    if config.exists() and json.loads(config.read_text())!=request: raise ValueError('Resume parameters differ')
    config.write_text(json.dumps(request,indent=2),encoding='utf-8')
    start=datetime.fromisoformat(args.start.replace('Z','+00:00'))
    end=datetime.fromisoformat(args.end.replace('Z','+00:00'))
    if start.tzinfo is None or end.tzinfo is None or start>=end: raise ValueError('Explicit UTC-offset range required')
    state=sqlite3.connect(output/'collection.sqlite3',timeout=2)
    state.executescript('CREATE TABLE IF NOT EXISTS requests(account INTEGER,start TEXT,end TEXT,state TEXT,rows INTEGER,file TEXT,sha256 TEXT,at TEXT,error TEXT,PRIMARY KEY(account,start,end));')
    attempted=0
    try:
        for account in selected:
            pending=[(start,end)]
            while pending:
                left,right=pending.pop()
                key=(account,left.isoformat(),right.isoformat())
                old=state.execute('SELECT state FROM requests WHERE account=? AND start=? AND end=?',key).fetchone()
                if old and old[0]=='response_covered': continue
                if old and old[0]=='split':
                    middle=left+(right-left)/2
                    pending.extend(((middle,right),(left,middle))); continue
                if old and not args.retry_failed: continue
                if attempted>=args.max_requests: return {'state':'bounded_stop','attempted':attempted}
                used=sum(p.stat().st_size for p in output.iterdir() if p.is_file())
                if used+2_000_000>args.max_bytes: raise OSError('Collection byte budget')
                attempted+=1
                count=0;filename=checksum=error=None
                at=datetime.now(timezone.utc).isoformat()
                status='not_in_provider_discovery'
                if account in discovered:
                    try:
                        time.sleep(args.interval)
                        rows=client.fetch_trade_history(account,left,right,require_valid_collection=True)
                        if any(r['account_id']!=account or not r.get('source_trade_id') for r in rows): raise ValueError('scope_or_id')
                        if any(r['timestamp'].tzinfo is None or not left<=r['timestamp']<=right for r in rows): raise ValueError('out_of_range')
                        identities={}
                        for row in rows:
                            identity=str(row['source_trade_id'])
                            if identity in identities and identities[identity]!=row: raise ValueError('conflicting_identity')
                            identities[identity]=row
                        count=len(rows)
                        if count>=1000:
                            status='partial_dense_timestamp' if right-left<=timedelta(microseconds=2) else 'split'
                            if status=='split':
                                middle=left+(right-left)/2
                                pending.extend(((middle,right),(left,middle)))
                        else:
                            status='response_covered'
                            filename=hashlib.sha256(json.dumps(key).encode()).hexdigest()+'.json.gz'
                            with gzip.open(output/filename,'wt',encoding='utf-8') as f: json.dump(rows,f,default=str)
                            checksum=hashlib.sha256((output/filename).read_bytes()).hexdigest()
                    except Exception as exc:
                        status='temporarily_inaccessible';error=type(exc).__name__
                state.execute('INSERT OR REPLACE INTO requests VALUES(?,?,?,?,?,?,?,?,?)',(*key,status,count,filename,checksum,at,error));state.commit()
                print(json.dumps({'account':account,'state':status,'rows':count,'attempted':attempted}),flush=True)
        return {'state':'finished_requested_scope','attempted':attempted}
    finally:
        state.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-root',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--owner',required=True);p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--accounts',type=int,nargs='+');p.add_argument('--all-owned',action='store_true')
    p.add_argument('--output',required=True,type=Path);p.add_argument('--max-requests',type=int,default=100)
    p.add_argument('--max-bytes',type=int,default=100_000_000)
    p.add_argument('--interval',type=float,default=2.1)
    p.add_argument('--retry-failed',action='store_true')
    a=p.parse_args()
    if a.interval<2 or not 1<=a.max_requests<=1000 or not 1<=a.max_bytes<=100_000_000: p.error('Bounds: interval >=2s; requests 1..1000; bytes <=100 MB')
    print(json.dumps(collect(a)))


if __name__=='__main__': main()
