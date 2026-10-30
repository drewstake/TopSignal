"""Local, transactional forward evidence and paper account. No broker dependencies."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

from .probabilistic_observation import enabled
from .probabilistic_shadow import cache_root
from .probabilistic_strategy import BAR, ET, Candle, Costs, contiguous, price_paths, utc, valid_stop, REGISTERED_STOP_POLICY
from .probabilistic_payoff import LEGACY_PAYOFF, PAPER_PAYOFF, execution_window
from .simulation_eligibility import assessment, at_recording

VERSION = 'forward-paper-v2'


def location(owner, account, config, root=None):
    identity = hashlib.sha256(f'{owner}|{account}|{config}'.encode()).hexdigest()
    return (Path(root) if root else cache_root()) / 'forward-results' / (identity + '.sqlite3')


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS evaluations (id TEXT PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS evaluations_at ON evaluations(at)')
    db.execute("CREATE INDEX IF NOT EXISTS evaluations_outcome ON evaluations(json_extract(payload,'$.outcome_status'))")
    db.execute("CREATE INDEX IF NOT EXISTS evaluations_volatility_outcome ON evaluations(json_extract(payload,'$.volatility_counterfactual.status'))")
    db.execute("CREATE INDEX IF NOT EXISTS evaluations_recording ON evaluations(json_extract(payload,'$.recording.status'))")
    db.execute('CREATE TABLE IF NOT EXISTS discontinuities (id TEXT PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL)')
    from .forward_evaluation import schema
    schema(db)
    return db


def initial():
    return dict(version=VERSION, net_usd=0., peak_usd=0., closed_drawdown_usd=0., trades=0,
                wins=0, daily={}, position=None, last_entry=None, blocked_reason=None, recent_trades=[])


def classify(forecast, action, risk_codes, reason):
    if forecast.get('forecasts'):
        if forecast.get('research_action') not in ('BUY', 'SELL'):
            sides = forecast['forecasts'].values()
            return 'insufficient_support' if forecast.get('training_paths', 0) < 300 or max((s.get('effective_days', 0) for s in sides), default=0) < 20 else 'edge_below_threshold'
        if risk_codes:
            return 'execution_risk_block'
        return 'qualified_signal' if action in ('BUY', 'SELL') else 'strategy_hold'
    if 'session' in reason or 'entry_too_close' in reason:
        return 'outside_entry_window'
    if forecast.get('data_status') == 'stale':
        return 'stale_candles'
    if forecast.get('reason_code') == 'volatility_above_risk_cap':
        return 'volatility_risk_cap'
    return 'inputs_or_model_unavailable'


def advance_paper(state, rows, now):
    p = state['position']
    if not p or state['blocked_reason']:
        return
    if p.get('recording_status') in ('pending', 'unknown'):
        state['blocked_reason'] = 'unknown_paper_recording_unconfirmed'
        return
    if any(r.contract_id != p['contract'] for r in rows):
        state['blocked_reason'] = 'unknown_paper_contract_roll'
        return
    if any(not r.valid() or r.partial or (p.get('data_live') is not None and r.live != p['data_live']) for r in rows):
        state['blocked_reason'] = 'unknown_paper_market_path'
        return
    # Never bridge a delivery roll or missing path with invented prices.
    relevant = [r for r in rows if r.contract_id == p['contract'] and r.timestamp >= utc(datetime.fromisoformat(p['next_bar'])) and r.timestamp+BAR <= now]
    for r in relevant:
        expected = utc(datetime.fromisoformat(p['next_bar']))
        if r.timestamp != expected:
            state['blocked_reason'] = 'unknown_paper_path_missing_bars'
            return
        side = 1 if p['side'] == 'BUY' else -1
        cost = Costs()
        if 'entry' not in p:
            p['entry'] = r.open + side * cost.adverse_points_per_side
            p['entry_at'] = r.timestamp.isoformat()
            state['last_entry'] = p['entry_at']
        entry = p['entry']
        stop, target = entry - side * p['stop'], entry + side * p['target']
        opening, adverse, favorable = side*r.open, side*(r.low if side == 1 else r.high), side*(r.high if side == 1 else r.low)
        exit_price = None
        kind = None
        if opening <= side*stop:
            exit_price, kind = r.open, 'stop_gap'
        elif opening >= side*target:
            exit_price, kind = target, 'target'
        elif adverse <= side*stop:
            exit_price, kind = stop, 'stop'
        elif favorable >= side*target:
            exit_price, kind = target, 'target'
        elif r.timestamp + BAR >= datetime.fromisoformat(p['deadline']):
            exit_price, kind = r.close, 'time'
        p['marked_net_usd'] = round(2 * side * (r.close - entry) - 2 * cost.adverse_points_per_side - 2 * cost.commission_per_side, 4)
        p['marked_at'] = (r.timestamp + BAR).isoformat()
        p['next_bar'] = (r.timestamp + BAR).isoformat()
        if exit_price is not None:
            net = round(2 * side * (exit_price - entry) - 2 * cost.adverse_points_per_side - 2 * cost.commission_per_side, 4)
            state['net_usd'] = round(state['net_usd'] + net, 4)
            state['trades'] += 1
            state['wins'] += int(net > 0)
            state['peak_usd'] = max(state['peak_usd'], state['net_usd'])
            state['closed_drawdown_usd'] = max(state['closed_drawdown_usd'], state['peak_usd'] - state['net_usd'])
            state['daily'][p['day']]['net_usd'] += net
            state['recent_trades'] = (state['recent_trades'] + [{**p, 'net_usd': net, 'exit_kind': kind, 'exit_at': (r.timestamp+BAR).isoformat()}])[-100:]
            state['position'] = None
            return
    if now > datetime.fromisoformat(p['deadline']) + timedelta(minutes=10):
        state['blocked_reason'] = 'unknown_paper_path_missing_bars'


def account_readiness(state, record, now):
    """Read-only shared + per-account gates, also evaluated on HOLDs."""
    f = record['forecast']
    shared = at_recording(record.get('simulation_eligibility'), f, datetime.fromisoformat(record['close']), now)
    if shared['status'] != 'eligible':
        return shared
    codes = []
    limits = shared['limits']
    if state['blocked_reason']:
        codes.append(state['blocked_reason'])
    if state['position']:
        codes.append('paper_position_open')
    if state['last_entry'] and (now - datetime.fromisoformat(state['last_entry'])).total_seconds() < limits['cooldown_seconds']:
        codes.append('paper_cooldown')
    if f.get('observation_only') is not True or f.get('routing_allowed') is not False:
        codes.append('paper_requires_observation_mode')
    stop, target = f.get('stop_points'), f.get('target_points')
    from .probabilistic_observation import stop_policy
    policy = stop_policy()
    if (f.get('stop_policy', REGISTERED_STOP_POLICY) != policy or not valid_stop(stop, policy)
            or target != math.ceil(stop*1.5/.25)*.25):
        return assessment([*codes, 'paper_invalid_bracket'])
    # Wait for the first *future* five-minute open; keep the original 15-minute deadline.
    window = execution_window(datetime.fromisoformat(record['close']), now)
    entry_at, deadline = datetime.fromisoformat(window['entry_at']), datetime.fromisoformat(window['deadline'])
    if entry_at >= deadline:
        codes.append('paper_entry_too_late')
    day = entry_at.astimezone(ET).date().isoformat()
    daily = state['daily'].get(day, dict(entries=0, net_usd=0.))
    if daily['entries'] >= limits['max_trades_per_day']:
        codes.append('paper_daily_entry_limit')
    stop_risk = 2*stop + 2*Costs().commission_per_side + 2*Costs().adverse_points_per_side
    if not math.isfinite(daily['net_usd']) or daily['net_usd'] - stop_risk <= -limits['max_daily_loss']:
        codes.append('paper_daily_loss_limit')
    return assessment(codes, limits=limits)


def admission(state, record, now, *, directional_baseline=False, comparison_rule=None):
    readiness = account_readiness(state, record, now)
    if readiness['status'] != 'eligible':
        return readiness['codes'][0] if readiness['codes'] else 'simulation_eligibility_unknown'
    f = record['forecast']
    if record['stage'] != 'qualified_signal':
        return record['stage']
    if comparison_rule not in ('always_long', 'always_short', 'empirical_pool') and record.get('status') != 'dry_run_attempt':
        return 'paper_requires_approved_dry_run_attempt'
    if not directional_baseline:
        choice = (f.get('forecasts') or {}).get(record['action'], {})
        values = (f.get('training_paths'), choice.get('effective_days'), choice.get('lower_utility_usd'))
        if (any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values)
                or values[0] < 300 or values[1] < 20 or values[2] <= 1):
            return 'paper_signal_support'
    entry_at = datetime.fromisoformat(f['entry_at'])
    deadline = datetime.fromisoformat(f['deadline'])
    day = entry_at.astimezone(ET).date().isoformat()
    daily = state['daily'].setdefault(day, dict(entries=0, net_usd=0.))
    daily['entries'] += 1
    state['position'] = dict(id=record['id'], contract=record['contract'], side=record['action'], quantity=1,
                             stop=f['stop_points'], target=f['target_points'], next_bar=entry_at.isoformat(), deadline=deadline.isoformat(),
                             payoff_semantics=PAPER_PAYOFF,
                             data_live=record['simulation_eligibility'].get('data_live'),
                             decision_at=record['at'], day=day, artifact=f.get('artifact_sha256'))
    if record.get('recording', {}).get('status') == 'pending':
        state['position']['recording_status'] = 'pending'
    return 'paper_entry_queued'


def _clock_time(clock, minimum):
    return max(minimum, utc(clock())) if clock is not None else minimum


def _recording_positions(db, state, record, *, confirmed_at=None, reject=False):
    """Confirm/cancel only this provisional admission; never rewrite prior trades."""
    def update(account):
        position = account['position']
        if not position or position['id'] != record['id'] or position.get('recording_status') != 'pending':
            return
        if reject:
            # This reservation has never been priced or filled.
            account['daily'][position['day']]['entries'] -= 1
            account['position'] = None
        elif confirmed_at is not None:
            position.update(recording_status='confirmed', recording_completed_at=confirmed_at, decision_at=confirmed_at)
        else:
            position['recording_status'] = 'unknown'
            account['blocked_reason'] = 'unknown_paper_recording_unconfirmed'
    update(state)
    row = db.execute('SELECT payload FROM comparison_cohorts WHERE id=?', (record.get('comparison_cohort'),)).fetchone()
    if row:
        cohort = json.loads(row[0])
        for account in cohort['accounts'].values():
            update(account)
        db.execute('UPDATE comparison_cohorts SET payload=? WHERE id=?',
                   (json.dumps(cohort, allow_nan=False), record['comparison_cohort']))


def _recover_unconfirmed_recordings(db, state):
    # A crash before the completion receipt leaves no trustworthy commit time.
    # Do not replay that provisional entry into bars already in the past.
    for ident, payload in db.execute("SELECT id,payload FROM evaluations WHERE json_extract(payload,'$.recording.status')='pending'").fetchall():
        record = json.loads(payload)
        record['recording']['status'] = 'unknown'
        shared = dict(record['simulation_eligibility'], status='unknown', codes=['simulation_recording_unconfirmed'])
        record.update(simulation_eligibility=shared, paper_readiness=shared,
                      paper_disposition='paper_recording_unconfirmed')
        record['comparison_dispositions'] = {k: 'shared_simulation_unknown' for k in record.get('comparison_dispositions', {})}
        _recording_positions(db, state, record)
        db.execute('UPDATE evaluations SET payload=? WHERE id=?', (json.dumps(record, allow_nan=False), ident))


def _confirm_recording(path, ident, completed_at, clock):
    # The initial transaction durably stores a conditional admission. Only its
    # completion receipt can authorize historical fills. The receipt itself may
    # arrive later; its completed_at is the observed end of the initial commit.
    with closing(connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        record = json.loads(db.execute('SELECT payload FROM evaluations WHERE id=?', (ident,)).fetchone()[0])
        if record.get('recording', {}).get('status') != 'pending':
            return
        checked_at = _clock_time(clock, completed_at)
        shared = at_recording(record['simulation_eligibility'], record['forecast'],
                              datetime.fromisoformat(record['close']), checked_at)
        state = json.loads(db.execute('SELECT payload FROM account WHERE id=1').fetchone()[0])
        record['at'] = completed_at.isoformat()
        record['recording'].update(status='confirmed', completed_at=record['at'], checked_at=checked_at.isoformat())
        record['simulation_eligibility'] = shared
        _recording_positions(db, state, record, confirmed_at=record['at'], reject=shared['status'] != 'eligible')
        if shared['status'] != 'eligible':
            record.update(paper_readiness=shared, paper_disposition=shared['codes'][0] if shared['codes'] else 'simulation_eligibility_unknown')
            record['comparison_dispositions'] = {k: 'shared_simulation_'+shared['status'] for k in record.get('comparison_dispositions', {})}
        state['updated_at'] = checked_at.isoformat()
        db.execute('UPDATE evaluations SET at=?,payload=? WHERE id=?', (record['at'], json.dumps(record, allow_nan=False), ident))
        db.execute('UPDATE account SET payload=? WHERE id=1', (json.dumps(state, allow_nan=False),))


def _record_roll(*, owner, account, config, contract, run, rows, evidence, now, root, clock):
    """Record a server-observed discontinuity; never price the replacement feed."""
    if not isinstance(evidence, dict):
        raise ValueError('Invalid market discontinuity evidence')
    observed = evidence.get('observed_contract')
    closed = sorted((c for c in rows if not c.partial and c.timestamp+BAR <= now), key=lambda c: c.timestamp)
    if (evidence.get('kind') != 'contract_roll' or evidence.get('configured_contract') != contract
            or not isinstance(observed, str) or not observed.startswith('CON.F.US.MNQ.') or observed == contract
            or not closed or closed[-1].contract_id != observed
            or any(c.owner != str(owner) or c.contract_id not in (contract, observed) for c in rows)
            or evidence.get('observed_close') != (closed[-1].timestamp+BAR).isoformat()):
        raise ValueError('Invalid market discontinuity scope')
    event = dict(evidence, owner=str(owner), account=account, config=config, run=run)
    ident = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
    with closing(connect(location(owner, account, config, root))) as db, db:
        db.execute('BEGIN IMMEDIATE')
        now = _clock_time(clock, now)
        saved = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        state = json.loads(saved[0]) if saved else initial()
        _recover_unconfirmed_recordings(db, state)
        def mark(value):
            if value['position'] and value['position']['contract'] == contract:
                value['blocked_reason'] = 'unknown_paper_contract_roll'
        mark(state)
        state.setdefault('started_at', now.isoformat())
        state['updated_at'] = now.isoformat()
        db.execute('INSERT OR REPLACE INTO account VALUES (1,?)', (json.dumps(state, allow_nan=False),))
        for key, payload in db.execute('SELECT id,payload FROM comparison_cohorts').fetchall():
            cohort = json.loads(payload)
            for value in cohort['accounts'].values(): mark(value)
            db.execute('UPDATE comparison_cohorts SET payload=? WHERE id=?', (json.dumps(cohort, allow_nan=False), key))
        for key, payload in db.execute("SELECT id,payload FROM evaluations WHERE json_extract(payload,'$.contract')=? AND (json_extract(payload,'$.outcome_status')='pending' OR json_extract(payload,'$.volatility_counterfactual.status')='pending')", (contract,)).fetchall():
            record = json.loads(payload)
            if record['outcome_status'] == 'pending': record['outcome_status'] = 'unknown_missing_bars_or_roll'
            if record.get('volatility_counterfactual', {}).get('status') == 'pending':
                record['volatility_counterfactual']['status'] = 'unknown_contract_roll'
            record['market_discontinuity'] = ident
            db.execute('UPDATE evaluations SET payload=? WHERE id=?', (json.dumps(record, allow_nan=False), key))
        db.execute('INSERT OR IGNORE INTO discontinuities VALUES (?,?,?)', (ident, now.isoformat(), json.dumps(event, allow_nan=False)))


def ingest(*, owner, account, config, contract, run, candles, forecast, action, status, reason, risk_codes, now, root=None, timing=None, config_hash=None,
           simulation_eligibility=None, broker_readiness=None, market_discontinuity=None, clock=None):
    if not enabled():
        return
    now = utc(now)
    converted = [Candle.from_row(c) for c in candles]
    if market_discontinuity is not None:
        if action != 'HOLD' or status != 'held' or reason != 'contract_rolled':
            raise ValueError('Invalid market discontinuity disposition')
        return _record_roll(owner=owner, account=account, config=config, contract=contract, run=run,
                            rows=converted, evidence=market_discontinuity, now=now, root=root, clock=clock)
    if any(c.owner != str(owner) or c.contract_id != contract for c in converted):
        raise ValueError('Forward observation scope mismatch')
    rows = [c for c in converted if not c.partial and c.timestamp+BAR <= now]
    if any(not c.valid() for c in rows) or any(a.timestamp >= b.timestamp for a,b in zip(rows,rows[1:])):
        raise ValueError('Invalid forward candle sequence')
    close = (rows[-1].timestamp+BAR).isoformat() if rows else datetime.fromtimestamp(math.floor(now.timestamp()/300)*300, timezone.utc).isoformat()
    key = contract + ':' + close
    # Retain forecast metadata, never provider payloads or full input candle series.
    allowed = ('forecasts','research_action','data_status','training_paths','stop_points','target_points','artifact_sha256',
               'model_age_days','training_stale','observation_only','routing_allowed','reason_code','candle_close_timestamp',
               'costs','evaluated_at','pool_baseline','stop_policy','payoff_semantics','entry_at','deadline','entry_offset_bars','exposure_bars','data_live')
    f = {k: forecast[k] for k in allowed if k in forecast}
    if f.get('forecasts') and f.get('candle_close_timestamp') != close:
        raise ValueError('Forecast/candle alignment mismatch')
    shared = at_recording(simulation_eligibility, f, datetime.fromisoformat(close), now)
    if shared['status'] == 'eligible' and any(shared.get(k) != v for k,v in dict(owner=str(owner), account=account, config=config, contract=contract).items()):
        shared = assessment(['simulation_ownership_mismatch'])
    record = dict(id=key, at=now.isoformat(), close=close, contract=contract, run=run,
                  simulation_eligibility=shared, broker_readiness=broker_readiness,
                  action=action, status=status, reason=str(reason)[:500], risk_codes=list(risk_codes), forecast=f,
                  stage=classify(f, action, risk_codes, str(reason)), outcome_status='pending' if f.get('forecasts') else 'no_forecast',
                  evaluation_version=3, timing=timing or {}, config_hash=config_hash)
    path = location(owner, account, config, root)
    inserted = False
    with closing(connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        now = _clock_time(clock, now)
        saved = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        state = json.loads(saved[0]) if saved else initial()
        _recover_unconfirmed_recordings(db, state)
        state.setdefault('started_at', now.isoformat())
        state['updated_at'] = now.isoformat()
        advance_paper(state, rows, now)
        from .forward_evaluation import (advance_comparisons, admit_comparisons, delayed_outcomes,
                                        start_volatility_counterfactual, resolve_volatility_counterfactual)
        advance_comparisons(db, rows, now)
        for trade in state['recent_trades']:
            db.execute('INSERT OR IGNORE INTO trades VALUES (?,?)', (trade['id'],json.dumps(trade,allow_nan=False)))
        by_stamp = {c.timestamp: c for c in rows}
        # Pending forecasts are few; JSON query avoids rescanning the historical ledger.
        for ident, payload in db.execute("SELECT id,payload FROM evaluations WHERE json_extract(payload,'$.outcome_status')='pending'").fetchall():
            old = json.loads(payload)
            t = datetime.fromisoformat(old['close'])
            offset = old['forecast'].get('entry_offset_bars', 0) if old['forecast'].get('payoff_semantics') == PAPER_PAYOFF else 0
            future = [by_stamp.get(t+i*BAR) for i in range(offset, 3)]
            if now < t+3*BAR:
                continue
            if (old['contract'] == contract and future and all(future) and contiguous(future)
                    and all(c.live == old['forecast'].get('data_live', c.live) for c in future)):
                reference = future[0].open
                payoff_path = np.array([[[v-reference for v in (c.open,c.high,c.low,c.close)] for c in future]])
                old['outcomes'] = {}
                for side, sign in (('BUY',1),('SELL',-1)):
                    net,kind,_ = price_paths(payoff_path,stop=old['forecast']['stop_points'],side=sign,costs=Costs(),scale=1.)
                    old['outcomes'][side] = dict(net_usd=float(net[0]),exit_kind=('stop','time','target')[int(kind[0])])
                old['outcome_status'] = 'observed'
                if old.get('evaluation_version', 1) >= 2:
                    old['delayed_outcomes'] = delayed_outcomes(old, rows, now)
            elif now > t+timedelta(hours=2) or old['contract'] != contract:
                old['outcome_status'] = 'unknown_missing_bars_or_roll'
            db.execute('UPDATE evaluations SET payload=? WHERE id=?', (json.dumps(old,allow_nan=False),ident))
        for ident, payload in db.execute("SELECT id,payload FROM evaluations WHERE json_extract(payload,'$.volatility_counterfactual.status')='pending'").fetchall():
            old = json.loads(payload)
            resolve_volatility_counterfactual(old, rows, now, contract)
            db.execute('UPDATE evaluations SET payload=? WHERE id=?', (json.dumps(old,allow_nan=False),ident))
        if not db.execute('SELECT 1 FROM evaluations WHERE id=?',(key,)).fetchone():
            # Include lock wait and all maturity work in the admission clock.
            now = _clock_time(clock, now)
            record['at'] = now.isoformat()
            record['simulation_eligibility'] = at_recording(shared, f, datetime.fromisoformat(close), now)
            if clock is not None:
                record['recording'] = dict(status='pending', prepared_at=record['at'])
            scenario = start_volatility_counterfactual(record, rows)
            if scenario:
                record['volatility_counterfactual'] = scenario
            record['paper_readiness'] = account_readiness(state, record, now)
            record['paper_disposition'] = admission(state,record,now)
            admit_comparisons(db,record,now)
            db.execute('INSERT INTO evaluations VALUES (?,?,?)',(key,record['at'],json.dumps(record,allow_nan=False)))
            inserted = True
        state['updated_at'] = now.isoformat()
        db.execute('INSERT OR REPLACE INTO account VALUES (1,?)',(json.dumps(state,allow_nan=False),))
    if inserted and clock is not None:
        _confirm_recording(path, key, _clock_time(clock, now), clock)


def prepare_committed_record(result):
    """Freeze inputs before commit expires ORM rows; this performs no writes."""
    from .topbot_mathematical import selected
    if not enabled() or result.run is None or not result.run.dry_run or not selected(result.config.strategy_params):
        return
    explanation = result.analysis.get('bot_decision', {})
    raw = getattr(result.decision, 'raw_payload', None)
    return dict(owner=str(result.config.user_id), account=result.config.account_id, config=result.config.id,
           contract=result.config.contract_id, run=result.run.id, candles=result.candles,
           forecast=explanation.get('probabilistic_research') or {},
           action=str(result.decision.action), status=result.status, reason=result.decision.reason,
           risk_codes=[r.code for r in result.risk_events],
           simulation_eligibility=explanation.get('simulation_eligibility'), broker_readiness=explanation.get('broker_readiness'),
           market_discontinuity=raw.get('market_discontinuity') if isinstance(raw, dict) else None,
           timing=result.analysis.get('evaluation_timing'), config_hash=result.analysis.get('evaluation_config_hash'))


def record_committed(result=None, *, prepared=None):
    """Only called by the lease-owning worker after the execution DB commit."""
    inputs = prepared if prepared is not None else prepare_committed_record(result)
    if inputs is not None:
        ingest(**inputs, now=datetime.now(timezone.utc), clock=lambda: datetime.now(timezone.utc))


def report(owner, account, config, *, days=7, root=None, now=None):
    now = utc(now or datetime.now(timezone.utc))
    path = location(owner,account,config,root)
    result = dict(status='awaiting_observations', generated_at=now.isoformat(), days=days,
                  evaluations=0, forecasts=0, completed=0, pending=0, unknown=0, overdue=0,
                  rejection_counts={}, paper_blocks={}, calibration={}, recent=[], account=None,
                  scan_limited=False, latest_at=None, artifacts=[], version=VERSION,
                  timing_summary=None, comparison=None, volatility_counterfactuals=None, coverage=None, discontinuities=[])
    if not path.exists():
        return result
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        # All sections must describe one snapshot while the worker keeps writing.
        db.execute('BEGIN')
        state = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        first = db.execute('SELECT payload FROM evaluations WHERE at<=? ORDER BY at LIMIT 1', (now.isoformat(),)).fetchone()
        window_start = now-timedelta(days=days)
        entries = db.execute('SELECT payload FROM evaluations WHERE at>=? AND at<=? ORDER BY at DESC LIMIT 9001',
                             (window_start.isoformat(), now.isoformat())).fetchall()
        records = [json.loads(r[0]) for r in entries[:9000]]
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discontinuities'").fetchone():
            result['discontinuities'] = [dict(at=at, **json.loads(payload)) for at, payload in db.execute(
                'SELECT at,payload FROM discontinuities WHERE at>=? AND at<=? ORDER BY at DESC LIMIT 30',
                (window_start.isoformat(), now.isoformat()))]
        from .forward_evaluation import comparison_report, collection_coverage
        result['comparison'] = comparison_report(db,records,now,len(entries)>9000,window_start)
        result['coverage'] = collection_coverage(records, first_record=json.loads(first[0]) if first else None,
                                                 now=now, window_start=window_start, scan_limited=len(entries)>9000)
    result.update(status='available', scan_limited=len(entries)>9000, account=json.loads(state[0]) if state else None)
    result['evaluations'] = len(records)
    result['latest_at'] = records[0]['at'] if records else None
    result['rejection_counts'] = dict(Counter(r['stage'] for r in records))
    result['paper_blocks'] = dict(Counter(r['paper_disposition'] for r in records if r.get('paper_disposition') not in (None, r['stage'])))
    forecasts = [r for r in records if r['forecast'].get('forecasts')]
    observed = [r for r in forecasts if r['outcome_status']=='observed']
    result.update(forecasts=len(forecasts), completed=len(observed), pending=sum(r['outcome_status']=='pending' for r in forecasts),
                  unknown=sum(r['outcome_status'].startswith('unknown') for r in forecasts),
                  overdue=sum(r['outcome_status']=='pending' and datetime.fromisoformat(r['close'])+timedelta(minutes=17)<now for r in forecasts))
    result['artifacts'] = sorted({r['forecast']['artifact_sha256'] for r in forecasts if r['forecast'].get('artifact_sha256')})
    # Nonoverlapping 15-minute lattice; BUY and SELL scores are separate, not independent samples.
    binding = lambda r: (r['forecast'].get('artifact_sha256'), r['forecast'].get('payoff_semantics', LEGACY_PAYOFF))
    selected_binding = binding(forecasts[0]) if forecasts else None
    result['calibration_binding'] = dict(artifact=selected_binding[0], payoff_semantics=selected_binding[1]) if selected_binding else None
    scored = [r for r in observed if datetime.fromisoformat(r['close']).minute % 15 == 0 and binding(r) == selected_binding]
    for side in ('BUY','SELL'):
        pairs = [(r['forecast']['forecasts'][side]['probability_net_positive'], int(r['outcomes'][side]['net_usd']>0)) for r in scored]
        bins=[]
        for i in range(5):
            subset=[(p,y) for p,y in pairs if min(int(p*5),4)==i]
            bins.append(dict(lower=i/5,upper=(i+1)/5,count=len(subset),predicted=sum(p for p,_ in subset)/len(subset) if subset else None,observed=sum(y for _,y in subset)/len(subset) if subset else None))
        result['calibration'][side]=dict(count=len(pairs),brier=sum((p-y)**2 for p,y in pairs)/len(pairs) if pairs else None,bins=bins)
    from .forward_evaluation import details, volatility_counterfactual_report
    result['volatility_counterfactuals'] = volatility_counterfactual_report(records, now)
    result['recent']=[{**{k:r.get(k) for k in ('at','action','stage','reason','risk_codes','paper_disposition','outcome_status','outcomes')},
                       'details': details(r)} for r in records[:30]]
    latencies=[(datetime.fromisoformat(r['at'])-datetime.fromisoformat(r['close'])).total_seconds() for r in forecasts]
    durations=[r.get('timing',{}).get('evaluation_duration_ms') for r in records]
    durations=[v for v in durations if isinstance(v,(int,float)) and math.isfinite(v)]
    result['timing_summary']=dict(count=len(latencies), median_seconds=float(np.median(latencies)) if latencies else None,
                                  p95_seconds=float(np.quantile(latencies,.95)) if latencies else None,
                                  max_seconds=max(latencies) if latencies else None,
                                  measured_duration_count=len(durations),mean_evaluation_ms=float(np.mean(durations)) if durations else None)
    return result
