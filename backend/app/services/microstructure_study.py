"""Deterministic research statistics, episode controls, and conservative simulation."""
from bisect import bisect_left
from collections import Counter, defaultdict
import math
import random
from statistics import mean, median

ENGINE_VERSION = 'microstructure-study-v1.2'


def usable(p):
    return not p['quality_flags'] and p.get('mid_price') is not None and p.get('spread', 0) > 0


def same(a, b):
    return all(a.get(k) == b.get(k) for k in ('capture_id', 'generation', 'contract_id', 'reconstruction_id'))


def continuous(a, b):
    return usable(a) and usable(b) and same(a, b) and 0 < b['time']-a['time'] <= 2


def partition(p, config):
    if not config.validation_session:
        return 'exploration'
    return 'training' if p['session'] < str(config.validation_session) else 'validation' if p['session'] < str(config.test_session) else 'test'


def feature(p, name, config):
    return p.get(name) if name in p else p.get('activity', {}).get(str(config.activity_window), {}).get(name)


def condition(p, config):
    if not usable(p):
        return False
    values = []
    for c in config.conditions:
        v = feature(p, c.feature, config)
        values.append(None if v is None else v <= c.threshold if c.operator == 'lte' else v >= c.threshold)
    # Unknown inputs never turn a multi-condition setup into evidence.
    return False if None in values else all(values) if config.combine == 'all' else any(values)


def horizon(points, times, i, seconds, config):
    p = points[i]
    j = bisect_left(times, p['time']+seconds, lo=i+1)
    if j >= len(points) or points[j]['time']-p['time']-seconds > 1:
        return dict(valid=False, reason='missing_horizon')
    window = points[i:j+1]
    if any(partition(q, config) != partition(p, config) or q['session'] != p['session'] for q in window):
        return dict(valid=False, reason='split_or_session_boundary')
    if any(not continuous(a, b) for a, b in zip(window, window[1:])):
        return dict(valid=False, reason='invalid_gap_or_generation')
    if any(q.get('execution_interval', {}).get('valid') is False for q in window[1:]):
        return dict(valid=False, reason='invalid_intra_sample_book')
    sign = 1 if config.execution.direction == 'long' else -1
    changes = [sign*(q['mid_price']-p['mid_price']) for q in window]
    return dict(valid=True, return_points=changes[-1], favorable=max(changes), adverse=min(changes),
                nothing=max(abs(v) for v in changes) < config.nothing_ticks*config.execution.tick_size,
                end=points[j]['timestamp'])


def percentile(values, fraction):
    """Linearly interpolate sorted observations, including small samples."""
    if not values:
        return None
    position = (len(values)-1)*fraction
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower]+(values[upper]-values[lower])*(position-lower)


def summarize_outcomes(rows):
    valid = [r for r in rows if r['valid']]
    values = sorted(r['return_points'] for r in valid)
    return dict(n=len(rows), valid=len(valid), missing=len(rows)-len(valid),
        exclusions=dict(Counter(r['reason'] for r in rows if not r['valid'])),
        mean=mean(values) if values else None, median=median(values) if values else None,
        p10=percentile(values, .1), p90=percentile(values, .9),
        mean_favorable=mean(r['favorable'] for r in valid) if valid else None,
        mean_adverse=mean(r['adverse'] for r in valid) if valid else None,
        nothing_rate=mean(r['nothing'] for r in valid) if valid else None,
        distribution=[dict(low=lo, high=hi, count=sum(lo <= v < hi for v in values)) for lo, hi in
                      [(-1e9, -5), (-5, -2), (-2, -.5), (-.5, .5), (.5, 2), (2, 5), (5, 1e9)]])


def control_key(p):
    v = p.get('volatility')
    return (p['session'], p['contract_id'], p['minute_et']//30,
            'unknown' if v is None else 'low' if v < .5 else 'medium' if v < 2 else 'high',
            1 if p['spread'] <= .25 else 2 if p['spread'] <= .5 else 3)


def episodes(points, config):
    indices, candidates = [], []
    since, active, last_trigger, count, previous = None, False, -math.inf, 0, None
    unavailable = 0
    for i, p in enumerate(points):
        linked = previous is not None and continuous(previous, p) and partition(previous, config) == partition(p, config) and previous['session'] == p['session']
        if not linked:
            since, active = None, False
        matched = condition(p, config)
        if usable(p) and any(feature(p, c.feature, config) is None for c in config.conditions):
            unavailable += 1
        if matched:
            count += 1
            since = p['time'] if since is None else since
            if not active and p['time']-since >= config.persistence_seconds:
                active = True
                if p['time']-last_trigger >= config.cooldown_seconds:
                    indices.append(i)
                    last_trigger = p['time']
        else:
            since, active = None, False
            if usable(p):
                candidates.append(i)
        previous = p
    return indices, candidates, count, unavailable


def simulate(points, times, indices, config, *, harsh=False):
    e = config.execution
    direction = 1 if e.direction == 'long' else -1
    tick, value, size = e.tick_size, e.tick_value, e.size
    stop, target = e.stop_ticks*tick, (e.stop_ticks if e.equal_reward_risk else e.target_ticks)*tick
    slip = (e.slippage_ticks_per_side+(1 if harsh else 0))*tick
    fees = e.commission_fees_round_trip*(1.5 if harsh else 1)*size
    latency = (e.entry_latency_ms+(500 if harsh else 0))/1000
    exit_latency = (e.exit_latency_ms+(500 if harsh else 0))/1000
    trades, busy_until = [], -math.inf
    for i in indices:
        p = points[i]
        base = dict(timestamp=p['timestamp'], time=p['time'], session=p['session'], partition=partition(p, config),
                    contract_id=p['contract_id'], status='excluded', reason=None)
        if e.overlapping == 'skip' and p['time'] < busy_until:
            trades.append(dict(base, reason='overlapping_position')); continue
        if e.order != 'market':
            trades.append(dict(base, status='unresolved', reason='limit_queue_unavailable')); continue
        entry_at = p['time']+e.entry_delay_ms/1000+latency
        j = bisect_left(times, entry_at, lo=i+1)
        if j >= len(points) or points[j]['time']-entry_at > 1:
            trades.append(dict(base, status='unfilled', reason='entry_quote_unavailable')); continue
        entry = points[j]
        path_valid = lambda a, b: continuous(a, b) and partition(a, config) == partition(b, config) and a['session'] == b['session']
        if any(not path_valid(a, b) for a, b in zip(points[i:j], points[i+1:j+1])):
            trades.append(dict(base, status='unfilled', reason='entry_gap')); continue
        entry_quantity = entry.get('best_ask_quantity' if direction == 1 else 'best_bid_quantity')
        if entry_quantity is None or entry_quantity < size:
            trades.append(dict(base, status='unresolved', reason='insufficient_displayed_entry_quantity')); continue
        entry_price = entry['best_ask' if direction == 1 else 'best_bid']+direction*slip
        deadline = entry['time']+e.max_hold_seconds
        busy_until = deadline if e.overlapping == 'skip' else -math.inf
        exit_index, reason, ambiguous = None, 'missing_exit', False
        for k in range(j+1, len(points)):
            q = points[k]
            if not path_valid(points[k-1], q):
                reason = 'exit_gap_or_split'; break
            move = direction*(q['best_bid' if direction == 1 else 'best_ask']-entry_price)
            extrema = q.get('execution_interval')
            if extrema is not None and (not extrema['valid'] or extrema['after_source_index'] != points[k-1]['index']):
                reason = 'invalid_intra_sample_execution_path'; break
            if extrema:
                low = extrema['bid_low'] if direction == 1 else extrema['ask_high']
                high = extrema['bid_high'] if direction == 1 else extrema['ask_low']
                hit_stop = direction*(low-entry_price) <= -stop
                hit_target = direction*(high-entry_price) >= target
            else:
                hit_stop, hit_target = move <= -stop, move >= target
            if hit_stop or hit_target or q['time'] >= deadline:
                reason = 'stop' if hit_stop else 'target' if hit_target else 'timeout'
                ambiguous = (hit_stop and hit_target) if extrema else reason != 'stop'
                exit_index = bisect_left(times, q['time']+exit_latency, lo=k)
                if exit_index >= len(points) or points[exit_index]['time']-q['time']-exit_latency > 1:
                    exit_index, reason = None, 'exit_latency_quote_unavailable'; break
                if any(not path_valid(a,b) for a,b in zip(points[k:exit_index],points[k+1:exit_index+1])):
                    exit_index, reason = None, 'exit_latency_gap'; break
                break
        if exit_index is None:
            trades.append(dict(base, status='unresolved', reason=reason)); continue
        exit_point = points[exit_index]
        busy_until = exit_point['time']
        qty = exit_point.get('best_bid_quantity' if direction == 1 else 'best_ask_quantity')
        if qty is None or qty < size:
            trades.append(dict(base, status='unresolved', reason='insufficient_displayed_exit_quantity')); continue
        if ambiguous and e.ambiguity == 'exclude':
            trades.append(dict(base, status='unresolved', reason='unsampled_barrier_order', ambiguous=True)); continue
        exit_price = exit_point['best_bid' if direction == 1 else 'best_ask']-direction*slip
        observed = direction*(exit_price-entry_price)
        gross_points = min(observed, -stop-slip) if ambiguous or reason == 'stop' else observed
        gross = gross_points/tick*value*size
        trades.append(dict(base, status='filled', reason=reason, entry_time=entry['timestamp'], exit_time=exit_point['timestamp'],
            entry_price=entry_price, exit_price=exit_price, modeled_exit_price=entry_price+direction*gross_points,
            gross=gross, net=gross-fees, fees=fees, ambiguous=ambiguous, observed_sampled_gross=observed/tick*value*size,
            holding_seconds=exit_point['time']-entry['time'], risk=e.stop_ticks*value*size))
    return dict(trades=trades, metrics=trade_metrics(trades), scenario='harsher' if harsh else 'baseline',
                assumptions=e.model_dump(), additional_slippage_ticks=1 if harsh else 0,
                additional_latency_ms=500 if harsh else 0, fee_multiplier=1.5 if harsh else 1)


def trade_metrics(trades):
    fills = [t for t in trades if t['status'] == 'filled']
    net = [t['net'] for t in fills]
    winners, losers = [v for v in net if v > 0], [v for v in net if v < 0]
    equity = peak = drawdown = streak = longest = 0
    for v in net:
        equity += v; peak = max(peak, equity); drawdown = max(drawdown, peak-equity)
        streak = streak+1 if v < 0 else 0; longest = max(longest, streak)
    gross = mean(t['gross'] for t in fills) if fills else None
    return dict(trades=len(fills), attempts=len(trades), exclusions=dict(Counter(t['reason'] for t in trades if t['status'] != 'filled')),
        ambiguous=sum(bool(t.get('ambiguous')) for t in trades), gross_expectancy=gross,
        net_expectancy=mean(net) if net else None, net_expectancy_r=mean(t['net']/t['risk'] for t in fills) if fills else None,
        win_rate=len(winners)/len(net) if net else None,
        realized_reward_risk=mean(winners)/abs(mean(losers)) if winners and losers else None,
        profit_factor=sum(winners)/abs(sum(losers)) if losers else None,
        no_losses=bool(net) and not losers, net_profit=sum(net), max_drawdown=drawdown, longest_loss_streak=longest,
        exposure_seconds=sum(t['holding_seconds'] for t in fills),
        break_even_round_trip_cost=gross, cost_sensitivity=[dict(additional_round_trip_cost=c,
            net_expectancy=mean(net)-c if net else None) for c in (0, .5, 1, 2, 5)],
        uncertainty=cluster_interval(fills, 'net'))


def cluster_interval(rows, field):
    clusters = defaultdict(list)
    for r in rows:
        clusters[r['session']].append(r[field])
    if len(clusters) < 3:
        return dict(method='session cluster bootstrap, 1000 resamples, seed 1729', status='insufficient_sessions', sessions=len(clusters), low=None, high=None)
    rng = random.Random(1729)
    groups = list(clusters.values())
    samples = []
    for _ in range(1000):
        sample = [v for g in rng.choices(groups, k=len(groups)) for v in g]
        samples.append(mean(sample))
    samples.sort()
    return dict(method='session cluster bootstrap, 1000 resamples, seed 1729', status='estimated', sessions=len(clusters), low=samples[24], high=samples[974])


def run_study(points, config, *, cancelled=lambda: False):
    points = sorted(points, key=lambda p: (p['time'], p['capture_id'], p['index']))
    times = [p['time'] for p in points]
    indices, candidates, occurrences, unknown = episodes(points, config)
    if len(indices) > 2000:
        raise ValueError('More than 2,000 episodes; narrow the study or increase cooldown')
    used_controls, results = set(), []
    horizon_max = max(config.horizons)
    controls_by_key = defaultdict(list)
    # Control anchors are regularly spaced, chosen without inspecting outcomes.
    last_candidate = {}
    for i in candidates:
        key = (partition(points[i], config), control_key(points[i]))
        if points[i]['time']-last_candidate.get(key, -math.inf) >= max(config.cooldown_seconds, horizon_max):
            controls_by_key[key].append(i)
            last_candidate[key] = points[i]['time']
    for ordinal, i in enumerate(indices):
        if cancelled():
            raise InterruptedError('Research cancelled')
        p = points[i]
        pool = controls_by_key[(partition(p, config), control_key(p))]
        c = next((j for j in reversed(pool) if j not in used_controls and points[j]['time']+horizon_max+config.cooldown_seconds < p['time']), None)
        if c is not None:
            used_controls.add(c)
        results.append(dict(episode=ordinal+1, timestamp=p['timestamp'], time=p['time'], contract_id=p['contract_id'],
            session=p['session'], partition=partition(p, config), capture_id=p['capture_id'], index=p['index'], generation=p['generation'],
            time_of_day=p['time_of_day'], hour_et=p['minute_et']//60, volatility_band=control_key(p)[3], spread_band=control_key(p)[4],
            features={r.feature: feature(p, r.feature, config) for r in config.conditions},
            outcomes={str(h): horizon(points, times, i, h, config) for h in config.horizons},
            control_timestamp=points[c]['timestamp'] if c is not None else None,
            control={str(h): horizon(points, times, c, h, config) if c is not None else dict(valid=False, reason='no_prior_matched_control') for h in config.horizons}))
    summaries = {}
    for h in config.horizons:
        s = summarize_outcomes([r['outcomes'][str(h)] for r in results])
        s['baseline'] = summarize_outcomes([r['control'][str(h)] for r in results])
        paired = [dict(session=r['session'], difference=r['outcomes'][str(h)]['return_points']-r['control'][str(h)]['return_points']) for r in results
                  if r['outcomes'][str(h)]['valid'] and r['control'][str(h)]['valid']]
        s['paired_mean_difference'] = mean(r['difference'] for r in paired) if paired else None
        s['uncertainty'] = cluster_interval(paired, 'difference')
        summaries[str(h)] = s
    groups = {}
    for key in ('session', 'partition', 'time_of_day', 'hour_et', 'volatility_band', 'spread_band'):
        groups[key] = {str(value): {str(h): summarize_outcomes([r['outcomes'][str(h)] for r in results if r[key] == value]) for h in config.horizons}
                       for value in sorted({r[key] for r in results}, key=str)}
    baseline = simulate(points, times, indices, config)
    harsher = simulate(points, times, indices, config, harsh=True)
    # Fixed rules carried forward, session by session. No fit or random tick split.
    walk_forward = [dict(session=session, prior_sessions=sum(s < session for s in {p['session'] for p in points}),
        partition=next(partition(p, config) for p in points if p['session'] == session),
        metrics=trade_metrics([t for t in baseline['trades'] if t['session'] == session])) for session in sorted({p['session'] for p in points})]
    status = ('invalid_data' if points and not any(usable(p) for p in points) else
              'insufficient_data' if not results or len({p['session'] for p in points}) < 3 else
              'unresolved_execution' if not baseline['metrics']['trades'] else 'no_confirmed_edge')
    return dict(engine_version=ENGINE_VERSION, occurrences=occurrences, independent_episodes=len(results), episodes=results,
        sessions=sorted({p['session'] for p in points}), days=sorted({p['timestamp'][:10] for p in points}),
        invalid_measurements=sum(not usable(p) for p in points), unavailable_feature_measurements=unknown,
        outcome_summary=summaries, breakdowns=groups, tester=baseline, harsher=harsher, walk_forward=walk_forward,
        partition_metrics={name: trade_metrics([t for t in baseline['trades'] if t['partition'] == name]) for name in ('exploration', 'training', 'validation', 'test')},
        status=status,
        methodology=dict(episodes='One trigger after persistence per contiguous condition episode; cooldown from last accepted trigger. Boundaries reset persistence.',
            controls='Earlier non-trigger anchors without replacement, same session/contract/30-minute bucket/fixed volatility and spread bins. Control outcomes end before the signal and cooldown. No outcome-based selection.',
            outcomes='Direction-adjusted mid-price points; any invalid sample, >2s gap, generation, session or split boundary invalidates the outcome. End tolerance 1s.',
            execution='Market entry/exit at recorded executable ask/bid plus slippage; spread is included once. Displayed top quantity must cover size. Raw-event BBO extrema detect barriers inside sample intervals; both barriers in one interval are ambiguous and default to stop-first. Any invalid intra-sample path is unresolved. Detection occurs at the sample, then exit latency applies; targets are market-trigger exits, not assumed limit fills. Without extrema, non-stop exits remain ambiguous.',
            validation='Chronological sessions only; no automatically fitted thresholds. Session bootstrap requires at least three sessions. Reported intervals do not correct for repeated testing.',
            exposure='Sum of holding seconds; can exceed elapsed time under independent overlapping positions. Research simulations are separate from broker and TopBot results.'))
