"""Offline exploratory replay on verified copied captures; append new artifacts.

No production writes, selections, enrollments or holdout outcomes. Use the exact
retained release whose model hashes match each saved strategy snapshot.
"""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket
import sys

from trade_review import verify,sha,readonly


def replay(args):
    manifest=verify(args.bundle)
    copies=json.loads(args.captures_manifest.read_text(encoding='utf-8'))
    if copies.get('owner')!=manifest['owner']:raise ValueError('Capture owner mismatch')
    sys.path.insert(0,str(args.release_root/'backend'))
    from app.services.topbot_study import EXPERIMENT_ID,MODEL_CODE,simulate
    from app.services.topbot_study_store import quotes
    from app.services.topbot_runner import statistics_item
    from types import SimpleNamespace
    data=json.load(gzip.open(args.bundle/'execution.json.gz','rt',encoding='utf-8'))
    attempts={int(r['id']):r for r in data['bot_order_attempts']}
    with closing(readonly(args.bundle,'exit-results.sqlite3')) as db:
        allowed={int(r[0]) for r in db.execute("SELECT entry FROM enrollments WHERE phase='exploratory'")}
    if not set(args.entries)<=allowed:raise ValueError('Only already-enrolled exploratory entries may be replayed')
    if args.output.exists():raise FileExistsError('Use unique output; original revisions are immutable')
    args.output.mkdir(parents=True)
    def denied(*_a,**_kw):raise RuntimeError('Offline replay networking is disabled')
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
    receipt={'experiment':EXPERIMENT_ID,'model_code':MODEL_CODE,'bundle_sha256':sha(args.bundle/'manifest.json'),'entries':[]}
    for identity in args.entries:
        row=attempts[identity]
        request=row['raw_request'];saved=request['strategySnapshot']
        if saved['experiment_id']!=EXPERIMENT_ID or saved['study_model_code']!=MODEL_CODE:
            raise ValueError('Use the matching retained experiment release')
        evidence=request.get('entryEvidence') or {}
        ref=(evidence.get('pre_submit') or evidence.get('signal') or {}).get('evidence') or {}
        capture=ref.get('capture_id')
        if capture not in copies['files']:raise ValueError('Required capture has not been copied and verified')
        record=copies['files'][capture]
        path=(args.captures_manifest.parent/record['file']).resolve()
        if not path.is_relative_to(args.captures_manifest.parent.resolve()) or sha(path)!=record['sha256']:
            raise ValueError('Capture checksum/path mismatch')
        entry=statistics_item(SimpleNamespace(**row))
        result=simulate(entry,quotes(path,owner=manifest['owner'],contract=row['contract_id'],
            start=datetime.fromisoformat(entry['entry_at']),cutoff=datetime.fromisoformat(saved['research_cutoff'])))
        result.update(phase='exploratory',source_bundle_sha256=receipt['bundle_sha256'],source_capture_sha256=record['sha256'])
        output=args.output/f'{identity}.json'
        output.write_text(json.dumps(result,indent=2),encoding='utf-8')
        receipt['entries'].append({'entry':identity,'sha256':sha(output),'paired_valid':result['paired_valid']})
    receipt['completed_at']=datetime.now(timezone.utc).isoformat()
    (args.output/'replay-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',required=True,type=Path);p.add_argument('--captures-manifest',required=True,type=Path)
    p.add_argument('--release-root',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--entries',required=True,type=int,nargs='+');p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    if len(a.entries)>100:p.error('At most 100 explicit entries per invocation')
    print(json.dumps(replay(a)))
