"""Optional Discord bot transport and read-only trade button interactions.

The Gateway thread only queues requests. Source reads, delivery state and public
message edits stay on the reporter thread, serialized with normal polling.
"""
import asyncio
from concurrent.futures import Future
import io
import queue
import re
import threading
import time

import requests

if __package__:
    from .discord_topbot import Discord, DeliveryError, attachment_files, deliver, payload
else:
    from discord_topbot import Discord, DeliveryError, attachment_files, deliver, payload


class BotDiscord(Discord):
    interactive = True

    def __init__(self, token, guild_id, legacy):
        if not token or any(c.isspace() for c in token):
            raise ValueError('Invalid bot token file')
        if not str(guild_id).isdigit() or not legacy.channel_id.isdigit():
            raise ValueError('Invalid Discord destination')
        self.token, self.guild_id, self.legacy = token, str(guild_id), legacy
        self.channel_id = legacy.channel_id
        self.url = 'https://discord.com/api/v10'
        self.session = requests.Session()
        self.session.headers.update(Authorization='Bot ' + token,
                                    **{'User-Agent': 'TopSignal-TopBot-Reporter/2.0'})

    def verify(self):
        self.user_id = str(self.request('GET', '/users/@me')['id'])
        channel = self.request('GET', '/channels/' + self.channel_id)
        if str(channel.get('guild_id')) != self.guild_id:
            raise ValueError('Bot guild mismatch')

    def send(self, data, message_id=None):
        route = '/channels/' + self.channel_id + '/messages'
        if message_id:
            if not str(message_id).isdigit():
                raise ValueError('Invalid message ID')
            route += '/' + str(message_id)
            # Keep existing webhook messages in place when enabling the bot.
            # A bot cannot attach callbacks to another author's message.
            original = self.request('GET', route)
            if str(original.get('webhook_id')) == self.legacy.url.split('/')[-2]:
                return self.legacy.send({k: v for k, v in data.items() if k != 'components'}, message_id)
            if str(original.get('author', {}).get('id')) != self.user_id:
                raise DeliveryError('message_owner_mismatch')
        result = self.request('PATCH' if message_id else 'POST', route, data)
        identity = str(result.get('id', ''))
        if (not identity.isdigit() or str(result.get('channel_id')) != self.channel_id
                or (message_id and identity != str(message_id))):
            raise DeliveryError('invalid_response' if message_id else 'uncertain')
        return identity


class Buttons:
    def __init__(self, transport):
        self.transport = transport
        self.pending = queue.Queue(maxsize=32)
        self.cooldowns = {}
        self.client = None
        self.loop = None
        self.thread = None

    def ready(self):
        return self.client is not None and self.client.is_ready()

    async def handle(self, interaction):
        import discord

        if interaction.type != discord.InteractionType.component:
            return
        match = re.fullmatch(r'topbot:(view|refresh):(\d{1,20})',
                             (interaction.data or {}).get('custom_id', ''))
        if not match:
            return
        message = interaction.message
        if (str(interaction.guild_id) != self.transport.guild_id
                or str(interaction.channel_id) != self.transport.channel_id
                or message is None or str(message.author.id) != self.transport.user_id):
            await interaction.response.send_message('This control is not available here.', ephemeral=True)
            return
        # Acknowledge immediately, before any DB/network work. No message text,
        # members, or privileged Gateway intents are required.
        await interaction.response.defer(ephemeral=True, thinking=True)
        future = Future()
        try:
            self.pending.put_nowait((match[1], int(match[2]), str(message.id), future))
        except queue.Full:
            await interaction.followup.send('TopBot is busy. Please try again shortly.', ephemeral=True)
            return
        try:
            result = await asyncio.wait_for(asyncio.wrap_future(future), timeout=60)
        except (TimeoutError, asyncio.CancelledError):
            future.cancel()
            await interaction.followup.send('Status check timed out. Please try again shortly.', ephemeral=True)
            return
        files = [discord.File(io.BytesIO(content), filename=item['filename'],
                              description=item['description'])
                 for item, content in attachment_files(result)]
        try:
            await interaction.followup.send(
                content=result.get('content'), ephemeral=True, files=files,
                embeds=[discord.Embed.from_dict(e) for e in result.get('embeds', [])],
                allowed_mentions=discord.AllowedMentions.none())
        finally:
            for file in files:
                file.close()

    def drain(self, db, source):
        # A bounded queue and per-trade cooldown keep clicks from starving the
        # automatic reporter. Futures cancelled on timeout never trigger writes.
        current = time.monotonic()
        self.cooldowns = {key: until for key, until in self.cooldowns.items() if until > current}
        for _ in range(4):
            try:
                action, trade_id, message_id, future = self.pending.get_nowait()
            except queue.Empty:
                return [], 5
            if not future.set_running_or_notify_cancel():
                continue
            result = {'content': 'Unable to verify this trade right now. Please try again shortly.'}
            try:
                saved = db.execute('SELECT message_id,status FROM deliveries WHERE id=?', (trade_id,)).fetchone()
                if not saved or saved['message_id'] != message_id or saved['status'] != 'ready':
                    result = {'content': 'This message is not an active TopBot report.'}
                elif trade_id in self.cooldowns:
                    result = {'content': 'This trade was just checked. Please wait a few seconds.'}
                else:
                    self.cooldowns[trade_id] = current + 10
                    rows = source.rows(ids=[trade_id])
                    if len(rows) == 1 and rows[0]['id'] == trade_id:
                        data = payload(rows[0], snapshot=True)
                        if data:
                            if action == 'refresh':
                                deliver(db, self.transport, rows[0], force=True)
                            result = dict(data, content=('Status checked; the trade message is up to date.'
                                                       if action == 'refresh' else 'Latest verified trade details.'))
            except DeliveryError as exc:
                db.rollback()
                future.set_result(result)
                return [exc.kind], exc.retry_after
            except Exception:
                # No SQL, provider objects, token URLs, or raw exceptions in replies.
                db.rollback()
            future.set_result(result)
        return [], 5

    def start(self):
        # Optional dependency: webhook-only reporting never imports discord.py.
        import discord

        self.client = discord.Client(intents=discord.Intents.none(), max_messages=None)

        @self.client.event
        async def on_interaction(interaction):
            try:
                await self.handle(interaction)
            except Exception:
                # The SDK can include interaction tokens in HTTP diagnostics.
                # Automatic polling continues if a private acknowledgement fails.
                pass

        async def run():
            self.loop = asyncio.get_running_loop()
            async with self.client:
                await self.client.start(self.transport.token)

        def worker():
            try:
                asyncio.run(run())
            except Exception:
                pass  # main reporter health exposes buttons_disconnected

        self.thread = threading.Thread(target=worker, name='topbot-discord-buttons', daemon=True)
        self.thread.start()

    def close(self):
        if self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self.client.close(), self.loop).result(timeout=10)
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=10)
