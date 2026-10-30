"""Causal observed-book features. No network, credentials, database, or execution."""
from collections import deque
from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

FEATURE_VERSION = 'microstructure-v2.1'
WINDOWS = (1, 5, 15)
LEVELS = (1, 5, 10, 20, 50, 100)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def context(at):
    local = datetime.fromisoformat(at).astimezone(ZoneInfo('America/New_York'))
    minute = local.hour * 60 + local.minute
    session = (local + timedelta(days=1)).date() if minute >= 1080 else local.date()
    return dict(session=str(session), minute_et=minute,
                time_of_day='rth' if 570 <= minute < 960 else 'overnight',
                session_seconds=((minute - 1080) % 1440) * 60 + local.second)


class Features:
    """Activity counts raw received events before 250ms sampling.

    Depth changes are accepted explicit price-level changes, not cancellations.
    Baselines contain prior valid samples only; a gap clears all rolling state.
    """
    def __init__(self, tick_size=.25, tick_distances=(1, 5, 10, 20)):
        if not finite(tick_size) or tick_size <= 0 or len(tick_distances) > 8:
            raise ValueError('Invalid tick configuration')
        if any(type(n) is not int or not 1 <= n <= 100 for n in tick_distances):
            raise ValueError('Tick distances must be integers from 1 to 100')
        self.tick_size, self.tick_distances = tick_size, tick_distances
        self.reset()

    def reset(self):
        self.trades, self.changes, self.baseline = deque(), deque(), deque()
        self.started = self.last_sample = None
        self.persistence = {}
        self.invalid_trade_at = None

    def event(self, event, before_size, after_size):
        t = event['monotonic_ns'] / 1e9
        if self.started is None:
            self.started = t
        p = event['payload']
        if event['kind'] == 'trade':
            volume = p.get('volume')
            valid_time = False
            try:
                age = (datetime.fromisoformat(event['received_at']) - datetime.fromisoformat(event['provider_at'])).total_seconds()
                valid_time = 0 <= age <= 2
            except (ValueError, TypeError):
                pass
            valid = finite(volume) and volume > 0 and finite(p.get('price')) and p['price'] > 0 and valid_time
            self.trades.append((t, volume if valid else None))
            if not valid:
                self.invalid_trade_at = t
        if before_size is not None and after_size is not None and before_size != after_size:
            self.changes.append((t, 'bid' if p['type'] == 2 else 'ask', float(after_size - before_size)))
        for queue in (self.trades, self.changes):
            while queue and queue[0][0] <= t - 15:
                queue.popleft()
        # Fail closed instead of keeping unbounded bursts in memory.
        if len(self.trades) + len(self.changes) > 200000:
            raise ValueError('Activity window exceeds event budget')

    def sample(self, event, row, snapshot, explicit_counts):
        t = event['monotonic_ns'] / 1e9
        elapsed = t - self.started if self.started is not None else 0
        if self.last_sample is not None and (t <= self.last_sample or t - self.last_sample > 2):
            self.baseline.clear()
            self.persistence.clear()
        self.last_sample = t
        while self.baseline and self.baseline[0][0] < t - 60:
            self.baseline.popleft()
        scopes = {'all': {k: row[k] for k in ('bid_contracts', 'ask_contracts', 'bid_levels', 'ask_levels', 'imbalance')}}
        scopes.update(row['nearest'])
        for ticks in self.tick_distances:
            sides = []
            for side in ('bids', 'asks'):
                levels = snapshot[side]
                selected = [v for v in levels if abs(v['price'] - levels[0]['price']) <= ticks * self.tick_size] if levels else []
                sides.append((sum(v['size'] for v in selected), len(selected)))
            b, a = sides
            scopes[f'ticks:{ticks}'] = dict(bid_contracts=b[0], ask_contracts=a[0], bid_levels=b[1], ask_levels=a[1],
                                          imbalance=(b[0]-a[0])/(b[0]+a[0]) if b[0]+a[0] else None)
        warm = len(self.baseline) >= 40 and t - self.baseline[0][0] >= 30
        prior_count = len(self.baseline)
        valid = not row['quality_flags']
        for name, values in scopes.items():
            for side in ('bid', 'ask'):
                previous = [s[name][f'{side}_contracts'] for _, s, _ in self.baseline if name in s]
                mean = sum(previous)/len(previous) if warm and previous else None
                std = (sum((v-mean)**2 for v in previous)/len(previous))**.5 if mean is not None else None
                value = values[f'{side}_contracts']
                ratio = value/mean if mean and valid else None
                values[f'{side}_depth_ratio'] = ratio
                values[f'{side}_depth_z'] = (value-mean)/std if std and valid else None
                key = (name, side, 'thin')
                thin = ratio is not None and ratio < .5
                self.persistence[key] = self.persistence.get(key, t) if thin else t
                values[f'{side}_thin_seconds'] = t-self.persistence[key] if thin else 0 if ratio is not None else None
            imb = values['imbalance']
            for sign, label in ((-1, 'negative'), (1, 'positive')):
                key = (name, label)
                active = valid and imb is not None and sign*imb >= .3
                self.persistence[key] = self.persistence.get(key, t) if active else t
                values[f'{label}_imbalance_seconds'] = t-self.persistence[key] if active else 0
        activity = {}
        for seconds in WINDOWS:
            trades = [v for at, v in self.trades if at > t-seconds]
            ready = elapsed >= seconds
            known = ready and all(v is not None for v in trades)
            changes = [(s, v) for at, s, v in self.changes if at > t-seconds]
            activity[str(seconds)] = dict(trade_count=len(trades) if ready else None,
                traded_volume=sum(trades) if known else None,
                trade_count_rate=len(trades)/seconds if ready else None,
                volume_rate=sum(trades)/seconds if known else None,
                buy_volume=None, sell_volume=None, directional_valid=False,
                invalid_trades=sum(v is None for v in trades), warmup=not ready,
                **{f'{s}_{label}_rate': sum(abs(v) for side, v in changes if side == s and (v < 0 if label == 'depletion' else v > 0))/seconds if ready else None
                   for s in ('bid', 'ask') for label in ('depletion', 'refill')})
        mids = [(at, mid) for at, _, mid in self.baseline if mid is not None]
        volatility = None
        if warm and len(mids) > 1:
            volatility = math.sqrt(sum((b[1]-a[1])**2 for a, b in zip(mids, mids[1:]))/(mids[-1][0]-mids[0][0]))
        if valid:
            self.baseline.append((t, scopes, row['mid_price']))
        else:
            self.baseline.clear()
            self.persistence.clear()
        return dict(feature_version=FEATURE_VERSION, scopes=scopes, activity=activity,
            baseline_seconds=60, baseline_warmup=not warm or not valid, baseline_observations=prior_count,
            volatility=volatility, volatility_units='price points / sqrt(second)',
            tick_size=self.tick_size, tick_distances=list(self.tick_distances),
            best_bid_quantity=snapshot['bids'][0]['size'] if snapshot['bids'] else None,
            best_ask_quantity=snapshot['asks'][0]['size'] if snapshot['asks'] else None,
            explicit_depth_levels=explicit_counts,
            depth_change_scope='accepted explicit price-level updates; all observed prices',
            directional_semantics='Unavailable: aggressor interpretation not verified',
            **context(event['received_at']))
