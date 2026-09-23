"""Least-privilege music access, with an explicit GamerHQ area allow-list."""
import logging
import re

import discord

from database import db

log = logging.getLogger(__name__)
GAME_CHANNEL_FIELDS = ('chat_channel_id', 'memes_channel_id', 'lfg_channel_id', 'clips_channel_id', 'create_voice_channel_id')
TEXT_RIGHTS = ('view_channel', 'send_messages', 'read_message_history', 'embed_links', 'add_reactions')
VOICE_RIGHTS = ('view_channel', 'connect', 'speak', 'use_voice_activation')
DENIED_RIGHTS = ('administrator', 'manage_guild', 'manage_roles', 'manage_channels', 'manage_messages', 'kick_members', 'ban_members', 'move_members', 'mute_members', 'deafen_members', 'mention_everyone')


def role_key(guild): return f'music_bots_role:{guild.id}'


def resolve_music_role(guild, *, persist=True):
    stored = db.get_setting(role_key(guild))
    if stored:
        role = guild.get_role(int(stored)) if stored.isdigit() else None
        if role is None:
            return None, 'Configured Music Bots role is missing; configure it with /server music-bots-role.'
    else:
        matches = [r for r in guild.roles if re.sub(r'[^a-z0-9]+', ' ', r.name.casefold()).strip() == 'music bots']
        if len(matches) != 1:
            return None, 'Music Bots role not found uniquely; configure it with /server music-bots-role.'
        role = matches[0]
    if role.is_default() or role.managed or any(getattr(role.permissions, name) for name in DENIED_RIGHTS):
        return None, 'Music Bots must be a dedicated role without administrative/moderation permissions.'
    if not stored and persist:
        db.set_setting(role_key(guild), role.id)
    return role, None


def music_overwrite(kind):
    allowed = TEXT_RIGHTS if kind == 'text' else VOICE_RIGHTS if kind == 'voice' else set(TEXT_RIGHTS) | set(VOICE_RIGHTS)
    return discord.PermissionOverwrite(**{**dict.fromkeys(DENIED_RIGHTS, False), **dict.fromkeys(allowed, True)})


def blocked_name(obj):
    words = set(re.sub(r'[^a-z0-9]+', ' ', obj.name.casefold()).split())
    return bool(words & {'staff', 'admin', 'admins', 'administration', 'moderation', 'moderator', 'management', 'private', 'affiliate', 'ticket', 'tickets', 'support'})


def should_allow_music_bots(channel):
    guild = channel.guild
    category = channel if isinstance(channel, discord.CategoryChannel) else channel.category
    if category is None or blocked_name(category) or blocked_name(channel):
        return False
    games = [g for g in db.get_area_games() if g.get('category_id') == category.id]
    if games:
        if len(games) != 1:
            return False
        game = games[0]
        role = guild.get_role(game['role_id']) if game.get('role_id') else None
        # Normal game areas are gated by the game role, not open to @everyone.
        if not role or category.overwrites_for(role).view_channel is not True:
            return False
        if channel is category:
            return all(should_allow_music_bots(child) for child in category.channels)
        known = {game.get(field) for field in GAME_CHANNEL_FIELDS} | set(db.get_temp_voice_ids())
        if channel.id not in known:
            return False
        # An unsynchronised private child may omit the game role entirely.
        # Require its positive game-role grant instead of interpreting None as public.
        return channel.overwrites_for(role).view_channel is True
    name = re.sub(r'[^a-z0-9]+', '-', category.name.lower()).strip('-')
    if name not in {'community', 'voice-channels', 'events'}:
        return False
    if category.overwrites_for(guild.default_role).view_channel is False:
        return False
    if channel is category:
        return all(should_allow_music_bots(child) for child in category.channels)
    if channel.overwrites_for(guild.default_role).view_channel is False:
        return False
    alias = re.sub(r'[^a-z0-9]+', '-', channel.name.lower()).strip('-')
    if name == 'events':
        return alias in {'tournaments', 'giveaways'}
    if name == 'community':
        return alias in {'general', 'newbies', 'introductions', 'bot-commands'}
    return isinstance(channel, discord.VoiceChannel)


def with_music_access(guild, overwrites, kind='voice', *, parent=None):
    """Explicitly called only by approved gaming resource creators (including private lobby voice)."""
    result = dict(overwrites)
    if parent is not None and blocked_name(parent):
        return result
    role, error = resolve_music_role(guild)
    if role:
        result[role] = music_overwrite(kind)
    else:
        log.warning('GamerHQ music access: %s', error)
    return result


async def apply_music_access(channel, role, *, approved=False):
    if not approved and not should_allow_music_bots(channel):
        return 'skipped'
    kind = 'category' if isinstance(channel, discord.CategoryChannel) else 'voice' if isinstance(channel, discord.VoiceChannel) else 'text'
    desired = music_overwrite(kind)
    if channel.overwrites_for(role) == desired:
        return 'correct'
    await channel.set_permissions(role, overwrite=desired, reason='GamerHQ Music Bots access')
    return 'updated'


async def sync_music_access(guild):
    result = {'updated': 0, 'correct': 0, 'skipped': 0, 'failures': []}
    role, error = resolve_music_role(guild)
    if not role:
        result['failures'].append(error)
        return result
    for channel in guild.channels:
        try:
            outcome = await apply_music_access(channel, role)
            result[outcome] += 1
        except discord.HTTPException:
            result['failures'].append(f'Could not update {channel.name} ({channel.id}).')
    return result


def render_music_result(result):
    text = (f"**MUSIC BOT ACCESS**\nUpdated: {result['updated']} · Already correct: {result['correct']}\n"
            f"Skipped private/staff/unmanaged: {result['skipped']} · Errors: {len(result['failures'])}")
    if result['failures']:
        text += '\n' + '\n'.join(result['failures'][:3])
    return text
