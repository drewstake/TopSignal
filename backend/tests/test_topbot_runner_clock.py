"""Runner freshness must use the locked observation clock, not cycle start."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import topbot_depth, topbot_runner
from test_topbot_dom import dom_entry, db_session, open_exchange_session, CONTRACT_ID


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('age,points,expected', [
    (.1, 110, 'modify'), (.1, 84, 'flatten'),
    (3, 110, 'wait'), (-.1, 110, 'wait'),
])
def test_runner_uses_current_snapshot_after_slow_broker_reads(
    db_session, dom_entry, monkeypatch, side, age, points, expected,
):
    result, client, token = dom_entry
    entry = result.order_attempt
    entry.side = side
    checked_at = datetime.now(timezone.utc)
    cycle_started = checked_at - timedelta(seconds=10)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return checked_at.astimezone(tz) if tz else checked_at.replace(tzinfo=None)

    monkeypatch.setattr(topbot_depth, 'datetime', Clock)
    direction = 1 if side == 'BUY' else -1
    mark = 20000 + direction * points
    book = topbot_depth.DepthState(CONTRACT_ID)
    book.connected = True
    book.last_at = checked_at - timedelta(seconds=age)
    book.bids = {mark if side == 'BUY' else mark-.25: 100}
    book.asks = {mark+.25 if side == 'BUY' else mark: 100}
    feed = topbot_depth.DepthFeed()
    feed.streams[(str(entry.user_id), CONTRACT_ID)] = {'state': book, 'touched': 0}
    monkeypatch.setattr(feed, 'ensure', lambda *a, **kw: None)
    monkeypatch.setattr(topbot_runner, 'feed', feed)
    plan = entry.raw_request['timeExit']
    plan['runner'].update(entry_at=(cycle_started-timedelta(minutes=5)).isoformat(),
                          entry_price=20000, side=side, mfe_points=120, mae_points=-5)
    initial_stop = 20000 - direction*25
    stop = dict(order_id='123', account_id=entry.account_id, contract_id=CONTRACT_ID,
                raw_payload={'stopPrice': initial_stop})
    client.open_orders = [stop]
    changes = []

    def modify(**kwargs):
        changes.append(kwargs)
        stop['raw_payload']['stopPrice'] = kwargs['stop_price']

    client.modify_stop_order = modify
    flatten = topbot_runner.manage(db_session, entry=entry, plan=plan, client=client,
        stops=[stop], worker_lease_token=token, now=cycle_started)
    assert flatten is (expected == 'flatten')
    assert not client.cancel_order_calls
    if expected == 'modify':
        assert changes == [dict(account_id=entry.account_id, order_id='123',
                                stop_price=20000+direction*95)]
        assert plan['runner']['confirmed_stop'] == 20000+direction*95
    else:
        assert changes == []
        assert stop['raw_payload']['stopPrice'] == initial_stop
    if expected == 'flatten':
        assert plan['runner_exit_reason'] == 'trailing_level_crossed'
    if expected == 'wait':
        assert plan['runner']['management_status'].startswith('Waiting for fresh prices')
