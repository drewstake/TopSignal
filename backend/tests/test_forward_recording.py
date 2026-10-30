"""Recording-boundary and trusted delivery-roll regressions; all state is local."""
from datetime import datetime, timedelta
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
import copy
import json
import sqlite3

import pytest

from app.services import bot_service, forward_evaluation, forward_results as paper
from test_bot_execution_safety import db_session, RecordingClient, USER_A
from test_inactivity_fixes import setup, AT, CLOSE, Clock as ServiceClock
from test_forward_results import NOW, CONTRACT, candle, forecast, ingest
from paper_fixtures import eligible
from app.services.probabilistic_payoff import LEGACY_PAYOFF, PAPER_PAYOFF, execution_window


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key, value in dict(TOPSIGNAL_DATABENTO_CACHE_DIR=str(tmp_path), TOPSIGNAL_UNVALIDATED_DRY_RUN='true',
                           TOPSIGNAL_OBSERVATION_UNCAPPED_STOPS='false', TOPSIGNAL_LIVE_EXECUTION_ENABLED='false',
                           TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION='false').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(bot_service, 'datetime', ServiceClock)
    monkeypatch.setattr(paper, 'datetime', ServiceClock)


def inputs(root):
    row = candle(55)
    row.candle_timestamp -= timedelta(hours=1)
    return dict(owner='a', account=1, config=32, contract=CONTRACT, run=9, candles=[row],
                forecast=forecast(), action='BUY', status='dry_run_attempt', reason='synthetic signal',
                risk_codes=[], root=root, simulation_eligibility=eligible(NOW.replace(second=0)))


def freeze(monkeypatch, tick):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return tick[0]
    monkeypatch.setattr(paper, 'datetime', Clock)


def assert_no_entries(result):
    assert result['account']['position'] is None
    assert result['account']['trades'] == 0
    assert sum(d['entries'] for d in result['account']['daily'].values()) == 0
    for cohort in result['comparison']['cohorts']:
        assert all(a['position'] is None and a['trades'] == 0 for a in cohort['accounts'].values())


def test_freshness_rechecked_after_real_sqlite_writer_wait(tmp_path, monkeypatch):
    close, started = NOW.replace(second=0), monotonic()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return close+timedelta(seconds=119+monotonic()-started)
    monkeypatch.setattr(paper, 'datetime', Clock)
    path = paper.location('a', 1, 32, tmp_path)
    paper.connect(path).close()
    blocker = sqlite3.connect(path, check_same_thread=False)
    blocker.execute('BEGIN IMMEDIATE')
    release = Event()
    thread = Thread(target=lambda: (release.wait(2.2), blocker.commit()))
    thread.start()
    try:
        paper.record_committed(prepared=inputs(tmp_path))
    finally:
        release.set(); thread.join(3); blocker.close()
    result = paper.report('a', 1, 32, root=tmp_path, now=Clock.now())
    assert (Clock.now()-close).total_seconds() > 121
    assert_no_entries(result)
    assert result['recent'][0]['paper_disposition'] == 'stale_market_data'
    assert datetime.fromisoformat(result['recent'][0]['at']) > close+timedelta(seconds=120)


def test_pending_work_crossing_entry_open_cannot_backdate_admission(tmp_path, monkeypatch):
    tick = [NOW]; freeze(monkeypatch, tick)
    original = forward_evaluation.advance_comparisons
    def slow(*args):
        original(*args)
        tick[0] = NOW.replace(second=0)+timedelta(minutes=5, seconds=1)
    monkeypatch.setattr(forward_evaluation, 'advance_comparisons', slow)
    paper.record_committed(prepared=inputs(tmp_path))
    result = paper.report('a', 1, 32, root=tmp_path, now=tick[0])
    assert_no_entries(result)
    detail = result['recent'][0]['details']
    assert 'simulation_timing_mismatch' in detail['simulation_eligibility']['codes']
    assert detail['timing']['theoretical_entry_at'].endswith('14:05:00+00:00')
    assert detail['timing']['exit_deadline'].endswith('14:15:00+00:00')


class AfterCommit:
    def __init__(self, db, callback): self.db, self.callback = db, callback
    def __getattr__(self, key): return getattr(self.db, key)
    def __enter__(self): self.db.__enter__(); return self
    def __exit__(self, *args):
        result = self.db.__exit__(*args)
        if args[0] is None: self.callback()
        return result


def after_first_commit(monkeypatch, callback):
    connect = paper.connect
    calls = [0]
    def wrapped(path):
        calls[0] += 1
        db = connect(path)
        return AfterCommit(db, callback) if calls[0] == 1 else db
    monkeypatch.setattr(paper, 'connect', wrapped)


@pytest.mark.parametrize('delay', [121, 301, 901])
def test_commit_completion_rejects_late_reservations_without_booked_pnl(tmp_path, monkeypatch, delay):
    tick = [NOW]; freeze(monkeypatch, tick)
    after_first_commit(monkeypatch, lambda: tick.__setitem__(0, NOW.replace(second=0)+timedelta(seconds=delay)))
    paper.record_committed(prepared=inputs(tmp_path))
    result = paper.report('a', 1, 32, root=tmp_path, now=tick[0])
    assert_no_entries(result)
    record = result['recent'][0]
    assert record['details']['recording']['completed_at'] == tick[0].isoformat() == record['at']
    assert record['details']['simulation_eligibility']['status'] == 'blocked'
    assert result['account']['net_usd'] == 0 and result['account']['recent_trades'] == []
    paper.record_committed(prepared=inputs(tmp_path))  # Retry cannot resurrect it.
    assert_no_entries(paper.report('a', 1, 32, root=tmp_path, now=tick[0]))


def test_crash_before_completion_receipt_never_replays_a_provisional_fill(tmp_path, monkeypatch):
    tick = [NOW]; freeze(monkeypatch, tick)
    def crash(): raise SystemExit('after initial commit')
    after_first_commit(monkeypatch, crash)
    with pytest.raises(SystemExit): paper.record_committed(prepared=inputs(tmp_path))
    path = paper.location('a', 1, 32, tmp_path)
    with sqlite3.connect(path) as db:
        record = json.loads(db.execute('SELECT payload FROM evaluations').fetchone()[0])
        assert record['recording']['status'] == 'pending'
    tick[0] += timedelta(minutes=15)
    ingest(tmp_path, now=tick[0], candles=[candle(0), candle(5), candle(10)], forecast={}, action='HOLD')
    result = paper.report('a', 1, 32, root=tmp_path, now=tick[0])
    assert result['account']['trades'] == 0
    assert result['account']['blocked_reason'] == 'unknown_paper_recording_unconfirmed'
    assert result['account']['position']['recording_status'] == 'unknown'
    assert 'entry' not in result['account']['position']
    old = next(r for r in result['recent'] if r['action'] == 'BUY')
    assert old['details']['recording']['status'] == 'unknown'
    for cohort in result['comparison']['cohorts']:
        assert all(a['trades'] == 0 for a in cohort['accounts'].values())


def test_confirmed_record_retries_keep_one_reserved_entry(tmp_path, monkeypatch):
    tick = [NOW]; freeze(monkeypatch, tick)
    for _ in range(2): paper.record_committed(prepared=inputs(tmp_path))
    result = paper.report('a', 1, 32, root=tmp_path, now=tick[0])
    assert result['evaluations'] == 1
    assert result['account']['daily']['2026-10-22']['entries'] == 1
    assert result['account']['position']['recording_status'] == 'confirmed'
    assert result['recent'][0]['details']['recording']['status'] == 'confirmed'


@pytest.mark.parametrize('old_semantics', [LEGACY_PAYOFF, PAPER_PAYOFF])
@pytest.mark.parametrize('action', ['HOLD', 'BUY', 'SELL'])
def test_prior_outcome_resolution_preserves_new_recording_receipt(tmp_path, monkeypatch, old_semantics, action):
    tick = [NOW]; freeze(monkeypatch, tick)
    prior = inputs(tmp_path)
    prior.update(action='HOLD', status='held', reason='prior forecast')
    prior['forecast'].update(payoff_semantics=old_semantics, research_action='HOLD')
    paper.ingest(**prior, now=NOW)

    tick[0] += timedelta(minutes=15)
    close = tick[0].replace(second=0)
    current = inputs(tmp_path)
    current.update(candles=[candle(0), candle(5), candle(10)], action=action,
                   status='held' if action == 'HOLD' else 'dry_run_attempt',
                   simulation_eligibility=eligible(close))
    current['forecast'].update(candle_close_timestamp=close.isoformat(), research_action=action,
                               **execution_window(close, tick[0]))
    paper.record_committed(prepared=current)

    with sqlite3.connect(paper.location('a', 1, 32, tmp_path)) as db:
        records = [json.loads(row[0]) for row in db.execute('SELECT payload FROM evaluations ORDER BY at')]
    assert len(records) == 2
    assert records[0]['outcome_status'] == 'observed'
    assert set(records[0]['outcomes']) == {'BUY', 'SELL'}
    assert records[1]['recording']['status'] == 'confirmed'
    assert records[1]['recording']['completed_at'] == tick[0].isoformat()
    assert records[1]['simulation_eligibility']['status'] == 'eligible'
    first = paper.report('a', 1, 32, root=tmp_path, now=tick[0])
    if action != 'HOLD':
        assert first['account']['position']['recording_status'] == 'confirmed'
        assert first['account']['daily']['2026-10-22']['entries'] == 1
    paper.record_committed(prepared=current)
    repeated = paper.report('a', 1, 32, root=tmp_path, now=tick[0])
    assert repeated['evaluations'] == 2
    assert repeated['account'] == first['account']


@pytest.mark.parametrize('trailing_partial', [False, True])
def test_service_roll_marks_existing_paper_and_comparisons_unknown(db_session, monkeypatch, trailing_partial):
    account, config, run, rows, _ = setup(db_session, monkeypatch)
    client = RecordingClient()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account,
                                             client=client, run=run, dry_run=True)
    prepared = paper.prepare_committed_record(result); db_session.commit()
    paper.record_committed(prepared=prepared)
    before = paper.report(USER_A, config.account_id, config.id, now=AT)
    assert before['account']['position']
    replacement = 'CON.F.US.MNQ.H27'
    new_rows = [SimpleNamespace(**{**vars(row), 'contract_id': replacement,
                    'candle_timestamp': row.candle_timestamp+timedelta(minutes=5),
                    'fetched_at': row.fetched_at+timedelta(minutes=5)}) for row in rows]
    if trailing_partial:
        new_rows.append(SimpleNamespace(**{**vars(new_rows[-1]), 'is_partial': True,
                                          'candle_timestamp': CLOSE+timedelta(minutes=5)}))
    signal = bot_service.SignalResult('HOLD', 'fixture', new_rows[-1].candle_timestamp, 100., {})
    monkeypatch.setattr(bot_service, 'fetch_candles_and_evaluate_strategy', lambda *a, **kw: (new_rows, signal))
    at = AT+timedelta(minutes=5)
    class RollClock(datetime):
        @classmethod
        def now(cls, tz=None): return at
    monkeypatch.setattr(bot_service, 'datetime', RollClock)
    monkeypatch.setattr(paper, 'datetime', RollClock)
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config, account=account,
                                             client=client, run=run, dry_run=True)
    assert result.status == 'held' and result.decision.reason == 'contract_rolled'
    assert not config.enabled and run.status == 'blocked'
    prepared = paper.prepare_committed_record(result); db_session.commit()
    assert prepared['market_discontinuity']['observed_contract'] == replacement
    paper.record_committed(prepared=prepared); paper.record_committed(prepared=prepared)
    after = paper.report(USER_A, config.account_id, config.id, now=at)
    assert after['unknown'] == 1 and after['pending'] == 0
    assert after['account']['blocked_reason'] == 'unknown_paper_contract_roll'
    assert after['account']['net_usd'] == before['account']['net_usd']
    assert after['account']['trades'] == before['account']['trades'] == 0
    assert len(after['discontinuities']) == 1
    for cohort in after['comparison']['cohorts']:
        for state in cohort['accounts'].values():
            if state['position']: assert state['blocked_reason'] == 'unknown_paper_contract_roll'
    assert client.place_order_calls == client.cancel_order_calls == client.close_position_calls == []


@pytest.mark.parametrize('fault', ['no_evidence', 'wrong_owner', 'wrong_observed', 'wrong_configured', 'not_hold'])
def test_roll_does_not_authorize_foreign_candles_or_forged_disposition(tmp_path, fault):
    ingest(tmp_path)
    row = candle(5); row.contract_id = 'CON.F.US.MNQ.H27'
    evidence = dict(kind='contract_roll', configured_contract=CONTRACT, observed_contract=row.contract_id,
                    observed_close=(row.candle_timestamp+timedelta(minutes=5)).isoformat())
    action = 'HOLD'
    if fault == 'no_evidence': evidence = None
    elif fault == 'wrong_owner': row.user_id = 'foreign'
    elif fault == 'wrong_observed': evidence['observed_contract'] = 'CON.F.US.MNQ.M27'
    elif fault == 'wrong_configured': evidence['configured_contract'] = row.contract_id
    else: action = 'BUY'
    with pytest.raises(ValueError):
        ingest(tmp_path, now=NOW+timedelta(minutes=10), candles=[row], forecast={}, action=action,
               status='held', reason='contract_rolled', simulation_eligibility=None, market_discontinuity=evidence)
    result = paper.report('a', 1, 32, root=tmp_path, now=NOW+timedelta(minutes=10))
    assert result['account']['blocked_reason'] is None and result['discontinuities'] == []
