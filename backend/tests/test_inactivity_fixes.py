"""Synthetic offline regressions; no fitted production artifacts or broker writes."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import socket
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.models import BotRun, BotOrderAttempt
from app.services import bot_service, forward_results as paper, topbot_mathematical as strategy
from app.services import simulation_eligibility as eligibility
from app.services.probabilistic_payoff import LEGACY_PAYOFF, PAPER_PAYOFF, execution_window
from app.services.probabilistic_strategy import BAR, Candle, Costs, PathModel, price_paths, make_samples
from app.services.topbot_session import entry_boundary_reason
from test_bot_execution_safety import db_session, RecordingClient, _add_account_and_config, USER_A
from test_probabilistic_strategy import NOW as CLOSE, candles, as_rows, fitted, query
from test_forward_results import forecast as base_forecast
from paper_fixtures import eligible

AT = CLOSE+timedelta(seconds=20)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return AT if tz else AT.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key, value in dict(TOPSIGNAL_UNVALIDATED_DRY_RUN='true', TOPSIGNAL_OBSERVATION_UNCAPPED_STOPS='false',
                           TOPSIGNAL_LIVE_EXECUTION_ENABLED='false', TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION='false',
                           TOPSIGNAL_DATABENTO_CACHE_DIR=str(tmp_path)).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('network forbidden')))
    monkeypatch.setattr(bot_service, 'datetime', Clock)
    monkeypatch.setattr(paper, 'datetime', Clock)


def setup(db, monkeypatch, state='HIDDEN', action='BUY'):
    account, config = _add_account_and_config(db)
    config.strategy_type = 'topbot_adaptive'; config.strategy_params = dict(strategy.RULES)
    config.max_trades_per_day = 30; config.cooldown_seconds = 300
    config.contract_id = candles()[0].contract_id; config.allowed_contracts = [config.contract_id]
    account.account_state = state; account.can_trade = state == 'ACTIVE'
    run = BotRun(user_id=USER_A, bot_config_id=config.id, account_id=config.account_id, status='running',
                 dry_run=True, started_at=CLOSE, raw_state={'continuous': True, 'live_routing_confirmed': False})
    db.add(run); db.commit()
    rows = as_rows(candles(21))
    for row in rows:
        row.user_id = USER_A
        row.symbol = 'MNQ'
        row.fetched_at = row.candle_timestamp+BAR
    f = base_forecast()
    f.update(**execution_window(CLOSE, AT), candle_close_timestamp=CLOSE.isoformat(), quantity=1,
             costs=Costs().describe(), integrity_verified=True, validation_status='unvalidated', data_live=False,
             model_version='bayesian_cells_v1', probability_basis='uncalibrated_model_estimate',
             model_age_days=5., training_stale=False, reasons=['synthetic fixture'], research_action=action)
    if action == 'NO_TRADE':
        for side in f['forecasts'].values(): side['lower_utility_usd'] = -2.
    for side in f['forecasts'].values(): side.update(expected_net_usd=10., uncertainty_penalty_usd=8.)
    monkeypatch.setattr(strategy, 'explain_shadow', lambda **kw: copy.deepcopy(f))
    signal = strategy.evaluate(rows, as_of=AT, owner=USER_A, contract_id=config.contract_id)
    monkeypatch.setattr(bot_service, 'fetch_candles_and_evaluate_strategy', lambda *a, **kw: (rows, signal))
    monkeypatch.setattr(bot_service, 'build_bot_market_analysis', lambda **kw: {})
    monkeypatch.setattr(bot_service, 'build_signal_trade_evaluation', lambda **kw: None)
    return account, config, run, rows, f


@pytest.mark.parametrize('state', ['HIDDEN', 'MISSING', 'LOCKED_OUT', 'ACTIVE'])
@pytest.mark.parametrize('action', ['BUY', 'NO_TRADE'])
def test_readiness_on_holds_and_signals_durable_admission_and_no_broker_mutations(db_session, monkeypatch, state, action):
    account, config, run, rows, f = setup(db_session, monkeypatch, state, action)
    client = RecordingClient()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account,
                                             client=client, run=run, dry_run=True)
    assert result.status == ('dry_run_attempt' if action == 'BUY' else 'held')
    explanation = result.analysis['bot_decision']
    assert explanation['simulation_eligibility']['status'] == 'eligible'
    assert explanation['broker_readiness']['status'] == 'blocked'
    assert ('account_not_active' in explanation['broker_readiness']['codes']) == (state != 'ACTIVE')
    prepared = paper.prepare_committed_record(result); db_session.commit()
    paper.record_committed(prepared=prepared); paper.record_committed(prepared=prepared)
    report = paper.report(USER_A, config.account_id, config.id, now=AT)
    assert report['evaluations'] == 1
    assert report['recent'][0]['details']['paper_readiness']['status'] == 'eligible'
    assert bool(report['account']['position']) == (action == 'BUY')
    assert report['comparison']['cohorts'][0]['accounts']['always_long']['position']
    if action == 'BUY':
        assert report['account']['position']['next_bar'] == (CLOSE+BAR).isoformat()
        future = [SimpleNamespace(**{**vars(rows[-1]), 'candle_timestamp': CLOSE+i*BAR,
                         'open_price':20000., 'high_price':20001., 'low_price':19999., 'close_price':20000.}) for i in range(3)]
        for _ in range(2):
            paper.ingest(owner=USER_A, account=config.account_id, config=config.id, contract=config.contract_id,
                         run=run.id, candles=future, forecast={}, action='HOLD', status='held', reason='maturity',
                         risk_codes=[], now=CLOSE+3*BAR)
        report = paper.report(USER_A, config.account_id, config.id, now=CLOSE+3*BAR)
        assert report['account']['trades'] == 1 and report['account']['net_usd'] == pytest.approx(-3.22)
        with sqlite3.connect(paper.location(USER_A, config.account_id, config.id)) as db:
            assert db.execute('SELECT count(*) FROM trades').fetchone()[0] == 1
        assert db_session.query(BotOrderAttempt).count() == 1
    assert config.enabled and run.status == 'running' and account.account_state == state
    assert client.place_order_calls == client.cancel_order_calls == client.close_position_calls == []


@pytest.mark.parametrize('fault', ['owner', 'run_owner', 'mode', 'run_mode', 'authorization', 'live_gate', 'scope', 'stale', 'artifact', 'invalid_limit'])
def test_shared_assessment_fails_closed_without_mutation(db_session, monkeypatch, fault):
    account, config, run, rows, f = setup(db_session, monkeypatch)
    now = AT
    if fault == 'owner': account.user_id = 'wrong'
    elif fault == 'run_owner': run.user_id = 'wrong'
    elif fault == 'mode': config.execution_mode = 'live'
    elif fault == 'run_mode': run.dry_run = False
    elif fault == 'authorization': run.raw_state = {}
    elif fault == 'live_gate': monkeypatch.setenv('TOPSIGNAL_LIVE_EXECUTION_ENABLED', 'true')
    elif fault == 'scope': rows[5].contract_id = 'CON.F.US.MNQ.OTHER'
    elif fault == 'stale': now = CLOSE+timedelta(seconds=121)
    elif fault == 'invalid_limit': config.max_daily_loss = float('nan')
    else: f['payoff_semantics'] = LEGACY_PAYOFF
    dirty_before = set(db_session.dirty)
    result = eligibility.assess(owner=USER_A, config=config, account=account, run=run, dry_run=True, candles=rows, forecast=f, now=now)
    assert result['status'] in ('blocked', 'unknown') and result['codes']
    assert dirty_before == set(db_session.dirty) and config.enabled and run.status == 'running'


def test_missing_evidence_never_infers_eligibility_from_empty_risk_codes(tmp_path):
    from test_forward_results import ingest, NOW
    ingest(tmp_path, action='HOLD', status='held', simulation_eligibility=None)
    result = paper.report('a',1,32,root=tmp_path,now=NOW)
    assert result['recent'][0]['details']['simulation_eligibility']['status'] == 'unknown'
    assert all(a['position'] is None for a in result['comparison']['cohorts'][0]['accounts'].values())


def test_broker_visibility_change_during_fetch_does_not_disarm_simulation(db_session, monkeypatch):
    account, config, run, rows, f = setup(db_session, monkeypatch, state='ACTIVE')
    original=bot_service.fetch_candles_and_evaluate_strategy
    def fetch(*args,**kwargs):
        account.account_state='HIDDEN';account.can_trade=False;db_session.commit()
        return original(*args,**kwargs)
    monkeypatch.setattr(bot_service,'fetch_candles_and_evaluate_strategy',fetch)
    client=RecordingClient()
    result=bot_service.evaluate_bot_config(db_session,user_id=USER_A,config=config,account=account,client=client,run=run,dry_run=True)
    assert result.status=='dry_run_attempt' and config.enabled and run.status=='running'
    assert client.place_order_calls==[]


def test_scope_snapshot_still_rejects_ownership_changes(db_session, monkeypatch):
    account, config, run, rows, f=setup(db_session,monkeypatch)
    original=bot_service.fetch_candles_and_evaluate_strategy
    def fetch(*args,**kwargs):
        account.external_id='987654';db_session.commit()
        return original(*args,**kwargs)
    monkeypatch.setattr(bot_service,'fetch_candles_and_evaluate_strategy',fetch)
    client=RecordingClient()
    with pytest.raises(LookupError,match='account_not_found'):
        bot_service.evaluate_bot_config(db_session,user_id=USER_A,config=config,account=account,client=client,run=run,dry_run=True)
    assert client.place_order_calls==[]


@pytest.mark.parametrize('day', ['2026-10-29', '2026-11-03'])
@pytest.mark.parametrize('delay', [0, .000001, 10, 119, 120, 121])
def test_inclusive_decision_close_boundary_with_independent_freshness(db_session, monkeypatch, day, delay):
    account, config, run, rows, f = setup(db_session, monkeypatch)
    close = datetime.fromisoformat(day+'T15:30:00').replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
    now = close+timedelta(seconds=delay)
    for row in rows: row.candle_timestamp += close-CLOSE
    f.update(**execution_window(close, now), candle_close_timestamp=close.isoformat())
    result = eligibility.assess(owner=USER_A, config=config, account=account, run=run, dry_run=True, candles=rows, forecast=f, now=now)
    assert result['status'] == ('eligible' if delay <= 120 else 'blocked')
    assert entry_boundary_reason(now, decision_at=close) is None
    assert entry_boundary_reason(close+BAR, decision_at=close+BAR) == 'outside_research_session'
    blocks = bot_service.evaluate_risk_gates(db_session, user_id=USER_A, config=config, account=account,
                latest_candle=rows[-1], contract_id=config.contract_id, symbol='MNQ', action='BUY',
                dry_run=True, confirm_live_order_routing=False, now=now)
    assert ('stale_market_data' in [b.code for b in blocks]) == (delay > 120)
    assert 'outside_research_session' not in [b.code for b in blocks]
    signal = strategy.evaluate(rows, as_of=now, owner=USER_A, contract_id=config.contract_id)
    # Stubbed forecast isolates the adapter's boundary; real freshness is tested above and by loader tests.
    assert signal.raw_payload.get('hold_reason') != 'outside_research_session'


def test_early_close_wall_clock_and_coverage_are_independent():
    from app.services.forward_evaluation import expected_closes
    tz = ZoneInfo('America/New_York')
    close = datetime(2026,11,26,12,40,tzinfo=tz)
    assert entry_boundary_reason(close+timedelta(seconds=119),decision_at=close) is None
    assert entry_boundary_reason(close+BAR,decision_at=close+BAR) == 'entry_too_close_to_session_close'
    assert entry_boundary_reason(close+timedelta(minutes=21),decision_at=close) in ('entry_too_close_to_session_close','exchange_session_closed')
    assert expected_closes('2026-11-26')[-1] == close.astimezone(timezone.utc).isoformat()
    assert len(expected_closes('2026-11-03')) == 72


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('offset', [1, 2])
@pytest.mark.parametrize('kind', ['time', 'stop', 'target', 'ambiguous', 'stop_gap', 'target_gap'])
def test_payoff_simulator_parity_shortened_exposure_costs_gaps_and_deadline(side, offset, kind):
    absolute = [[100.,101.,99.,100.], [100.,101.,99.,100.], [100.,101.,99.,100.]]
    if kind == 'stop': absolute[-1] = [100.,101.,90.,100.] if side == 1 else [100.,110.,99.,100.]
    if kind == 'target': absolute[-1] = [100.,110.,99.,100.] if side == 1 else [100.,101.,90.,100.]
    if kind == 'ambiguous': absolute[-1] = [100.,110.,90.,100.]
    if kind == 'stop_gap': absolute[-1] = [80.,81.,79.,80.] if side == 1 else [120.,121.,119.,120.]
    if kind == 'target_gap': absolute[-1] = [120.,121.,119.,120.] if side == 1 else [80.,81.,79.,80.]
    path = np.asarray([absolute])-100
    net, exit_kind, bars = price_paths(path, stop=4., side=side, costs=Costs(), scale=1., entry_offset_bars=offset)
    entry = CLOSE+offset*BAR
    state = paper.initial(); day = CLOSE.date().isoformat(); state['daily'][day] = dict(entries=1,net_usd=0.)
    state['position'] = dict(id='fixture',contract='CON.F.US.MNQ.U26',side='BUY' if side==1 else 'SELL',quantity=1,
                            stop=4.,target=6.,next_bar=entry.isoformat(),deadline=(CLOSE+3*BAR).isoformat(),day=day)
    rows = [Candle(CLOSE+i*BAR,*bar,100.,'CON.F.US.MNQ.U26','fixture') for i,bar in enumerate(absolute)]
    paper.advance_paper(state, rows, CLOSE+3*BAR)
    trade = state['recent_trades'][0]
    assert trade['net_usd'] == pytest.approx(net[0],abs=.0001)
    assert trade['exit_kind'].replace('_gap','') == ('stop','time','target')[exit_kind[0]]
    assert datetime.fromisoformat(trade['exit_at']) == entry+int(bars[0])*BAR


def test_versioned_labels_and_models_cannot_silently_mix():
    legacy = fitted()
    assert PathModel.from_dict(legacy.to_dict()).to_dict() == legacy.to_dict()
    samples, _ = make_samples(candles(80), stride=3, payoff_semantics=PAPER_PAYOFF)
    assert samples and all(s.label_end == s.features.decision_at+3*BAR and s.path[1][0] == 0 for s in samples)
    with pytest.raises(ValueError, match='shifted'):
        make_samples(candles(80), payoff_semantics=PAPER_PAYOFF, delay_bars=1)
    with pytest.raises(ValueError): replace(legacy, payoff_semantics=PAPER_PAYOFF)
    model = PathModel('bayesian_cells_v1',tuple(samples),payoff_semantics=PAPER_PAYOFF)
    assert PathModel.from_dict(model.to_dict()).payoff_semantics == PAPER_PAYOFF
    from app.services.probabilistic_artifacts import validate_binding
    with pytest.raises(ValueError, match='no registered broker-routing'):
        validate_binding({'model':model.to_dict()},root=None,as_of=AT)


def test_commit_delay_cannot_reuse_forecast_for_a_different_open():
    f = base_forecast(); f.update(**execution_window(CLOSE, AT))
    snapshot = eligible(CLOSE)
    assert eligibility.at_recording(snapshot,f,CLOSE,CLOSE+BAR)['status'] == 'blocked'
    window = execution_window(CLOSE, CLOSE+BAR)
    assert window['exposure_bars'] == 1 and window['deadline'] == (CLOSE+3*BAR).isoformat()
    assert execution_window(CLOSE,CLOSE+2*BAR)['exposure_bars'] == 0


def test_old_observation_artifact_rejected_even_with_current_code_hash():
    from app.services import probabilistic_observation as obs
    from app.services.probabilistic_protocol import digest, protocol, implementation_sha
    model = fitted('bayesian_cells_v1')
    artifact = dict(validation_status='unvalidated',purpose='dry_run_observation_only',routing_allowed=False,
                    protocol_sha256=digest(protocol()),implementation_sha256=implementation_sha(),model=model.to_dict())
    artifact['model_sha256'] = digest(artifact['model'])
    before = copy.deepcopy(artifact)
    with pytest.raises(ValueError,match='separately fitted'):
        obs.load_model(artifact,as_of=AT)
    assert artifact == before


@pytest.mark.parametrize('offset',[1,2])
def test_model_forecast_and_evaluation_price_the_same_versioned_labels(offset):
    from app.services.probabilistic_validation import probability_scores, simulate
    old=fitted('bayesian_cells_v1')
    samples=tuple(replace(s,payoff_semantics=PAPER_PAYOFF,
                         path=tuple(tuple(v-s.path[1][0] for v in bar) for bar in s.path)) for s in old.samples)
    model=replace(old,samples=samples,payoff_semantics=PAPER_PAYOFF)
    f=query(); forecast=model.forecast(f,entry_offset_bars=offset)
    for action,sign in (('BUY',1),('SELL',-1)):
        net,_,_=price_paths(model.paths,stop=f.stop_points,scale=f.volatility*3**.5,side=sign,costs=Costs(),entry_offset_bars=offset)
        assert forecast['forecasts'][action]['expected_net_usd'] == pytest.approx(float(np.dot(model.weights(f),net)))
    label=replace(samples[0],features=f,label_end=f.decision_at+3*BAR)
    score=probability_scores(model,[label],Costs())
    assert score
    ledger=simulate(model,[label],Costs(),[f.day],rule='always_long')
    expected=price_paths(np.asarray([label.path]),stop=f.stop_points,scale=f.volatility*3**.5,side=1,costs=Costs(),entry_offset_bars=1)[0][0]
    assert ledger[0]['net_usd'] == pytest.approx(expected)
    assert ledger[0]['holding_minutes'] <= 10


def test_legacy_forecast_outcomes_are_not_reinterpreted_and_new_cohort_is_separate(tmp_path):
    from test_forward_results import NOW, ingest, candle
    from app.services.forward_evaluation import POLICY
    ingest(tmp_path,action='HOLD',status='held')
    path=paper.location('a',1,32,tmp_path)
    with sqlite3.connect(path) as db:
        old_id,payload=db.execute('SELECT id,payload FROM evaluations').fetchone()
        old=json.loads(payload)
        for key in ('payoff_semantics','entry_offset_bars','entry_at','deadline','exposure_bars'):
            old['forecast'].pop(key,None)
        old['evaluation_version']=2
        db.execute('UPDATE evaluations SET payload=?',(json.dumps(old),))
        cohort_id,cp=db.execute('SELECT id,payload FROM comparison_cohorts').fetchone()
        cohort=json.loads(cp);cohort['policy']='forward-comparison-v1';cohort['binding_sha']='legacy-binding'
        for state in cohort['accounts'].values(): state['position']=None
        db.execute('UPDATE comparison_cohorts SET payload=?',(json.dumps(cohort),))
    ingest(tmp_path,now=NOW+3*BAR,candles=[candle(0,high=110,close=105),candle(5),candle(10)],forecast={},action='HOLD')
    with sqlite3.connect(path) as db:
        updated=json.loads(db.execute('SELECT payload FROM evaluations WHERE id=?',(old_id,)).fetchone()[0])
    assert updated['forecast'] == old['forecast']
    assert updated['outcomes']['BUY']['net_usd'] > 0
    assert updated['delayed_outcomes']['sides']['BUY']['net_usd'] == pytest.approx(-3.22)
    newer=base_forecast();newer['candle_close_timestamp']=(NOW.replace(second=0)+4*BAR).isoformat()
    latest=candle(15)
    ingest(tmp_path,now=NOW+4*BAR,candles=[latest],forecast=newer,action='HOLD',status='held')
    result=paper.report('a',1,32,root=tmp_path,now=NOW+4*BAR)
    assert {c['policy'] for c in result['comparison']['cohorts']} == {'forward-comparison-v1',POLICY}
    assert result['calibration_binding']['payoff_semantics'] == PAPER_PAYOFF
    assert result['calibration']['BUY']['count'] == 0  # Excludes legacy outcome.


def test_contract_roll_and_missing_exposure_fail_without_booking_zero():
    state=paper.initial();state['daily'][CLOSE.date().isoformat()]=dict(entries=1,net_usd=0.)
    state['position']=dict(id='x',contract='CON.F.US.MNQ.U26',side='BUY',quantity=1,stop=4.,target=6.,
                          next_bar=(CLOSE+BAR).isoformat(),deadline=(CLOSE+3*BAR).isoformat(),day=CLOSE.date().isoformat())
    other=replace(candles(1)[0],timestamp=CLOSE+BAR,contract_id='CON.F.US.MNQ.Z26')
    paper.advance_paper(state,[other],CLOSE+3*BAR)
    assert state['blocked_reason'] == 'unknown_paper_contract_roll' and state['trades'] == 0
