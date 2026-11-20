"""Ephemeral GET-only REST adapter; executed inside the already running Host.

No Discord login/start/connect, Gateway session or lifecycle mutation. Credentials
come only from the existing container environment and never enter the receipt.
"""
import asyncio
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import aiohttp
import discord

from database import db
from database.tenants import GuildTenantStore
from services.tenant_routing import verify_membership, verify_guild_permissions, GuildRoutes
from services.tenant_acceptance import collect, fingerprint, guard_probes


MAX_BODY = 2 * 1024**2


async def read_bounded(stream,limit):
    """StreamReader.read(n) may return a fragment; require EOF within the cap."""
    chunks=[]
    total=0
    while True:
        chunk=await stream.read(min(65536,limit+1-total))
        if not chunk:return b''.join(chunks)
        total+=len(chunk)
        if total>limit:raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
        chunks.append(chunk)


def preview_client():
    """Model-only Host interface; no login, sync, cog load or Gateway.

    Shared renderers expect a tree even while building unrelated Core boards.
    This empty local tree is NOT evidence of the running command registry.
    Command-derived boards are rejected separately by the projection contract.
    """
    client = discord.Client(intents=discord.Intents.none())
    client.tree = discord.app_commands.CommandTree(client)
    client.get_cog = lambda _: None
    return client


class AcceptanceReadError(ValueError):
    def __init__(self, reason, status=None, *, operation=None):
        super().__init__(reason)
        self.status = status
        self.operation = operation


class ReadOnlyDiscord:
    def __init__(self, session, token, application, cohort):
        self.session, self.token, self.application = session, token, application
        self.cohort = frozenset(cohort)
        self.guild_id = None
        self.owner_id = None
        self.channels = set()
        self.requests = 0
        self.denied = 0
        self.failures = []
        # This adapter replaces discord.py's HTTP request handler, including
        # its rate limiter. Serialize this observer's GETs and honor Discord's
        # reset guidance across previews; never share a cached message result.
        self._gate = asyncio.Lock()
        self._not_before = 0
        self._waited = 0
        self._clock, self._sleep = time.monotonic, asyncio.sleep
        self.rate_limits = []
        self.rate_limit_count = 0

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
        operation = {'/users/@me':'BOT_IDENTITY', '/users/@me/guilds':'GUILD_MEMBERSHIP',
                     '/guilds/{guild_id}':'GUILD_IDENTITY', '/guilds/{guild_id}/roles':'GUILD_ROLES',
                     '/guilds/{guild_id}/channels':'GUILD_CHANNELS'}.get(route.path, 'MANAGED_MESSAGE')
        if '/members/' in route.path:
            operation = 'HOST_MEMBER' if int(route.url.rsplit('/',1)[1]) == self.application else 'OWNER_MEMBER'
        try:
            result = await self._get(route.url, params, operation,
                {'channelId':str(channel) if channel else None,
                 'messageId':route.url.rsplit('/',1)[1] if route.path.endswith('/messages/{message_id}') else None})
            if route.path == '/channels/{channel_id}/messages/{message_id}':
                if (not isinstance(result,dict) or str(result.get('id'))!=route.url.rsplit('/',1)[1]
                        or str(result.get('channel_id'))!=str(channel)):
                    raise AcceptanceReadError('DISCORD_MESSAGE_BINDING_REJECTED', operation=operation)
            return result
        except (TimeoutError, aiohttp.ClientConnectionError) as error:
            reason = 'READ_TIMEOUT' if isinstance(error, TimeoutError) else 'DISCORD_TRANSPORT_FAILED'
            if len(self.failures)<24:self.failures.append({'operation':operation,'reason':reason,'channelId':str(channel) if channel else None,
                'messageId':route.url.rsplit('/',1)[1] if route.path.endswith('/messages/{message_id}') else None})
            raise AcceptanceReadError(reason, operation=operation) from None
        except (AcceptanceReadError,discord.HTTPException) as error:
            if len(self.failures)<24:
                self.failures.append({'operation':operation,'httpStatus':error.status,
                    'reason':error.args[0] if isinstance(error,AcceptanceReadError) else 'DISCORD_HTTP_REJECTED',
                    'discordCode':error.code if isinstance(error,discord.HTTPException) else None,
                    'channelId':str(channel) if channel else None,
                    'messageId':route.url.rsplit('/',1)[1] if route.path.endswith('/messages/{message_id}') else None})
            raise

    async def _get(self, url, params, operation, context):
        async with self._gate:
            for attempt in range(3):
                pause=max(0,self._not_before-self._clock())
                if not math.isfinite(pause) or pause>30.1 or self._waited+pause>60:
                    raise AcceptanceReadError('DISCORD_RATE_LIMITED',429,operation=operation)
                if pause:
                    self._waited+=pause
                    await self._sleep(pause)
                if attempt:
                    self.requests+=1
                    if self.requests>500:raise AcceptanceReadError('ACCEPTANCE_BUDGET_EXCEEDED',operation=operation)
                status,value,headers = await self._response(url,params,operation)
                def seconds(raw):
                    if isinstance(raw,bool):return None
                    try:result=float(raw)
                    except (TypeError,ValueError,OverflowError):return None
                    return result if math.isfinite(result) and 0<=result<=30 else None
                reset=seconds(headers.get('X-RateLimit-Reset-After'))
                if str(headers.get('X-RateLimit-Remaining'))=='0':
                    # Unknown/oversized guidance is a hold, never permission
                    # to continue sending requests against an exhausted bucket.
                    if reset is not None:self._not_before=max(self._not_before,self._clock()+reset+0.05)
                    elif status!=429:self._not_before=float('inf')
                if status==429:
                    delay=seconds(value.get('retry_after')) if isinstance(value,dict) else None
                    self.rate_limit_count+=1
                    if len(self.rate_limits)<8:
                        self.rate_limits.append({**context,'operation':operation,'httpStatus':429,
                            'reason':'DISCORD_RATE_LIMITED','retryAfterSeconds':delay,
                            'global':value.get('global') is True if isinstance(value,dict) else False,
                            'attempt':attempt+1})
                    self._not_before=max(self._not_before,self._clock()+delay+0.05 if delay is not None else float('inf'))
                    if delay is None or attempt==2:raise AcceptanceReadError('DISCORD_RATE_LIMITED',429,operation=operation)
                    continue
                if status!=200:
                    raise AcceptanceReadError('DISCORD_READ_REJECTED',status,operation=operation)
                return value

    async def _response(self,url,params,operation):
        async with self.session.get(url, params=params,
                                    headers={'Authorization': 'Bot ' + self.token}, allow_redirects=False) as response:
            try:
                body = await read_bounded(response.content,MAX_BODY)
            except ValueError:
                raise AcceptanceReadError('ACCEPTANCE_BUDGET_EXCEEDED',response.status,operation=operation) from None
            try:
                value = json.loads(body)
            except (ValueError, UnicodeError):
                raise AcceptanceReadError('DISCORD_RESPONSE_INVALID', response.status, operation=operation) from None
            if response.status in (403,404):
                code = value.get('code') if isinstance(value,dict) else None
                error = (discord.NotFound if response.status == 404 else discord.Forbidden)(
                    response, {'code':code if type(code) is int and 0 <= code <= 999999 else 0, 'message':'Read access unavailable'})
                error.operation = operation
                raise error
            return response.status,value,getattr(response,'headers',{})


async def inspect_mapped_messages(guild,draft,application):
    """Probe only reviewed DB-mapped public boards, never discover replacements."""
    from services import managed_message_service as managed, server_operations as ops
    from services import message_reconciliation as messages
    rows=[]
    specs=managed.specs(guild)
    for row in sorted(draft['boards']['rows'],key=lambda item:item['key']):
        raw=row.get('raw')
        if row['kind']!='message' or row['status']=='EXACT_MATCH' or not isinstance(raw,str) or not raw.isdecimal():continue
        channel=row.get('channel');spec=specs.get(row['key'])
        item={'key':row['key'],'messageId':raw,'channelId':str(channel.id) if channel else None}
        if (channel is None or channel.guild.id!=guild.id or not spec
                or db.get_setting(spec[1])!=str(channel.id)):
            rows.append({**item,'status':'UNVERIFIED','reason':'AUTHORITATIVE_CHANNEL_BINDING_UNVERIFIED'})
            continue
        permissions=channel.permissions_for(guild.me)
        item['access']={'viewChannel':permissions.view_channel,'readHistory':permissions.read_message_history}
        try:
            message=await channel.fetch_message(int(raw))
            item.update(status='OBSERVED',httpStatus=200,exists=True,
                        idMatches=str(message.id)==raw,channelMatches=message.channel.id==channel.id,
                        guildMatches=message.guild.id==guild.id,authorIsHost=message.author.id==application,
                        defaultType=message.type==discord.MessageType.default,pinned=message.pinned)
            expected=ops.message_view(guild,row['key'])
            controls=expected.to_components() if expected else []
            state=managed.load(row['key'])
            content=state['content'] if state and state.get('customized') else row['content']
            item.update(
                        canonicalContentMatches=messages.canonical_equal(message.content,content),
                        contentFingerprint=fingerprint(message.content),
                        controlsMatch=ops.component_payload_matches([c.to_dict() for c in message.components],controls),
                        stateOwnershipMatches=managed.owns(state,channel,message) if state else None)
        except discord.HTTPException as error:
            item.update(status='UNVERIFIED',reason='MAPPED_MESSAGE_HTTP_REJECTED',httpStatus=error.status,
                        discordCode=error.code)
        except AcceptanceReadError as error:
            item.update(status='UNVERIFIED',reason=error.args[0],httpStatus=error.status)
        except (KeyError,TypeError,ValueError,AttributeError):
            item.update(status='UNVERIFIED',reason='MAPPED_MESSAGE_RENDER_OR_STATE_REJECTED')
        except TimeoutError:
            item.update(status='UNVERIFIED',reason='MAPPED_MESSAGE_READ_TIMEOUT')
        rows.append(item)
    return rows


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
        client = preview_client()
        client.http.request = rest.request
        client._connection.user = discord.ClientUser(state=client._connection, data=identity)

        async def load_guild(guild_id):
            rest.guild_id, rest.channels, rest.owner_id = guild_id, set(), None
            rest.failures=[]
            rest.rate_limits=[]
            rest.rate_limit_count=0
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
            try:
                model = discord.Guild(data={**payload, 'roles': roles, 'channels': channels, 'members': [bot, owner]}, state=client._connection)
                model._add_member(discord.Member(data=owner, guild=model, state=client._connection))
                model._add_member(discord.Member(data=bot, guild=model, state=client._connection))
            except (KeyError,TypeError,ValueError,AttributeError):
                raise AcceptanceReadError('DISCORD_MODEL_INVALID', operation='MODEL_BUILD') from None
            client._connection._guilds[guild_id] = model
            return model, model.get_member(rest.owner_id), client

        async def host_get(guild_id, authorized):
            # Fixed protected loopback endpoint, no arbitrary URL or HTTP method.
            url = 'http://127.0.0.1:8080/api/v1/servers/' + str(guild_id) + '/skills'
            async with session.get(url, headers={'Authorization': 'Bearer ' + api_token,
                                                 'X-GamerHQ-Authorized-Guild-Ids': str(authorized)}, allow_redirects=False) as response:
                body = await read_bounded(response.content,262144)
                return response.status, json.loads(body)

        async def load_skills(guild_id):
            status, body = await host_get(guild_id, guild_id)
            if status != 200:
                raise AcceptanceReadError('HOST_SKILL_READ_REJECTED', status)
            return body['skills']

        async def message_probes(guild,draft,application):
            lookups=await inspect_mapped_messages(guild,draft,application)
            uncertain={item.get('messageId') for item in rest.failures
                       if item.get('messageId') is not None and item.get('reason') in
                       {'DISCORD_RATE_LIMITED','READ_TIMEOUT','DISCORD_TRANSPORT_FAILED','DISCORD_RESPONSE_INVALID'}}
            return {'lookups':lookups,'httpFailures':list(rest.failures),'rateLimits':list(rest.rate_limits),
                    'rateLimitCount':rest.rate_limit_count,'rateLimitsTruncated':rest.rate_limit_count>len(rest.rate_limits),
                    'unverifiedRows':sorted(row['key'] for row in draft['boards']['rows']
                        if row['kind']=='message' and row['status']!='EXACT_MATCH' and row.get('raw') in uncertain)}
        result = await collect(store, application, load_guild, load_skills, inspect_messages=message_probes)
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
        result['commandRegistry'] = {'status': 'UNVERIFIED', 'reason': 'LIVE_COMMAND_CALLBACK_REGISTRY_NOT_LOADED'}
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
