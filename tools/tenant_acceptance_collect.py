"""Ephemeral GET-only REST adapter; executed inside the already running Host.

No Discord login/start/connect, Gateway session or lifecycle mutation. Credentials
come only from the existing container environment and never enter the receipt.
"""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from types import SimpleNamespace
from urllib.parse import urlsplit

import aiohttp
import discord

from database import db
from database.tenants import GuildTenantStore
from services.tenant_routing import verify_membership, verify_guild_permissions, GuildRoutes
from services.tenant_acceptance import collect, fingerprint, guard_probes


MAX_BODY = 2 * 1024**2


class AcceptanceReadError(ValueError):
    def __init__(self, reason, status):
        super().__init__(reason)
        self.status = status


class ReadOnlyDiscord:
    def __init__(self, session, token, application, cohort):
        self.session, self.token, self.application = session, token, application
        self.cohort = frozenset(cohort)
        self.guild_id = None
        self.owner_id = None
        self.channels = set()
        self.requests = 0
        self.denied = 0

    async def request(self, route, **kwargs):
        self.requests += 1
        if self.requests > 500:
            raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
        allowed = route.method == 'GET' and route.path in {'/users/@me', '/users/@me/guilds'}
        guild = getattr(route, 'guild_id', None)
        channel = getattr(route, 'channel_id', None)
        if route.method == 'GET' and guild == self.guild_id and guild in self.cohort:
            allowed = route.path in {'/guilds/{guild_id}', '/guilds/{guild_id}/channels', '/guilds/{guild_id}/roles'}
            if route.path in {'/guilds/{guild_id}/members/{user_id}', '/guilds/{guild_id}/members/{member_id}'}:
                user = int(route.url.rsplit('/', 1)[1])
                allowed = user in {self.application, self.owner_id}
        if route.method == 'GET' and channel in self.channels:
            allowed = route.path in {'/channels/{channel_id}', '/channels/{channel_id}/messages', '/channels/{channel_id}/messages/{message_id}',
                                    '/channels/{channel_id}/pins', '/channels/{channel_id}/messages/pins'}
        url = urlsplit(route.url)
        if url.scheme != 'https' or url.netloc != 'discord.com' or not url.path.startswith('/api/v10/'):
            allowed = False
        if not allowed:
            self.denied += 1
            raise ValueError('READ_ONLY_REQUEST_REJECTED')
        params = kwargs.get('params') or {}
        if any(key not in {'limit', 'before', 'after', 'around'} for key in params) or int(params.get('limit', 100)) > 100:
            raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
        async with self.session.get(route.url, params=params,
                                    headers={'Authorization': 'Bot ' + self.token}, allow_redirects=False) as response:
            body = await response.content.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
            if response.status == 404:
                raise discord.NotFound(response, {'code': 10003, 'message': 'Resource unavailable'})
            if response.status == 403:
                raise discord.Forbidden(response, {'code': 50013, 'message': 'Read access unavailable'})
            if response.status != 200:
                raise ValueError('DISCORD_READ_REJECTED')
            return json.loads(body)


async def gather(packet):
    application = int(packet['applicationId'])
    guilds = tuple(map(int, packet['guildIds']))
    if len(guilds) < 1 or len(set(guilds)) != len(guilds):
        raise ValueError('GUILD_IDENTITY_REJECTED')
    primary = int(packet['primaryGuildId'])
    if primary not in guilds:
        raise ValueError('GUILD_IDENTITY_REJECTED')
    database = Path(packet['database'])
    if database.is_symlink() or not database.is_file():
        raise ValueError('DATABASE_IDENTITY_REJECTED')
    store = GuildTenantStore(primary_guild=primary, primary_database=database,
                            tenant_directory=database.parent / 'tenants', additional_guilds=set(guilds) - {primary})
    # Provisioning is expressly excluded. Verify each already admitted file.
    for guild_id in guilds:
        with store.scope(guild_id) as binding:
            if not binding.database.is_file():
                raise ValueError('DATABASE_IDENTITY_REJECTED')
    token = os.environ.get('DISCORD_TOKEN') or os.environ.get('BOT_TOKEN')
    api_token = os.environ.get('GAMERHQ_WEB_API_SECRET')
    if not token or not api_token:
        raise ValueError('CREDENTIAL_UNAVAILABLE')
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        rest = ReadOnlyDiscord(session, token, application, guilds)
        from discord.http import Route
        identity = await rest.request(Route('GET', '/users/@me'))
        membership = await rest.request(Route('GET', '/users/@me/guilds'), params={'limit': 100})
        verify_membership(application, frozenset(guilds), identity, membership)
        # Client only supplies discord.py model deserialization, never login/start.
        client = discord.Client(intents=discord.Intents.none())
        client.get_cog = lambda _: None
        client.http.request = rest.request
        client._connection.user = discord.ClientUser(state=client._connection, data=identity)

        async def load_guild(guild_id):
            rest.guild_id, rest.channels, rest.owner_id = guild_id, set(), None
            payload = await rest.request(Route('GET', '/guilds/{guild_id}', guild_id=guild_id))
            if payload.get('id') != str(guild_id):
                raise ValueError('GUILD_IDENTITY_REJECTED')
            rest.owner_id = int(payload['owner_id'])
            roles = await rest.request(Route('GET', '/guilds/{guild_id}/roles', guild_id=guild_id))
            channels = await rest.request(Route('GET', '/guilds/{guild_id}/channels', guild_id=guild_id))
            if len(roles) > 300 or len(channels) > 600:
                raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
            if any(str(row.get('guild_id', guild_id)) != str(guild_id) for row in channels):
                raise ValueError('FOREIGN_RESOURCE_REJECTED')
            rest.channels = {int(row['id']) for row in channels}
            bot = await rest.request(Route('GET', '/guilds/{guild_id}/members/{user_id}', guild_id=guild_id, user_id=application))
            owner = await rest.request(Route('GET', '/guilds/{guild_id}/members/{user_id}', guild_id=guild_id, user_id=rest.owner_id))
            verify_guild_permissions(guild_id, application, payload, bot, roles)
            model = discord.Guild(data={**payload, 'roles': roles, 'channels': channels, 'members': [bot, owner]}, state=client._connection)
            model._add_member(discord.Member(data=owner, guild=model, state=client._connection))
            model._add_member(discord.Member(data=bot, guild=model, state=client._connection))
            client._connection._guilds[guild_id] = model
            return model, model.get_member(rest.owner_id), client

        async def host_get(guild_id, authorized):
            # Fixed protected loopback endpoint, no arbitrary URL or HTTP method.
            url = 'http://127.0.0.1:8080/api/v1/servers/' + str(guild_id) + '/skills'
            async with session.get(url, headers={'Authorization': 'Bearer ' + api_token,
                                                 'X-GamerHQ-Authorized-Guild-Ids': str(authorized)}, allow_redirects=False) as response:
                body = await response.content.read(262145)
                if len(body) > 262144:
                    raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
                return response.status, json.loads(body)

        async def load_skills(guild_id):
            status, body = await host_get(guild_id, guild_id)
            if status != 200:
                raise AcceptanceReadError('HOST_SKILL_READ_REJECTED', status)
            return body['skills']

        result = await collect(store, application, load_guild, load_skills)
        if len(guilds) == 2 and all(client.get_guild(guild) is not None for guild in guilds):
            routes = GuildRoutes(application_id=application, store=store, get_guild=client.get_guild)
            routes.verify(identity, membership)
            routes.accepting = True
            result['isolationGuards'] = await guard_probes(routes)
        else:
            result['isolationGuards'] = {'status': 'UNVERIFIED', 'reason': 'VERIFIED_TWO_GUILD_MODELS_REQUIRED'}
        denials = []
        for guild_id in guilds:
            other = next((value for value in guilds if value != guild_id), None)
            if other is not None:
                status, _ = await host_get(other, guild_id)
                denials.append({'authorizedGuild': str(guild_id), 'requestedGuild': str(other),
                                'httpStatus': status, 'status': 'PASS' if status == 403 else 'FAIL'})
        result['hostCrossTenantReadDenials'] = denials
        result['roleMembershipAudit'] = {'status': 'UNVERIFIED', 'reason': 'ONLY_OWNER_AND_HOST_MEMBERS_FETCHED'}
        result['discordReads'] = rest.requests
        result['mutationsAttempted'] |= rest.denied > 0
        if rest.denied or any(row['status'] == 'FAIL' for row in denials) or result.get('isolationGuards', {}).get('status') == 'FAIL':
            result['status'] = 'FAIL'
        return result


def main(packet):
    async def bounded():
        async with asyncio.timeout(540):
            return await gather(packet)
    return asyncio.run(bounded())
