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
from .probabilistic_strategy import BAR, ET, Candle, Costs, contiguous, price_paths, utc

VERSION = 'forward-paper-v1'


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
    # Never bridge a delivery roll or missing path with invented prices.
    relevant = [r for r in rows if r.contract_id == p['contract'] and r.timestamp >= utc(datetime.fromisoformat(p['next_bar']))]
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


def admission(state, record, now, *, directional_baseline=False):
    f = record['forecast']
    if record['stage'] != 'qualified_signal':
        return record['stage']
    if record.get('status') != 'dry_run_attempt':
        return 'paper_requires_approved_dry_run_attempt'
    if state['blocked_reason']:
        return state['blocked_reason']
    if state['position']:
        return 'paper_position_open'
    if state['last_entry'] and (now - datetime.fromisoformat(state['last_entry'])).total_seconds() < 300:
        return 'paper_cooldown'
    if f.get('observation_only') is not True or f.get('routing_allowed') is not False:
        return 'paper_requires_observation_mode'
    if not directional_baseline and (f.get('training_paths', 0) < 300 or f['forecasts'][record['action']].get('effective_days', 0) < 20 or f['forecasts'][record['action']].get('lower_utility_usd', 0) <= 1):
        return 'paper_signal_support'
    stop, target = f.get('stop_points'), f.get('target_points')
    if not isinstance(stop, (int, float)) or not math.isfinite(stop) or not 4 <= stop <= 25 or target != math.ceil(stop*1.5/.25)*.25:
        return 'paper_invalid_bracket'
    # Wait for the first *future* five-minute open; keep the original 15-minute deadline.
    entry_at = datetime.fromtimestamp((math.floor(now.timestamp()/300)+1)*300, timezone.utc)
    deadline = datetime.fromisoformat(record['close']) + 3*BAR
    if entry_at >= deadline:
        return 'paper_entry_too_late'
    day = entry_at.astimezone(ET).date().isoformat()
    daily = state['daily'].setdefault(day, dict(entries=0, net_usd=0.))
    if daily['entries'] >= 30:
        return 'paper_daily_entry_limit'
    stop_risk = 2*stop + 2*Costs().commission_per_side + 2*Costs().adverse_points_per_side
    if daily['net_usd'] - stop_risk <= -250:
        return 'paper_daily_loss_limit'
    daily['entries'] += 1
    state['position'] = dict(id=record['id'], contract=record['contract'], side=record['action'], quantity=1,
                             stop=stop, target=target, next_bar=entry_at.isoformat(), deadline=deadline.isoformat(),
                             decision_at=record['at'], day=day, artifact=f.get('artifact_sha256'))
    return 'paper_entry_queued'


def ingest(*, owner, account, config, contract, run, candles, forecast, action, status, reason, risk_codes, now, root=None, timing=None, config_hash=None):
    if not enabled():
        return
    now = utc(now)
    converted = [Candle.from_row(c) for c in candles]
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
               'costs','evaluated_at','pool_baseline')
    f = {k: forecast[k] for k in allowed if k in forecast}
    if f.get('forecasts') and f.get('candle_close_timestamp') != close:
        raise ValueError('Forecast/candle alignment mismatch')
    record = dict(id=key, at=now.isoformat(), close=close, contract=contract, run=run,
                  action=action, status=status, reason=str(reason)[:500], risk_codes=list(risk_codes), forecast=f,
                  stage=classify(f, action, risk_codes, str(reason)), outcome_status='pending' if f.get('forecasts') else 'no_forecast',
                  evaluation_version=2, timing=timing or {}, config_hash=config_hash)
    with closing(connect(location(owner, account, config, root))) as db, db:
        db.execute('BEGIN IMMEDIATE')
        saved = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        state = json.loads(saved[0]) if saved else initial()
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
            future = [by_stamp.get(t+i*BAR) for i in range(3)]
            if now < t+3*BAR:
                continue
            if old['contract'] == contract and all(future) and contiguous(future):
                reference = future[0].open
                path = np.array([[[v-reference for v in (c.open,c.high,c.low,c.close)] for c in future]])
                old['outcomes'] = {}
                for side, sign in (('BUY',1),('SELL',-1)):
                    net,kind,_ = price_paths(path,stop=old['forecast']['stop_points'],side=sign,costs=Costs(),scale=1.)
                    old['outcomes'][side] = dict(net_usd=float(net[0]),exit_kind=('stop','time','target')[int(kind[0])])
                old['outcome_status'] = 'observed'
                if old.get('evaluation_version') == 2:
                    old['delayed_outcomes'] = delayed_outcomes(old, rows, now)
            elif now > t+timedelta(hours=2) or old['contract'] != contract:
                old['outcome_status'] = 'unknown_missing_bars_or_roll'
            db.execute('UPDATE evaluations SET payload=? WHERE id=?', (json.dumps(old,allow_nan=False),ident))
        for ident, payload in db.execute("SELECT id,payload FROM evaluations WHERE json_extract(payload,'$.volatility_counterfactual.status')='pending'").fetchall():
            old = json.loads(payload)
            resolve_volatility_counterfactual(old, rows, now, contract)
            db.execute('UPDATE evaluations SET payload=? WHERE id=?', (json.dumps(old,allow_nan=False),ident))
        if not db.execute('SELECT 1 FROM evaluations WHERE id=?',(key,)).fetchone():
            scenario = start_volatility_counterfactual(record, rows)
            if scenario:
                record['volatility_counterfactual'] = scenario
            record['paper_disposition'] = admission(state,record,now)
            admit_comparisons(db,record,now)
            db.execute('INSERT INTO evaluations VALUES (?,?,?)',(key,record['at'],json.dumps(record,allow_nan=False)))
        db.execute('INSERT OR REPLACE INTO account VALUES (1,?)',(json.dumps(state,allow_nan=False),))


def prepare_committed_record(result):
    """Freeze inputs before commit expires ORM rows; this performs no writes."""
    from .topbot_mathematical import selected
    if not enabled() or result.run is None or not result.run.dry_run or not selected(result.config.strategy_params):
        return
    explanation = result.analysis.get('bot_decision', {})
    return dict(owner=str(result.config.user_id), account=result.config.account_id, config=result.config.id,
           contract=result.config.contract_id, run=result.run.id, candles=result.candles,
           forecast=explanation.get('probabilistic_research') or {},
           action=str(result.decision.action), status=result.status, reason=result.decision.reason,
           risk_codes=[r.code for r in result.risk_events],
           timing=result.analysis.get('evaluation_timing'), config_hash=result.analysis.get('evaluation_config_hash'))


def record_committed(result=None, *, prepared=None):
    """Only called by the lease-owning worker after the execution DB commit."""
    inputs = prepared if prepared is not None else prepare_committed_record(result)
    if inputs is not None:
        ingest(**inputs, now=datetime.now(timezone.utc))


def report(owner, account, config, *, days=7, root=None, now=None):
    now = utc(now or datetime.now(timezone.utc))
    path = location(owner,account,config,root)
    result = dict(status='awaiting_observations', generated_at=now.isoformat(), days=days,
                  evaluations=0, forecasts=0, completed=0, pending=0, unknown=0, overdue=0,
                  rejection_counts={}, paper_blocks={}, calibration={}, recent=[], account=None,
                  scan_limited=False, latest_at=None, artifacts=[], version=VERSION,
                  timing_summary=None, comparison=None, volatility_counterfactuals=None)
    if not path.exists():
        return result
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        state = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        entries = db.execute('SELECT payload FROM evaluations WHERE at>=? ORDER BY at DESC LIMIT 9001',((now-timedelta(days=days)).isoformat(),)).fetchall()
    records = [json.loads(r[0]) for r in entries[:9000]]
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
    scored = [r for r in observed if datetime.fromisoformat(r['close']).minute % 15 == 0]
    for side in ('BUY','SELL'):
        pairs = [(r['forecast']['forecasts'][side]['probability_net_positive'], int(r['outcomes'][side]['net_usd']>0)) for r in scored]
        bins=[]
        for i in range(5):
            subset=[(p,y) for p,y in pairs if min(int(p*5),4)==i]
            bins.append(dict(lower=i/5,upper=(i+1)/5,count=len(subset),predicted=sum(p for p,_ in subset)/len(subset) if subset else None,observed=sum(y for _,y in subset)/len(subset) if subset else None))
        result['calibration'][side]=dict(count=len(pairs),brier=sum((p-y)**2 for p,y in pairs)/len(pairs) if pairs else None,bins=bins)
    from .forward_evaluation import details, comparison_report, volatility_counterfactual_report
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
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        result['comparison']=comparison_report(db,records,now,result['scan_limited'],now-timedelta(days=days))
    return result
