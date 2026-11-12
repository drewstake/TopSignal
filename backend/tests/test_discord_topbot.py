import json
import contextlib
import importlib.util
import io
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from tools.discord_topbot import Discord, DeliveryError, cycle, deliver, payload, state_db


def trade(closed=False):
    runner = dict(entry_at='2026-11-12T14:30:00+00:00', entry_fill_id='fill1',
                  quantity=1, entry_price=28000, stats_status='open', confirmed_stop=27975,
                  confirmed_target=28025, account_id='PRIVATE', secret='DO_NOT_PUBLISH')
    if closed:
        runner.update(stats_status='complete', exit_fill_id='fill2', exit_at='2026-11-12T14:31:00+00:00',
                      exit_price=28025, gross_pnl_usd=50, fees_usd=1.22, net_pnl_usd=48.78,
                      commission_basis='configured_round_turn_schedule', exit_reason='take_profit')
    return dict(id=101, side='BUY', size=1, execution_mode='live', symbol='MNQ',
                contract_id='CON.F.US.MNQ.Z26', provider_simulated=True, runner=runner)


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch('tools.discord_topbot.time.time', return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'delivery.sqlite3'
        self.db = state_db(self.path)
        self.db.execute('INSERT INTO deliveries(id) VALUES(101)')
        self.db.commit()
        self.discord = Mock()
        self.discord.send.return_value = '12345'

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_open_then_closed_edits_original_and_survives_restart(self):
        deliver(self.db, self.discord, trade())
        self.assertIsNone(self.discord.send.call_args.args[1])
        self.db.close()
        self.db = state_db(self.path)
        deliver(self.db, self.discord, trade())
        self.assertEqual(self.discord.send.call_count, 1)
        deliver(self.db, self.discord, trade(True))
        self.assertEqual(self.discord.send.call_args.args[1], '12345')
        self.assertEqual(self.discord.send.call_count, 2)
        self.assertEqual(self.db.execute('SELECT closed FROM deliveries').fetchone()[0], 1)
        deliver(self.db, self.discord, trade(True))
        self.assertEqual(self.discord.send.call_count, 2)

    def test_payload_is_whitelisted_and_costs_are_explicit(self):
        output = payload(trade(True))
        encoded = json.dumps(output)
        for private in ('PRIVATE', 'DO_NOT_PUBLISH', 'account_id', 'entry_fill_id'):
            self.assertNotIn(private, encoded)
        self.assertIn('PRACTICE', encoded)
        self.assertEqual(output['_receipt']['net'], '+$48.78')
        self.assertTrue(output['_receipt']['estimated_fees'])
        self.assertNotIn('take profit', encoded)
        self.assertNotIn('take_profit', encoded)
        self.assertNotIn('exit_reason', encoded)
        self.assertNotIn('Trade complete. P&L', encoded)
        self.assertNotIn('configured round-turn schedule', encoded)
        self.assertEqual(output['allowed_mentions'], {'parse': []})

    def test_timeline_waits_for_verified_result_and_stops_animating(self):
        row = trade()
        opened = payload(row)
        self.assertTrue(opened['attachments'][0]['filename'].endswith('.gif'))
        self.assertEqual(opened['embeds'], [])
        self.assertEqual(opened['_receipt']['color'], 0xF0B232)
        row['runner']['stats_status'] = 'awaiting_exit_fill'
        pending = payload(row)
        self.assertEqual(pending['_receipt']['state'], 'reconciling')
        self.assertNotEqual(pending['attachments'], opened['attachments'])
        self.assertTrue(payload(trade(True))['attachments'][0]['filename'].endswith('.png'))
        snapshot = payload(trade(), snapshot=True)
        self.assertTrue(snapshot['attachments'][0]['filename'].endswith('.png'))
        self.assertTrue(snapshot['_receipt']['snapshot'])

    def test_animation_needs_no_poll_edits_and_close_keeps_original_message(self):
        with patch('tools.discord_topbot.time.time', return_value=150):
            deliver(self.db, self.discord, trade())
        with patch('tools.discord_topbot.time.time', return_value=155):
            deliver(self.db, self.discord, trade())
        self.assertEqual(self.discord.send.call_count, 1)
        with patch('tools.discord_topbot.time.time', return_value=165):
            deliver(self.db, self.discord, trade())
        self.assertEqual(self.discord.send.call_count, 1)
        with patch('tools.discord_topbot.time.time', return_value=166):
            deliver(self.db, self.discord, trade(True))
        self.assertEqual(self.discord.send.call_count, 2)
        self.assertEqual(self.discord.send.call_args.args[1], '12345')

    def test_attachments_replace_previous_image_and_use_multipart(self):
        client = Discord('https://discord.com/api/webhooks/1/SECRET', '42')
        client.session = Mock()
        client.session.request.return_value.status_code = 200
        client.session.request.return_value.json.return_value = {'id': '12345', 'channel_id': '42'}
        for row, extension in [(trade(), '.gif'), (trade(True), '.png')]:
            client.send(payload(row), '12345')
            call = client.session.request.call_args
            self.assertEqual(call.args[0], 'PATCH')
            self.assertNotIn('json', call.kwargs)
            body = json.loads(call.kwargs['data']['payload_json'])
            self.assertNotIn('_receipt', body)
            self.assertEqual(len(body['attachments']), 1)
            filename, content, mime = call.kwargs['files']['files[0]']
            self.assertTrue(filename.endswith(extension))
            self.assertEqual(body['embeds'], [])
            self.assertLess(len(content), 2000000)
            from PIL import Image
            with Image.open(io.BytesIO(content)) as image:
                self.assertEqual(image.size, (1000, 720))
                self.assertEqual(image.n_frames, 32 if extension == '.gif' else 1)

    def test_attachment_paths_are_allowlisted(self):
        from tools.discord_topbot_media import attachment_files
        with self.assertRaises(ValueError):
            attachment_files({'attachments': [{'id': 0, 'filename': '../config.json'}]})

    def test_buttons_only_for_interactive_transport_and_no_trade_actions(self):
        self.assertNotIn('components', payload(trade()))
        buttons = payload(trade(), interactive=True)['components'][0]['components']
        self.assertEqual([b['label'] for b in buttons], ['View trade', 'Refresh status'])
        self.assertEqual([b['custom_id'] for b in buttons], ['topbot:view:101', 'topbot:refresh:101'])

    def test_short_and_unverified_account_are_not_mislabeled(self):
        row = trade()
        row['side'], row['provider_simulated'] = 'SELL', None
        model = payload(row)['_receipt']
        self.assertEqual(model['side'], 'SHORT')
        self.assertEqual(model['mode'], 'ACCOUNT TYPE UNVERIFIED')

    def test_receipt_loss_color_and_overnight_times_without_exit_reason(self):
        from tools.discord_topbot_receipt import clock
        row = trade(True)
        row['runner'].update(gross_pnl_usd=-50, net_pnl_usd=-51.22, exit_reason='stop_loss')
        data = payload(row)
        self.assertEqual(data['_receipt']['color'], 0xE74C3C)
        self.assertEqual(data['_receipt']['net'], '-$51.22')
        self.assertNotIn('stop_loss', json.dumps(data))
        self.assertEqual(clock('2026-11-13T05:10:00Z', '2026-11-13T04:50:00Z'), '11/13 12:10 AM')

    def test_only_verified_fills_get_posted(self):
        for change in ('dry_run', 'no_fill', 'ambiguous'):
            row = trade()
            if change == 'dry_run': row['execution_mode'] = 'dry_run'
            if change == 'no_fill': row['runner'].pop('entry_fill_id')
            if change == 'ambiguous': row['runner']['stats_status'] = 'ambiguous_entry_fills'
            deliver(self.db, self.discord, row)
        self.discord.send.assert_not_called()

    def test_invalid_or_unreconciled_pnl_never_posts(self):
        for field, value in [('net_pnl_usd', 50), ('fees_usd', -1), ('exit_price', float('nan'))]:
            row = trade(True)
            row['runner'][field] = value
            with self.assertRaises(ValueError): payload(row)

    def test_timeout_on_create_is_not_blindly_retried(self):
        self.discord.send.side_effect = DeliveryError('uncertain')
        with self.assertRaises(DeliveryError): deliver(self.db, self.discord, trade())
        self.db.close()
        self.db = state_db(self.path)
        deliver(self.db, self.discord, trade(True))
        self.assertEqual(self.discord.send.call_count, 1)
        self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0], 'uncertain')

    def test_crash_before_saving_message_id_requires_reconciliation(self):
        self.db.execute("UPDATE deliveries SET status='sending'")
        self.db.commit()
        self.db.close()
        self.db = state_db(self.path)
        deliver(self.db, self.discord, trade())
        self.discord.send.assert_not_called()

    def test_unexpected_post_failure_in_same_process_is_not_retried(self):
        self.discord.send.side_effect = RuntimeError('unexpected transport failure')
        with self.assertRaises(RuntimeError): deliver(self.db, self.discord, trade())
        self.discord.send.side_effect = None
        deliver(self.db, self.discord, trade())
        self.assertEqual(self.discord.send.call_count, 1)
        self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0], 'uncertain')

    def test_rate_limit_retries_and_edit_timeout_is_idempotent(self):
        self.discord.send.side_effect = DeliveryError('rate_limited', 3)
        with self.assertRaises(DeliveryError): deliver(self.db, self.discord, trade())
        self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0], 'ready')
        self.discord.send.side_effect = None
        deliver(self.db, self.discord, trade())
        self.discord.send.side_effect = DeliveryError('network')
        with self.assertRaises(DeliveryError): deliver(self.db, self.discord, trade(True))
        self.discord.send.side_effect = None
        deliver(self.db, self.discord, trade(True))
        self.assertEqual(self.discord.send.call_args.args[1], '12345')

    def test_deleted_message_does_not_repost_automatically(self):
        deliver(self.db, self.discord, trade())
        self.discord.send.side_effect = DeliveryError('missing_message')
        with self.assertRaises(DeliveryError): deliver(self.db, self.discord, trade(True))
        deliver(self.db, self.discord, trade(True))
        self.assertEqual(self.discord.send.call_count, 2)

    def test_discovery_persists_unfilled_entry_and_recovers_later_fill(self):
        self.db.execute("INSERT INTO meta VALUES('cursor','100')")
        self.db.commit()
        source = Mock()
        waiting = trade()
        waiting['runner'] = {'stats_status': 'awaiting_fill'}
        source.rows.side_effect = [[waiting], [waiting]]
        cycle(self.db, source, self.discord)
        self.discord.send.assert_not_called()
        source.rows.side_effect = [[], [trade(True)]]
        cycle(self.db, source, self.discord)
        self.assertEqual(self.discord.send.call_count, 1)
        self.assertIn('CLOSED', json.dumps(self.discord.send.call_args.args[0]))

    def test_endpoint_validation_and_network_errors_do_not_expose_secret(self):
        for url in ['http://discord.com/api/webhooks/1/SECRET',
                    'https://evil.test/api/webhooks/1/SECRET',
                    'https://discord.com/api/webhooks/1/SECRET?redirect=evil']:
            with self.assertRaises(ValueError): Discord(url, '42')
        client = Discord('https://discord.com/api/webhooks/1/SECRET', '42')
        client.session = Mock()
        client.session.request.side_effect = requests.ConnectionError('SECRET')
        with self.assertRaises(DeliveryError) as caught: client.send(payload(trade()))
        self.assertEqual(str(caught.exception), 'uncertain')

    def test_wrong_channel_and_429_handling(self):
        client = Discord('https://discord.com/api/webhooks/1/SECRET', '42')
        client.session = Mock()
        response = client.session.request.return_value
        response.status_code = 200
        response.json.return_value = {'channel_id': '99'}
        with self.assertRaises(ValueError): client.verify()
        response.status_code = 429
        response.json.return_value = {'retry_after': 2.5}
        with self.assertRaises(DeliveryError) as caught: client.send(payload(trade()))
        self.assertEqual(caught.exception.retry_after, 2.5)

    def test_standalone_entrypoint_shares_transport_exception_classes(self):
        tools_dir = Path(__file__).parents[1] / 'tools'
        with patch.dict(sys.modules), patch.object(sys, 'path', [str(tools_dir), *sys.path]), patch('argparse.ArgumentParser.parse_args', side_effect=RuntimeError), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                runpy.run_path(str(tools_dir / 'discord_topbot.py'), run_name='__main__')
            spec = importlib.util.spec_from_file_location('standalone_buttons', tools_dir / 'discord_topbot_buttons.py')
            companion = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(companion)
            self.assertIs(companion.DeliveryError, sys.modules['discord_topbot'].DeliveryError)


if __name__ == '__main__':
    unittest.main()
