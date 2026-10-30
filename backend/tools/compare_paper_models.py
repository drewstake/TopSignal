"""Frozen offline comparison of existing model weights under paper-v2 timing."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import gzip
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def training_for_fold(samples, fold, first_decision, *, limit=4096):
    days = set(fold['training'])
    validation = [fold['validation']] if isinstance(fold['validation'], str) else fold['validation']
    if days.intersection(validation):
        raise ValueError('Overlapping training and validation dates')
    chosen = tuple(s for s in samples if s.features.day in days)[-limit:]
    if len(chosen) < 300 or any(s.label_end > first_decision-timedelta(minutes=15) for s in chosen):
        raise ValueError('Insufficient training or label embargo violation')
    return chosen


def run(plan_path, output, cache_root):
    from research_probabilistic_topbot import offline_guard
    offline_guard()
    import numpy as np
    from app.services.probabilistic_protocol import digest, implementation_sha, protocol
    from app.services.probabilistic_strategy import Candle, Costs, PathModel, ET, make_samples, price_paths, weighted_crps
    from app.services.probabilistic_validation import block_indices, simulate, summarize, paired_daily_bound

    plan = json.loads(plan_path.read_text())
    receipt = json.loads(plan_path.with_name('comparison-plan-receipt.json').read_text())
    if file_hash(plan_path) != receipt['sha256']:
        raise ValueError('Frozen plan receipt mismatch')
    if (plan['implementation_sha256'] != implementation_sha() or plan['protocol_sha256'] != digest(protocol())
            or plan['models'] != ['bayesian_cells_v1', 'kernel_paths_v1', 'empirical_pool_v1']
            or plan['pool_mix'] != 0 or plan['path_limit'] != 4096 or plan['embargo_minutes'] != 15
            or plan['payoff_semantics'] != 'post_record_open_fixed_deadline_v2'
            or plan['stop_policy'] != 'observation_volatility_uncapped_v1' or plan['promotion_allowed'] is not False):
        raise ValueError('Unexpected comparison specification')
    source = Path(plan['source']).resolve()
    if file_hash(source) != plan['source_sha256']:
        raise ValueError('Frozen input changed')
    if not output.resolve().is_relative_to(cache_root.resolve() / 'audits') or output.exists():
        raise ValueError('Output must be a new directory under cache/audits')
    rows = []
    with gzip.open(source, 'rt', encoding='utf-8') as stream:
        for line in stream:
            item = json.loads(line)
            item['timestamp'] = datetime.fromisoformat(item['timestamp'])
            rows.append(Candle(**item))
    samples, rejections = make_samples(rows, stride=1, stop_policy=plan['stop_policy'], payoff_semantics=plan['payoff_semantics'])
    train_samples = [s for s in samples if s.features.decision_at.astimezone(ET).minute % 15 == 0]
    days = plan['chronological_validation']['sessions']
    by_day = defaultdict(list)
    for s in samples:
        by_day[s.features.day].append(s)
    forecasts, scores, ledgers, folds = defaultdict(list), defaultdict(list), defaultdict(list), []
    costs = Costs()
    output.mkdir(parents=True, exist_ok=False)
    (output/'execution.json').write_text(json.dumps(dict(started_at=datetime.now(timezone.utc).isoformat(), plan_sha256=file_hash(plan_path), runner_sha256=file_hash(__file__), implementation_sha256=implementation_sha(), external_network='blocked', production_writes=False), indent=2))

    class CachedModel:
        def __init__(self, model, predictions):
            self.samples, self.payoff_semantics = model.samples, model.payoff_semantics
            self.predictions = predictions
        def forecast(self, f, costs):
            return self.predictions[f.decision_at]

    for fold in plan['chronological_validation']['folds']:
        validation = [fold['validation']] if isinstance(fold['validation'], str) else fold['validation']
        day = validation[0]
        test = by_day[day]
        if not test or len(validation) != 1:
            raise ValueError('Expected a complete single-day evaluation fold')
        training = training_for_fold(train_samples, fold, test[0].features.decision_at)
        fold_summary = dict(day=day, training_first=fold['training'][0], training_last=fold['training'][-1], paths=len(training), trained_through=max(s.label_end for s in training).isoformat(), models={})
        for name in plan['models']:
            model = PathModel(name, training, pool_mix=0, stop_policy=plan['stop_policy'], payoff_semantics=plan['payoff_semantics'])
            predictions = {s.features.decision_at: model.forecast(s.features) for s in test}
            ledgers[name].extend(simulate(CachedModel(model, predictions), test, costs, [day]))
            if name == 'bayesian_cells_v1':
                for rule in ('flat', 'always_long', 'always_short'):
                    ledgers[rule].extend(simulate(model, test, costs, [day], rule=rule))
            for s in test:
                f = s.features
                prediction = predictions[f.decision_at]
                forecasts[name].append(dict(at=f.decision_at.isoformat(), day=day, action=prediction['research_action'], forecasts=prediction['forecasts'], stop_points=f.stop_points))
                if f.decision_at.astimezone(ET).minute % 15:
                    continue
                weights = model.weights(f)
                for side, sign in (('BUY', 1), ('SELL', -1)):
                    values, _, _ = price_paths(model.paths, stop=f.stop_points, scale=f.volatility*math.sqrt(3), side=sign, costs=costs, entry_offset_bars=1)
                    actual, _, _ = price_paths(np.asarray([s.path]), stop=f.stop_points, scale=f.volatility*math.sqrt(3), side=sign, costs=costs, entry_offset_bars=1)
                    observed = float(actual[0])
                    probability = prediction['forecasts'][side]['probability_net_positive']
                    outcome = int(observed > 0)
                    scores[name].append(dict(at=f.decision_at.isoformat(), day=day, side=side, probability=probability, observed_net_usd=observed,
                        brier=(probability-outcome)**2, log_loss=-math.log(max(1e-12, probability if outcome else 1-probability)), crps_usd=weighted_crps(values, weights, observed)))
            fold_summary['models'][name] = dict(model_sha256=digest(model.to_dict()), signals=sum(p['research_action'] != 'NO_TRADE' for p in predictions.values()))
        folds.append(fold_summary)
        print(f"Compared session {len(folds)}/{len(days)}: {day}", flush=True)

    def score_summary(values):
        bins = []
        for i in range(10):
            group = [r for r in values if min(9, int(r['probability']*10)) == i]
            if group:
                bins.append(dict(lower=i/10, n=len(group), predicted=float(np.mean([r['probability'] for r in group])), observed=float(np.mean([r['observed_net_usd'] > 0 for r in group]))))
        return dict(predictions=len(values), **{metric:float(np.mean([r[metric] for r in values])) for metric in ('brier', 'log_loss', 'crps_usd')}, reliability=bins)

    paired = {}
    for name in plan['models'][:-1]:
        paired[name] = {}
        for side in ('BUY', 'SELL'):
            own = [r for r in scores[name] if r['side'] == side]
            pool = [r for r in scores['empirical_pool_v1'] if r['side'] == side]
            assert [(r['at'], r['side']) for r in own] == [(r['at'], r['side']) for r in pool]
            paired[name][side] = {}
            for metric in ('brier', 'crps_usd'):
                daily = np.array([np.mean([a[metric]-b[metric] for a,b in zip(own,pool) if a['day'] == day]) for day in days])
                paired[name][side][metric] = dict(mean_difference=float(np.mean(daily)), intervals={str(length):np.quantile(daily[block_indices(len(days), length)].mean(axis=1), [.05/12, 1-.05/12]).tolist() for length in (5, 10)})
    performance = {name: summarize(values, days) for name,values in ledgers.items()}
    results = dict(completed_at=datetime.now(timezone.utc).isoformat(), plan_sha256=file_hash(plan_path), source_sha256=file_hash(source), implementation_sha256=implementation_sha(),
        mode='previously_inspected_development_data', installed=False, promotion_eligible=False, sessions=len(days), first=days[0], last=days[-1],
        costs=costs.describe(), training_policy='Daily preceding 240-session fit, last 4096 stride-three paths, 15-minute embargo; fixed mixture 0',
        models={name:dict(forecasts=len(forecasts[name]), signals=dict(Counter(r['action'] for r in forecasts[name])),
                         best_entry_score=max(s['lower_utility_usd'] for r in forecasts[name] for s in r['forecasts'].values()),
                         scores={side:score_summary([r for r in scores[name] if r['side']==side]) for side in ('BUY','SELL')}, performance=performance[name],
                         paired_daily_pnl_lower_bounds_vs_flat=paired_daily_bound(performance[name], {}, days)) for name in plan['models']},
        baselines={name:performance[name] for name in ('flat','always_long','always_short')}, paired_score_differences_vs_pool=paired, folds=folds, label_rejections=rejections,
        limitations=['Previously inspected development data, not an untouched holdout.', 'No automatic model selection, installation or routing.', 'BUY and SELL are scored separately and are not independent evidence.', 'No positive expectancy can be inferred from abstention. Full promotion checks and prospective evidence remain required.'])
    for name,value in (('results.json',results), ('scores.json',scores), ('ledgers.json',ledgers)):
        with (output/name).open('x',encoding='utf-8') as stream:
            json.dump(value,stream,indent=2,allow_nan=False)
    with gzip.open(output/'forecasts.jsonl.gz','xt',encoding='utf-8') as stream:
        for name,values in forecasts.items():
            for row in values:
                stream.write(json.dumps(dict(model=name, **row),allow_nan=False)+'\n')
    assert file_hash(source) == plan['source_sha256'] and file_hash(plan_path) == receipt['sha256']
    print(json.dumps({name:{'forecasts':r['forecasts'],'signals':r['signals'],'trades':r['performance']['trades'],'net_usd':r['performance']['net_usd']} for name,r in results['models'].items()}),flush=True)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--cache-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.resolve(), args.output.resolve(), args.cache_root.resolve())
