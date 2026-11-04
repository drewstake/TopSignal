from datetime import datetime, timedelta, timezone
from tools.audit_projectx_depth_sides import DepthSideAudit

BASE = datetime(2026, 11, 3, 23, tzinfo=timezone.utc)


def event(kind, typ, price, *, at=0, provider=None, volume=1):
    return dict(kind=kind, capture_id='a'*32, contract_id='CON.F.US.MNQ.Z26', data_live=False,
                received_at=(BASE+timedelta(seconds=at)).isoformat(),
                provider_at=(BASE+timedelta(seconds=at if provider is None else provider)).isoformat(),
                payload=dict(type=typ, price=price, volume=volume))


def ready():
    audit = DepthSideAudit()
    audit.feed(event('depth', 9, 100))
    audit.feed(event('depth', 10, 100.25))
    return audit


def test_dom_and_trade_sides_are_separate():
    audit = ready()
    assert audit.feed(event('trade', 0, 100.25, at=.1)) == '0:ask_or_above'
    assert audit.feed(event('trade', 1, 100, at=.1)) == '1:bid_or_below'
    assert audit.feed(event('trade', 0, 100.1, at=.1)) == '0:inside'
    assert audit.feed(event('trade', False, 100, at=.1)) == 'unknown_trade_label'


def test_future_stale_reset_and_removed_best_are_not_evidence():
    audit = ready()
    assert audit.feed(event('trade', 0, 100.25, at=.1, provider=-.1)) == 'stale_or_later_provider_best'
    assert audit.feed(event('trade', 0, 100.25, at=.6)) == 'stale_or_later_provider_best'
    audit = ready()
    audit.feed(event('depth', 1, 100.25, at=.1, volume=0))
    assert audit.feed(event('trade', 0, 100.25, at=.2)) == 'missing_preceding_best'
    audit = ready()
    audit.feed(event('depth', 6, 0, at=.1))
    assert audit.feed(event('trade', 1, 100, at=.2)) == 'missing_preceding_best'
