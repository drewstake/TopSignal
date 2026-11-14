"""Exercise maintained tools on frozen files, including the real replay model."""
from datetime import datetime,timedelta,timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from trade_review import sha,verify,history_coverage,cashflows
from assemble_trade_review import assemble


def seal(root,owner='owner'):
    manifest={'owner':owner,'capture_started_at':'2026-11-13T00:00:00+00:00',
              'capture_completed_at':'2026-11-13T00:01:00+00:00',
              'files':{p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in root.iterdir() if p.is_file()}}
    (root/'manifest.json').write_text(json.dumps(manifest))


def test_assemble_preserves_original_and_reconciles_only_scoped_provider_ids(tmp_path):
    bundle=tmp_path/'bundle';bundle.mkdir()
    old={'id':1,'user_id':'owner','account_id':10,'source_trade_id':'leg','raw_payload':{'pnl':2}}
    data={'projectx_trade_events':[old,dict(old,id=2,account_id=20)]}
    with gzip.open(bundle/'execution.json.gz','wt') as f:json.dump(data,f)
    seal(bundle)
    source=tmp_path/'provider';source.mkdir()
    (source/'request.json').write_text(json.dumps({'owner':'owner','start':'2026-11-12T00:00:00+00:00','end':'2026-11-13T00:00:00+00:00','accounts':[10]}))
    with gzip.open(source/'response.json.gz','wt') as f:
        json.dump([dict(old,timestamp='2026-11-12T15:00:00+00:00',raw_payload={'pnl':3})],f)
    with sqlite3.connect(source/'collection.sqlite3') as db:
        db.execute('CREATE TABLE requests(account,start,end,state,rows,file,sha256,at,error)')
        db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)',(10,'2026-11-12T00:00:00+00:00','2026-11-13T00:00:00+00:00','response_covered',1,'response.json.gz',sha(source/'response.json.gz'),'2026-11-13T00:01:00+00:00',None))
    output=tmp_path/'assembled'
    assemble(bundle,source,output)
    verify(output)
    assert sha(bundle/'execution.json.gz')==sha(output/'original-execution.json.gz')
    merged=json.load(gzip.open(output/'execution.json.gz','rt'))['projectx_trade_events']
    assert [r['raw_payload']['pnl'] for r in merged]==[3,2]
    _,states,_=history_coverage(output,'owner',{10,20},datetime(2026,11,12,tzinfo=timezone.utc),datetime(2026,11,13,tzinfo=timezone.utc))
    assert states=={10:'response_covered',20:'not_examined'}
    (output/'unmanifested.txt').write_text('unverified')
    with pytest.raises(ValueError,match='Unmanifested'):verify(output)


def test_cashflows_use_matching_dates_and_do_not_allocate_owner_payouts_to_accounts():
    data={'expenses':[dict(user_id='owner',account_id=10,expense_date='2026-11-12',amount_cents=100,currency='USD')],
          'payouts':[dict(user_id='owner',payout_date='2026-11-12',amount_cents=1000,currency='USD')]}
    result=cashflows(data,'owner',{10},'2026-11-12','2026-11-12',True)
    assert result['expenses']['amounts_by_currency']=={'USD':1}
    assert result['payouts']['unallocated_rows_excluded']==1


def test_real_offline_replay_uses_matching_model_and_rejects_holdout(tmp_path):
    from app.services.topbot_evidence import Capture
    from app.services.topbot_study import EXPERIMENT_ID,MODEL_CODE
    contract='CON.F.US.MNQ.Z26';at=datetime(2026,11,12,15,tzinfo=timezone.utc)
    capture=Capture('owner',contract,root=tmp_path/'copies',free_floor=0)
    for i,(seconds,price) in enumerate([(0,20000),(.4,19974),(.8,19970)]):
        stamp=at+timedelta(seconds=seconds)
        capture.append({'event':'snapshot','data':{'contract_id':contract,'sequence':i+1,'timestamp':stamp.isoformat(),
            'bids':[{'price':price,'size':10}],'asks':[{'price':price+.25,'size':10}]}},stamp,[])
    capture.close()
    assert not capture.thread.is_alive()
    copies=tmp_path/'copies'/'manifest.json'
    copies.write_text(json.dumps({'owner':'owner','files':{capture.capture_id:{'file':capture.path.relative_to(copies.parent).as_posix(),'sha256':sha(capture.path)}}}))
    saved={'experiment_id':EXPERIMENT_ID,'study_model_code':MODEL_CODE,'quantity':1,'contract_id':contract,
           'initial_risk_points':25,'initial_risk_usd':50,'research_cutoff':(at+timedelta(seconds=2)).isoformat(),
           'session':'2026-11-12','strategy_version_id':'fixture','rules':{'exit':{'exit_mode':'fixed'}}}
    attempt={'id':1,'side':'BUY','contract_id':contract,'status':'submitted','raw_request':{'strategySnapshot':saved,
        'timeExit':{'runner':{'entry_at':at.isoformat(),'entry_price':20000}},
        'entryEvidence':{'pre_submit':{'evidence':{'capture_id':capture.capture_id}}}}}
    bundle=tmp_path/'bundle';bundle.mkdir()
    with gzip.open(bundle/'execution.json.gz','wt') as f:json.dump({'bot_order_attempts':[attempt]},f)
    with sqlite3.connect(bundle/'exit-results.sqlite3') as db:
        db.execute('CREATE TABLE enrollments(entry,phase)')
        db.execute('INSERT INTO enrollments VALUES(1,"exploratory")')
        db.execute('INSERT INTO enrollments VALUES(2,"holdout")')
    seal(bundle)
    command=[sys.executable,str(TOOLS/'replay_trade_review.py'),'--bundle',str(bundle),'--captures-manifest',str(copies),
             '--release-root',str(TOOLS.parents[1]),'--entries','1','--output',str(tmp_path/'replay')]
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads((tmp_path/'replay'/'1.json').read_text())['paired_valid'] is True
    command[-3]='2';command[-1]=str(tmp_path/'holdout')
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode!=0 and 'already-enrolled exploratory' in result.stderr
    assert not (tmp_path/'holdout').exists()
