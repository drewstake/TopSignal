from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.services import bot_model_diagnostics as diagnostics
from app.services.forward_results import location
from app.services.probabilistic_observation import observation_path
from app.services.probabilistic_protocol import protocol
from app.services.probabilistic_shadow import scope_hash

NOW = datetime(2026, 10, 30, 11, tzinfo=timezone.utc)
TRAINED = datetime(2026, 10, 23, 19, 45, tzinfo=timezone.utc)
CONTRACT = 'CON.F.US.MNQ.Z26'


def config():
    return SimpleNamespace(id=32, user_id='owner', account_id=123, execution_mode='dry_run',
        strategy_type='topbot_adaptive', strategy_params={'revision':'mnq_bayesian_payoff_v1'}, contract_id=CONTRACT)


def record(artifact=None, at='2026-10-29T14:00:20+00:00', **extra):
    close = datetime.fromisoformat(at).replace(second=0).isoformat()
    f = {'artifact_sha256':artifact, 'payoff_semantics':'paper-v2', 'data_live':False}
    if artifact:
        f['forecasts'] = {side:dict(expected_net_usd=2., uncertainty_penalty_usd=4., lower_utility_usd=-2., effective_days=100.) for side in ('BUY','SELL')}
    return dict(at=at, close=close, contract=CONTRACT, stage='edge_below_threshold' if artifact else 'outside_entry_window',
                reason='NO TRADE', forecast=f, **extra)


def installed():
    return dict(status='available', artifact='current', payoff_semantics='paper-v2', data_live=False,
                refresh=diagnostics.refresh_status(TRAINED.isoformat(), NOW))


def test_current_artifact_counts_exclude_old_unbound_and_incompatible_forecasts():
    current = record('current')
    old = record('old')
    wrong_payoff = record('current'); wrong_payoff['forecast']['payoff_semantics'] = 'legacy'
    wrong_stream = record('current'); wrong_stream['forecast']['data_live'] = True
    outside = record(at='2026-10-30T11:00:20+00:00')
    result = diagnostics.summarize([outside,current,old,wrong_payoff,wrong_stream],installed(),NOW)
    assert result['current_model']['forecasts'] == 1
    assert result['current_model']['session_eligible_evaluations'] == 1
    assert result['window_totals']['other_model_evaluations'] == 3
    assert result['window_totals']['unbound_evaluations'] == 1
    assert result['latest_evaluation']['forecast_expected'] is False
    assert 'no numerical forecast is expected' in result['latest_evaluation']['reason']
    assert result['latest_current_forecast']['sides']['BUY']['lower_utility_usd'] == -2


def test_new_installed_model_does_not_inherit_old_forecasts():
    result = diagnostics.summarize([record('old')], installed(), NOW)
    assert result['current_model']['forecasts'] == 0
    assert result['latest_current_forecast'] is None
    assert result['current_model']['first_forecast_at'] is None


@pytest.mark.parametrize('offset,status', [(5,'fresh'), (6,'due_soon'), (7,'due_soon'), (7.001,'stale')])
def test_refresh_tracks_training_cutoff_and_preserves_existing_age_boundary(offset,status):
    value = diagnostics.refresh_status(TRAINED.isoformat(), TRAINED+timedelta(days=offset))
    assert value['status'] == status
    assert value['due_at'] == '2026-10-30T19:45:00+00:00'
    assert value['automatic_refit'] is False and value['automatic_install'] is False


def test_future_or_naive_cutoff_rejected():
    with pytest.raises(ValueError): diagnostics.refresh_status(NOW.isoformat(), NOW-timedelta(seconds=1))
    with pytest.raises(ValueError): diagnostics.refresh_status('2026-10-23T19:45:00', NOW)


def test_session_uses_completed_close_and_finds_next_open():
    assert diagnostics._session(NOW)['next_eligible_close'] == '2026-10-30T13:35:00+00:00'
    assert diagnostics._session(datetime(2026,10,30,19,30,20,tzinfo=timezone.utc))['status'] == 'open'
    saturday = datetime(2026,10,31,14,tzinfo=timezone.utc)
    assert diagnostics._session(saturday)['next_eligible_close'] == '2026-11-02T14:35:00+00:00'


def test_report_reads_existing_ledger_without_mutation_and_never_creates_missing_ledger(tmp_path,monkeypatch):
    monkeypatch.setattr(diagnostics,'_installed',lambda *a: installed())
    monkeypatch.setattr(diagnostics,'_training_source',lambda *a: {'status':'unavailable'})
    result = diagnostics.report('owner',config(),root=tmp_path,now=NOW)
    assert result['current_model']['forecasts'] == 0 and not list(tmp_path.iterdir())
    path = location('owner',123,32,root=tmp_path)
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute('create table evaluations (at text,payload text)')
        row=record('current');db.execute('insert into evaluations values (?,?)',(row['at'],json.dumps(row)))
    before=sha256(path.read_bytes()).hexdigest()
    assert diagnostics.report('owner',config(),root=tmp_path,now=NOW)['current_model']['forecasts'] == 1
    assert sha256(path.read_bytes()).hexdigest() == before
    assert diagnostics.report('foreign',config(),root=tmp_path,now=NOW)['status'] == 'not_applicable'


def test_window_and_scan_limit_are_explicit(tmp_path,monkeypatch):
    monkeypatch.setattr(diagnostics,'_installed',lambda *a: installed())
    monkeypatch.setattr(diagnostics,'_training_source',lambda *a: {'status':'unavailable'})
    path=location('owner',123,32,root=tmp_path);path.parent.mkdir()
    row=record('current')
    with sqlite3.connect(path) as db:
        db.execute('create table evaluations (at text,payload text)')
        db.executemany('insert into evaluations values (?,?)',[(row['at'],json.dumps(row))]*9001)
        old=record('current',at='2026-09-01T14:00:20+00:00')
        db.execute('insert into evaluations values (?,?)',(old['at'],json.dumps(old)))
    result=diagnostics.report('owner',config(),root=tmp_path,now=NOW,days=7)
    assert result['scan_limited'] is True
    assert result['current_model']['forecasts']==9000


@pytest.mark.parametrize('foreign,oversized', [(True,False),(False,True)])
def test_bad_artifact_is_unavailable_without_leaking_contents(tmp_path,monkeypatch,foreign,oversized):
    tmp_path = tmp_path.parent / sha256(str(tmp_path).encode()).hexdigest()[:6]
    for key,value in {'TOPSIGNAL_UNVALIDATED_DRY_RUN':'true','TOPSIGNAL_LIVE_EXECUTION_ENABLED':'false','TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION':'false'}.items():monkeypatch.setenv(key,value)
    path=observation_path('owner',CONTRACT,False,root=tmp_path)
    path.parent.mkdir(parents=True)
    value=dict(owner_hash=scope_hash('foreign' if foreign else 'owner'),root_symbol='MNQ',roll_policy=protocol()['roll_policy'],data_live=False,private_field='secret')
    path.write_text('x'*4_000_001 if oversized else json.dumps(value))
    result=diagnostics.report('owner',config(),root=tmp_path,now=NOW)
    assert result['installed_model']=={'status':'unavailable','reason':'model_integrity_or_scope_failed'}
    assert 'secret' not in json.dumps(result)


def test_same_date_cache_is_not_claimed_as_new_complete_training_data(tmp_path,monkeypatch):
    from app.services.databento_cache import DatabentoReplayStore
    monkeypatch.setattr(DatabentoReplayStore,'history_bounds',lambda *a:(TRAINED-timedelta(days=100),TRAINED+timedelta(hours=1)))
    result=diagnostics._training_source(tmp_path,installed())
    assert result['status']=='no_newer_dates' and result['complete_sessions_verified'] is False


def test_ambiguous_stream_does_not_choose_model_arbitrarily(tmp_path,monkeypatch):
    tmp_path = tmp_path.parent / sha256(str(tmp_path).encode()).hexdigest()[:6]
    for key,value in {'TOPSIGNAL_UNVALIDATED_DRY_RUN':'true','TOPSIGNAL_LIVE_EXECUTION_ENABLED':'false','TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION':'false'}.items():monkeypatch.setenv(key,value)
    for live in (True,False):
        path=observation_path('owner',CONTRACT,live,root=tmp_path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
    assert diagnostics.report('owner',config(),root=tmp_path,now=NOW)['installed_model']['reason']=='model_stream_ambiguous'
