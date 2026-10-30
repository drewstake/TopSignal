"""Compact observed-book measurements; no prediction, trading, or database writes."""
from datetime import datetime
import math
from decimal import Decimal, InvalidOperation

if __package__:
    from .depth_book import MarketByPriceBook
    from .depth_recording import SegmentRecorder
    from .depth_features import Features
else:
    from depth_book import MarketByPriceBook
    from depth_recording import SegmentRecorder
    from depth_features import Features


def imbalance(bid, ask):
    return (bid - ask) / (bid + ask) if bid + ask > 0 else None


def finite_positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


class BookTotals:
    """Sample after receipt, never using later events or inventing quiet-period rows.

    Uses the display book's aggregate-volume semantics. Completeness and provider
    sequence guarantees remain unverified, even when both observed sides are fresh.
    """
    interval_ns = 250_000_000

    def __init__(self, *, tick_distances=(1, 5, 10, 20)):
        self.book = None
        self.contract = None
        self.last_sample_ns = None
        self.generation = 0
        self.features = Features(tick_distances=tick_distances)
        self.last_mono = None
        self.last_received = None
        self.execution_interval = None
        self.last_sample_index = None
        self._clear_metadata()

    def _clear_metadata(self):
        self.side_times = {}
        self.last_trade = None
        self.trade_ns = None
        self.last_epoch = None

    def apply(self, event):
        contract, kind, payload = event['contract_id'], event['kind'], event['payload']
        mono = event['monotonic_ns']
        boundary = kind if kind in {'connect', 'disconnect', 'gap', 'roll', 'end'} else None
        if self.last_mono is not None and mono < self.last_mono:
            boundary = 'receipt_gap'
        elif self.last_mono is not None and mono-self.last_mono > 2_000_000_000:
            self.features.reset()
        if self.last_received is not None and event['received_at'] < self.last_received:
            boundary = 'receipt_time_reversed'
        self.last_mono, self.last_received = mono, event['received_at']
        if self.contract != contract:
            self.contract = contract
            self.book = MarketByPriceBook(contract)
            boundary = boundary or 'contract_changed'
        if kind == 'depth':
            epoch = payload.get('epoch')
            if epoch is not None and self.last_epoch is not None and epoch != self.last_epoch:
                boundary = 'epoch_changed'
            if payload.get('type') == 6:
                boundary = 'reset'
        if boundary:
            self.book.clear_for_reconnect()
            self._clear_metadata()
            self.features.reset()
            self.generation += 1
            self.execution_interval = None
            self.last_sample_index = None
        before = after = None
        if kind == 'depth':
            if payload.get('epoch') is not None:
                self.last_epoch = payload['epoch']
            typ = payload.get('type')
            # Only accepted explicit updates count toward depth reduction/refill.
            levels = self.book._bids if typ == 2 else self.book._asks if typ == 1 else None
            try:
                price = Decimal(str(payload.get('price')))
                if not price.is_finite():
                    levels = None
            except InvalidOperation:
                levels = None
            if levels is not None:
                before = levels[price].size if price in levels else Decimal(0)
            changed = self.book.apply(payload)
            if levels is not None:
                after = levels[price].size if price in levels else Decimal(0)
            typ = payload.get('type')
            side = ('ask' if typ in {1, 3, 10} else 'bid' if typ in {2, 4, 9} else None) if type(typ) is int else None
            if changed and side:
                self.side_times[side] = (mono, event['provider_at'])
        elif kind == 'trade' and finite_positive(payload.get('price')):
            self.last_trade = payload['price']
            self.trade_ns = mono
        self.features.event(event, before, after)
        # Extrema accumulate from incoming accepted observed book states BEFORE
        # summary sampling. They can establish a single barrier crossing; when
        # both barriers lie inside one interval, their order remains unresolved.
        bid_price = float(max(self.book._bids)) if self.book._bids else None
        ask_price = float(min(self.book._asks)) if self.book._asks else None
        valid_path = bid_price is not None and ask_price is not None and bid_price < ask_price
        for side in ('bid', 'ask'):
            seen = self.side_times.get(side)
            if not seen or mono-seen[0] > 2_000_000_000 or not seen[1]:
                valid_path = False
            elif not 0 <= (datetime.fromisoformat(event['received_at'])-datetime.fromisoformat(seen[1])).total_seconds() <= 2:
                valid_path = False
        if self.execution_interval is None:
            self.execution_interval = dict(valid=valid_path, after_source_index=self.last_sample_index,
                bid_low=bid_price, bid_high=bid_price, ask_low=ask_price, ask_high=ask_price)
        else:
            self.execution_interval['valid'] &= valid_path
            for side, value in (('bid',bid_price),('ask',ask_price)):
                if value is not None:
                    for suffix, operation in (('low',min),('high',max)):
                        old=self.execution_interval[side+'_'+suffix]
                        self.execution_interval[side+'_'+suffix]=value if old is None else operation(old,value)
        if not boundary and self.last_sample_ns is not None and mono - self.last_sample_ns < self.interval_ns:
            return None
        self.last_sample_ns = mono
        snapshot = self.book.snapshot()
        bids, asks = snapshot['bids'], snapshot['asks']
        bid = sum(level['size'] for level in bids)
        ask = sum(level['size'] for level in asks)
        best_bid = bids[0]['price'] if bids else None
        best_ask = asks[0]['price'] if asks else None
        spread = best_ask - best_bid if bids and asks else None
        flags = [boundary] if boundary else []
        if not bids or not asks:
            flags.append('missing_side')
        if spread is not None and spread <= 0:
            flags.append('locked_or_crossed')
        ages, provider_ages = {}, {}
        for side in ('bid', 'ask'):
            seen = self.side_times.get(side)
            ages[side] = (mono - seen[0]) / 1e6 if seen else None
            provider_ages[side] = None
            if seen and seen[1]:
                provider_ages[side] = (datetime.fromisoformat(event['received_at']) - datetime.fromisoformat(seen[1])).total_seconds() * 1000
            if ages[side] is None or ages[side] > 2000:
                flags.append(f'{side}_stale')
            if provider_ages[side] is None or not 0 <= provider_ages[side] <= 2000:
                flags.append(f'{side}_provider_time_unreliable')
        nearest = {}
        for count in (1, 5, 10, 20, 50, 100):
            b, a = bids[:count], asks[:count]
            bs, az = sum(v['size'] for v in b), sum(v['size'] for v in a)
            nearest[str(count)] = dict(bid_contracts=bs, ask_contracts=az,
                                      bid_levels=len(b), ask_levels=len(a), imbalance=imbalance(bs, az))
        row = dict(schema='mnq-book-totals-v2', derived=True, scope='all_observed_levels',
                    source_capture_id=event['capture_id'], source_index=event['index'], generation=self.generation,
                    interval_ms=250, bid_contracts=bid, ask_contracts=ask,
                    bid_levels=len(bids), ask_levels=len(asks), imbalance=imbalance(bid, ask),
                    nearest=nearest, best_bid=best_bid, best_ask=best_ask, spread=spread,
                    mid_price=(best_bid + best_ask) / 2 if spread is not None else None,
                    last_trade_price=self.last_trade, trade_age_ms=(mono-self.trade_ns)/1e6 if self.trade_ns is not None else None,
                    side_age_ms=ages, provider_age_ms=provider_ages, quality_flags=flags,
                    snapshot_completion_verified=False, provider_sequence_guarantee=False,
                    model_inputs_enabled=False)
        row['features'] = self.features.sample(event, row, snapshot,
            {side: sum(s == side for s, _ in self.book._explicit_depth_levels) for side in ('bid', 'ask')})
        row['execution_interval'] = self.execution_interval
        self.execution_interval = None
        self.last_sample_index = event['index']
        return row


class TotalsRecorder:
    """Pair immutable raw segments with compact, independently readable segments.

    Both writers share the existing root budget, free-space floor, backup format,
    and retention stop. The summary stream has its own receipt index and includes
    a pointer to the last raw event used in each measurement.
    """
    def __init__(self, raw, *, before_segment=None):
        self.raw = raw
        self.book = BookTotals()
        self.totals = SegmentRecorder(raw.root / 'totals', raw.owner_hash, data_live=raw.data_live,
                                      segment_seconds=raw.limit_seconds, before_segment=before_segment)
        self.latest = None

    def append(self, *args, **kwargs):
        event = self.raw.append(*args, **kwargs)
        row = self.book.apply(event)
        if row is not None:
            self.totals.append(event['contract_id'], 'book_totals', row,
                               received_at=event['received_at'], monotonic_ns=event['monotonic_ns'])
            self.latest = dict(row, received_at=event['received_at'])
        return event

    def tick(self):
        self.raw.tick()
        self.totals.tick()

    def seal(self):
        self.raw.seal()
        self.totals.seal()

    def close(self):
        try:
            self.raw.close()
        finally:
            self.totals.close()

    def status(self):
        return dict(self.raw.status(), contract_totals=dict(
            enabled=True, interval_ms=250, samples=self.totals.index,
            sealed_bytes=self.totals.sealed_bytes, run_id=self.totals.run_id, latest=self.latest))
