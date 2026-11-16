"""Owner-reviewed, channel-local Pin Messages grants for Core template boards."""
from dataclasses import dataclass
from copy import copy
import time

import discord

from services import managed_message_service as managed, server_operations as ops
from services.operation_context import read_once, read_scope
from services.server_service import ServerMessageError


PIN_MISSING = 'Host lacks Pin Messages here.'


@dataclass(frozen=True)
class PinGrant:
    channel_id: int
    channel_name: str
    host_id: int
    parent_id: int | None
    before: bool | None
    synced: bool
    automatic: bool
    channel_acl: tuple
    parent_acl: tuple
    boards: tuple

    def description(self):
        method = 'eligible for repair' if self.automatic else 'owner must grant in Discord'
        sync = ' · this channel will stop syncing with its category' if self.synced else ''
        return (f'{self.channel_name} ({self.channel_id}) · Host member {self.host_id} · '
                f'Pin Messages {self.before} → allow · {method}{sync}\n  '
                + ', '.join(label for _, label in self.boards))


def acl(channel):
    return tuple(ops.policy_signature(channel.overwrites)) if channel else ()


async def plan(boards):
    grouped = {}
    for row in boards:
        if row['status'] == 'AMBIGUOUS' and row.get('reason', '').startswith(PIN_MISSING):
            grouped.setdefault(row['channel'].id, []).append(row)
    grants = {}
    with read_scope():
        for channel_id, rows in grouped.items():
            guild = rows[0]['guild']
            channel = rows[0]['channel']
            parent = guild.get_channel(channel.category_id)
            try:
                host = await read_once(('pin-grant-host', guild.id), lambda: guild.fetch_member(guild.me.id))
            except (discord.HTTPException, TimeoutError):
                continue
            if host.id != guild.me.id:
                continue
            before = channel.overwrites_for(host)
            proposed = discord.PermissionOverwrite.from_pair(*before.pair())
            proposed.pin_messages = True
            allow, deny = proposed.pair()
            host_snapshot = copy(host)
            host_snapshot.guild = guild
            available = host_snapshot.guild_permissions
            parent_permissions = ops.bulk_destination_permissions(guild, parent, host)
            if parent_permissions is not None:
                available = available | parent_permissions
            # Discord requires permission to manage overwrites and limits bits
            # to those held in the guild or parent. Leave privileged ACLs alone.
            automatic = (ops.snapshot_permissions(guild, channel, host).manage_roles
                         and not (allow.value | deny.value) & ~available.value
                         and proposed.manage_roles is None)
            parent_acl = acl(parent)
            channel_acl = acl(channel)
            grants[channel_id] = PinGrant(channel_id, channel.name, host.id, channel.category_id,
                                         before.pin_messages, parent is not None and channel_acl == parent_acl,
                                         automatic, channel_acl, parent_acl,
                                         tuple(sorted((row['key'], row['label']) for row in rows)))
    return grants


async def apply(guild, owner, draft, bot=None):
    from services import server_templates as templates
    from services.channel_change_service import expect, permissions

    structure = draft['structure']
    if (owner.id != guild.owner_id or owner.id != structure['actor_id'] or guild.id != structure['guild_id']
            or draft['template'] not in templates.TEMPLATES or time.time() - structure['created'] > 240):
        raise ServerMessageError('Open a fresh template preview as the server owner.')
    managed.require_admin(guild, owner)
    outcomes = []
    async with managed.lock(templates.progress_key(guild, draft['template'])), managed.lock(f'operations:{guild.id}'):
        fresh = await templates.preview(guild, owner, draft['template'], bot)
        for channel_id, reviewed in draft['pin_grants'].items():
            if not reviewed.automatic:
                continue
            if fresh['pin_grants'].get(channel_id) != reviewed:
                outcomes.append((reviewed.channel_name, 'Skipped. Channel permissions or board evidence changed.'))
                continue
            try:
                channel = await guild.fetch_channel(channel_id)
                parent = await guild.fetch_channel(reviewed.parent_id) if reviewed.parent_id else None
                host = guild.me
                if (host.id != reviewed.host_id or channel.guild.id != guild.id
                        or channel.category_id != reviewed.parent_id or acl(channel) != reviewed.channel_acl
                        or acl(parent) != reviewed.parent_acl):
                    outcomes.append((reviewed.channel_name, 'Skipped. Channel or category policy changed.'))
                    continue
                overwrite = channel.overwrites_for(host)
                overwrite.pin_messages = True
                expected = {**channel.overwrites, host: overwrite}
                expect(guild, channel.id, permissions=permissions(expected))
                await channel.set_permissions(host, overwrite=overwrite, reason='Owner-confirmed Core template pin permission')
                actual = await guild.fetch_channel(channel_id)
                if actual.category_id != reviewed.parent_id or acl(actual) != tuple(ops.policy_signature(expected)):
                    outcomes.append((reviewed.channel_name, 'Verify the current overwrite before retrying.'))
                else:
                    outcomes.append((reviewed.channel_name, 'Pin Messages overwrite verified.'))
            except (discord.HTTPException, TimeoutError, OSError) as error:
                outcomes.append((reviewed.channel_name, ops.bulk_error_reason(error)))
        try:
            refreshed = await templates.preview(guild, owner, draft['template'], bot)
        except (discord.HTTPException, TimeoutError):
            refreshed = None
    return outcomes, refreshed
