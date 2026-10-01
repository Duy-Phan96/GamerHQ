"""Confirmed admin visibility -> shared Games channel lifecycle.

Uses the existing games/settings tables, channel service, resource locks and
role safety rules. No startup worker, second channel registry or destructive
hide operation. Member game selection does not call this service.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import discord
import config
from database import db
from services import game_channel_service as channels
from services import managed_message_service as managed
from services import structure_adoption_service as runtime
from services.authorization_service import authorized
from services.game_area_safety import area_lock
from services.onboarding_service import alias, is_staff
from services.role_service import assignable
from services.server_operations import GuildSnapshot, persist, rights

CATEGORY_NAME = '🎮 Games'
CATEGORY_LIMIT = 50
SERVER_CHANNEL_LIMIT = 500
ROLE_LIMIT = 250


def operation_key(guild, game_id):
    return f'game_visibility_operation:{guild.id}:{game_id}'


def _signature(resource):
    """Only fields this operation can overwrite; absolute order is not identity."""
    if resource is None:
        return None
    return (resource.id, resource.name, getattr(resource, 'category_id', None),
            sorted((t.id, *[p.value for p in o.pair()]) for t, o in resource.overwrites.items()))


def _role(guild, game, visible):
    role = guild.get_role(int(game['role_id'])) if game.get('role_id') else None
    if not visible and role is None:
        return None, 'keep'
    if visible and role is None and game.get('role_id'):
        raise ValueError('The saved game role is missing. Review its mapping before showing this game.')
    mode = 'keep'
    if visible and role is None:
        name = f"{game.get('emoji') or '🎮'} {game['name']}"
        matches = [r for r in guild.roles if r.name.casefold() == name.casefold()]
        if len(matches) > 1:
            raise ValueError('Several matching game roles exist. Review their identities first.')
        role = matches[0] if matches else None
        mode = 'link' if role else 'create'
    if role:
        if not assignable(role, guild):
            raise ValueError('The game role is unsafe or above the bot. Review role permissions and hierarchy.')
        if any(g['id'] != game['id'] and g.get('role_id') == role.id for g in db.get_all_games()):
            raise ValueError('The game role is linked to another game.')
        if any(r['role_id'] == role.id for r in db.get_managed_roles()):
            raise ValueError('This role is already used by another managed feature.')
    elif visible and len(guild.roles) >= ROLE_LIMIT:
        raise ValueError('The server role limit is reached. No role or channel was created.')
    return role, mode


def _category_plan(guild):
    keys = [f'managed_category:{guild.id}:{name}' for name in ('games', 'gaming')]
    saved = [db.get_setting(k) for k in keys]
    ids = {int(value) for value in saved if value and str(value).isdigit()}
    if any(value and not str(value).isdigit() for value in saved) or len(ids) > 1:
        raise ValueError('The Games category mappings conflict. Review Games/Gaming in Server Structure.')
    if ids:
        parent = guild.get_channel(next(iter(ids)))
        if not isinstance(parent, discord.CategoryChannel):
            raise ValueError('The saved Games category is unavailable. Review its mapping; no duplicate was created.')
        return parent, 'keep', tuple(saved)
    if any(db.get_setting(f'managed_category_removed:{guild.id}:{name}') == '1'
           for name in ('games', 'gaming')):
        raise ValueError('The Games category was deliberately removed. Review a replacement in Server Structure first.')
    candidates = [c for c in guild.categories if alias(c.name) in {'games', 'gaming'}]
    if len(candidates) > 1:
        raise ValueError('Several Games/Gaming categories exist. Link the intended category in Server Structure.')
    parent = candidates[0] if candidates else None
    if parent:
        # Name discovery is only a candidate. The explicit confirmation links
        # this exact ID after its signature and existing ownership are rechecked.
        with db.connect() as conn:
            if conn.execute('SELECT 1 FROM settings WHERE key LIKE ? AND value=?',
                            (f'managed_category:{guild.id}:%', str(parent.id))).fetchone():
                raise ValueError('The matching category belongs to another managed area.')
        if any(g.get('category_id') == parent.id for g in db.get_all_games()):
            raise ValueError('The matching category belongs to a legacy game area. Review it before linking.')
    elif len(guild.channels) >= SERVER_CHANNEL_LIMIT:
        raise ValueError('The server channel limit is reached. The Games category cannot be created.')
    return parent, 'link' if parent else 'create', tuple(saved)


def _channel(guild, game):
    hints = channels.legacy_hints(game)
    cid = game.get('channel_id') or hints.get('chat_channel_id')
    channel = guild.get_channel(int(cid)) if cid else None
    if cid and not isinstance(channel, discord.TextChannel):
        raise ValueError('The saved game chat is unavailable or is not a text channel. Review its mapping first.')
    if channel:
        channels.dependencies(game['id'], channel.id)
        with db.connect() as conn:
            for row in conn.execute('SELECT game_id,resources_json FROM game_legacy_hints WHERE game_id!=?', (game['id'],)):
                data = json.loads(row['resources_json'])
                if channel.id in [v for k, v in data.items() if k.endswith('_channel_id')]:
                    raise ValueError('The saved chat is also referenced by another game. Review its ownership.')
    return channel, hints


def _validate_access(guild, role, channel):
    if channel is None:
        return
    allowed = {guild.default_role.id, guild.me.id}
    if role:
        allowed.add(role.id)
    allowed.update(r.id for r in guild.roles if is_staff(r))
    if any(o.view_channel is True and t.id not in allowed for t, o in channel.overwrites.items()):
        raise ValueError('This chat has additional access grants. Review them before changing game visibility.')


def _plan(guild, actor, game_id, visible):
    if not authorized(guild, actor):
        raise ValueError('Administrator access is required.')
    game = db.get_game_by_id(game_id)
    if not game:
        raise ValueError('This game is no longer in the library.')
    if not guild.me or not guild.me.guild_permissions.manage_channels or not guild.me.guild_permissions.manage_roles:
        raise ValueError('The bot needs Manage Channels and Manage Roles for game-channel visibility.')
    role, role_action = _role(guild, game, visible)
    channel, hints = _channel(guild, game)
    _validate_access(guild, role, channel)
    parent, category_action, mappings = _category_plan(guild) if visible else (None, 'keep', ())
    if visible and channel is None and db.get_setting(f'game_channel_removed:{guild.id}:{game_id}') == '1':
        raise ValueError('This game chat was deliberately removed. Review explicit recreation before showing it.')
    if visible and channel is None:
        if db.get_setting(f'game_channel_creation:{guild.id}:{game_id}'):
            raise ValueError('A previous channel creation needs review. No duplicate will be created.')
    if visible and parent and any(c.name == channels.slug(game['name']) and (channel is None or c.id != channel.id) for c in parent.channels):
        raise ValueError('A same-named channel is already present. Review its ownership before linking it.')
    entering = visible and (channel is None or parent is None or channel.category_id != parent.id)
    if entering and parent and len(parent.channels) >= CATEGORY_LIMIT:
        raise ValueError('Games already contains 50 channels, including hidden ones. Free space before continuing.')
    if visible and channel is None and len(guild.channels) + int(category_action == 'create') >= SERVER_CHANNEL_LIMIT:
        raise ValueError('The server channel limit leaves no space for this game chat.')
    role_signature = (role.id, role.name, role.permissions.value, role.position) if role else None
    return dict(guild_id=guild.id, actor_id=actor.id, game=game, visible=bool(visible),
                channel_id=channel.id if channel else None, channel_signature=_signature(channel),
                hints=hints, parent_id=parent.id if parent else None, parent_signature=_signature(parent),
                category_action=category_action, parent_name=parent.name if parent else CATEGORY_NAME,
                mappings=mappings, role_action=role_action,
                role_signature=role_signature, created=time.time(),
                override=bool(visible and channel is None and
                    sum(bool(g.get('channel_id')) for g in db.get_all_games()) >= config.GAME_CHANNEL_SOFT_LIMIT))


async def _snapshot(guild):
    # Configuration inventory only. No chat history or member-list scans.
    return GuildSnapshot(guild, await guild.fetch_channels(), await guild.fetch_roles())


async def preview(guild, actor, game_id, visible):
    if not authorized(guild, actor):
        raise ValueError('Administrator access is required.')
    return _plan(await _snapshot(guild), actor, game_id, visible)


def description(plan):
    name = discord.utils.escape_mentions(discord.utils.escape_markdown(plan['game']['name']))[:100]
    if not plan['visible']:
        return (f'# Hide {name}\nRemove this game from the selection.\n'
                + ('Hide its existing text channel from game members. Staff can still access it.\n'
                   if plan['channel_id'] else 'No game chat is currently linked; no channel will be created.\n')
                + 'Messages, channel identity and member roles are preserved. Nothing is deleted.')
    parent_name = discord.utils.escape_mentions(discord.utils.escape_markdown(plan['parent_name']))[:100]
    destination = {'create': 'Create the shared 🎮 Games category.',
                   'link': f'Link the existing category **{parent_name}** and name it 🎮 Games.',
                   'keep': f'Reuse the saved **{parent_name}** category.'}[plan['category_action']]
    room = 'Move/reuse the saved text chat; keep its messages and identity.' if plan['channel_id'] else 'Create one role-gated game text channel.'
    return (f'# Show {name}\n{destination}\n{room}\n'
            f"Game role: { {'keep': 'use existing role', 'link': 'link matching safe role', 'create': 'create a new role'}[plan['role_action']] }. Only this game role and staff will see the chat.\n"
            'No legacy category, LFG or voice channel will be deleted.\n'
            + ('The channel soft limit is reached. Confirm with Create Anyway.' if plan['override'] else 'Confirm to update the game and its channel together.'))


async def _reserved_create(guild, reservation, create):
    with db.connect() as conn:
        if not conn.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (reservation, 'reserved')).rowcount:
            raise ValueError('An earlier creation has an unverified result. Review it before retrying.')
    try:
        resource = await create()
    except discord.HTTPException as exc:
        if exc.status in (400, 403):
            with db.connect() as conn:
                conn.execute('DELETE FROM settings WHERE key=?', (reservation,))
        raise
    db.set_setting(reservation, json.dumps({'state': 'CREATED', 'id': resource.id}))
    return resource


def _replace(guild, resource, *, role=False):
    rooms = guild.channels if role else [c for c in guild.channels if c.id != resource.id] + [resource]
    roles = [r for r in guild.roles if r.id != resource.id] + [resource] if role else guild.roles
    return GuildSnapshot(guild.original, rooms, roles)


async def _ensure_parent(guild, plan):
    parent = guild.get_channel(plan['parent_id']) if plan['parent_id'] else None
    row = dict(key=f'managed_category:{guild.id}:games', kind='category', name='games',
               label=CATEGORY_NAME, private=False, parent=None)
    if parent is None:
        blank = SimpleNamespace(guild=guild, overwrites={})
        policy = rights(guild, row, blank)
        parent = await _reserved_create(guild, f'games_category_creation:{guild.id}',
            lambda: guild.create_category(CATEGORY_NAME, overwrites=policy,
                                          reason='GamerHQ confirmed shared Games setup'))
    persist(guild, row, parent)
    # Do not change existing category permissions or its other child channels.
    if parent.name != CATEGORY_NAME:
        from services.channel_change_service import edit
        result = await edit(parent, name=CATEGORY_NAME, reason='GamerHQ confirmed Games category name')
        if result is not None:
            parent = result
    runtime.save_runtime_state(guild, 'category', 'games', runtime.snapshot_category(parent))
    return parent


async def apply(guild, actor, plan, *, override=False):
    if (not authorized(guild, actor) or guild.id != plan['guild_id'] or actor.id != plan['actor_id']
            or time.time() - plan['created'] > 240):
        raise ValueError('This review expired or access changed. Open a fresh preview.')
    game_id, visible = plan['game']['id'], plan['visible']
    async with managed.lock(f'operations:{guild.id}'), channels._guild_locks.setdefault(guild.id, asyncio.Lock()), area_lock(game_id):
        fresh = await _snapshot(guild)
        current = _plan(fresh, actor, game_id, visible)
        for field in ('game', 'channel_id', 'channel_signature', 'hints', 'parent_id', 'parent_signature',
                      'category_action', 'mappings', 'role_action', 'role_signature'):
            if current[field] != plan[field]:
                raise ValueError('The game, role or channel changed. Reopen the preview; nothing was overwritten.')
        if current['override'] and not override:
            raise ValueError('The channel soft limit is reached. Review and choose Create Anyway.')
        role = fresh.get_role(plan['role_signature'][0]) if plan['role_signature'] else None
        channel = fresh.get_channel(plan['channel_id']) if plan['channel_id'] else None
        parent = None
        if visible:
            parent = await _ensure_parent(fresh, plan)
            fresh = _replace(fresh, parent)
            if role is None:
                game = plan['game']
                role = await _reserved_create(fresh, f'game_visibility_role_creation:{guild.id}:{game_id}',
                    lambda: fresh.create_role(name=f"{game.get('emoji') or '🎮'} {game['name']}",
                        permissions=discord.Permissions.none(), reason='GamerHQ confirmed visible game role'))
                fresh = _replace(fresh, role, role=True)
            if not assignable(role, fresh):
                raise ValueError('The game role is no longer assignable. Some setup steps may have completed; review again.')
            db.set_game_role(game_id, role.id)
        if not authorized(guild, actor):
            raise ValueError('Administrator access changed. No game visibility change was applied.')
        # Save the requested policy before the remote change. A failure is
        # explicit/pending, and repair must not reopen a deliberately hidden room.
        db.set_game_selectable(game_id, visible)
        db.set_setting(channels.hidden_key(guild, game_id), '0' if visible else '1')
        db.set_setting(operation_key(guild, game_id), json.dumps({'state': 'PENDING', 'visible': visible}))
        if visible and channel is None:
            channel = await channels.create_reserved_channel(fresh, db.get_game_by_id(game_id), parent, role,
                reason='GamerHQ confirmed visible game channel')
            fresh = _replace(fresh, channel)
        if channel:
            policy = channels.overwrites(fresh, role, channel, visible=visible)
            kwargs = {}
            if policy != channel.overwrites:
                kwargs['overwrites'] = policy
            if visible:
                if channel.category_id != parent.id:
                    kwargs.update(category=parent, sync_permissions=False)
                if channel.name != channels.slug(plan['game']['name']):
                    kwargs['name'] = channels.slug(plan['game']['name'])
            if kwargs:
                from services.channel_change_service import edit
                updated = await edit(channel, **kwargs, reason='GamerHQ confirmed game visibility')
                if updated is not None:
                    channel = updated
            # Targeted REST verification, not a claim based on the gateway cache.
            verified = await guild.fetch_channel(channel.id)
            if (not isinstance(verified, discord.TextChannel) or verified.overwrites != policy
                    or (visible and (verified.category_id != parent.id or verified.name != channels.slug(plan['game']['name'])))):
                raise ValueError('Discord did not verify the complete change. The request is saved as pending; review again.')
            channel = verified
            with db.connect() as conn:
                conn.execute('UPDATE games SET channel_id=? WHERE id=?', (channel.id, game_id))
                if not plan['game'].get('channel_id') and plan['hints'].get('chat_channel_id'):
                    conn.execute('INSERT OR REPLACE INTO game_legacy_hints VALUES(?,?)', (game_id, json.dumps(plan['hints'])))
                    conn.execute('UPDATE games SET area_enabled=0, category_id=NULL, chat_channel_id=NULL, lfg_channel_id=NULL, clips_channel_id=NULL, memes_channel_id=NULL, create_voice_channel_id=NULL WHERE id=?', (game_id,))
            if visible:
                runtime.save_runtime_state(guild, 'channel', f'game:{game_id}', runtime.snapshot_channel(channel))
                fresh = await _snapshot(guild)
                await channels.sort_channels(fresh)
        db.set_setting(operation_key(guild, game_id), json.dumps({'state': 'DONE', 'visible': visible,
                                                                'channel_id': channel.id if channel else None}))
        await channels.audit(guild, 'Game Shown' if visible else 'Game Hidden', plan['game']['name'])
        return channel


async def sync_preview(guild, actor, *, limit=3):
    """Small owner-selected rollout: first three not-ready visible games by name."""
    if not authorized(guild, actor):
        raise ValueError('Administrator access is required.')
    fresh = await _snapshot(guild)
    plans, blocked, ready = [], [], 0
    for game in db.get_all_games():
        if not game['active'] or not game['selectable']:
            continue
        try:
            plan = _plan(fresh, actor, game['id'], True)
            channel = fresh.get_channel(plan['channel_id']) if plan['channel_id'] else None
            role = fresh.get_role(game['role_id'])
            correct = (role is not None and plan['role_action'] == 'keep' and channel is not None and plan['parent_id'] is not None
                       and channel.category_id == plan['parent_id'] and channel.name == channels.slug(game['name'])
                       and channel.overwrites == channels.overwrites(fresh, role, channel, visible=True)
                       and db.get_setting(channels.hidden_key(guild, game['id'])) != '1'
                       and plan['category_action'] == 'keep')
            pending = json.loads(db.get_setting(operation_key(guild, game['id'])) or '{}').get('state') == 'PENDING'
            if correct and not pending:
                ready += 1
            elif len(plans) < limit:
                plans.append(plan)
        except ValueError as exc:
            blocked.append(f"{game['name']}: {exc}")
    return dict(plans=plans, blocked=blocked, ready=ready)
