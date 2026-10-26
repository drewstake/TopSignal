"""Versioned, filesystem-only forward comparisons. Never submits orders."""
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
import copy
import hashlib
import json
import math

import numpy as np

from .probabilistic_strategy import BAR, ET, Costs, SPEC, VolatilityAboveRiskCap, features, contiguous
from .topbot_session import entry_boundary_reason

POLICY = 'forward-comparison-v1'
RULES = ('topbot', 'flat', 'always_long', 'always_short', 'empirical_pool')
MIN_SESSIONS = 20
BOOTSTRAP_REPLICATES = 20_000
VOLATILITY_POLICY = 'volatility-blocked-what-if-v1'


def start_volatility_counterfactual(record, rows):
    """Freeze a rejected bracket for observation only; never create a signal."""
    if (record['stage'] != 'volatility_risk_cap' or record['action'] != 'HOLD'
            or record['status'] != 'held' or record['forecast'].get('data_status') != 'fresh'):
        return None
    close, at = datetime.fromisoformat(record['close']), datetime.fromisoformat(record['at'])
    if not 0 <= (at-close).total_seconds() <= 120:
        return None
    # Recheck actual observed input validity and scope instead of trusting a reason string.
    try:
        features(rows, as_of=at)
    except VolatilityAboveRiskCap as exc:
        stop, sigma = exc.stop, exc.sigma
    except ValueError:
        return None
    else:
        return None
    entry, deadline = next_open(record), close + 3*BAR
    return dict(policy=VOLATILITY_POLICY, status='pending' if entry < deadline else 'entry_too_late',
                recorded_at=record['at'], entry_at=entry.isoformat(), deadline=deadline.isoformat(),
                stop_points=stop, target_points=math.ceil(stop*SPEC['target_stop_ratio']/.25)*.25,
                risk_cap_points=SPEC['maximum_candidate_stop_points'], volatility_sigma=sigma,
                data_live=rows[-1].live, quantity=1, costs=Costs().describe(), outcomes=None,
                basis='Independent BUY/SELL paths using the rejected implied stop, first five-minute open after recording, original 15-minute deadline, assumed costs and stop-first OHLC. No model probability, account trade or actual fill.')


def resolve_volatility_counterfactual(record, rows, now, contract):
    scenario = record['volatility_counterfactual']
    if scenario['status'] != 'pending':
        return
    deadline, entry = datetime.fromisoformat(scenario['deadline']), datetime.fromisoformat(scenario['entry_at'])
    if record['contract'] != contract:
        scenario['status'] = 'unknown_contract_roll'
        return
    if now < deadline:
        return
    by_stamp = {r.timestamp: r for r in rows}
    future = [by_stamp.get(entry+i*BAR) for i in range(int((deadline-entry)/BAR))]
    if (future and all(future) and contiguous(future)
            and all(r.live == scenario['data_live'] for r in future)):
        hypothetical = dict(record, forecast={'stop_points': scenario['stop_points'], 'target_points': scenario['target_points']})
        result = delayed_outcomes(hypothetical, future, now)
        scenario.update(status=result['status'], outcomes=result['sides'], resolved_at=now.isoformat())
    elif now > deadline + timedelta(hours=2):
        scenario['status'] = 'unknown_missing_bars'


def volatility_counterfactual_report(records, now):
    rows = [r for r in records if r.get('volatility_counterfactual')]
    scenarios = [r['volatility_counterfactual'] for r in rows]
    sides = {}
    for side in ('BUY', 'SELL'):
        completed = [s for s in scenarios if s['status'] == 'observed']
        values = [finite((s.get('outcomes') or {}).get(side, {}).get('net_usd')) for s in completed]
        observed = [v for v in values if v is not None]
        total = math.fsum(observed) if observed else None
        sides[side] = dict(completed=len(observed), unavailable=len(values)-len(observed),
                           net_usd=round(total, 4) if total is not None else None,
                           mean_net_usd=round(total/len(observed), 4) if observed else None,
                           positive=sum(v > 0 for v in observed), negative=sum(v < 0 for v in observed),
                           breakeven=sum(v == 0 for v in observed))
    return dict(policy=VOLATILITY_POLICY, recorded=len(rows),
                completed=sum(s['status']=='observed' for s in scenarios),
                pending=sum(s['status']=='pending' for s in scenarios),
                unknown=sum(s['status'].startswith('unknown') or s['status']=='entry_too_late' for s in scenarios),
                overdue=sum(s['status']=='pending' and now > datetime.fromisoformat(s['deadline'])+timedelta(minutes=2) for s in scenarios),
                sides=sides,
                recent=[dict(at=r['at'], **r['volatility_counterfactual']) for r in rows[:30]])


def finite(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def next_open(record):
    at = datetime.fromisoformat(record['at'])
    return datetime.fromtimestamp((math.floor(at.timestamp()/300)+1)*300, timezone.utc)


def details(record):
    f = record['forecast']
    close, at = datetime.fromisoformat(record['close']), datetime.fromisoformat(record['at'])
    entry, deadline = next_open(record), close + 3*BAR
    timing = dict(record.get('timing') or {})
    timing.update(candle_close=record['close'], recorded_at=record['at'],
                  close_to_record_seconds=round((at-close).total_seconds(), 3),
                  theoretical_entry_at=record['close'], simulated_entry_at=entry.isoformat(),
                  exit_deadline=deadline.isoformat(),
                  remaining_horizon_seconds=max(0, (deadline-entry).total_seconds()),
                  entry_available=entry < deadline, quote_status='not_captured')
    sides = {}
    paths = finite(f.get('training_paths'))
    for side, values in (f.get('forecasts') or {}).items():
        mean = finite(values.get('expected_net_usd'))
        penalty = finite(values.get('uncertainty_penalty_usd'))
        lower, days = finite(values.get('lower_utility_usd')), finite(values.get('effective_days'))
        blocks = []
        if paths is None or paths < 300: blocks.append('fewer_than_300_training_paths')
        if days is None or days < 20: blocks.append('fewer_than_20_effective_days')
        if lower is None: blocks.append('utility_unavailable')
        elif lower <= 1: blocks.append('net_edge_not_above_1_usd')
        sides[side] = dict(expected_net_usd=mean, uncertainty_penalty_usd=penalty,
                           lower_utility_usd=lower, threshold_usd=1.,
                           margin_usd=None if lower is None else round(lower-1, 6),
                           effective_days=days, training_paths=paths, blocks=blocks)
    return dict(timing=timing, sides=sides, costs=f.get('costs'),
                risk_codes=record['risk_codes'], stage=record['stage'],
                paper_disposition=record.get('paper_disposition'),
                theoretical_outcomes=record.get('outcomes'), delayed_outcomes=record.get('delayed_outcomes'))


def delayed_outcomes(record, rows, now):
    """Separate hypothetical payoffs using the same post-record entry as paper accounts."""
    from .forward_results import initial, advance_paper
    entry = next_open(record)
    deadline = datetime.fromisoformat(record['close']) + 3*BAR
    if entry >= deadline:
        return dict(status='entry_too_late', sides={})
    day = entry.astimezone(ET).date().isoformat()
    output = {}
    for side in ('BUY', 'SELL'):
        state = initial()
        state['daily'][day] = dict(entries=1, net_usd=0.)
        state['position'] = dict(id=record['id'], contract=record['contract'], side=side, quantity=1,
                                 stop=record['forecast']['stop_points'], target=record['forecast']['target_points'],
                                 next_bar=entry.isoformat(), deadline=deadline.isoformat(),
                                 decision_at=record['at'], day=day)
        advance_paper(state, rows, now)
        if state['blocked_reason'] or state['position']:
            return dict(status='unknown_missing_path', sides={})
        trade = state['recent_trades'][0]
        output[side] = {k: trade[k] for k in ('net_usd','entry_at','entry','exit_at','exit_kind')}
    return dict(status='observed', sides=output)


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS comparison_cohorts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS comparison_trades (cohort TEXT, rule TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(cohort,rule,id))')


def advance_comparisons(db, rows, now):
    from .forward_results import advance_paper
    for ident, payload in db.execute('SELECT id,payload FROM comparison_cohorts').fetchall():
        cohort = json.loads(payload)
        # Closed cohorts have no work until selected again. Open positions still resolve.
        for rule, state in cohort['accounts'].items():
            advance_paper(state, rows, now)
            for trade in state['recent_trades']:
                db.execute('INSERT OR IGNORE INTO comparison_trades VALUES (?,?,?,?)',
                           (ident,rule,trade['id'],json.dumps(trade,allow_nan=False)))
        db.execute('UPDATE comparison_cohorts SET payload=? WHERE id=?', (json.dumps(cohort,allow_nan=False),ident))


def admit_comparisons(db, record, now):
    from .forward_results import initial, admission
    forecast = record['forecast']
    if not forecast.get('forecasts'):
        # Record coverage for the current cohort without inventing an eligible signal.
        row = db.execute('SELECT id,payload FROM comparison_cohorts ORDER BY rowid DESC LIMIT 1').fetchone()
        if row and json.loads(row[1])['config_hash'] == record.get('config_hash'):
            record['comparison_cohort'] = row[0]
        return
    if forecast.get('observation_only') is not True or forecast.get('routing_allowed') is not False:
        return
    if record['status'] not in ('held','evaluated','dry_run_attempt','risk_blocked'):
        return
    binding = dict(policy=POLICY, artifact=forecast.get('artifact_sha256'),
                   config_hash=record.get('config_hash'), costs=Costs().describe())
    binding_sha = hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
    latest = db.execute('SELECT id,payload FROM comparison_cohorts ORDER BY rowid DESC LIMIT 1').fetchone()
    previous = json.loads(latest[1]) if latest else None
    if previous and previous.get('binding_sha') == binding_sha:
        ident, cohort = latest[0], previous
    else:
        if previous:
            previous['ended_at'] = record['at']
            db.execute('UPDATE comparison_cohorts SET payload=? WHERE id=?',(json.dumps(previous,allow_nan=False),latest[0]))
        ident = hashlib.sha256((binding_sha+record['id']).encode()).hexdigest()
        cohort = dict(**binding, binding_sha=binding_sha, started_at=record['at'], ended_at=None,
                      accounts={name: initial() for name in RULES})
    record['comparison_cohort'] = ident
    dispositions = {}
    pool = forecast.get('pool_baseline') or {}
    for rule, state in cohort['accounts'].items():
        if rule == 'flat':
            dispositions[rule] = 'flat_no_trade'
            continue
        if rule != 'topbot' and record['risk_codes']:
            dispositions[rule] = 'shared_execution_risk_block'
            continue
        candidate = copy.deepcopy(record)
        if rule in ('always_long','always_short'):
            candidate.update(action='BUY' if rule == 'always_long' else 'SELL', stage='qualified_signal', status='dry_run_attempt')
        elif rule == 'empirical_pool':
            if not pool.get('forecasts'):
                dispositions[rule] = 'pool_forecast_unavailable'
                continue
            action = pool.get('research_action')
            if action not in ('BUY','SELL'):
                dispositions[rule] = 'pool_hold'
                continue
            candidate.update(action=action,stage='qualified_signal',status='dry_run_attempt')
            candidate['forecast']['forecasts'] = pool['forecasts']
        dispositions[rule] = admission(state,candidate,now, directional_baseline=rule in ('always_long','always_short'))
    record['comparison_dispositions'] = dispositions
    db.execute('INSERT OR REPLACE INTO comparison_cohorts VALUES (?,?)',(ident,json.dumps(cohort,allow_nan=False)))


@lru_cache(maxsize=128)
def expected_closes(day):
    start = datetime.combine(datetime.fromisoformat(day).date(),time(9,35),ET)
    candidates = [start+i*BAR for i in range(72)]
    return tuple(t.astimezone(timezone.utc).isoformat() for t in candidates if entry_boundary_reason(t) is None)


def collection_coverage(records, *, first_record, now, window_start, scan_limited):
    """Describe observed coverage, without treating time before collection as data.

    These are scheduled research-window closes, including HOLDs, not independent
    forecasts or complete outcome sessions. Allow the collector two minutes to
    finish the latest close, as the collection-health checker does.
    """
    first_close = datetime.fromisoformat(first_record['close']) if first_record else None
    start = max(window_start, first_close) if first_close else now
    due_through = now - timedelta(minutes=2)
    closes = Counter(datetime.fromisoformat(r['close']) for r in records)
    sessions = []
    day = start.astimezone(ET).date()
    while first_close and day <= now.astimezone(ET).date():
        scheduled = [datetime.fromisoformat(t) for t in expected_closes(day.isoformat())]
        due = {t for t in scheduled if start <= t <= due_through}
        if scheduled and due:
            seen = due & closes.keys()
            failures = sum(r['stage'] in ('stale_candles', 'inputs_or_model_unavailable')
                           for r in records if datetime.fromisoformat(r['close']) in due)
            duplicates = sum(closes[t]-1 for t in seen)
            partial = scheduled[0] < start
            unfinished = scheduled[-1] > due_through
            sessions.append(dict(day=day.isoformat(), scheduled=len(scheduled), due=len(due),
                                 recorded=len(seen), missing=len(due-seen), quality_failures=failures, duplicates=duplicates,
                                 partial_window=partial, unfinished=unfinished,
                                 complete=not (partial or unfinished or failures or scan_limited
                                               or duplicates)
                                          and len(seen) == len(scheduled)))
        day += timedelta(days=1)
    forecasts = [r for r in records if r['forecast'].get('forecasts')]
    scenarios = [r for r in records if r.get('volatility_counterfactual')]
    comparisons = [r for r in records if r.get('comparison_cohort')]
    return dict(collection_started_at=first_record['at'] if first_record else None,
                requested_start=window_start.isoformat(), requested_end=now.isoformat(),
                first_recorded_at=min((r['at'] for r in records), default=None),
                last_recorded_at=max((r['at'] for r in records), default=None),
                started_after_requested_window=bool(first_record and datetime.fromisoformat(first_record['at']) > window_start),
                scan_limited=scan_limited, sessions=sessions,
                complete_evaluation_sessions=sum(s['complete'] for s in sessions),
                missing_due_closes=sum(s['missing'] for s in sessions),
                duplicate_closes=sum(n-1 for n in closes.values()),
                legacy_forecasts_without_delayed_entry=sum(r.get('evaluation_version') != 2 for r in forecasts),
                volatility_without_scenario=sum(r['stage']=='volatility_risk_cap' and not r.get('volatility_counterfactual') for r in records),
                first_scenario_at=min((r['at'] for r in scenarios), default=None),
                comparison_evaluations=len(comparisons))


def paired_uncertainty(differences):
    values = np.array(differences,dtype=float)
    if len(values) < MIN_SESSIONS or not np.isfinite(values).all() or np.ptp(values)==0:
        return None
    bounds = []
    # Paired daily differences retain zero-trade sessions; blocks retain serial dependence.
    # Four comparisons share a conservative Bonferroni-adjusted 95% family envelope.
    for block in (5,10):
        rng = np.random.default_rng(20261022+block)
        starts = rng.integers(0,len(values)-block+1,size=(BOOTSTRAP_REPLICATES,math.ceil(len(values)/block)))
        indices = (starts[:,:,None]+np.arange(block)).reshape(BOOTSTRAP_REPLICATES,-1)[:,:len(values)]
        draws = values[indices].mean(axis=1)
        bounds.append(np.quantile(draws,[.05/(2*4),1-.05/(2*4)]))
    return dict(lower_usd=float(min(b[0] for b in bounds)), upper_usd=float(max(b[1] for b in bounds)),
                mean_usd=float(values.mean()), replicates=BOOTSTRAP_REPLICATES,
                degenerate=bool(np.ptp(values)==0))


def comparison_report(db, records, now, scan_limited, window_start=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='comparison_cohorts'").fetchone():
        return dict(policy=POLICY, cohorts=[], minimum_sessions=MIN_SESSIONS)
    cohorts=[]
    for ident, payload in db.execute('SELECT id,payload FROM comparison_cohorts'):
        selected=[r for r in records if r.get('comparison_cohort')==ident]
        cohort=json.loads(payload)
        began=datetime.fromisoformat(cohort['started_at'])
        period_start=max(began,window_start) if window_start else began
        period_end=min(now,datetime.fromisoformat(cohort['ended_at'])) if cohort.get('ended_at') else now
        if period_end < period_start: continue
        by_day={}
        for r in selected:
            day=datetime.fromisoformat(r['close']).astimezone(ET).date().isoformat()
            by_day.setdefault(day,[]).append(r)
        day=period_start.astimezone(ET).date()
        while day<=period_end.astimezone(ET).date():
            by_day.setdefault(day.isoformat(),[])
            day+=timedelta(days=1)
        sessions=[]
        usable=[]
        for day, daily in sorted(by_day.items()):
            expected=set(expected_closes(day))
            seen={r['close'] for r in daily} & expected
            unfinished=bool(expected) and now < max(datetime.fromisoformat(t) for t in expected)+3*BAR+timedelta(minutes=2)
            # Starting a comparison partway through a session is not a data outage.
            start_slot=datetime.fromtimestamp(math.floor(period_start.timestamp()/300)*300,timezone.utc)
            partial_window=bool(expected) and (min(datetime.fromisoformat(t) for t in expected) < start_slot
                                               or max(datetime.fromisoformat(t) for t in expected) > period_end)
            bad=sum(r['stage'] in ('inputs_or_model_unavailable','stale_candles') or r['outcome_status'].startswith('unknown')
                    or r['outcome_status']=='pending' or r.get('comparison_dispositions',{}).get('empirical_pool')=='pool_forecast_unavailable'
                    or (r.get('delayed_outcomes') or {}).get('status') in ('unknown_missing_path','entry_too_late') for r in daily)
            usable_day=bool(expected) and seen==expected and not unfinished and not bad and not partial_window
            sessions.append(dict(day=day, expected=len(expected), recorded=len(seen), missing=len(expected-seen),
                                 unfinished=unfinished, partial_window=partial_window, quality_failures=bad, usable=usable_day))
            if usable_day: usable.append(day)
        blocked=any(s['blocked_reason'] for s in cohort['accounts'].values())
        coverage_gaps=any(s['expected'] and not s['unfinished'] and not s['partial_window'] and not s['usable'] for s in sessions)
        # Daily results include every no-trade day; P&L is from entry-day complete portfolios.
        daily_net={rule:{day:state['daily'].get(day,{}).get('net_usd',0.) for day in usable} for rule,state in cohort['accounts'].items()}
        comparisons=[]
        for rule in RULES[1:]:
            differences=[daily_net['topbot'][day]-daily_net[rule][day] for day in usable]
            interval=None if blocked or scan_limited or coverage_gaps else paired_uncertainty(differences)
            comparisons.append(dict(rule=rule, mean_daily_advantage_usd=float(np.mean(differences)) if differences else None,
                                    interval=interval, status='unknown_exposure' if blocked else 'report_truncated' if scan_limited else
                                    'coverage_gaps' if coverage_gaps else
                                    'insufficient_complete_sessions' if len(usable)<MIN_SESSIONS else
                                    'no_observed_variation' if interval is None else 'descriptive_only'))
        accounts={rule:dict(net_usd=state['net_usd'],trades=state['trades'],position=state['position'],
                            blocked_reason=state['blocked_reason'],closed_drawdown_usd=state['closed_drawdown_usd']) for rule,state in cohort['accounts'].items()}
        cohorts.append(dict(id=ident,artifact=cohort['artifact'],started_at=cohort['started_at'],accounts=accounts,
                            sessions=sessions,complete_sessions=len(usable),comparisons=comparisons,
                            dispositions={rule:dict(Counter(r.get('comparison_dispositions',{}).get(rule,'no_forecast') for r in selected)) for rule in RULES}))
    return dict(policy=POLICY,cohorts=cohorts,minimum_sessions=MIN_SESSIONS,
                method='Paired session net differences; 20,000 moving-block replicates (5/10 sessions), conservative envelope, four-comparison adjustment. Descriptive, not validation.')
