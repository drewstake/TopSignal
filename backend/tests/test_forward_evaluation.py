from datetime import datetime, timedelta, timezone
from contextlib import closing
import copy
import json
import sqlite3

import numpy as np
import pytest

from app.services import forward_evaluation as e, forward_results as f
from test_forward_results import NOW, CONTRACT, candle, forecast, ingest, dry


def predicted(action='NO_TRADE'):
    result=forecast()
    result['research_action']=action
    for side in result['forecasts'].values():
        side.update(expected_net_usd=3.,uncertainty_penalty_usd=4.,lower_utility_usd=-1.)
    result['costs']={'total_usd':3.22,'fees_usd':1.22}
    result['pool_baseline']={'research_action':'NO_TRADE','forecasts':copy.deepcopy(result['forecasts'])}
    return result


def test_hold_records_conservative_timing_thresholds_and_independent_baselines(tmp_path):
    ingest(tmp_path,forecast=predicted(),action='HOLD',status='held',timing={'evaluation_duration_ms':1500})
    report=f.report('a',1,32,root=tmp_path,now=NOW)
    details=report['recent'][0]['details']
    assert details['timing']['close_to_record_seconds']==20
    assert details['timing']['simulated_entry_at'].endswith('14:05:00+00:00')
    assert details['timing']['remaining_horizon_seconds']==600
    assert details['sides']['BUY']['margin_usd']==-2
    assert details['sides']['BUY']['expected_net_usd']==3
    assert details['sides']['BUY']['blocks']==['net_edge_not_above_1_usd']
    assert report['timing_summary']['mean_evaluation_ms']==1500
    assert report['account']['position'] is None
    cohort=report['comparison']['cohorts'][0]
    assert cohort['accounts']['topbot']['position'] is None
    assert cohort['accounts']['always_long']['position']['next_bar'].endswith('14:05:00+00:00')
    assert 'entry' not in cohort['accounts']['always_long']['position']
    assert cohort['accounts']['always_short']['position']['side']=='SELL'
    assert cohort['accounts']['empirical_pool']['position'] is None
    assert all(c['interval'] is None for c in cohort['comparisons'])


def test_paper_and_baselines_resolve_once_without_retrospective_entry(tmp_path):
    pred=forecast();pred['pool_baseline']={'research_action':'BUY','forecasts':copy.deepcopy(pred['forecasts'])}
    ingest(tmp_path,forecast=pred)
    # First bar rallies before any simulated entry; delayed accounts cannot collect it.
    rows=[candle(0,high=110,close=105),candle(5),candle(10)]
    for _ in range(2):
        ingest(tmp_path,now=NOW+timedelta(minutes=15),candles=rows,forecast={},action='HOLD',status='held')
    report=f.report('a',1,32,root=tmp_path,now=NOW+timedelta(minutes=15))
    cohort=report['comparison']['cohorts'][0]
    for rule in ('topbot','always_long','always_short','empirical_pool'):
        assert cohort['accounts'][rule]['trades']==1
        assert cohort['accounts'][rule]['net_usd']==pytest.approx(-3.22)
    old=report['recent'][1]['details']
    assert old['theoretical_outcomes']['BUY']['net_usd']>0
    assert old['delayed_outcomes']['sides']['BUY']['net_usd']==pytest.approx(-3.22)
    with closing(sqlite3.connect(f.location('a',1,32,tmp_path))) as db:
        assert db.execute('select count(*) from comparison_trades').fetchone()[0]==4


def test_shared_risk_and_late_decisions_block_baselines(tmp_path):
    ingest(tmp_path,forecast=predicted(),action='HOLD',status='risk_blocked',risk_codes=['account_unavailable'])
    cohort=f.report('a',1,32,root=tmp_path,now=NOW)['comparison']['cohorts'][0]
    assert all(a['position'] is None for a in cohort['accounts'].values())
    assert cohort['dispositions']['always_long']=={'shared_execution_risk_block':1}
    record={'at':(NOW+timedelta(minutes=11)).isoformat(),'close':NOW.replace(second=0).isoformat()}
    assert e.next_open(record)>=datetime.fromisoformat(record['close'])+timedelta(minutes=15)


def test_missing_bars_block_exposure_and_uncertainty(tmp_path):
    ingest(tmp_path,forecast=predicted(),action='HOLD',status='held')
    ingest(tmp_path,now=NOW+timedelta(hours=3),candles=[],forecast={},action='HOLD',status='held')
    cohort=f.report('a',1,32,root=tmp_path,now=NOW+timedelta(hours=3))['comparison']['cohorts'][0]
    assert cohort['accounts']['always_long']['blocked_reason']
    assert all(c['interval'] is None and c['status']=='unknown_exposure' for c in cohort['comparisons'])


def test_model_versions_start_separate_comparisons_preserving_old_exposure(tmp_path):
    ingest(tmp_path,forecast=predicted(),action='HOLD',status='held',config_hash='cfg')
    newer=predicted();newer['artifact_sha256']='new';newer['candle_close_timestamp']=(NOW.replace(second=0)+timedelta(minutes=5)).isoformat()
    ingest(tmp_path,forecast=newer,candles=[candle(0)],now=NOW+timedelta(minutes=5),action='HOLD',status='held',config_hash='cfg')
    cohorts=f.report('a',1,32,root=tmp_path,now=NOW+timedelta(minutes=5))['comparison']['cohorts']
    assert len(cohorts)==2
    assert {c['artifact'] for c in cohorts}=={'test','new'}
    assert all(c['accounts']['always_long']['position'] for c in cohorts)


def test_uncertainty_uses_sessions_and_preserves_paired_dependence():
    assert e.paired_uncertainty([1.]*19) is None
    assert e.paired_uncertainty([0.]*20) is None
    differences=[(-1)**i*(i+1) for i in range(24)]
    estimate=e.paired_uncertainty(differences)
    assert estimate==e.paired_uncertainty(differences)
    assert estimate['lower_usd']<0<estimate['upper_usd']
    shifted=e.paired_uncertainty([x+100 for x in differences])
    assert shifted['lower_usd']==pytest.approx(estimate['lower_usd']+100)


def test_complete_session_coverage_counts_holds_and_excludes_missing_window(tmp_path):
    ingest(tmp_path,forecast=predicted(),action='HOLD',status='held')
    path=f.location('a',1,32,tmp_path)
    with closing(sqlite3.connect(path)) as db:
        ident=db.execute('select id from comparison_cohorts').fetchone()[0]
        cohort=json.loads(db.execute('select payload from comparison_cohorts').fetchone()[0])
        cohort['started_at']='2026-10-22T13:30:00+00:00'
        db.execute('update comparison_cohorts set payload=?',(json.dumps(cohort),))
        records=[dict(close=t,at=t,comparison_cohort=ident,stage='edge_below_threshold',outcome_status='no_forecast') for t in e.expected_closes('2026-10-22')]
        later=datetime(2026,10,22,22,tzinfo=timezone.utc)
        result=e.comparison_report(db,records,later,False)['cohorts'][0]
        assert result['complete_sessions']==1 and result['sessions'][0]['recorded']==72
        assert result['comparisons'][0]['mean_daily_advantage_usd']==0
        assert e.comparison_report(db,records[:-1],later,False)['cohorts'][0]['complete_sessions']==0
        records[0]['outcome_status']='unknown_missing_bars_or_roll'
        assert e.comparison_report(db,records,later,False)['cohorts'][0]['complete_sessions']==0


def test_entire_missing_session_is_visible_and_suppresses_uncertainty(tmp_path):
    ingest(tmp_path,forecast=predicted(),action='HOLD',status='held')
    with closing(sqlite3.connect(f.location('a',1,32,tmp_path))) as db:
        ident=db.execute('select id from comparison_cohorts').fetchone()[0]
        record=dict(close=NOW.replace(second=0).isoformat(),at=NOW.isoformat(),comparison_cohort=ident,
                    stage='edge_below_threshold',outcome_status='no_forecast')
        later=datetime(2026,10,23,22,tzinfo=timezone.utc)
        cohort=e.comparison_report(db,[record],later,False)['cohorts'][0]
        missing=next(s for s in cohort['sessions'] if s['day']=='2026-10-23')
        assert missing['missing']==72 and missing['recorded']==0 and not missing['partial_window']
        assert all(c['status']=='coverage_gaps' for c in cohort['comparisons'])
        empty=e.comparison_report(db,[],later,False,datetime(2026,10,23,tzinfo=timezone.utc))['cohorts'][0]
        assert all(c['status']=='coverage_gaps' for c in empty['comparisons'])


def test_legacy_ledger_is_readable_without_comparison_tables(tmp_path):
    ingest(tmp_path)
    with closing(sqlite3.connect(f.location('a',1,32,tmp_path))) as db:
        db.execute('drop table comparison_cohorts');db.execute('drop table comparison_trades');db.commit()
    result=f.report('a',1,32,root=tmp_path,now=NOW)
    assert result['evaluations']==1
    assert result['comparison']['cohorts']==[]


@pytest.mark.parametrize('status',['duplicate_skipped','error','submitted'])
def test_unapproved_records_do_not_start_baseline_accounts(tmp_path,status):
    ingest(tmp_path,forecast=predicted(),action='HOLD',status=status)
    assert f.report('a',1,32,root=tmp_path,now=NOW)['comparison']['cohorts']==[]


def test_directional_baseline_bypasses_only_edge_not_risk_limits():
    record=dict(stage='qualified_signal',forecast=predicted(),action='BUY',status='dry_run_attempt',
                id='test',contract=CONTRACT,at=NOW.isoformat(),close=NOW.replace(second=0).isoformat())
    assert f.admission(f.initial(),record,NOW)=='paper_signal_support'
    state=f.initial()
    assert f.admission(state,record,NOW,directional_baseline=True)=='paper_entry_queued'
    assert f.admission(state,record,NOW,directional_baseline=True)=='paper_position_open'
    state=f.initial();state['daily']={'2026-10-22':dict(entries=30,net_usd=0)}
    assert f.admission(state,record,NOW,directional_baseline=True)=='paper_daily_entry_limit'
    state['daily']['2026-10-22']=dict(entries=0,net_usd=-245)
    assert f.admission(state,record,NOW,directional_baseline=True)=='paper_daily_loss_limit'
