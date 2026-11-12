from datetime import date, datetime, timezone
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.discord_topbot import DeliveryError, state_db
from tools.discord_topbot_recap import Recap, deliver_recap, latest_due, session_bounds, summarize
from tests.test_discord_topbot import trade


DAY = date(2026, 11, 12)


def fields(data):
    return {item['name']: item['value'] for item in data['embeds'][0]['fields']}


class RecapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'state.sqlite3'
        self.db = state_db(self.path)
        self.recap = Recap.__new__(Recap)
        self.recap.config = {'channel_id': '42'}
        self.recap.source = Mock()
        self.recap.source.rows.return_value = []
        self.recap.discord = Mock()
        self.recap.discord.send.return_value = '777'
        self.recap.verified = False
        self.recap.initialize(self.db, datetime(2026, 11, 12, 16, tzinfo=timezone.utc))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_counts_net_winners_losses_breakeven_and_pending(self):
        win = trade(True)
        loss = trade(True)
        loss['id'] = 102
        loss['runner'].update(gross_pnl_usd=1, fees_usd=1.22, net_pnl_usd=-.22)
        even = trade(True)
        even['id'] = 103
        even['runner'].update(gross_pnl_usd=1.22, fees_usd=1.22, net_pnl_usd=0)
        opened = trade()
        opened['id'] = 104
        data, pending = summarize(DAY, [win, loss, even, opened, win])
        values = fields(data)
        self.assertEqual([values[k] for k in ('Total trades','Wins','Losses','Breakeven','Pending results')], ['4','1','1','1','1'])
        self.assertEqual(values['Net P&L'], '**$+48.56**')
        self.assertEqual(pending, 1)
        self.assertIn('PROVISIONAL', json.dumps(data))
        self.assertNotIn('PRIVATE', json.dumps(data))

    def test_no_trades_and_overnight_bounds(self):
        start, end = session_bounds(DAY)
        self.assertEqual(start.isoformat(), '2026-11-11T23:00:00+00:00')
        self.assertEqual(end.isoformat(), '2026-11-12T23:00:00+00:00')
        row = trade(True)
        row['runner']['entry_at'] = end.isoformat()
        data, pending = summarize(DAY, [row])
        self.assertEqual(fields(data)['Total trades'], '0')
        row['runner']['entry_at'] = start.isoformat()
        self.assertEqual(fields(summarize(DAY, [row])[0])['Total trades'], '1')

    def test_schedule_dst_weekend_and_exact_time(self):
        for clock, expected in [
            ('2026-11-12T22:04:59+00:00', date(2026,11,11)),
            ('2026-11-12T22:05:00+00:00', DAY),
            ('2026-07-02T21:05:00+00:00', date(2026,7,2)),
            ('2026-11-15T23:00:00+00:00', date(2026,11,13)),
        ]:
            self.assertEqual(latest_due(datetime.fromisoformat(clock)), expected)
        start, end = session_bounds(date(2026,11,1))
        self.assertEqual((end-start).total_seconds(), 25*3600)

    def test_no_premature_post_and_no_duplicate_after_restart(self):
        self.recap.cycle(self.db, datetime.fromisoformat('2026-11-12T22:04:59+00:00'))
        self.recap.discord.send.assert_not_called()
        self.recap.cycle(self.db, datetime.fromisoformat('2026-11-12T22:05:00+00:00'))
        self.assertEqual(self.recap.discord.send.call_count, 1)
        self.db.close()
        self.db = state_db(self.path)
        self.recap.initialize(self.db)
        self.recap.cycle(self.db, datetime.fromisoformat('2026-11-12T23:00:00+00:00'))
        self.assertEqual(self.recap.discord.send.call_count, 1)

    def test_late_reconciliation_edits_existing_recap(self):
        self.recap.source.rows.return_value = [trade()]
        at = datetime.fromisoformat('2026-11-12T22:05:00+00:00')
        self.recap.cycle(self.db, at)
        self.recap.source.rows.return_value = [trade(True)]
        self.recap.cycle(self.db, at)
        self.assertEqual(self.recap.discord.send.call_count, 2)
        data, identity = self.recap.discord.send.call_args.args
        self.assertEqual(identity, '777')
        self.assertEqual(fields(data)['Pending results'], '0')
        self.assertEqual(fields(data)['Net P&L'], '**$+48.78**')

    def test_downtime_catches_up_weekdays_only(self):
        self.recap.cycle(self.db, datetime.fromisoformat('2026-11-16T22:05:00+00:00'))
        days = [r[0] for r in self.db.execute('SELECT day FROM recaps ORDER BY day')]
        self.assertEqual(days, ['2026-11-12', '2026-11-13', '2026-11-16'])

    def test_timeout_does_not_duplicate_and_health_requires_review(self):
        self.recap.discord.send.side_effect = DeliveryError('uncertain')
        at = datetime.fromisoformat('2026-11-12T22:05:00+00:00')
        errors, delay = self.recap.cycle(self.db, at)
        self.assertIn('recap_uncertain', errors)
        self.recap.discord.send.side_effect = None
        errors, delay = self.recap.cycle(self.db, at)
        self.assertIn('recap_delivery_needs_review', errors)
        self.assertEqual(self.recap.discord.send.call_count, 1)

    def test_corrupt_complete_trade_prevents_partial_total(self):
        bad = trade(True)
        bad['runner']['net_pnl_usd'] = 99
        with self.assertRaises(ValueError): summarize(DAY, [bad])

    def test_wrong_destination_is_rejected(self):
        self.recap.config['channel_id'] = '43'
        with self.assertRaises(ValueError): self.recap.initialize(self.db)


if __name__ == '__main__':
    unittest.main()
