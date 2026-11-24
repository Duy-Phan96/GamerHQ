"""Ephemeral Games-only observer. No Gateway, dotenv, provisioning or DB init.

The packet comes only from the reviewed controller or owner-operated launcher.
Credentials stay in the existing container environment; stdout is operator-private.
"""
import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import aiohttp
import discord
from tools.game_audit_execution import REASONS


class AuditUnavailable(ValueError):
    def __init__(self, reason, http_status=None):
        super().__init__(reason)
        self.reason, self.http_status = reason, http_status


def binding(packet):
    if (not isinstance(packet, dict) or set(packet) != {'environment', 'guildId', 'applicationId', 'database'}
            or packet['environment'] not in {'production', 'staging'}
            or any(type(packet[k]) is not int or not 0 < packet[k] < 2**63 for k in ('guildId', 'applicationId'))
            or not isinstance(packet['database'], str) or not Path(packet['database']).is_absolute()):
        raise AuditUnavailable('GAMES_AUDIT_BINDING_REJECTED')
    return packet


class GamesDiscord:
    """Bounded single-guild GET allowlist. No messages, membership lists or extras."""
    def __init__(self, session, token, packet):
        self.session, self.token, self.packet = session, token, binding(packet)
        self.owner = None
        self.reads = 0
        self.started = time.monotonic()
        self.cache = {}
        self.not_before = 0

    async def request(self, route, **kwargs):
        gid, app = self.packet['guildId'], self.packet['applicationId']
        url = urlsplit(route.url)
        paths = {'/users/@me': 'BOT_IDENTITY', f'/guilds/{gid}': 'GUILD_IDENTITY',
                 f'/guilds/{gid}/roles': 'GUILD_ROLES', f'/guilds/{gid}/channels': 'GUILD_CHANNELS',
                 f'/guilds/{gid}/members/{app}': 'BOT_MEMBERSHIP'}
        if self.owner:
            paths[f'/guilds/{gid}/members/{self.owner}'] = 'OWNER_MEMBERSHIP'
        path = url.path.removeprefix('/api/v10')
        if (route.method != 'GET' or kwargs or url.scheme != 'https' or url.netloc != 'discord.com'
                or url.query or url.fragment or url.path != '/api/v10' + path or path not in paths):
            raise AuditUnavailable('READ_ONLY_REQUEST_REJECTED')
        # One immutable roles/channels inventory per collection, never reused across runs.
        if path in self.cache:
            return self.cache[path]
        for attempt in range(2):
            pause = max(0, self.not_before - time.monotonic())
            if pause > 5.1:
                raise AuditUnavailable('DISCORD_RATE_LIMITED', 429)
            if pause:
                await asyncio.sleep(pause)
            self.reads += 1
            if self.reads > 24 or time.monotonic() - self.started > 90:
                raise AuditUnavailable('GAMES_AUDIT_BUDGET_EXCEEDED')
            try:
                async with self.session.get(route.url, headers={'Authorization': 'Bot ' + self.token},
                                            allow_redirects=False) as response:
                    chunks, total = [], 0
                    while True:
                        chunk = await response.content.read(min(65536, 2 * 1024**2 + 1 - total))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > 2 * 1024**2:
                            raise AuditUnavailable('GAMES_AUDIT_BODY_REJECTED')
                    status = response.status
                    headers = getattr(response, 'headers', {})
                    if str(headers.get('X-RateLimit-Remaining')) == '0':
                        try:
                            reset = float(headers.get('X-RateLimit-Reset-After'))
                        except (ValueError, TypeError):
                            reset = float('inf')
                        self.not_before = time.monotonic() + reset + 0.05 if 0 <= reset <= 5 else float('inf')
                    if status not in (200, 429):
                        raise AuditUnavailable('DISCORD_HTTP_REJECTED', status)
                    try:
                        value = json.loads(b''.join(chunks))
                    except (ValueError, UnicodeError):
                        raise AuditUnavailable('DISCORD_SCHEMA_REJECTED', status) from None
                    if status == 429:
                        delay = value.get('retry_after') if isinstance(value, dict) else None
                        if attempt or type(delay) not in (int, float) or not 0 <= delay <= 5:
                            raise AuditUnavailable('DISCORD_RATE_LIMITED', 429)
                        self.not_before = max(self.not_before, time.monotonic() + delay + 0.05)
                        continue
                    if path.endswith(('/roles', '/channels')):
                        self.cache[path] = value
                    return value
            except asyncio.TimeoutError:
                raise AuditUnavailable('DISCORD_TIMEOUT') from None
            except aiohttp.ClientError:
                raise AuditUnavailable('DISCORD_TRANSPORT_UNAVAILABLE') from None


def verified_rest_inventory(guild, raw_channels, raw_roles, *, diagnostic=None, allow_opaque_type20=False):
    """Check exact REST/model coverage; report only bounded type counts privately.

    No IDs, names, parent references, overwrites or original channel payloads
    are included in the exception or diagnostic.
    """
    channels, roles = list(guild.channels), list(guild.roles)
    opaque_type20_count = 0
    for raw, models, reason in (
        (raw_channels, channels, 'DISCORD_CHANNEL_MODEL_INCOMPLETE'),
        (raw_roles, roles, 'DISCORD_ROLE_MODEL_INCOMPLETE'),
    ):
        expected = {int(item['id']) for item in raw}
        actual = {item.id for item in models}
        incomplete = (len(raw) != len(models) or expected != actual
                      or any(getattr(getattr(item, 'guild', None), 'id', None) != guild.id
                             for item in models))
        if incomplete:
            if reason == 'DISCORD_CHANNEL_MODEL_INCOMPLETE' and allow_opaque_type20:
                missing = expected - actual
                opaque = [item for item in raw if int(item['id']) in missing]
                # Only the exact known 92:91 compatibility shape is accepted:
                # one opaque channel of type 20, with all modeled resources
                # accounted for. Unknown objects are never constructed or
                # made available to any readiness/permission decision.
                if (len(missing) == 1 and len(opaque) == 1
                        and type(opaque[0].get('type')) is int
                        and opaque[0]['type'] == 20
                        and len(raw) == len(models) + 1 and not (actual - expected)
                        and all(getattr(getattr(item, 'guild', None), 'id', None) == guild.id
                                for item in models)):
                    opaque_type20_count = 1
                    continue
            if reason == 'DISCORD_CHANNEL_MODEL_INCOMPLETE' and diagnostic is not None:
                missing = expected - actual
                by_type = {}
                for item in raw:
                    if int(item['id']) not in missing:
                        continue
                    # Discord's channel type is a public protocol enum, but
                    # unknown/unbounded values must never be printed verbatim.
                    kind = item.get('type')
                    label = str(kind) if type(kind) is int and 0 <= kind <= 31 else 'OTHER'
                    by_type[label] = by_type.get(label, 0) + 1
                diagnostic['channelModelCoverage'] = {
                    'rawCount': len(raw),
                    'modeledCount': len(models),
                    'missingCount': len(missing),
                    'missingByType': dict(sorted(by_type.items())),
                    'extraModelCount': len(actual - expected),
                }
            raise AuditUnavailable(reason)
    if opaque_type20_count:
        # Third tuple element is a fixed flag, not a resource model or ID.
        # The projection must mark every Games result UNVERIFIED.
        return channels, roles, 'OPAQUE_TYPE20_UNVERIFIED'
    return channels, roles


def finalize_projection(result, discord_reads):
    """Only a fully modeled inventory may produce the public COMPLETE path."""
    common = {**result, 'collectedAt': datetime.now(timezone.utc).isoformat(),
              'discordReads': discord_reads, 'mutationsAttempted': False,
              'gatewayRestarted': False}
    if result.get('inventory') == {'state': 'UNVERIFIED', 'reason': 'DISCORD_OPAQUE_CHANNEL_TYPE20'}:
        return {**common, 'state': 'GAMES_AUDIT_UNAVAILABLE', 'status': 'UNVERIFIED',
                'reason': 'DISCORD_OPAQUE_CHANNEL_TYPE20',
                'diagnosticStage': 'GAMES_OPAQUE_TYPE20'}
    return {**common, 'state': 'GAMES_AUDIT_COLLECTED'}


async def gather(packet, *, session_factory=aiohttp.ClientSession, diagnostic=None):
    # Private, fixed-vocabulary phase marker. Never store exception text or Discord payloads.
    def phase(value):
        if diagnostic is not None:
            diagnostic['stage'] = value
    phase('BINDING')
    packet = binding(packet)
    token = os.environ.get('DISCORD_TOKEN') or os.environ.get('BOT_TOKEN')
    if not token:
        raise AuditUnavailable('CREDENTIAL_UNAVAILABLE')
    # Imports occur after the source-verified launcher has suppressed dotenv.
    phase('RUNTIME_IMPORT')
    from database import db
    from services import game_launch_audit as projection
    import config
    phase('DATABASE_BINDING')
    database = Path(packet['database'])
    if database.is_symlink() or not database.is_file():
        raise AuditUnavailable('DATABASE_IDENTITY_REJECTED')
    legacy = not hasattr(db, 'current_path')
    if legacy:
        if packet['environment'] != 'production' or config.GUILD_ID != packet['guildId'] or db.DB_PATH.resolve() != database.resolve():
            raise AuditUnavailable('DATABASE_IDENTITY_REJECTED')
        scope = nullcontext()
    else:
        from database import tenants
        current = tenants.current()
        if current is not None or db.DB_PATH.resolve() != database.resolve() or config.GUILD_ID != packet['guildId']:
            raise AuditUnavailable('DATABASE_IDENTITY_REJECTED')
        # Bind only the already configured primary file; never admit/provision Servertest.
        scope = tenants._bind(tenants.GuildTenant(packet['guildId'], database.resolve()))
    with scope:
        phase('DISCORD_SESSION')
        async with session_factory(timeout=aiohttp.ClientTimeout(total=15), trust_env=False) as session:
            rest = GamesDiscord(session, token, packet)
            from discord.http import Route
            phase('DISCORD_INVENTORY')
            identity = await rest.request(Route('GET', '/users/@me'))
            if not isinstance(identity, dict) or identity.get('id') != str(packet['applicationId']) or identity.get('bot') is not True:
                raise AuditUnavailable('BOT_IDENTITY_REJECTED')
            gid = packet['guildId']
            payload = await rest.request(Route('GET', '/guilds/{guild_id}', guild_id=gid))
            if (not isinstance(payload, dict) or payload.get('id') != str(gid)
                    or not str(payload.get('owner_id', '')).isdecimal()):
                raise AuditUnavailable('GUILD_IDENTITY_REJECTED')
            rest.owner = int(payload['owner_id'])
            roles = await rest.request(Route('GET', '/guilds/{guild_id}/roles', guild_id=gid))
            channels = await rest.request(Route('GET', '/guilds/{guild_id}/channels', guild_id=gid))
            for rows, limit in ((roles, 300), (channels, 600)):
                if (not isinstance(rows, list) or len(rows) > limit
                        or any(not isinstance(row, dict) or not str(row.get('id', '')).isdecimal()
                               or str(row.get('guild_id', gid)) != str(gid) for row in rows)
                        or len({row['id'] for row in rows}) != len(rows)):
                    raise AuditUnavailable('DISCORD_SCHEMA_REJECTED')
            members = []
            for user in (packet['applicationId'], rest.owner):
                member = await rest.request(Route('GET', '/guilds/{guild_id}/members/{user_id}', guild_id=gid, user_id=user))
                if not isinstance(member, dict) or member.get('user', {}).get('id') != str(user):
                    raise AuditUnavailable('GUILD_MEMBERSHIP_REJECTED')
                members.append(member)
            phase('DISCORD_MODEL')
            client = discord.Client(intents=discord.Intents.none())  # Models only, never login/start.
            client.http.request = rest.request
            client._connection.user = discord.ClientUser(state=client._connection, data=identity)
            model = discord.Guild(data={**payload, 'roles': roles, 'channels': channels, 'members': members}, state=client._connection)
            for member in members:
                model._add_member(discord.Member(data=member, guild=model, state=client._connection))
            phase('GAMES_REST_MODEL_COVERAGE')
            inventory = verified_rest_inventory(model, channels, roles, diagnostic=diagnostic,
                                                allow_opaque_type20=True)
            phase('GAMES_PROJECTION')
            result = await projection.collect(model, model.get_member(rest.owner), environment=packet['environment'],
                        guild_id=gid, application_id=packet['applicationId'], legacy_primary=legacy,
                        diagnostic=diagnostic, verified_rest_inventory=inventory)
            phase('REPORT_ASSEMBLY')
            return finalize_projection(result, rest.reads)


def main(packet):
    diagnostic = {'stage': 'BINDING'}
    try:
        return asyncio.run(gather(packet, diagnostic=diagnostic))
    except AuditUnavailable as error:
        return {'schemaVersion': 1, 'state': 'GAMES_AUDIT_UNAVAILABLE', 'status': 'UNVERIFIED',
                'reason': error.reason, 'httpStatus': error.http_status,
                'diagnosticStage': diagnostic['stage'],
                'channelFetchFailureClass': diagnostic.get('channelFetchFailureClass'),
                'channelModelCoverage': diagnostic.get('channelModelCoverage'),
                'databaseChanged': False, 'discordResourcesChanged': False, 'gatewayRestarted': False}
    except Exception as error:
        # No token, private response, SQL or filesystem exception is forwarded.
        return {'schemaVersion': 1, 'state': 'GAMES_AUDIT_UNAVAILABLE', 'status': 'UNVERIFIED',
                'reason': error.args[0] if error.args and isinstance(error.args[0], str) and error.args[0] in REASONS else 'RUNTIME_OR_SCHEMA_UNAVAILABLE',
                'diagnosticStage': diagnostic['stage'],
                'channelFetchFailureClass': diagnostic.get('channelFetchFailureClass'),
                'channelModelCoverage': diagnostic.get('channelModelCoverage'),
                'databaseChanged': False,
                'discordResourcesChanged': False, 'gatewayRestarted': False}
