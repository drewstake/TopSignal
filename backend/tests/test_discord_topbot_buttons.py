import asyncio
from concurrent.futures import Future
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from tools.discord_topbot import Discord, DeliveryError, payload, state_db
from tools.discord_topbot_buttons import BotDiscord, Buttons
from tests.test_discord_topbot import trade


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = state_db(Path(self.temp.name) / 'delivery.sqlite3')
        self.db.execute("INSERT INTO deliveries(id,message_id) VALUES(101,'12345')")
        self.db.commit()
        self.transport = Mock(channel_id='42', guild_id='99', user_id='77', interactive=True)
        self.transport.send.return_value = '12345'
        self.buttons = Buttons(self.transport)
        self.source = Mock()
        self.source.rows.return_value = [trade()]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def request(self, action='view', trade_id=101, message_id='12345'):
        future = Future()
        self.buttons.pending.put((action, trade_id, message_id, future))
        result = self.buttons.drain(self.db, self.source)
        return future.result(), result

    def test_view_is_private_snapshot_with_no_public_write(self):
        result, _ = self.request()
        self.assertIn('Latest verified', result['content'])
        self.assertNotIn('components', result)
        self.assertTrue(result['attachments'][0]['filename'].endswith('.png'))
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.transport.send.assert_not_called()
        self.source.rows.assert_called_once_with(ids=[101])

    def test_refresh_reconciles_existing_message_and_persists_digest(self):
        self.source.rows.return_value = [trade(True)]
        result, _ = self.request('refresh')
        self.assertIn('up to date', result['content'])
        self.assertEqual(self.transport.send.call_args.args[1], '12345')
        self.assertEqual(result['_receipt']['status'], 'CLOSED')
        saved = self.db.execute('SELECT closed,digest FROM deliveries').fetchone()
        self.assertEqual(saved['closed'], 1)
        self.assertTrue(saved['digest'])

    def test_forged_trade_or_message_id_cannot_read_source(self):
        for trade_id, message_id in [(999, '12345'), (101, '22222')]:
            result, _ = self.request(trade_id=trade_id, message_id=message_id)
            self.assertIn('not an active', result['content'])
        self.source.rows.assert_not_called()
        self.transport.send.assert_not_called()

    def test_repeated_clicks_and_cancelled_requests_do_not_hit_database(self):
        self.request()
        result, _ = self.request('refresh')
        self.assertIn('just checked', result['content'])
        future = Future()
        future.cancel()
        self.buttons.pending.put(('refresh', 101, '12345', future))
        self.buttons.drain(self.db, self.source)
        self.source.rows.assert_called_once()
        self.transport.send.assert_not_called()

    def test_errors_are_sanitized_and_rate_limits_return_to_poll_loop(self):
        self.transport.send.side_effect = DeliveryError('rate_limited', 30)
        result, (errors, delay) = self.request('refresh')
        self.assertEqual(errors, ['rate_limited'])
        self.assertEqual(delay, 30)
        self.assertNotIn('up to date', result['content'])
        self.buttons.cooldowns.clear()
        self.source.rows.side_effect = ValueError('SECRET SQL')
        result, _ = self.request()
        self.assertNotIn('SECRET', json.dumps(result))

    def test_gateway_acknowledges_before_queue_and_replies_ephemerally(self):
        interaction = SimpleNamespace(
            type=discord.InteractionType.component, data={'custom_id': 'topbot:view:101'},
            guild_id=99, channel_id=42, message=SimpleNamespace(id=12345, author=SimpleNamespace(id=77)),
            response=AsyncMock(), followup=AsyncMock())

        async def exercise():
            task = asyncio.create_task(self.buttons.handle(interaction))
            await asyncio.sleep(0)
            interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            self.buttons.drain(self.db, self.source)
            await task

        asyncio.run(exercise())
        self.assertTrue(interaction.followup.send.call_args.kwargs['ephemeral'])
        self.assertTrue(interaction.followup.send.call_args.kwargs['files'][0].filename.endswith('.png'))
        self.assertFalse(interaction.followup.send.call_args.kwargs['allowed_mentions'].everyone)
        interaction.channel_id = 666
        asyncio.run(self.buttons.handle(interaction))
        interaction.response.send_message.assert_awaited_once()


class BotTransportTests(unittest.TestCase):
    def setUp(self):
        self.legacy = Discord('https://discord.com/api/webhooks/1/SECRET', '42')
        self.legacy.send = Mock(return_value='12345')
        self.bot = BotDiscord('TOKEN', '99', self.legacy)
        self.bot.user_id = '77'
        self.bot.request = Mock()

    def test_new_and_existing_bot_messages_use_bot_rest(self):
        data = payload(trade(), interactive=True)
        self.bot.request.return_value = {'id': '12345', 'channel_id': '42'}
        self.assertEqual(self.bot.send(data), '12345')
        self.bot.request.assert_called_with('POST', '/channels/42/messages', data)
        self.bot.request.side_effect = [{'author': {'id': '77'}}, {'id': '12345', 'channel_id': '42'}]
        self.assertEqual(self.bot.send(data, '12345'), '12345')
        self.bot.request.assert_called_with('PATCH', '/channels/42/messages/12345', data)

    def test_legacy_message_edits_keep_webhook_and_remove_unsupported_buttons(self):
        self.bot.request.return_value = {'webhook_id': '1'}
        self.bot.send(payload(trade(), interactive=True), '12345')
        sent, message_id = self.legacy.send.call_args.args
        self.assertEqual(message_id, '12345')
        self.assertNotIn('components', sent)
        self.assertTrue(sent['attachments'][0]['filename'].endswith('.gif'))

    def test_other_authors_and_wrong_guild_are_rejected(self):
        self.bot.request.return_value = {'author': {'id': '88'}}
        with self.assertRaises(DeliveryError):
            self.bot.send(payload(trade(), interactive=True), '12345')
        self.bot.request.side_effect = [{'id': '77'}, {'guild_id': '100'}]
        with self.assertRaises(ValueError):
            self.bot.verify()
        self.legacy.send.assert_not_called()

    def test_restart_does_not_depend_on_in_memory_message_ownership(self):
        restarted = BotDiscord('TOKEN', '99', self.legacy)
        restarted.user_id = '77'
        restarted.request = Mock(side_effect=[{'author': {'id': '77'}}, {'id': '12345', 'channel_id': '42'}])
        self.assertEqual(restarted.send(payload(trade(True), interactive=True), '12345'), '12345')

    def test_missing_message_and_network_errors_never_repost_or_expose_token(self):
        import requests

        self.bot.request = BotDiscord.request.__get__(self.bot)
        self.bot.session = Mock()
        self.bot.session.request.return_value.status_code = 404
        with self.assertRaises(DeliveryError) as caught:
            self.bot.send(payload(trade(), interactive=True), '12345')
        self.assertEqual(caught.exception.kind, 'missing_message')
        self.bot.session.request.side_effect = requests.ConnectionError('TOKEN')
        with self.assertRaises(DeliveryError) as caught:
            self.bot.send(payload(trade(), interactive=True))
        self.assertEqual(str(caught.exception), 'uncertain')
        self.legacy.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
