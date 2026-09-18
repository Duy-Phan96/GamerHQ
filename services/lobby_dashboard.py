"""One canonical dashboard card per lobby; private cards stay private."""
import asyncio
import logging
import time

import discord

from database import db
from services.lfg_service import find_lfg_channel, render_event

log = logging.getLogger(__name__)
_locks = {}
_channel_locks = {}


def event_lock(event_id):
    return _locks.setdefault(event_id, asyncio.Lock())


async def dashboard_channel(guild):
    async with _channel_locks.setdefault(guild.id, asyncio.Lock()):
        key = f'active_lobbies_channel:{guild.id}'
        channel_id = db.get_setting(key)
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            channel = next((c for c in guild.text_channels if c.name.lstrip('🎮・').replace('_', '-') == 'active-lobbies'), None)
        if channel is None:
            from services.onboarding_service import unique
            community = unique(guild.categories, "community")
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, create_public_threads=False, create_private_threads=False, send_messages_in_threads=False),
            }
            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            channel = await guild.create_text_channel('active-lobbies', category=community, overwrites=overwrites, topic='Active GamerHQ lobbies · use the buttons to join or manage.', reason='GamerHQ active lobby dashboard')
        else:
            overwrites = dict(channel.overwrites)
            before = dict(channel.overwrites)
            targets = set(overwrites) | {guild.default_role}
            for target in targets:
                if guild.me and target.id == guild.me.id:
                    continue
                overwrite = channel.overwrites_for(target)
                overwrite.send_messages = False
                overwrite.create_public_threads = False
                overwrite.create_private_threads = False
                overwrite.send_messages_in_threads = False
                overwrites[target] = overwrite
            if guild.me:
                overwrite = channel.overwrites_for(guild.me)
                overwrite.view_channel = True
                overwrite.send_messages = True
                overwrite.read_message_history = True
                overwrites[guild.me] = overwrite
            if overwrites != before:
                await channel.edit(overwrites=overwrites, reason='GamerHQ dashboard is managed by the bot')
        db.set_setting(key, channel.id)
        return channel


async def sync_card(guild, event, view):
    """Caller holds event_lock. Never replace a card on Forbidden/transient failure."""
    if event['visibility'] == 'private':
        channel = guild.get_channel(event['private_channel_id']) if event.get('private_channel_id') else None
    else:
        channel = await dashboard_channel(guild)
    if not isinstance(channel, discord.TextChannel):
        return
    message_id = event.get('dashboard_message_id')
    if event.get('dashboard_channel_id') != channel.id:
        message_id = None
    # Reuse the existing private/public post if this channel already has one.
    if not message_id:
        message_id = next((r['message_id'] for r in db.get_lfg_event_messages(event['id']) if r['channel_id'] == channel.id), None)
    message = None
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            pass
    if message and (not guild.me or message.author.id != guild.me.id):
        log.warning('Stale dashboard mapping event=%s points to another author; preserving message.', event['id'])
        message = None
    content = render_event(guild, event)
    if message:
        await message.edit(content=content, view=view, allowed_mentions=discord.AllowedMentions.none())
    elif event['status'] == 'scheduled':
        # Recover a send that succeeded just before a process crash/DB write failure.
        marker = f'gamerhq:lfg:join:{event["id"]}'
        async for candidate in channel.history(limit=100):
            if guild.me and candidate.author.id == guild.me.id and any(getattr(child, 'custom_id', None) == marker for row in candidate.components for child in row.children):
                message = candidate
                await message.edit(content=content, view=view, allowed_mentions=discord.AllowedMentions.none())
                break
        if message is None:
            message = await channel.send(content, view=view, allowed_mentions=discord.AllowedMentions.none())
    if message:
        with db.connect() as conn:
            conn.execute('UPDATE lfg_events SET dashboard_channel_id=?, dashboard_message_id=? WHERE id=?', (channel.id, message.id, event['id']))
        db.add_lfg_event_message(event['id'], channel_id=channel.id, message_id=message.id)


async def cleanup_ended(guild, *, reconcile=False):
    from cogs.lfg import delete_event_posts, delete_event_voice, delete_private_event_channel, refresh_event_posts
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM lfg_events WHERE guild_id=? AND ended_at IS NOT NULL AND status IN ('cancelled','completed')", (guild.id,))]
    for event in rows:
        if reconcile:
            await refresh_event_posts(guild, event['id'])
        voice = guild.get_channel(event['voice_channel_id']) if event.get('voice_channel_id') else None
        if isinstance(voice, discord.VoiceChannel) and voice.members:
            continue
        voice_clean = await delete_event_voice(guild, event)
        if int(time.time()) >= event['ended_at'] + 86400:
            posts_clean = await delete_event_posts(guild, event)
            channel_clean = await delete_private_event_channel(guild, event)
            if voice_clean and posts_clean and channel_clean:
                with db.connect() as conn:
                    conn.execute('UPDATE lfg_events SET ended_at=NULL, dashboard_channel_id=NULL, dashboard_message_id=NULL WHERE id=?', (event['id'],))
                    conn.execute('DELETE FROM lfg_event_messages WHERE event_id=?', (event['id'],))
