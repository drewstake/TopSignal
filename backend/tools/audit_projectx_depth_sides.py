"""Causal cross-check of trade labels against recorded DOM best-price events.

Uses already-copied, checksum-verified recordings. Never interprets DOM size as
executed volume, sorts by undocumented provider sequence, or looks ahead.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path

if __package__:
    from .read_depth_history import verified_rows, timestamp
    from .read_depth_research import inventory
else:
    from read_depth_history import verified_rows, timestamp
    from read_depth_research import inventory


class DepthSideAudit:
    def __init__(self):
        self.best = {}
        self.scope = self.epoch = self.previous = None
        self.types = Counter()

    def feed(self, event):
        scope = (event['capture_id'], event['contract_id'], event['data_live'])
        received = timestamp(event['received_at'])
        p, kind = event['payload'], event['kind']
        if (scope != self.scope or (self.previous and not 0 <= (received-self.previous).total_seconds() <= 2)
                or kind in {'connect', 'disconnect', 'gap', 'reset', 'roll', 'end'}
                or (self.epoch is not None and p.get('epoch') is not None and self.epoch != p['epoch'])):
            self.best.clear()
        self.scope, self.previous = scope, received
        if p.get('epoch') is not None:
            self.epoch = p['epoch']
        try:
            provider = timestamp(event['provider_at'])
        except (ValueError, TypeError, AttributeError):
            provider = None
        if kind == 'depth':
            typ, price, volume = p.get('type'), p.get('price'), p.get('volume')
            self.types[str(typ)] += 1
            if typ == 6:
                self.best.clear()
            side = 'bid' if type(typ) is int and typ in {2, 4, 9} else 'ask' if type(typ) is int and typ in {1, 3, 10} else None
            if side and provider:
                old = self.best.get(side)
                valid = (type(price) in (float, int) and math.isfinite(price) and price > 0
                         and type(volume) in (float, int) and math.isfinite(volume) and volume > 0)
                if typ in {3, 4, 9, 10}:
                    if not old or provider >= old[1]:
                        if valid:
                            self.best[side] = (price, provider, received)
                        else:
                            self.best.pop(side, None)
                elif old and provider >= old[1]:
                    # A deletion at best, or a new better explicit level, makes
                    # the last advertised best ambiguous until a new best event.
                    better = valid and ((side == 'bid' and price > old[0]) or (side == 'ask' and price < old[0]))
                    if better or (price == old[0] and not valid):
                        self.best.pop(side, None)
            return None
        if kind != 'trade':
            return None
        if not provider or not 0 <= (received-provider).total_seconds() <= 2:
            return 'unreliable_trade_time'
        if type(p.get('type')) is not int or p['type'] not in {0, 1}:
            return 'unknown_trade_label'
        if len(self.best) != 2:
            return 'missing_preceding_best'
        bid, ask = self.best['bid'], self.best['ask']
        if not bid[0] < ask[0]:
            return 'locked_or_crossed'
        for best in (bid, ask):
            if not (0 <= (received-best[2]).total_seconds() <= .5 and 0 <= (provider-best[1]).total_seconds() <= .5):
                return 'stale_or_later_provider_best'
        price = p.get('price')
        if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
            return 'invalid_trade_price'
        return f"{p['type']}:" + ('ask_or_above' if price >= ask[0] else 'bid_or_below' if price <= bid[0] else 'inside')


def audit(root, owner):
    root = Path(root)
    manifests, errors = inventory(root, owner)
    if errors or len(manifests) > 8:
        raise ValueError('Use at most eight frozen verified source segments')
    counts, types, volumes, depth_types = Counter(), Counter(), Counter(), Counter()
    sources = []
    for _, _, marker, manifest in manifests:
        engine = DepthSideAudit()  # Disjoint samples never inherit a book.
        for event in verified_rows(marker, manifest, root, raw_events=True):
            classification = engine.feed(event)
            if event['kind'] == 'trade':
                types[str(event['payload'].get('type'))] += 1
                counts[classification] += 1
                volume = event['payload'].get('volume')
                if type(volume) in (float, int) and math.isfinite(volume) and volume > 0:
                    volumes[classification] += volume
        depth_types.update(engine.types)
        sources.append({key: manifest[key] for key in ('capture_id', 'first_index', 'last_index', 'sha256')})
    return dict(schema='projectx-dom-side-audit-v1', sources=sources, trade_types=dict(types),
                depth_types=dict(depth_types), counts=dict(counts), volume=dict(volumes),
                method='Prior received DOM BestBid/BestAsk/NewBestBid/NewBestAsk; both receipt and provider ages 0..500ms; trade delivery lag 0..2s. No future events or sequence-based reordering.',
                conclusion='A DOM consistency cross-check, not proof of provider aggressor semantics. No strategy or feed changes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.owner), indent=2))
