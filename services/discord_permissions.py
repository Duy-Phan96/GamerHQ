"""Read-only permission checks shared by Host setup and Skill activation."""
from copy import copy
from types import SimpleNamespace
from urllib.parse import urlencode

import discord

from services.operation_context import read_once


BOARD_READ = ('view_channel', 'read_message_history')
BOARD_PUBLISH = (*BOARD_READ, 'send_messages', 'pin_messages')
CORE_BOARD_OVERWRITES = ('add_reactions', 'use_application_commands', 'send_messages_in_threads',
                         'create_public_threads', 'create_private_threads')
CORE_INSTALL = (*BOARD_PUBLISH, *CORE_BOARD_OVERWRITES, 'embed_links', 'attach_files',
                'manage_channels', 'manage_roles')

VOICE_CREATE = ('view_channel', 'manage_channels', 'manage_roles', 'connect', 'speak', 'move_members')
TICKET_WRITE = (*BOARD_PUBLISH, 'manage_channels', 'manage_roles', 'manage_messages', 'attach_files', 'embed_links')

LABELS = {
    'view_channel': 'View Channel', 'read_message_history': 'Read Message History',
    'send_messages': 'Send Messages', 'pin_messages': 'Pin Messages',
    'embed_links': 'Embed Links', 'attach_files': 'Attach Files', 'manage_channels': 'Manage Channels',
    'manage_roles': 'Manage Roles (Manage Permissions in channels)',
    'add_reactions': 'Add Reactions', 'use_application_commands': 'Use Application Commands',
    'send_messages_in_threads': 'Send Messages in Threads', 'create_public_threads': 'Create Public Threads',
    'create_private_threads': 'Create Private Threads',
    'create_events': 'Create Events', 'manage_events': 'Manage Events',
    'connect': 'Connect', 'speak': 'Speak', 'move_members': 'Move Members',
    'manage_messages': 'Manage Messages',
}


class DiscordPermissionPreflightError(PermissionError):
    """An owner-actionable failure from a read-only Discord permission check."""


def core_install_url(application_id: int) -> str:
    query = urlencode(dict(client_id=application_id, scope='bot applications.commands', integration_type=0,
                           permissions=discord.Permissions(**dict.fromkeys(CORE_INSTALL, True)).value))
    return 'https://discord.com/oauth2/authorize?' + query


def permission_guild(guild, get_role):
    return SimpleNamespace(id=guild.id, owner_id=getattr(guild, 'owner_id', None),
                           default_role=get_role(guild.id), get_role=get_role)


def guild_permissions(guild, member):
    subject = copy(member)
    subject.guild = permission_guild(guild, guild.get_role)
    return subject.guild_permissions


def snapshot_permissions(guild, channel, subject):
    detached = copy(channel)
    detached.guild = permission_guild(guild, guild.get_role)
    return detached.permissions_for(subject)


def missing(permissions, required):
    return tuple(flag for flag in required if not getattr(permissions, flag))


def permission_names(flags):
    return ', '.join(LABELS[flag] for flag in flags)


def role_below_host(role, member):
    return member is not None and not role.is_default() and not role.managed and role < member.top_role


def channel_remediation(guild, channel, member, flags):
    parent = guild.get_channel(channel.category_id) if channel.category_id is not None else None
    guild_missing = set(missing(guild_permissions(guild, member), flags))
    detail = []
    if guild_missing:
        detail.append('Host role lacks ' + permission_names(flag for flag in flags if flag in guild_missing)
                      + ' at server level.')
    if parent is not None:
        synced = channel.overwrites == parent.overwrites
        parent_missing = (missing(snapshot_permissions(guild, parent, member), flags)
                          if hasattr(parent, 'permissions_for') else ())
        if synced and parent_missing:
            detail.append('Synced category restricts effective permissions. Review all synced children before a category change.')
        elif not synced:
            detail.append('This channel has independent overrides. Review the Host member and role entries here.')
    return ' '.join(detail)


async def installation_issues(guild, required):
    """Verify current server role grants without relying on the Gateway cache."""
    if not guild.me:
        return 'Host identity could not be verified. Confirm the bot is installed in this server.'
    try:
        roles = await read_once(('roles', guild.id), guild.fetch_roles)
        member = await read_once(('permission-preflight-member', guild.id),
                                 lambda: guild.fetch_member(guild.me.id))
    except (discord.HTTPException, TimeoutError):
        return 'Discord permissions could not be verified. Retry the permission check.'
    role_map = {role.id: role for role in roles}
    if member.id != guild.me.id or guild.id not in role_map:
        return 'Host identity or role inventory could not be verified. Refresh server permissions.'
    member = copy(member)
    member.guild = permission_guild(guild, role_map.get)
    absent = missing(member.guild_permissions, required)
    return install_remediation(absent) if absent else ''


def install_remediation(absent):
    return 'Host role is missing ' + permission_names(absent) + '. Review Server Settings → Roles → Host bot role.'

def resource_issues(guild, channel, required):
    """Check both server grants and the current resource's effective grants."""
    member = guild.me
    if not member or not channel or channel.guild.id != guild.id:
        return 'Host or channel identity is unavailable.'
    server = member.guild_permissions
    resolve = getattr(channel, 'permissions_for', None)
    if not callable(resolve):
        return 'Host channel permissions could not be verified.'
    effective = resolve(member)
    if not isinstance(server, discord.Permissions) or not isinstance(effective, discord.Permissions):
        return 'Host permissions could not be verified.'
    absent = missing(server, required)
    if absent:
        return install_remediation(absent)
    absent = missing(effective, required)
    if absent:
        return 'Host channel permissions are missing ' + permission_names(absent) + '. Review the category/channel overrides.'
    return ''


def unsupported_overwrite_bits(permissions, overwrites):
    """Creation accepts only guild-granted allow/deny bits. Never strip a deny."""
    mask = 0
    for overwrite in overwrites.values():
        allow, deny = overwrite.pair()
        mask |= allow.value | deny.value
    return discord.Permissions(mask & ~permissions.value)
