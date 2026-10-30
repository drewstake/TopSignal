"""Frozen, offline uncertainty ablations and chronological mean calibration.

This is an experiment runner, not a production model or installation command.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import gzip
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

POLICIES = ('registered', 'two_action_t95', 'one_se_diagnostic',
            'cost_buffer_only_diagnostic', 'zero_penalty_diagnostic',
            'block_joint95', 'block_daily95', 'rolling_mean_calibration95')
SIDES = ('BUY', 'SELL')


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def block_counts(n, length, repeats=2000, seed=20261030):
    import numpy as np
    from app.services.probabilistic_validation import block_indices
    indexes = block_indices(n, length, repeats=repeats, seed=seed)
    counts = np.zeros((repeats, n))
    np.add.at(counts, (np.arange(repeats)[:, None], indexes), 1)
    return counts


def bootstrap_penalties(day_sums, day_weights, means, ses, *, repeats=20000, seed=20261030):
    """Ratio bootstrap with the same day blocks for all queries and both actions.

    Columns are [query0 BUY, query0 SELL, query1 BUY, ...]. This estimates
    sampling error of a weighted mean, not the spread of individual payoffs.
    Each query is independent of future query columns. The daily policy uses
    a fixed .05/72 budget, not an ex-post maximum over future query features.
    """
    import numpy as np
    day_sums, day_weights, means, ses = map(np.asarray, (day_sums, day_weights, means, ses))
    if (day_sums.shape != day_weights.shape or day_sums.ndim != 2
            or day_sums.shape[1] != len(means) or len(means) != len(ses)
            or len(means) % 2 or len(means) > 144 or np.any(ses <= 0) or np.any(day_weights < 0)
            or not all(np.isfinite(a).all() for a in (day_sums, day_weights, means, ses))):
        raise ValueError('Invalid bootstrap sufficient statistics')
    pointwise = np.zeros(len(means))
    simultaneous = np.zeros(len(means))
    criticals = {}
    for length in (5, 10):
        counts = block_counts(len(day_sums), length, repeats, seed)
        denominator = counts @ day_weights
        if np.any(denominator <= 0):
            raise ValueError('Bootstrap resample has no model support')
        bootstrap_means = (counts @ day_sums) / denominator
        errors = (bootstrap_means - means) / ses
        pair_max = errors.reshape(repeats, -1, 2).max(axis=2)
        joint = np.maximum(0, np.quantile(pair_max, .95, axis=0)).repeat(2)
        daily = np.maximum(0, np.quantile(pair_max, 1-.05/72, axis=0)).repeat(2)
        pointwise = np.maximum(pointwise, joint * ses)
        simultaneous = np.maximum(simultaneous, daily * ses)
        criticals[str(length)] = dict(joint_min=float(joint.min()), joint_median=float(np.median(joint)),
                                      joint_max=float(joint.max()), daily_min=float(daily.min()),
                                      daily_median=float(np.median(daily)), daily_max=float(daily.max()))
    return pointwise + 1., simultaneous + 1., criticals


def fit_affine(stats):
    """Day-balanced least squares, with a predeclared slope constrained to [0,1]."""
    import numpy as np
    stats = np.asarray(stats)
    x, y, xx, xy = (stats[..., i] for i in range(4))
    slope = np.clip((xy-x*y) / np.maximum(xx-x*x, 1e-12), 0., 1.)
    return y-slope*x, slope


def prior_calibration(rows, day, *, repeats=2000, seed=20261030):
    """Completed, prior-day OOS forecasts only, on the nonoverlapping lattice."""
    import numpy as np
    past = [r for r in rows if r['day'] < day and r['lattice']]
    days = sorted({r['day'] for r in past})
    if len(days) < 20:
        raise ValueError('Need 20 completed prior out-of-sample calibration days')
    result = {}
    for side in SIDES:
        stats = []
        for prior in days:
            group = [r for r in past if r['day'] == prior and r['side'] == side]
            if not group:
                raise ValueError('Incomplete calibration side/day')
            x = np.array([r['mean']/r['risk'] for r in group])
            y = np.array([r['actual']/r['risk'] for r in group])
            stats.append([x.mean(), y.mean(), (x*x).mean(), (x*y).mean()])
        stats = np.asarray(stats)
        intercept, slope = fit_affine(stats.mean(axis=0))
        boot = {str(length): fit_affine(block_counts(len(days), length, repeats, seed) @ stats / len(days))
                for length in (5, 10)}
        result[side] = dict(intercept=float(intercept), slope=float(slope), bootstrap=boot)
    return dict(first_day=days[0], last_day=days[-1], days=len(days), sides=result)


def calibrated_bounds(calibration, raw_sides, risk):
    import numpy as np
    from scipy.stats import t
    means, bounds = {}, {}
    for side in SIDES:
        raw, fitted = raw_sides[side], calibration['sides'][side]
        x = raw['expected_net_usd']/risk
        means[side] = risk * (fitted['intercept'] + fitted['slope']*x)
        # Allocate .05 across two actions and two sources of estimation error.
        # These are approximate bootstrap/model intervals, not exact guarantees.
        lower = min(float(np.quantile(a+b*x, .0125)) for a,b in fitted['bootstrap'].values())
        raw_deduction = fitted['slope'] * t.ppf(.9875, max(1, raw['effective_days']-1)) * raw['standard_error_usd']
        bounds[side] = risk*lower - raw_deduction - 1.
    return means, bounds


def decision(raw_sides, policy, *, paths, extra_penalties=None, calibration=None, risk=None):
    from scipy.stats import t
    if policy not in POLICIES:
        raise ValueError('Unregistered policy')
    if policy == 'rolling_mean_calibration95':
        means, scores = calibrated_bounds(calibration, raw_sides, risk)
    else:
        means, scores = {}, {}
        for side in SIDES:
            raw = raw_sides[side]
            se, days, mean = raw['standard_error_usd'], raw['effective_days'], raw['expected_net_usd']
            if not all(math.isfinite(v) for v in (se, days, mean)) or se < 0 or days <= 0:
                raise ValueError('Invalid forecast')
            penalty = (raw['uncertainty_penalty_usd'] if policy == 'registered' else
                       float(t.ppf(.975, max(1, days-1)))*se+1 if policy == 'two_action_t95' else
                       se+1 if policy == 'one_se_diagnostic' else
                       1. if policy == 'cost_buffer_only_diagnostic' else
                       0. if policy == 'zero_penalty_diagnostic' else extra_penalties[side])
            means[side], scores[side] = mean, mean-penalty
    if not all(math.isfinite(v) for v in (*means.values(), *scores.values())):
        raise ValueError('Nonfinite calibrated decision')
    best = max(SIDES, key=lambda side: scores[side])
    supported = paths >= 300 and all(raw_sides[side]['effective_days'] >= 20 for side in SIDES)
    action = best if supported and scores[best] > 1 else 'NO_TRADE'
    return dict(research_action=action, action='NO_TRADE', routing_allowed=False,
                adjusted_means=means, entry_scores=scores)


class FrozenPolicy:
    def __init__(self, model, predictions):
        self.samples, self.payoff_semantics = model.samples, model.payoff_semantics
        self.predictions = predictions

    def forecast(self, features, costs):
        return self.predictions[features.decision_at]


def performance(ledger, days, *, configurations=24):
    import numpy as np
    from app.services.probabilistic_validation import summarize, block_indices
    alpha = .05/configurations
    result = summarize(ledger, days, alpha=alpha)
    result['multiplicity_configurations'] = configurations
    daily = np.array([r['net_usd'] for r in result['daily']])
    result['mean_daily_pnl_interval'] = {str(length): np.quantile(daily[block_indices(len(days), length)].mean(axis=1), [alpha/2, 1-alpha/2]).tolist()
                                         for length in (5, 10)}
    result['twenty_session_halves_net_usd'] = [float(daily[:20].sum()), float(daily[20:].sum())]
    return result


def mean_diagnostics(rows, days, field):
    import numpy as np
    from app.services.probabilistic_validation import block_indices
    result = {}
    for side in SIDES:
        group = [r for r in rows if r['side'] == side and r['lattice'] and r['day'] in days]
        errors = np.array([r[field]-r['actual'] for r in group])
        daily = np.array([np.mean([r[field]-r['actual'] for r in group if r['day'] == day]) for day in days])
        result[side] = dict(predictions=len(group), mean_forecast_usd=float(np.mean([r[field] for r in group])),
                            mean_actual_usd=float(np.mean([r['actual'] for r in group])), mean_overprediction_usd=float(errors.mean()),
                            rmse_usd=float(np.sqrt(np.mean(errors**2))),
                            normalized_mse=float(np.mean([(e/r['risk'])**2 for e,r in zip(errors,group)])),
                            overprediction_descriptive_95_intervals={str(length):np.quantile(daily[block_indices(len(days),length)].mean(axis=1), [.025,.975]).tolist() for length in (5,10)})
    return result


def run(plan_path, output, cache_root):
    from research_probabilistic_topbot import offline_guard
    offline_guard()
    import numpy as np
    from compare_paper_models import training_for_fold
    from app.services.probabilistic_protocol import implementation_sha, digest, protocol
    from app.services.probabilistic_strategy import Candle, Costs, PathModel, ET, make_samples, price_paths
    from app.services.probabilistic_validation import simulate

    plan = json.loads(plan_path.read_text())
    receipt = json.loads(plan_path.with_name('plan-receipt.json').read_text())
    if (file_hash(plan_path) != receipt['sha256'] or plan['experiment'] != 'topbot-uncertainty-calibration-v2'
            or plan['promotion_allowed'] is not False or tuple(plan['policies']) != POLICIES
            or plan['implementation_sha256'] != implementation_sha() or plan['protocol_sha256'] != digest(protocol())
            or plan['bootstrap'] != dict(replicates=20000,block_lengths=[5,10],seed=20261030,method='Non-circular moving day blocks; shared resamples across queries and sides; use larger uncertainty across both lengths; daily policy budgets .05/72 per query and never uses later queries.')
            or plan['calibration_bootstrap_replicates'] != 2000
            or plan['warmup_sessions'] != 20 or plan['inference']['configurations'] != 24):
        raise ValueError('Frozen experiment specification mismatch')
    for name in ('source', 'forecasts', 'comparison_plan'):
        if file_hash(plan[name]) != plan[name+'_sha256']:
            raise ValueError('Frozen input changed: '+name)
    previous = json.loads(Path(plan['comparison_plan']).read_text())
    all_days = previous['chronological_validation']['sessions']
    days = all_days[20:]
    if days != plan['primary_sessions'] or len(days) != 40 or plan['models'] != previous['models']:
        raise ValueError('Unexpected primary evaluation period')
    if output.exists() or not output.resolve().is_relative_to(cache_root.resolve()/'audits'):
        raise ValueError('Output must be a new directory under cache/audits')
    output.mkdir(parents=True)
    (output/'execution.json').write_text(json.dumps(dict(started_at=datetime.now(timezone.utc).isoformat(),plan_sha256=file_hash(plan_path),runner_sha256=file_hash(__file__),external_network='blocked',production_writes=False),indent=2))
    with gzip.open(plan['source'], 'rt') as f:
        candles=[]
        for line in f:
            row=json.loads(line);row['timestamp']=datetime.fromisoformat(row['timestamp']);candles.append(Candle(**row))
    samples, rejections=make_samples(candles,stride=1,stop_policy=previous['stop_policy'],payoff_semantics=previous['payoff_semantics'])
    by_day=defaultdict(list)
    for s in samples:by_day[s.features.day].append(s)
    training_samples=[s for s in samples if s.features.decision_at.astimezone(ET).minute%15==0]
    frozen=defaultdict(dict)
    with gzip.open(plan['forecasts'],'rt') as f:
        for line in f:
            row=json.loads(line);frozen[row['model']][datetime.fromisoformat(row['at'])]=row
    costs=Costs()
    stresses={'spread_2_ticks':(Costs(spread_ticks=2),0),'spread_4_ticks':(Costs(spread_ticks=4),0),
              'slippage_2_ticks':(Costs(slippage_ticks=2),0),'slippage_4_ticks':(Costs(slippage_ticks=4),0),
              'target_trade_through_1_tick':(costs,1)}
    histories=defaultdict(list);ledgers=defaultdict(list);stress_ledgers=defaultdict(lambda:defaultdict(list))
    decisions=defaultdict(list);folds=[];calibrated_rows=defaultdict(list)
    max_mean_difference=0.
    for index, fold in enumerate(previous['chronological_validation']['folds']):
        day=fold['validation'] if isinstance(fold['validation'],str) else fold['validation'][0]
        test=by_day[day]
        if set(s.features.decision_at for s in test) != set(at for at,r in frozen[plan['models'][0]].items() if r['day']==day):
            raise ValueError('Frozen forecast/sample mismatch')
        training=training_for_fold(training_samples,fold,test[0].features.decision_at)
        fold_report=dict(day=day,primary=day in days,models={})
        actuals={}
        for s in test:
            f=s.features
            actuals[f.decision_at]={side:float(price_paths(np.asarray([s.path]),stop=f.stop_points,scale=f.volatility*math.sqrt(3),side=sign,costs=costs,entry_offset_bars=1)[0][0]) for side,sign in (('BUY',1),('SELL',-1))}
        for name in plan['models']:
            model=PathModel(name,training,pool_mix=0,stop_policy=previous['stop_policy'],payoff_semantics=previous['payoff_semantics'])
            if day in days:
                calibration=prior_calibration(histories[name],day)
                if calibration['last_day'] >= day:raise ValueError('Calibration leakage')
                sums=[];weights_by_day=[];means=[];ses=[]
                for s in test:
                    f=s.features;raw=frozen[name][f.decision_at]['forecasts'];weights=model.weights(f)
                    for side,sign in (('BUY',1),('SELL',-1)):
                        payoffs=price_paths(model.paths,stop=f.stop_points,scale=f.volatility*math.sqrt(3),side=sign,costs=costs,entry_offset_bars=1)[0]
                        mean=float(weights@payoffs)
                        difference=abs(mean-raw[side]['expected_net_usd']);max_mean_difference=max(max_mean_difference,difference)
                        if difference>1e-9:raise ValueError('Frozen forecast mean drift')
                        sums.append(np.bincount(model._day_codes,weights=weights*payoffs))
                        weights_by_day.append(np.bincount(model._day_codes,weights=weights))
                        means.append(mean);ses.append(raw[side]['standard_error_usd'])
                joint,daily,criticals=bootstrap_penalties(np.array(sums).T,np.array(weights_by_day).T,np.array(means),np.array(ses))
                predictions={policy:{} for policy in POLICIES}
                for j,s in enumerate(test):
                    at=s.features.decision_at;raw=frozen[name][at]
                    for policy in POLICIES:
                        extra={side:float((joint if policy=='block_joint95' else daily)[2*j+k]) for k,side in enumerate(SIDES)}
                        prediction=decision(raw['forecasts'],policy,paths=len(training),extra_penalties=extra,calibration=calibration,risk=2*s.features.stop_points)
                        if policy=='registered' and prediction['research_action']!=raw['action']:raise ValueError('Control decision mismatch')
                        predictions[policy][at]=prediction
                        decisions[name+'/'+policy].append(dict(at=at.isoformat(),day=day,**prediction))
                    for side in SIDES:
                        calibrated_rows[name].append(dict(day=day,side=side,lattice=at.astimezone(ET).minute%15==0,risk=2*s.features.stop_points,
                            mean=raw['forecasts'][side]['expected_net_usd'],actual=actuals[at][side],calibrated=predictions['rolling_mean_calibration95'][at]['adjusted_means'][side]))
                for policy in POLICIES:
                    key=name+'/'+policy;wrapper=FrozenPolicy(model,predictions[policy])
                    ledgers[key].extend(simulate(wrapper,test,costs,[day]))
                    for stress,(stress_costs,ticks) in stresses.items():
                        stress_ledgers[key][stress].extend(simulate(wrapper,test,stress_costs,[day],trade_through_ticks=ticks))
                fold_report['models'][name]=dict(bootstrap_criticals=criticals,calibration={k:v for k,v in calibration.items() if k!='sides'},
                    coefficients={side:{k:v for k,v in calibration['sides'][side].items() if k!='bootstrap'} for side in SIDES})
            # Reveal this day's labels only after every prediction for that model/day.
            for s in test:
                at=s.features.decision_at
                for side in SIDES:
                    histories[name].append(dict(day=day,side=side,lattice=at.astimezone(ET).minute%15==0,risk=2*s.features.stop_points,
                        mean=frozen[name][at]['forecasts'][side]['expected_net_usd'],actual=actuals[at][side]))
        folds.append(fold_report)
        print(f'Completed {index+1}/60: {day} ({"primary" if day in days else "calibration warmup"})',flush=True)
    results={}
    for key,ledger in ledgers.items():
        perf=performance(ledger,days)
        stress={name:dict(trades=len(values),net_usd=sum(r['net_usd'] for r in values)) for name,values in stress_ledgers[key].items()}
        gates=dict(minimum_200_trades=perf['trades']>=200,positive_corrected_daily_lower_bounds=all(x[0]>0 for x in perf['mean_daily_pnl_interval'].values()),
            positive_without_best_five_days=(perf['net_without_best_five_days_usd'] or 0)>0,
            positive_without_best_five_trades=(perf['net_without_best_five_trades_usd'] or 0)>0,
            positive_both_halves=all(x>0 for x in perf['twenty_session_halves_net_usd']),positive_all_stresses=all(v['net_usd']>0 for v in stress.values()),
            drawdown_below_1000=perf['closed_equity_max_drawdown_usd']<=1000,
            expected_shortfall_at_most_100=perf['expected_shortfall_95_usd'] is not None and perf['expected_shortfall_95_usd']<=100)
        results[key]=dict(signals=sum(r['research_action']!='NO_TRADE' for r in decisions[key]),performance=perf,stresses=stress,gates=gates,
                          research_readiness_pass=all(gates.values()),installed=False)
    out=dict(completed_at=datetime.now(timezone.utc).isoformat(),plan_sha256=file_hash(plan_path),implementation_sha256=implementation_sha(),
        mode=plan['mode'],installed=False,promotion_eligible=False,primary_days=days,calibration_warmup_days=all_days[:20],
        frozen_mean_max_difference=max_mean_difference,results=results,
        mean_calibration={name:dict(raw=mean_diagnostics(rows,days,'mean'),calibrated=mean_diagnostics(rows,days,'calibrated')) for name,rows in calibrated_rows.items()},
        label_rejections=rejections,folds=folds,
        limitations=['Previously inspected development history; not an untouched holdout.',
          'Conditional mean coverage cannot be measured from a single realized payoff. Bootstrap estimates assume weakly dependent, locally representative days.',
          '99% registered eight-test rule and alternative 95% policies have different confidence scopes. Joint95 is per query; daily95 uses a fixed nominal budget over72 queries for that model, not future sessions or all research. Bootstrap tail precision is limited.',
          'Rolling mean calibration has as few as20 prior days; constrained slopes and bootstrap intervals do not establish conditional or distribution-free validity.',
          'Inference corrects24 configurations in this run only, not historical research. Forty primary sessions give limited tail precision.',
          'Execution stresses retain the base decision policy; they change fills, exits, costs and resulting position/risk availability.',
          'Closed-equity drawdown excludes intratrade mark-to-market. No broker/model/configuration writes.'])
    for name,value in [('results.json',out),('ledgers.json',ledgers),('stress-ledgers.json',stress_ledgers),('mean-calibration-rows.json',calibrated_rows)]:
        with (output/name).open('x') as f:json.dump(value,f,indent=2,allow_nan=False)
    with gzip.open(output/'decisions.jsonl.gz','xt') as f:
        for key,rows in decisions.items():
            for row in rows:f.write(json.dumps(dict(candidate=key,**row),allow_nan=False)+'\n')
    assert file_hash(plan_path)==receipt['sha256']
    for name in ('source','forecasts','comparison_plan'):assert file_hash(plan[name])==plan[name+'_sha256']
    print(json.dumps({key:dict(trades=v['performance']['trades'],net_usd=round(v['performance']['net_usd'],2),ready=v['research_readiness_pass']) for key,v in results.items()}),flush=True)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--cache-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.plan.resolve(),args.output.resolve(),args.cache_root.resolve())
