import importlib.util
from pathlib import Path
from datetime import datetime,timezone
import json
import sqlite3
import pytest

spec=importlib.util.spec_from_file_location('offline_trade_review',Path(__file__).resolve().parents[1]/'tools/trade_review.py')
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)
C='CON.F.US.MNQ.Z26'


def fill(i,side,pnl,*,qty=1,at=None,owner='a',account=10,commission=.25):
    return dict(id=i,user_id=owner,account_id=account,source_trade_id=str(i),order_id=str(i),contract_id=C,
                side=side,pnl=pnl,size=qty,price=100,fees=.36,commissions=commission,
                raw_payload={'voided':False},trade_timestamp=at or f'2026-11-12T15:00:0{i}+00:00')


def data(fills):
    return {'accounts':[{'user_id':'a','external_id':'10','provider_simulated':True}],
            'projectx_trade_events':fills,'bot_order_attempts':[]}


def ledger(fills):
    return review.ledger(data(fills),'a',datetime(2026,11,13,tzinfo=timezone.utc))


def test_partial_exit_and_reversal_legs_keep_costs_and_authoritative_profit():
    rows=[fill(1,'BUY',None,qty=2),fill(2,'SELL',10),fill(3,'SELL',20),
          fill(4,'SELL',None,at='2026-11-12T15:00:03+00:00'),fill(5,'BUY',-8)]
    positions,_,residual,_,_=ledger(rows)
    assert not residual
    assert [p['gross'] for p in positions]==[30,-8]
    assert [p['known_costs'] for p in positions]==[1.83,1.22]
    assert [p['net'] for p in positions]==[28.17,-9.22]
    assert positions[0]['qty_max']==2


def test_owners_not_merged_and_orphan_closing_leg_not_invented():
    positions,_,_,diag,_=ledger([fill(1,'SELL',10),fill(2,'BUY',None,owner='b')])
    assert positions==[] and diag['orphan_closing_legs']==1 and diag['other_owner_excluded']==1


def test_missing_fees_withhold_net_and_voided_leg_excluded():
    rows=[fill(1,'BUY',None,commission=None),fill(2,'SELL',10)]
    assert ledger(rows)[0][0]['net'] is None
    rows[1]['raw_payload']['voided']=True
    positions,_,residual,diag,_=ledger(rows)
    assert positions==[] and residual[0]['quantity']==1 and diag['voided_legs']==1


def test_unknown_attribution_is_not_manual():
    assert ledger([fill(1,'BUY',None),fill(2,'SELL',10)])[0][0]['attribution']=='not_bot'


def test_source_alias_warning_does_not_merge_accounts():
    accounts={10:dict(name='Account',trade_data_source='csv_import'),20:dict(name='Account',trade_data_source='projectx')}
    assert review.source_overlaps(accounts,{10,20})[0]['accounts']==[10,20]
    assert review.source_overlaps(accounts,{20})==[]


def test_conflicting_same_scoped_leg_rejected():
    with pytest.raises(ValueError,match='Conflicting'):
        ledger([fill(1,'BUY',None),dict(fill(1,'BUY',None),price=200)])


def test_paths_exclude_post_exit_observations_and_do_not_choose_nearest(tmp_path):
    positions=ledger([fill(1,'BUY',None),fill(2,'SELL',10)])[0]
    start=review.timestamp(positions[0]['open_utc']).timestamp()
    end=review.timestamp(positions[0]['close_utc']).timestamp()
    with sqlite3.connect(tmp_path/'progress.sqlite3') as db:
        db.executescript('CREATE TABLE trade_progress_scopes(owner,account,enabled);CREATE TABLE trade_progress_positions(id,owner,account,contract,opened,ended,state);CREATE TABLE trade_progress_samples(id,position,ts,payload);')
        db.execute('INSERT INTO trade_progress_scopes VALUES(?,?,?)',('a',10,1))
        db.execute('INSERT INTO trade_progress_positions VALUES(?,?,?,?,?,?,?)',('p','a',10,C,start,end+10,json.dumps({'id':'p','reasons':[]})))
        for i,(at,points) in enumerate([(start,1),(end,2),(end+5,999)]):
            db.execute('INSERT INTO trade_progress_samples VALUES(?,?,?,?)',(i,'p',at,json.dumps({'ts':at,'mark_timestamp':at,'points':points})))
    review.paths(tmp_path,'a',positions)
    assert positions[0]['path']['observed_best_points']==2
    positions[0]['open_utc']='2026-11-13T15:00:01+00:00';positions[0]['close_utc']='2026-11-13T15:00:02+00:00'
    review.paths(tmp_path,'a',positions)
    assert positions[0]['path']['state']=='no_matching_recording'


def test_holdout_payloads_are_never_deserialized(tmp_path):
    with sqlite3.connect(tmp_path/'exit-results.sqlite3') as db:
        db.executescript('CREATE TABLE results(seq,account,entry,experiment,payload);CREATE TABLE enrollments(account,entry,experiment,phase);')
        db.execute('INSERT INTO results VALUES(1,10,1,"exp","intentionally invalid secret holdout")')
        db.execute('INSERT INTO enrollments VALUES(10,1,"exp","holdout")')
    assert review.study(tmp_path,[],datetime(2026,11,13,tzinfo=timezone.utc))['entries']==[]
