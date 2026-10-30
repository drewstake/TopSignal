from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import copy
import json
import pytest
from app.services import forward_results as f
from app.services.probabilistic_payoff import PAPER_PAYOFF, execution_window
from paper_fixtures import eligible

NOW = datetime(2026,10,22,14,0,20,tzinfo=timezone.utc)
CONTRACT='CON.F.US.MNQ.Z26'

@pytest.fixture(autouse=True)
def dry(monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_UNVALIDATED_DRY_RUN','true')
    monkeypatch.setenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED','false')
    monkeypatch.setenv('TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION','false')

def candle(minute, low=99., high=101., close=100.):
    return SimpleNamespace(unit='minute',unit_number=5,candle_timestamp=NOW.replace(minute=minute,second=0),
                           open_price=100., high_price=high,low_price=low,close_price=close,volume=100,
                           user_id='a',contract_id=CONTRACT,live=False,is_partial=False)

def forecast():
    return dict(observation_only=True,routing_allowed=False,training_paths=1000,data_status='fresh',
                payoff_semantics=PAPER_PAYOFF, **execution_window(NOW.replace(second=0), NOW),
                research_action='BUY',stop_points=4.,target_points=6.,artifact_sha256='test',
                candle_close_timestamp=NOW.replace(second=0).isoformat(),
                forecasts={s:dict(effective_days=40,lower_utility_usd=2.,probability_net_positive=.6) for s in ('BUY','SELL')})

def ingest(root, now=NOW, **kwargs):
    values=dict(owner='a',account=1,config=32,contract=CONTRACT,run=9,candles=[candle(55)],forecast=forecast(),
                action='BUY',status='dry_run_attempt',reason='supported',risk_codes=[],now=now,root=root)
    # 13:55, preceding the 14:00 forecast.
    values['candles'][0].candle_timestamp-=timedelta(hours=1)
    values.update(kwargs)
    if values['forecast'].get('payoff_semantics') == PAPER_PAYOFF:
        values['forecast'].update(execution_window(datetime.fromisoformat(values['forecast']['candle_close_timestamp']), now))
    values.setdefault('simulation_eligibility', eligible(datetime.fromisoformat(values['forecast'].get('candle_close_timestamp', now.replace(second=0).isoformat())),
                      owner=values['owner'], account=values['account'], config=values['config'], contract=values['contract']))
    f.ingest(**values)

def test_retries_restart_and_future_fill(tmp_path):
    ingest(tmp_path);ingest(tmp_path)
    r=f.report('a',1,32,root=tmp_path,now=NOW)
    assert r['evaluations']==1 and r['account']['position']['next_bar'].endswith('14:05:00+00:00')
    assert 'entry' not in r['account']['position']
    ingest(tmp_path,now=NOW+timedelta(minutes=15),candles=[candle(0),candle(5),candle(10)],forecast={},action='HOLD')
    r=f.report('a',1,32,root=tmp_path,now=NOW+timedelta(minutes=15))
    assert r['completed']==1 and r['account']['trades']==1
    assert r['account']['net_usd']==pytest.approx(-3.22)
    assert r['calibration']['BUY']['count']==1 and r['calibration']['BUY']['brier']==pytest.approx(.36)
    ingest(tmp_path,now=NOW+timedelta(minutes=15),candles=[candle(0),candle(5),candle(10)],forecast={},action='HOLD')
    assert f.report('a',1,32,root=tmp_path,now=NOW+timedelta(minutes=15))['account']['trades']==1

def test_scope_isolation(tmp_path):
    ingest(tmp_path)
    for owner,account,config in [('b',1,32),('a',2,32),('a',1,33)]:
        assert f.report(owner,account,config,root=tmp_path)['account'] is None
    with pytest.raises(ValueError,match='scope'):
        ingest(tmp_path,owner='b')

def test_risk_block_not_paper_trade(tmp_path):
    ingest(tmp_path,risk_codes=['daily_loss_limit'],status='risk_blocked')
    r=f.report('a',1,32,root=tmp_path,now=NOW)
    assert r['rejection_counts']=={'execution_risk_block':1}
    assert r['account']['position'] is None

def test_gap_blocks_simulation_unknown_not_zero(tmp_path):
    ingest(tmp_path)
    ingest(tmp_path,now=NOW+timedelta(hours=3),candles=[],forecast={},action='HOLD')
    r=f.report('a',1,32,root=tmp_path,now=NOW+timedelta(hours=3))
    assert r['unknown']==1 and r['completed']==0
    assert r['account']['blocked_reason']=='unknown_paper_path_missing_bars'
    assert r['account']['trades']==0

def test_stop_before_target_and_closed_drawdown(tmp_path):
    ingest(tmp_path)
    ingest(tmp_path,now=NOW+timedelta(minutes=10),candles=[candle(0),candle(5,low=90,high=110)],forecast={},action='HOLD')
    a=f.report('a',1,32,root=tmp_path,now=NOW+timedelta(minutes=10))['account']
    assert a['recent_trades'][0]['exit_kind']=='stop'
    assert a['net_usd']==pytest.approx(-10.22)
    assert a['closed_drawdown_usd']==pytest.approx(10.22)

def test_limits_and_no_overlapping_positions():
    rec=dict(stage='qualified_signal',forecast=forecast(),action='BUY',status='dry_run_attempt',id='test',contract=CONTRACT,at=NOW.isoformat(),close=NOW.replace(second=0).isoformat())
    rec['simulation_eligibility'] = eligible(NOW.replace(second=0))
    state=f.initial()
    assert f.admission(state,rec,NOW)=='paper_entry_queued'
    assert f.admission(state,rec,NOW)=='paper_position_open'
    state=f.initial();state['last_entry']=NOW.isoformat()
    assert f.admission(state,rec,NOW)=='paper_cooldown'
    state=f.initial();state['daily']={'2026-10-22':dict(entries=30,net_usd=0)}
    assert f.admission(state,rec,NOW)=='paper_daily_entry_limit'
    state['daily']['2026-10-22']=dict(entries=1,net_usd=-245)
    assert f.admission(state,rec,NOW)=='paper_daily_loss_limit'

def test_off_and_conflicting_live_flags(tmp_path,monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_UNVALIDATED_DRY_RUN','false');ingest(tmp_path)
    assert not f.location('a',1,32,tmp_path).exists()
    monkeypatch.setenv('TOPSIGNAL_UNVALIDATED_DRY_RUN','true')
    monkeypatch.setenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED','true')
    with pytest.raises(ValueError):ingest(tmp_path)


@pytest.mark.parametrize('status',['duplicate_skipped','error','submitted','risk_blocked'])
def test_unapproved_disposition_never_opens_paper_position(tmp_path,status):
    ingest(tmp_path,status=status)
    result=f.report('a',1,32,root=tmp_path,now=NOW)
    assert result['account']['position'] is None
    assert result['paper_blocks']=={'paper_requires_approved_dry_run_attempt':1}

def test_hold_breakdown_and_transaction_rollback(tmp_path):
    pred=forecast();pred['research_action']='NO_TRADE'
    ingest(tmp_path,forecast=pred,action='HOLD')
    assert f.report('a',1,32,root=tmp_path,now=NOW)['rejection_counts']=={'edge_below_threshold':1}
    pred=forecast();pred['forecasts']['BUY']['probability_net_positive']=float('nan')
    with pytest.raises(ValueError):
        ingest(tmp_path,now=NOW+timedelta(minutes=5),forecast=pred,candles=[candle(0)])
    assert f.report('a',1,32,root=tmp_path,now=NOW)['evaluations']==1
