"""Outbound-only Twitch Device OAuth and EventSub; no public HTTP listener."""
import asyncio
from contextlib import suppress
from datetime import datetime
import logging
import re
import time
from urllib.parse import urlsplit

import aiohttp
import discord
import config
from database import twitch
from services import streamer_hub_service as hub

log = logging.getLogger(__name__)
WS_URL = 'wss://eventsub.wss.twitch.tv/ws'


class TwitchError(Exception):
    def __init__(self, status, code='request_failed'):
        self.status, self.code = status, code
        super().__init__('Twitch request failed')  # Never include payloads/tokens/URLs.


class TwitchAPI:
    async def request(self, method, path, *, token=None, oauth=False, data=None, params=None, body=None):
        url = ('https://id.twitch.tv/oauth2/' if oauth else 'https://api.twitch.tv/helix/') + path
        headers = {'Client-Id': config.TWITCH_CLIENT_ID}
        if token: headers['Authorization'] = ('OAuth ' if path == 'validate' else 'Bearer ') + token
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.request(method, url, headers=headers, data=data, params=params, json=body, allow_redirects=False) as response:
                result = await response.json() if response.status != 204 else {}
                if response.status >= 300:
                    code = result.get('message', '')
                    raise TwitchError(response.status, code if code in {'authorization_pending', 'slow_down', 'access_denied', 'expired_token'} else 'request_failed')
                return result

    async def device(self):
        return await self.request('POST', 'device', oauth=True, data={'client_id': config.TWITCH_CLIENT_ID, 'scopes': ''})

    async def exchange(self, code):
        return await self.request('POST', 'token', oauth=True, data={'client_id': config.TWITCH_CLIENT_ID, 'scopes': '',
            'device_code': code, 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'})

    async def refresh(self, row):
        return await self.request('POST', 'token', oauth=True, data={'client_id': config.TWITCH_CLIENT_ID,
            'refresh_token': row['refresh_token'], 'grant_type': 'refresh_token'})

    async def account(self, token, expected=None):
        valid = await self.request('GET', 'validate', oauth=True, token=token)
        if valid.get('client_id') != config.TWITCH_CLIENT_ID or not valid.get('user_id') or (expected and valid['user_id'] != expected):
            raise TwitchError(401)
        users = await self.request('GET', 'users', token=token)
        user = users['data'][0]
        if user['id'] != valid['user_id'] or not re.fullmatch(r'[a-zA-Z0-9_]{1,25}', user['login']):
            raise TwitchError(401)
        return user

    async def stream(self, row):
        result = await self.request('GET', 'streams', token=row['access_token'], params={'user_id': row['twitch_user_id']})
        return result['data'][0] if result['data'] else None

    async def subscribe(self, row, session_id):
        ids = []
        for event in ('stream.online', 'stream.offline'):
            result = await self.request('POST', 'eventsub/subscriptions', token=row['access_token'], body={
                'type': event, 'version': '1', 'condition': {'broadcaster_user_id': row['twitch_user_id']},
                'transport': {'method': 'websocket', 'session_id': session_id}})
            ids.append(result['data'][0]['id'])
        twitch.subscriptions(row, ids)

    async def revoke(self, token):
        await self.request('POST', 'revoke', oauth=True, data={'client_id': config.TWITCH_CLIENT_ID, 'token': token})


def notification(row, stream):
    if not re.fullmatch(r'[a-zA-Z0-9_]{1,25}', row['twitch_login']):
        raise ValueError('Invalid Twitch login')
    def clean(text, limit):
        return discord.utils.escape_markdown(discord.utils.escape_mentions(str(text)))[:limit].replace('\n', ' ')
    lines = [f"# 🔴 {clean(row['twitch_display_name'], 100)} is live!"]
    if stream.get('title'): lines += ['', '**' + clean(stream['title'], 200) + '**']
    if stream.get('game_name'): lines += ['', '🎮 ' + clean(stream['game_name'], 100)]
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label='Watch on Twitch', emoji='🟣', url='https://www.twitch.tv/' + row['twitch_login']))
    return dict(content='\n'.join(lines), view=view, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)


class TwitchHub:
    def __init__(self, bot):
        self.bot, self.api = bot, TwitchAPI()
        self.tasks, self.pending, self.locks, self.attempts = {}, {}, {}, {}
        self.initialized = False

    def lock(self, key):
        return self.locks.setdefault(key, asyncio.Lock())

    async def start(self):
        if self.initialized or not config.STREAMER_HUB_ENABLED:
            return
        self.initialized = True
        for row in twitch.connections():
            if row['guild_id'] == config.GUILD_ID and row['notifications_enabled']:
                self.launch(row)

    def launch(self, row):
        key = (row['guild_id'], row['discord_user_id'])
        if key not in self.tasks or self.tasks[key].done():
            self.tasks[key] = asyncio.create_task(self.watch(key))

    async def close(self):
        self.initialized = False
        tasks = list(self.tasks.values()) + list(self.pending.values())
        for task in tasks: task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError): await task
        self.tasks.clear()
        self.pending.clear()
        self.attempts.clear()

    async def disconnect(self, guild, user_id):
        key = (guild.id, user_id)
        self.attempts.pop(key, None)
        for tasks in (self.pending, self.tasks):
            task = tasks.pop(key, None)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError): await task
        async with self.lock(key):
            row = twitch.connection(*key)
            twitch.disconnect(*key)
        if row:
            try:
                await self.api.revoke(row['access_token'])
            except Exception:
                log.warning('[twitch] local disconnect complete; remote revocation unavailable')
        # Closing the dedicated socket removes its subscriptions on Twitch.

    async def online(self, row, expected_session=None):
        key = (row['guild_id'], row['discord_user_id'])
        async with self.lock(key):
            current = twitch.connection(*key)
            guild = self.bot.get_guild(row['guild_id'])
            if not current or current['twitch_user_id'] != row['twitch_user_id'] or not current['notifications_enabled'] or not await hub.authorize(guild, row['discord_user_id']):
                return
            target = hub.channel(guild, 'stream-updates')
            if not target: return
            stream = await self.api.stream(current)
            if not stream:
                twitch.offline(row)
                return
            if stream.get('user_id') != row['twitch_user_id'] or (expected_session and stream['id'] != expected_session):
                return
            if stream.get('user_login') and re.fullmatch(r'[a-zA-Z0-9_]{1,25}', stream['user_login']):
                current['twitch_login'] = stream['user_login']
                current['twitch_display_name'] = stream.get('user_name') or current['twitch_display_name']
                twitch.identity(row, {'login': current['twitch_login'], 'display_name': current['twitch_display_name']})
            twitch.live(row, stream)
            if not twitch.claim(row, target.id, stream): return
            try:
                message = await target.send(**notification(current, stream))
            except Exception:
                twitch.finish(row, stream['id'], 'uncertain')
                log.warning('[twitch] delivery uncertain; retained session claim')
                return
            twitch.finish(row, stream['id'], 'posted', message.id)

    async def event(self, row, packet):
        payload = packet.get('payload', {})
        event = payload.get('event', {})
        kind = payload.get('subscription', {}).get('type')
        if event.get('broadcaster_user_id') != row['twitch_user_id']: return
        if kind == 'stream.online':
            await self.online(row, event.get('id'))
        elif kind == 'stream.offline':
            # Compare UTC values rather than lexicographic fractional timestamps.
            stamp = packet['metadata']['message_timestamp']
            current = twitch.connection(row['guild_id'], row['discord_user_id'])
            if current and (not current['last_live_started_at'] or datetime.fromisoformat(stamp.replace('Z', '+00:00')) >= datetime.fromisoformat(current['last_live_started_at'].replace('Z', '+00:00'))):
                twitch.offline(row)

    async def valid_token(self, row):
        if row['expires_at'] < time.time() + 3600:
            token = await self.api.refresh(row)
            twitch.tokens(row, token)
            row = twitch.connection(row['guild_id'], row['discord_user_id'])
        try:
            account = await self.api.account(row['access_token'], row['twitch_user_id'])
        except TwitchError as exc:
            if exc.status != 401: raise
            token = await self.api.refresh(row)
            twitch.tokens(row, token)
            row = twitch.connection(row['guild_id'], row['discord_user_id'])
            account = await self.api.account(row['access_token'], row['twitch_user_id'])
        twitch.identity(row, account)
        return twitch.connection(row['guild_id'], row['discord_user_id'])

    async def socket(self, row):
        # One socket per authorized user, two subscriptions; all traffic outbound.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            ws = await session.ws_connect(WS_URL, autoping=True, heartbeat=None, max_msg_size=1_000_000)
            try:
                welcome = await ws.receive_json(timeout=10)
                details = welcome['payload']['session']
                await self.api.subscribe(row, details['id'])
                timeout = details.get('keepalive_timeout_seconds') or 10
                await self.online(row)  # Reconcile live sessions missed during downtime.
                validated = time.monotonic()
                while config.STREAMER_HUB_ENABLED:
                    packet = await ws.receive_json(timeout=timeout + 2)
                    kind = packet['metadata']['message_type']
                    if kind == 'session_reconnect':
                        url = packet['payload']['session']['reconnect_url']
                        parsed = urlsplit(url)
                        if parsed.scheme != 'wss' or parsed.netloc != 'eventsub.wss.twitch.tv' or parsed.path != '/ws':
                            raise TwitchError(400)
                        replacement = await session.ws_connect(url, autoping=True, heartbeat=None, max_msg_size=1_000_000)
                        try:
                            welcome = await replacement.receive_json(timeout=10)
                            if welcome['metadata']['message_type'] != 'session_welcome': raise TwitchError(400)
                        except BaseException:
                            await replacement.close()
                            raise
                        await ws.close()
                        ws = replacement  # Twitch transfers subscriptions; do not resubscribe.
                        timeout = welcome['payload']['session'].get('keepalive_timeout_seconds') or timeout
                        await self.online(row)
                    elif kind == 'notification':
                        await self.event(row, packet)
                    elif kind == 'revocation':
                        raise TwitchError(401)
                    if time.monotonic() - validated > 3300:
                        row = await self.valid_token(row)
                        validated = time.monotonic()
            finally:
                await ws.close()

    async def watch(self, key):
        delay = 5
        while config.STREAMER_HUB_ENABLED:
            row = twitch.connection(*key)
            if not row or not row['notifications_enabled']: return
            try:
                row = await self.valid_token(row)
                await self.socket(row)
            except TwitchError as exc:
                if exc.status in (400, 401, 403):
                    twitch.disable(row)
                    log.warning('[twitch] authorization unavailable; reconnect required')
                    return
                log.warning('[twitch] service unavailable; retrying with backoff')
            except Exception as exc:
                log.warning('[twitch] connection interrupted (%s); retrying with backoff', type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
