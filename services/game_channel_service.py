"""Reviewed optional game channels; legacy area IDs are never canonical channels."""
import asyncio
import json
import logging
import time
import unicodedata
import re

import discord
import config
from database import db
from services.authorization_service import authorized
from services.game_area_safety import area_lock
from services.role_service import game_role

log = logging.getLogger(__name__)
_guild_locks = {}
LEGACY_FIELDS = ('category_id', 'chat_channel_id', 'lfg_channel_id', 'clips_channel_id', 'memes_channel_id', 'create_voice_channel_id')


def slug(name):
    text = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')[:100] or 'game'


def category(guild):
    raw = db.get_setting(f'managed_category:{guild.id}:games') or db.get_setting(f'managed_category:{guild.id}:gaming')
    result = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if not isinstance(result, discord.CategoryChannel):
        result = next(
            (c for c in guild.categories if c.name.casefold() in {"🎮 games", "🎮 gaming"}),
            None,
        )
    if not isinstance(result, discord.CategoryChannel):
        raise ValueError('Review the GAMES category in Server Structure before creating a channel.')
    return result


def log_channel(guild):
    from services.server_log_service import channel
    return channel(guild, name='games-log')


def overwrites(guild, role, resource=None):
    from services.onboarding_service import is_staff
    values = {target: discord.PermissionOverwrite.from_pair(*value.pair()) for target, value in resource.overwrites.items()} if resource else {}
    for target in set(values) | {guild.default_role, role, guild.me} | {r for r in guild.roles if is_staff(r)}:
        value = values.get(target, discord.PermissionOverwrite())
        allowed = target == role or target == guild.me or (target in guild.roles and is_staff(target))
        value.view_channel = allowed
        if allowed:
            value.read_message_history = value.send_messages = True
        if target == guild.me:
            value.embed_links = value.attach_files = value.manage_channels = True
        values[target] = value
    return values


def candidates(guild):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM game_channel_candidates WHERE guild_id=?', (guild.id,))]


def legacy_hints(game):
    with db.connect() as conn:
        row = conn.execute('SELECT resources_json FROM game_legacy_hints WHERE game_id=?', (game['id'],)).fetchone()
    hints = json.loads(row[0]) if row else {}
    hints.update({k: game[k] for k in LEGACY_FIELDS if game.get(k)})
    return hints


async def audit(guild, title, description):
    try:
        destination = log_channel(guild)
        if destination:
            await destination.send(embed=discord.Embed(title=title, description=description[:1800]), allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        # Optional audit delivery must never roll back an already successful game
        # approval or channel mutation. No private exception payload is printed.
        log.warning('Game audit delivery failed; review the private log configuration')


async def check_threshold(guild, game_id, *, count=None):
    game = db.get_game_by_id(game_id)
    if not game or not game['active'] or not game['selectable'] or game.get('channel_id'):
        return
    try:
        role = game_role(guild, game_id, game.get('role_id'))
    except ValueError:
        return
    if count is None:
        count = len(role.members)  # only this game; no API inventory or history scan
    if count < config.GAME_CHANNEL_MEMBER_THRESHOLD:
        return
    destination = log_channel(guild)
    if not destination:
        return  # optional retry once setup has supplied the private log
    with db.connect() as conn:
        claimed = conn.execute('INSERT OR IGNORE INTO game_channel_candidates (guild_id,game_id,threshold_reached_at) VALUES(?,?,?)',
                               (guild.id, game_id, int(time.time()))).rowcount
    if not claimed:
        return
    from cogs.game_channels import CandidateView
    try:
        message = await destination.send(embed=discord.Embed(title='🎮 Game Channel Candidate',
            description=f'**{discord.utils.escape_markdown(game["name"])}** now has **{count} members**.\nNo dedicated game channel exists yet.'),
            view=CandidateView(game_id), allowed_mentions=discord.AllowedMentions.none())
        with db.connect() as conn:
            conn.execute('UPDATE game_channel_candidates SET log_message_id=? WHERE guild_id=? AND game_id=?', (message.id, guild.id, game_id))
    except discord.HTTPException:
        # Keep the reservation: uncertain send is never automatically repeated.
        log.warning('Game candidate delivery uncertain for game %s; review in Games', game_id)


def preview(guild, actor, game_id, action='create'):
    if not authorized(guild, actor):
        raise ValueError('Administrator access is required.')
    game = db.get_game_by_id(game_id)
    if not game:
        raise ValueError('This game is no longer available.')
    role = game_role(guild, game_id, game.get('role_id')) if action != 'remove' else guild.get_role(game.get('role_id'))
    parent = category(guild)
    hints = legacy_hints(game)
    channel = guild.get_channel(game.get('channel_id')) if game.get('channel_id') else None
    if channel and any(g['id'] != game_id and g.get('channel_id') == channel.id for g in db.get_all_games()):
        raise ValueError('This channel is linked to more than one game; owner review is required.')
    if action == 'migrate':
        if channel:
            raise ValueError('This game already has a dedicated channel.')
        channel = guild.get_channel(hints.get('chat_channel_id'))
        if not isinstance(channel, discord.TextChannel) or channel.category_id != hints.get('category_id'):
            raise ValueError('The recorded legacy chat is missing or moved; owner review is required.')
    if action == 'remove' and (not isinstance(channel, discord.TextChannel) or channel.category_id != parent.id):
        raise ValueError('The stored game channel is missing or moved; owner review is required.')
    if action == 'create' and game.get('channel_id') and channel is None:
        raise ValueError('The stored channel is unavailable; review its mapping before creating anything.')
    if action == 'create' and not channel and hints.get('chat_channel_id'):
        raise ValueError('An existing chat is recorded. Preview Migration to preserve its history.')
    if action == 'create' and not channel and any(c.name == slug(game['name']) for c in parent.channels):
        raise ValueError('A matching channel already exists. Review its ownership before linking it.')
    from services.server_operations import wire
    count = sum(bool(g.get('channel_id')) for g in db.get_all_games())
    return dict(game=game, action=action, parent=parent.id, actor=actor.id, guild=guild.id,
                channel=channel.id if channel else None, signature=wire(channel) if channel else None,
                parent_signature=wire(parent), count=count, expires=time.time()+240,
                hints=hints, role=role.id if role else None)


def description(plan):
    text = f'**{discord.utils.escape_markdown(plan["game"]["name"])}**\n'
    if plan['action'] == 'remove':
        return text + 'Delete this managed Discord channel and its message history? The game, role and stored game/event history remain. This cannot be undone.'
    if plan['action'] == 'migrate':
        return text + 'Move and rename the recorded chat into 🎮 GAMES, preserving its ID and history. Old category, LFG and create-voice resources remain for separate manual review.'
    return text + 'Create one role-gated text channel in 🎮 GAMES.\n' + (
        'The configured soft limit has been reached. Create Anyway requires explicit approval.' if plan['count'] >= config.GAME_CHANNEL_SOFT_LIMIT else 'Members with this game role can see and chat here.')


def dependencies(game_id, channel_id):
    with db.connect() as conn:
        for table, fields in [('lfg_events', ('channel_id','dashboard_channel_id','private_channel_id','voice_channel_id')),
                              ('lfg_event_messages', ('channel_id',)), ('temp_voice_channels', ('channel_id',)),
                              ('streamer_channels', ('channel_id',))]:
            if conn.execute(f'SELECT 1 FROM {table} WHERE ' + ' OR '.join(f'{f}=?' for f in fields), [channel_id]*len(fields)).fetchone():
                raise ValueError('Other stored resources reference this channel; review dependencies first.')
        if conn.execute('SELECT 1 FROM settings WHERE value=?', (str(channel_id),)).fetchone():
            raise ValueError('Another managed resource references this channel.')
        if conn.execute('SELECT 1 FROM games WHERE id!=? AND (channel_id=? OR chat_channel_id=? OR lfg_channel_id=? OR clips_channel_id=? OR memes_channel_id=? OR create_voice_channel_id=?)',
                        (game_id, *([channel_id]*6))).fetchone():
            raise ValueError('Another game references this channel.')


async def apply(guild, actor, plan, *, override=False):
    if not authorized(guild, actor) or actor.id != plan['actor'] or guild.id != plan['guild'] or time.time() > plan['expires']:
        raise ValueError('This review expired or permission changed. Open a new preview.')
    game_id = plan['game']['id']
    async with _guild_locks.setdefault(guild.id, asyncio.Lock()), area_lock(game_id):
        current = preview(guild, actor, game_id, plan['action'])
        for key in ('game', 'channel', 'signature', 'parent', 'parent_signature', 'role', 'hints'):
            if current[key] != plan[key]:
                raise ValueError('The game or channel changed. Open a new preview.')
        channel = guild.get_channel(current['channel']) if current['channel'] else None
        # Destructive/move confirmations validate exact IDs against REST as well
        # as cached signatures. Do not trust an unchanged gateway cache alone.
        from services.server_operations import wire
        fresh_parent = await guild.fetch_channel(plan['parent'])
        if not isinstance(fresh_parent, discord.CategoryChannel) or wire(fresh_parent) != plan['parent_signature']:
            raise ValueError('The destination changed. Open a new preview.')
        if channel:
            fresh_channel = await guild.fetch_channel(channel.id)
            if not isinstance(fresh_channel, discord.TextChannel) or wire(fresh_channel) != plan['signature']:
                raise ValueError('The channel changed. Open a new preview.')
            channel = fresh_channel
        if not authorized(guild, actor):
            raise ValueError('Administrator access changed. Open a new preview.')
        if db.get_game_by_id(game_id) != plan['game']:
            raise ValueError('The game changed during validation. Open a new preview.')
        if plan['action'] == 'remove':
            dependencies(game_id, channel.id)
            await channel.delete(reason='GamerHQ explicitly confirmed game channel removal')
            with db.connect() as conn:
                conn.execute('UPDATE games SET channel_id=NULL WHERE id=? AND channel_id=?', (game_id, channel.id))
                conn.execute('DELETE FROM settings WHERE key=?', (f'game_channel_creation:{guild.id}:{game_id}',))
                conn.execute("UPDATE game_channel_candidates SET status='IGNORED' WHERE guild_id=? AND game_id=?", (guild.id, game_id))
            await audit(guild, 'Game Channel Removed', plan['game']['name'])
            from services.server_log_service import emit
            await emit(guild, f'game-channel-removed:{channel.id}', 'Game Channel Removed', plan['game']['name'])
            return
        if channel and plan['action'] == 'create':
            return channel
        if current['count'] >= config.GAME_CHANNEL_SOFT_LIMIT and not override:
            raise ValueError('Channel soft limit reached. Preview again and explicitly choose Create Anyway.')
        parent = fresh_parent
        role = game_role(guild, game_id, current['role'])
        if plan['action'] == 'migrate':
            dependencies(game_id, channel.id)
            updated = await channel.edit(name=slug(plan['game']['name']), category=parent, overwrites=overwrites(guild, role, channel), reason='GamerHQ confirmed legacy chat migration')
            if updated is not None:
                channel = updated
        else:
            # Persist intent before an API write: uncertain delivery requires review,
            # never an automatic second create after a crash/retry.
            key = f'game_channel_creation:{guild.id}:{game_id}'
            with db.connect() as conn:
                if not conn.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, 'reserved')).rowcount:
                    raise ValueError('A previous creation needs owner review before retrying.')
            channel = await guild.create_text_channel(slug(plan['game']['name']), category=parent,
                overwrites=overwrites(guild, role), reason='GamerHQ confirmed optional game channel')
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (channel.id, game_id))
            if plan['action'] == 'migrate':
                conn.execute('INSERT OR REPLACE INTO game_legacy_hints VALUES(?,?)', (game_id, json.dumps(plan['hints'])))
                conn.execute('UPDATE games SET area_enabled=0, category_id=NULL, chat_channel_id=NULL, lfg_channel_id=NULL, clips_channel_id=NULL, memes_channel_id=NULL, create_voice_channel_id=NULL WHERE id=?', (game_id,))
            conn.execute("INSERT INTO game_channel_candidates(guild_id,game_id,threshold_reached_at,status) VALUES(?,?,?,'CREATED') ON CONFLICT(guild_id,game_id) DO UPDATE SET status='CREATED'", (guild.id, game_id, int(time.time())))
        await sort_channels(guild, extra=channel)
        await audit(guild, 'Game Channel Created', plan['game']['name'])
        from services.server_log_service import emit
        await emit(guild, f'game-channel-created:{channel.id}', 'Game Channel Created', plan['game']['name'])
        return channel


async def sort_channels(guild, *, extra=None):
    parent = category(guild)
    shared_ids = []
    for key in ('gaming-chat', 'looking-for-group'):
        raw = db.get_setting(f'managed_channel:{guild.id}:{key}')
        if raw and str(raw).isdigit():
            shared_ids.append(int(raw))
    shared = [c for sid in shared_ids if (c := guild.get_channel(sid)) and c.category_id == parent.id]
    for index, channel in enumerate(shared):
        if channel.position != index:
            await channel.edit(position=index, reason='GamerHQ shared GAMES channel order')

    channels = []
    for game in sorted(db.get_all_games(), key=lambda g: g['name'].casefold()):
        cid = game.get('channel_id')
        ch = extra if extra and cid == extra.id else guild.get_channel(cid) if cid else None
        if ch and ch.category_id == parent.id:
            channels.append(ch)
    start = len(shared)
    for index, channel in enumerate(channels):
        if channel.position != start + index:
            await channel.edit(position=start + index, reason='GamerHQ alphabetical game channels')


def order_plan(guild):
    try:
        parent = category(guild)
    except ValueError:
        return []
    games = sorted(db.get_all_games(), key=lambda g: g['name'].casefold())
    current = sorted([c for c in guild.channels if getattr(c, 'category_id', None) == parent.id], key=lambda c: c.position)
    managed = [c for g in games if g.get('channel_id') for c in current if c.id == g['channel_id']]
    return [(current, managed + [c for c in current if c not in managed])]


def ignore(guild, actor, game_id):
    if not authorized(guild, actor):
        raise ValueError('Administrator access is required.')
    with db.connect() as conn:
        conn.execute("UPDATE game_channel_candidates SET status='IGNORED' WHERE guild_id=? AND game_id=? AND status='PENDING'", (guild.id, game_id))
