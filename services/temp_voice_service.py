"""Bot-mediated controls scoped to persisted Create Voice rooms."""
import asyncio
import json
import logging
import time

import discord
from database import db
from services.onboarding_service import is_staff
from services.music_bot_service import resolve_music_role, with_music_access, blocked_name

log = logging.getLogger(__name__)
_locks = {}


def room_lock(channel_id):
    return _locks.setdefault(channel_id, asyncio.Lock())


def staff(actor):
    return actor.id == actor.guild.owner_id or any(is_staff(role) for role in actor.roles)


def resolve(guild, actor, channel_id):
    channel = guild.get_channel(channel_id)
    row = db.get_temp_voice(channel_id)
    if not isinstance(channel, discord.VoiceChannel) or not row or row['game_id'] < 0:
        raise ValueError('This is not a GamerHQ Create Voice room. Streamer and lobby controls stay separate.')
    if channel.category and blocked_name(channel.category):
        raise ValueError('These controls cannot manage a protected category.')
    if actor.guild.id != guild.id or (row['host_id'] != actor.id and not staff(actor)):
        raise ValueError('Only this temporary room’s owner or staff can manage it.')
    return channel, row


def forget(channel_id):
    db.remove_temp_voice(channel_id)
    db.set_setting(f'temp_voice_lock:{channel_id}', '')


async def restrict_legacy_owner(channel):
    row = db.get_temp_voice(channel.id)
    if not row or row['game_id'] < 0:
        return
    owner = next((target for target in channel.overwrites if target.id == row['host_id']), None)
    if owner:
        overwrite = channel.overwrites_for(owner)
        if overwrite.manage_channels or overwrite.move_members:
            overwrite.manage_channels = overwrite.move_members = False
            await channel.set_permissions(owner, overwrite=overwrite, reason='GamerHQ bot-mediated temporary voice controls')


async def empty_cleanup(channel):
    async with room_lock(channel.id):
        if not db.get_temp_voice(channel.id) or channel.members:
            return
        try:
            await channel.delete(reason='Empty GamerHQ temporary voice')
        except discord.NotFound:
            pass
        forget(channel.id)


async def act(guild, actor, channel_id, action, value=None):
    async with room_lock(channel_id):
        channel, row = resolve(guild, actor, channel_id)
        await restrict_legacy_owner(channel)
        reason = f'GamerHQ temporary voice {action}: actor {actor.id}'
        if action == 'rename':
            name = str(value).strip()
            if not 1 <= len(name) <= 100:
                raise ValueError('Use a name between 1 and 100 characters.')
            await channel.edit(name=name, reason=reason)
        elif action == 'limit':
            try:
                limit = int(value)
            except (ValueError, TypeError):
                raise ValueError('Use a whole number from 0 (unlimited) to 99.')
            if not 0 <= limit <= 99:
                raise ValueError('Use a whole number from 0 (unlimited) to 99.')
            await channel.edit(user_limit=limit, reason=reason)
        elif action in {'lock', 'unlock'}:
            setting = f'temp_voice_lock:{channel.id}'
            saved = db.get_setting(setting)
            overwrites = dict(channel.overwrites)
            if action == 'lock' and not saved:
                music, _ = resolve_music_role(guild)
                targets = {guild.default_role} | {r for r in guild.roles if r in overwrites}
                original = {}
                for target in targets:
                    if target == music or is_staff(target):
                        continue
                    overwrite = channel.overwrites_for(target)
                    original[str(target.id)] = overwrite.connect
                    overwrite.connect = False
                    overwrites[target] = overwrite
                # Store before edit: interrupted edits are safe to retry/unlock.
                db.set_setting(setting, json.dumps(original))
                overwrites = with_music_access(guild, overwrites, parent=channel.category)
                await channel.edit(overwrites=overwrites, reason=reason)
            elif action == 'lock':
                # Reapply saved targets if an earlier Discord edit failed.
                for target in guild.roles:
                    if str(target.id) in json.loads(saved):
                        overwrite = channel.overwrites_for(target); overwrite.connect = False
                        overwrites[target] = overwrite
                await channel.edit(overwrites=with_music_access(guild, overwrites, parent=channel.category), reason=reason)
            elif saved:
                original = json.loads(saved)
                for target in guild.roles:
                    if str(target.id) in original:
                        overwrite = channel.overwrites_for(target)
                        overwrite.connect = original[str(target.id)]
                        overwrites[target] = overwrite
                await channel.edit(overwrites=overwrites, reason=reason)
                db.set_setting(setting, '')
        elif action in {'invite', 'remove'}:
            member = guild.get_member(int(value))
            if member is None:
                raise ValueError('Select a current server member.')
            if action == 'remove':
                if member.id == row['host_id'] or member.bot or staff(member):
                    raise ValueError('Owner, staff and bots cannot be removed with these controls.')
                if not member.voice or not member.voice.channel or member.voice.channel.id != channel.id:
                    raise ValueError('That member is not in this temporary room.')
                overwrite = channel.overwrites_for(member); overwrite.connect = False
                await channel.set_permissions(member, overwrite=overwrite, reason=reason)
                # Recheck location after the permission request; never disconnect another room.
                if member.voice and member.voice.channel and member.voice.channel.id == channel.id:
                    await member.move_to(None, reason=reason)
            else:
                overwrite = channel.overwrites_for(member)
                overwrite.view_channel = overwrite.connect = True
                await channel.set_permissions(member, overwrite=overwrite, reason=reason)
        elif action == 'close':
            await channel.delete(reason=reason)
            forget(channel.id)
        else:
            raise ValueError('Unknown control.')
        logger = log.warning if action in {'remove', 'close'} else log.info
        logger('GamerHQ voice timestamp=%s actor=%s channel=%s action=%s target=%s', int(time.time()), actor.id, channel.id, action, value if action in {'invite', 'remove'} else '')
        return '✅ Voice updated.' if action != 'close' else '✅ Temporary voice closed.'
