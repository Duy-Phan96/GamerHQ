"""Operator-private Games projection; no provisioning or live execution entry point.

The reviewed collector must supply fresh REST guild/member models through a
GET-only transport and bind the already admitted tenant before calling collect.
Do not publish these game identities/Discord mappings in a public issue.
"""
from collections import Counter
import json

import discord

from database import db
from services import game_channel_service as channels
from services import game_readiness_service as readiness
from services import game_visibility_service as visibility
from services.authorization_service import authorized
from services.game_audit_context import snapshot
from tools.game_catalog_import import name_key


def acl(resource, application_id):
    """Role policy and bot grant only; never emit private member identities."""
    roles, bot, other = [], None, 0
    for principal, overwrite in resource.overwrites.items():
        allow, deny = overwrite.pair()
        value = {'allow': allow.value, 'deny': deny.value}
        if isinstance(principal, discord.Role):
            roles.append({'roleId': principal.id, **value})
        elif principal.id == application_id:
            bot = value
        else:
            other += 1
    return {'roleOverwrites': sorted(roles, key=lambda row: row['roleId']),
            'botOverwrite': bot, 'otherMemberOverwriteCount': other}


def pending(raw):
    if not raw:
        return 'ABSENT'
    try:
        value = json.loads(raw)
        return value['state'] if isinstance(value, dict) and value.get('state') in {'PENDING', 'DONE', 'CREATED'} else 'UNVERIFIED'
    except (ValueError, TypeError):
        return 'UNVERIFIED'


def access(resource, guild, role=None):
    """Effective policy, independent of environment-local overwrite IDs."""
    if resource is None:
        return None
    everyone = resource.permissions_for(guild.default_role)
    bot = resource.permissions_for(guild.me)
    selected = resource.permissions_for(role) if role else None
    return {'everyoneView': everyone.view_channel, 'botView': bot.view_channel,
            'botManageChannels': bot.manage_channels,
            'gameRoleView': selected.view_channel if selected else None,
            'gameRoleSend': selected.send_messages if selected else None,
            'gameRoleReadHistory': selected.read_message_history if selected else None}


async def collect(guild, actor, *, environment, guild_id, application_id, legacy_primary=False, diagnostic=None):
    """Inspect every catalog row against one inventory and one in-memory DB copy."""
    def phase(value):
        # Fixed, non-sensitive stage names only; never persist a traceback or row.
        if diagnostic is not None:
            diagnostic['stage'] = value

    phase('GAMES_AUTHORIZATION')
    def verify():
        if legacy_primary:
            import config
            bound = (environment == 'production' and not hasattr(db, 'current_path')
                     and type(config.GUILD_ID) is int and config.GUILD_ID == guild_id)
        else:
            from database import tenants
            tenant = tenants.current()
            bound = tenant is not None and tenant.guild_id == guild_id
        if (type(legacy_primary) is not bool or not bound or environment not in {'production', 'staging'}
                or type(guild_id) is not int or type(application_id) is not int
                or guild.id != guild_id
                or not guild.me or guild.me.id != application_id
                or not authorized(guild, actor)):
            raise PermissionError('GAMES_AUDIT_BINDING_REJECTED')

    verify()
    denied = []
    phase('DATABASE_SNAPSHOT')
    with snapshot(db, denied, legacy_primary=legacy_primary):
        phase('DATABASE_SCHEMA')
        with db.connect() as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('GAMES_AUDIT_DATABASE_REJECTED')
            schema = conn.execute('PRAGMA user_version').fetchone()[0]
            if schema != db.SCHEMA_VERSION:
                raise ValueError('GAMES_AUDIT_SCHEMA_REJECTED')
        phase('GAME_CATALOG')
        catalog = db.get_all_games()  # Include inactive imported entries, unlike the admin selector.
        duplicates = Counter(name_key(game['name']) for game in catalog)
        fresh, failure = None, None
        phase('GAMES_READINESS_INVENTORY')
        try:
            fresh = await visibility._snapshot(guild)
            if getattr(guild, 'unavailable', False):
                raise ValueError('GUILD_UNAVAILABLE')
            if any(getattr(getattr(resource, 'guild', None), 'id', None) != guild_id
                   for resource in (*fresh.channels, *fresh.roles)):
                raise ValueError('FOREIGN_INVENTORY_REJECTED')
        except discord.HTTPException as error:
            failure = {'reason': 'DISCORD_INVENTORY_HTTP_REJECTED', 'httpStatus': error.status}
        except TimeoutError:
            failure = {'reason': 'DISCORD_INVENTORY_TIMEOUT'}
        phase('GAMES_AUTHORIZATION_RECHECK')
        verify()  # Revalidate tenant/admission/owner after awaits, before exposing DB metadata.
        rows = []
        phase('GAMES_ROW_EVALUATION')
        for game in catalog:
            observed = readiness.unverified(failure['reason']) if failure else readiness.inspect_one(fresh, actor, game)
            status = readiness.REVIEW if observed['code'] == readiness.PENDING else observed['code']
            key = name_key(game['name'])
            conflict = duplicates[key] > 1
            reason = observed['reason']
            if conflict or (not game['active'] and game['selectable']):
                status = readiness.REVIEW if failure is None else readiness.UNVERIFIED
                if failure is None:
                    reason = 'LOGICAL_GAME_IDENTITY_CONFLICT' if conflict else 'CATALOG_FLAGS_CONFLICT'
            role = fresh.get_role(game.get('role_id')) if fresh else None
            channel = fresh.get_channel(game.get('channel_id')) if fresh else None
            try:
                aliases = json.loads(game.get('aliases_json') or '[]')
                if not isinstance(aliases, list) or any(not isinstance(v, str) or len(v) > 200 for v in aliases):
                    raise ValueError()
            except (ValueError, TypeError):
                aliases, status, reason = [], readiness.REVIEW if failure is None else readiness.UNVERIFIED, 'CATALOG_ALIASES_INVALID'
            operation = pending(db.get_setting(visibility.operation_key(guild, game['id'])))
            try:
                raw_hints = channels.legacy_hints(game)
                legacy_ids = {field: value if type(value) is int and value > 0 else None
                              for field in channels.LEGACY_FIELDS for value in (raw_hints.get(field),)}
                hints_status = 'REVIEW' if any(raw_hints.get(k) is not None and legacy_ids[k] is None for k in legacy_ids) else 'VERIFIED_RECORDED_HINTS'
            except (ValueError, TypeError, AttributeError):
                legacy_ids, hints_status = {}, 'REVIEW'
            rows.append({'logicalKey': key, 'logicalIdentityConflict': conflict,
                         'name': game['name'], 'aliases': aliases, 'catalogId': game['id'],
                         'active': bool(game['active']), 'selectable': bool(game['selectable']),
                         'status': status, 'reason': reason,
                         'roleId': game.get('role_id'),
                         'roleVerified': None if failure else role is not None,
                         'roleName': role.name if role else None,
                         'rolePermissions': role.permissions.value if role else None,
                         'rolePosition': role.position if role else None,
                         'roleHierarchySafe': None if role is None else visibility.assignable(role, fresh),
                         'recordedResourceOwnership': 'UNVERIFIED' if failure else 'REVIEW' if status == readiness.REVIEW else 'VERIFIED_RECORDED_RESOURCES',
                         'canonicalChannelId': game.get('channel_id'),
                         'canonicalChannelVerified': None if failure else isinstance(channel, discord.TextChannel),
                         'parentId': channel.category_id if isinstance(channel, discord.TextChannel) else None,
                         'channelPolicy': acl(channel, application_id) if isinstance(channel, discord.TextChannel) else None,
                         'effectiveAccess': access(channel, fresh, role) if isinstance(channel, discord.TextChannel) else None,
                         'legacyChatId': legacy_ids.get('chat_channel_id'),
                         'legacyIds': legacy_ids, 'legacyHintsStatus': hints_status,
                         'operationPending': operation == 'PENDING', 'operationState': operation,
                         'channelCreationState': pending(db.get_setting(f'game_channel_creation:{guild_id}:{game["id"]}')),
                         'roleCreationState': pending(db.get_setting(f'game_visibility_role_creation:{guild_id}:{game["id"]}')),
                         'channelIntent': 'UNVERIFIED_OWNER_LAUNCH_SELECTION_REQUIRED'})
        phase('GAMES_CATEGORY_EVALUATION')
        category = {'state': 'UNVERIFIED'}
        if fresh:
            try:
                parent, action, _ = visibility._category_plan(fresh)
                category = {'state': 'NOT_SET_UP' if parent is None else 'REVIEW' if action == 'link' else 'VERIFIED',
                            'id': parent.id if parent else None,
                            'policy': acl(parent, application_id) if parent else None,
                            'effectiveAccess': access(parent, fresh) if parent else None,
                            'hostCanManage': parent.permissions_for(guild.me).manage_channels if parent else None}
            except ValueError:
                category = {'state': 'REVIEW', 'reason': 'GAMES_CATEGORY_MAPPING_REQUIRES_REVIEW'}
        phase('GAMES_FINAL_GUARDS')
        if denied:
            raise PermissionError('GAMES_AUDIT_WRITE_ATTEMPTED')
        return {'schemaVersion': 1, 'environment': environment, 'guildId': guild_id,
                'applicationId': application_id, 'databaseIntegrity': 'PASS', 'databaseSchema': schema,
                'inventory': failure or {'state': 'VERIFIED'},
                'gamesCategory': category,
                'gamesCategoryMappings': {name: db.get_setting(f'managed_category:{guild_id}:{name}')
                                         for name in ('games', 'gaming')},
                'counts': dict(Counter(row['status'] for row in rows)),
                'games': sorted(rows, key=lambda row: (row['logicalKey'], row['catalogId'])),
                'channelIntentVerified': False, 'databaseChanged': False,
                'discordResourcesChanged': False}
