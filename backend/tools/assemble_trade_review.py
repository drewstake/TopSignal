"""Derive a new frozen bundle from a capture and explicit provider collection.

Preserve the original execution export and all source receipts. Only fresh
authenticated provider responses may supersede the same owner/account/leg ID;
missing responses never delete old fills. No execution database is modified.
"""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import gzip
import json
from pathlib import Path
import shutil
import sqlite3

from trade_review import verify,sha,timestamp


def assemble(bundle,collection,output):
    manifest=verify(bundle)
    request=json.loads((collection/'request.json').read_text(encoding='utf-8'))
    if request['owner']!=manifest['owner']: raise ValueError('Collection owner differs')
    if output.exists(): raise FileExistsError('Use unique output')
    output.mkdir(parents=True)
    for name in manifest['files']:
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(bundle/name,target)
    shutil.copyfile(bundle/'manifest.json',output/'original-capture-manifest.json')
    shutil.copyfile(bundle/'execution.json.gz',output/'original-execution.json.gz')
    data=json.load(gzip.open(bundle/'execution.json.gz','rt',encoding='utf-8'))
    heads={(str(r['user_id']),int(r['account_id']),str(r['source_trade_id'])):r for r in data['projectx_trade_events'] if r.get('source_trade_id') is not None}
    no_id=[r for r in data['projectx_trade_events'] if r.get('source_trade_id') is None]
    owner=request['owner'];windows=[];added=updated=0
    with closing(sqlite3.connect((collection/'collection.sqlite3').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        db.row_factory=sqlite3.Row
        for row in db.execute('SELECT * FROM requests ORDER BY at,account,start'):
            row=dict(row);windows.append(row)
            if row['state']!='response_covered':continue
            source=collection/row['file']
            if source.parent.resolve()!=collection.resolve() or sha(source)!=row['sha256']: raise ValueError('Provider receipt mismatch')
            target=output/'provider-responses'/row['file'];target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source,target)
            for event in json.load(gzip.open(source,'rt',encoding='utf-8')):
                if int(event['account_id'])!=row['account']:raise ValueError('Provider scope mismatch')
                if not event.get('source_trade_id') or not timestamp(row['start'])<=timestamp(event['timestamp'])<=timestamp(row['end']):
                    raise ValueError('Provider identity or time range mismatch')
                key=(owner,row['account'],str(event['source_trade_id']))
                previous=heads.get(key)
                if previous and previous.get('import_batch_id'):
                    continue  # Reviewed import remains independently authoritative.
                item=dict(previous or {},**{k:event.get(k) for k in ('account_id','contract_id','symbol','side','size','price','fees','pnl','order_id','source_trade_id','status','raw_payload')},
                          user_id=owner,id=previous['id'] if previous else 'provider:'+str(event['source_trade_id']),trade_timestamp=event['timestamp'],
                          commissions=(event.get('raw_payload') or {}).get('commissions'),fee_scope='per_side',
                          collection_received_at=row['at'])
                if previous: updated+=int(previous.get('raw_payload')!=item['raw_payload'])
                else:added+=1
                heads[key]=item
    data['projectx_trade_events']=list(heads.values())+no_id
    with gzip.open(output/'execution.json.gz','wt',encoding='utf-8') as f:json.dump(data,f,default=str)
    coverage=dict(request=request,windows=windows,added_provider_legs=added,changed_provider_payloads=updated,
                  source='explicit_authenticated_provider_history',production_merged=False,
                  meaning='Empty responses do not prove never traded or provider retention completeness')
    (output/'provider-coverage.json').write_text(json.dumps(coverage,indent=2),encoding='utf-8')
    now=datetime.now(timezone.utc).isoformat()
    derived=dict(manifest,assembly_at=now,original_manifest_sha256=sha(bundle/'manifest.json'),
        provider_request=request,provider_reconciliation={'added':added,'changed':updated},
        capture_completed_at=max([manifest['capture_completed_at']]+[r['at'] for r in windows]),files={})
    for p in output.rglob('*'):
        if p.is_file():derived['files'][p.relative_to(output).as_posix()]={'bytes':p.stat().st_size,'sha256':sha(p)}
    (output/'manifest.json').write_text(json.dumps(derived,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'added':added,'changed':updated,'windows':len(windows)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',required=True,type=Path);p.add_argument('--collection',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();assemble(a.bundle,a.collection,a.output)
