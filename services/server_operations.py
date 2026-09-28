"""Preview/apply orchestration over existing resource keys, policies and renderers.

Reconciliation only writes mappings. Setup only creates missing resources. Repair
only edits persisted resources. No operation here deletes a Discord resource.
"""
import json
import logging
import time
from contextlib import AsyncExitStack
from types import SimpleNamespace

import discord
from database import db
from services import managed_message_service as managed, message_reconciliation as messages
from services.server_service import ServerMessageError
from services.onboarding_service import alias, INTERACTIVE_BOARDS, STATIC_BOARDS
from services.server_setup_service import SERVER_BLUEPRINT

log = logging.getLogger(__name__)


class GuildSnapshot:
    """Fresh REST inventory without modifying discord.py's gateway cache."""
    def __init__(self, guild, channels, roles):
        self.original, self.channels, self.roles = guild, channels, roles
        self.categories = [c for c in channels if isinstance(c, discord.CategoryChannel)]
        self.text_channels = [c for c in channels if c in guild.text_channels or isinstance(c, discord.TextChannel)]
        self.voice_channels = [c for c in channels if isinstance(c, discord.VoiceChannel)]

    def __getattr__(self, name): return getattr(self.original, name)
    def get_channel(self, cid): return next((c for c in self.channels if c.id == cid), None)
    def get_role(self, rid): return next((r for r in self.roles if r.id == rid), None)


def definitions(guild):
    from services.channel_change_service import removed
    from services.role_service import ROLE_GROUPS, _role_terms
    result = []
    for category in SERVER_BLUEPRINT:
        name = alias(category.name)
        name = 'partners-benefits' if name == 'marketplace' else name
        parent = f'managed_category:{guild.id}:{name}'
        names = {name, 'marketplace', 'partner-benefits'} if name == 'partners-benefits' else {name}
        result.append(dict(key=parent, kind='category', name=name, label=category.name,
                           aliases=names, private=category.private, parent=None))
        for channel in category.channels:
            child = {'purchases': 'ig-purchases', 'buyer-ranking': 'ig-buyer-ranking'}.get(alias(channel.name), alias(channel.name))
            if removed(guild, child):
                continue
            names = {child, alias(channel.name)}
            if child == 'bot-commands': names.add('community-commands')
            if child == 'electricity': names.add('haushaltscheck')
            result.append(dict(key=f'managed_channel:{guild.id}:{child}', kind=channel.kind, name=child,
                               label=channel.name, aliases=names, private=category.private, parent=parent))
    for group, options in ROLE_GROUPS.items():
        for option in options:
            result.append(dict(key='base:' + option.key, kind='role', name=option.key,
                               label=f'{option.emoji} {option.label}', aliases=_role_terms(option), group=group, parent=None))
    from services.bot_group_service import GROUPS, key
    for group, (label, _) in GROUPS.items():
        result.append(dict(key=key(guild, group), kind='bot-role', name=group, label=label,
                           aliases={alias(label)}, parent=None))
    # Optional beta resources are repaired only when already recorded; no implicit rollout.
    import config
    from services.streamer_hub_service import NAMES
    for name, label in NAMES.items():
        stored_key = f'managed_channel:{guild.id}:{name}'
        if db.get_setting(stored_key):
            result.append(dict(key=stored_key, kind='text', name=name, label=label, aliases={name}, parent=None,
                               private=not (config.STREAMER_HUB_ENABLED and name == 'stream-updates'), existing_only=True))
    return result


def mapped(row):
    if row['kind'] == 'role':
        stored = db.get_managed_role_by_key('base', row['name'])
        return str(stored['role_id']) if stored else None
    return messages.mapping(row['key']) if row['kind'] == 'message' else db.get_setting(row['key'])


def private(resource):
    everyone = resource.guild.default_role
    return (resource.overwrites_for(everyone).view_channel is False or
            (getattr(resource, 'category', None) and resource.category.overwrites_for(everyone).view_channel is False))


def eligible(guild, row, resource):
    if row['kind'] in ('role', 'bot-role'):
        from services.role_service import assignable
        return assignable(resource, guild) and (row['kind'] != 'bot-role' or all(m.bot for m in resource.members))
    # Explicit choice can reconnect a public channel outside its expected category,
    # but never turns an unknown private resource into a public one.
    parent = guild.get_channel(getattr(resource, 'category_id', None))
    hidden = (resource.overwrites_for(guild.default_role).view_channel is False or
              parent and parent.overwrites_for(guild.default_role).view_channel is False)
    return bool(hidden) == row['private']


def wire(resource):
    if hasattr(resource, 'content'):
        return (resource.id, messages.fingerprint(resource), resource.pinned)
    rights = sorted((str(target.id), value.pair()[0].value, value.pair()[1].value)
                    for target, value in getattr(resource, 'overwrites', {}).items())
    return (resource.id, resource.name, getattr(resource, 'category_id', None), rights,
            getattr(getattr(resource, 'permissions', None), 'value', None),
            getattr(resource, 'position', None), getattr(resource, 'hoist', None))


def target(guild, row):
    raw = db.get_setting(row['parent']) if row.get('parent') else None
    parent = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
    from services.channel_adoption_service import supported, placement
    if row['name'] in supported():
        _, parent = placement(guild, row['name'], row['label'], parent)
    if parent is not None and parent not in guild.categories:
        raise ServerMessageError('Recorded destination is not a category. Review /server reconcile.')
    if parent is not None and not row.get('private') and parent.overwrites_for(guild.default_role).view_channel is False:
        raise ServerMessageError('Recorded destination is private. Review the mapping; nothing is exposed.')
    return parent


def display_name(guild, row):
    from services.channel_adoption_service import supported, placement
    if row['name'] in supported():
        return placement(guild, row['name'], row['label'], target(guild, row))[0]
    return row['label']


def rights(guild, row, resource):
    from services.channel_change_service import safe_rights
    if row.get('existing_only'):
        import config
        from services.streamer_hub_service import overwrites, role
        enabled = config.STREAMER_HUB_ENABLED
        return overwrites(resource, public=enabled and row['name'] == 'stream-updates',
                          streamer=role(guild) if enabled and row['name'] != 'choose-streamers' else None)
    if row['kind'] == 'category' and row['name'] == 'partners-benefits':
        from services.support_service import partner_overwrites
        return partner_overwrites(resource)
    if row['kind'] == 'category' and row['name'] == 'affiliate-stats':
        from services.instant_gaming_service import overwrites
        return overwrites(guild, 'ig-purchases', resource.overwrites)
    if row['name'] in {'ig-purchases', 'ig-buyer-ranking', 'gaming-news', 'gaming-deals', 'free-games'}:
        return safe_rights(resource, row['name'])
    if row.get('private'):
        from cogs.suggestions import private_overwrites
        return private_overwrites(guild, resource)
    if row['name'] in INTERACTIVE_BOARDS | STATIC_BOARDS:
        return safe_rights(resource, row['name'])
    return resource.overwrites


def message_view(guild, key):
    state = managed.load(key)
    if state and state.get('customized'):
        return managed.render(state['buttons'])
    from cogs.server import managed_channel_view
    from services.support_service import support_sections, message_key, section_view
    for section, _, affiliate in support_sections():
        if key == message_key(guild, section):
            return section_view(section, affiliate)
    from services.role_panel_service import channel, message_keys, SECTIONS
    board = channel(guild)
    if board:
        keys = message_keys(guild, board)
        from cogs.roles import ChooseRolesHubView, RoleToggleView
        if key == keys['intro']: return ChooseRolesHubView()
        for section, group, _ in SECTIONS:
            if key == keys[section]: return RoleToggleView(group)
    if key.startswith('server_pinned_message_'):
        board = guild.get_channel(int(key.removeprefix('server_pinned_message_')))
        if board and alias(board.name) == 'welcome':
            from cogs.roles import OnboardingEntry
            return OnboardingEntry()
        return managed_channel_view(board) if board else None
    if key.startswith('choose_games_'):
        from cogs.games import ChooseGamesButtons, GameCategoryView
        from services.game_service import build_choose_games_sections, _choose_games_section_key
        if key == 'choose_games_message_id': return ChooseGamesButtons()
        for title, games in build_choose_games_sections():
            if key == messages.SECTION_PREFIX + _choose_games_section_key(title): return GameCategoryView(games)
    if key.startswith('suggestions_entry:'):
        from cogs.suggestions import SuggestionEntryView
        return SuggestionEntryView()
    if key.startswith('ticket_entry:'):
        from cogs.tickets import TicketEntry
        return TicketEntry()
    if key == 'streamer_guide_message_id':
        import config
        from cogs.twitch_hub import HubView
        return HubView() if config.STREAMER_HUB_ENABLED else None
    return None


async def scan(guild, bot=None):
    channels = await guild.fetch_channels()
    roles = await guild.fetch_roles() if hasattr(guild, 'fetch_roles') else guild.roles
    snapshot = GuildSnapshot(guild, channels, roles)
    with db.read_only():
        return await _scan(snapshot, bot)


async def _scan(guild, bot):
    from services.role_service import normalize_role_name
    rows = definitions(guild)
    for row in rows:
        kind = row['kind']
        collection = (guild.roles if kind in ('role', 'bot-role') else guild.categories if kind == 'category'
                      else getattr(guild, 'voice_channels', []) if kind == 'voice' else guild.text_channels)
        raw = mapped(row)
        existing = next((r for r in collection if str(r.id) == raw), None)
        candidates = [r for r in collection if (normalize_role_name(r.name) if kind == 'role' else alias(r.name)) in row['aliases']]
        if kind == 'text':
            import config
            fallback_keys = {'welcome': f'onboarding:{guild.id}:welcome', 'newbies': f'onboarding:{guild.id}:newbies',
                             'bot-commands': 'server_community_commands_channel_id', 'mod-commands': 'server_staff_commands_channel_id'}
            fallback = db.get_setting(fallback_keys[row['name']]) if row['name'] in fallback_keys else None
            if row['name'] == 'choose-your-games': fallback = str(config.CHOOSE_GAMES_CHANNEL_ID or '')
            found = next((r for r in collection if str(r.id) == fallback), None)
            if found and found not in candidates: candidates.append(found)
        if existing and existing not in candidates: candidates.append(existing)
        safe = [r.id for r in candidates if eligible(guild, row, r)]
        try:
            expected_parent = target(guild, row) if row.get('parent') else None
            placement_error = False
        except (ServerMessageError, ValueError, KeyError):
            expected_parent, placement_error = None, True
        status = 'EXACT_MATCH' if existing else 'STALE' if raw else 'MISSING'
        if not existing and candidates:
            expected_names = next((r['aliases'] for r in rows if r['key'] == row.get('parent')), set())
            placed = (not row.get('parent') or (len(candidates) == 1 and candidates[0].category and
                      (candidates[0].category == expected_parent or alias(candidates[0].category.name) in expected_names)))
            status = 'SAFE_ADOPTION' if len(candidates) == 1 and len(safe) == 1 and placed else 'AMBIGUOUS'
        if placement_error: safe, status = [], 'AMBIGUOUS'
        row.update(raw=raw, candidates=candidates, safe=safe, status=status,
                   signature=[wire(r) for r in candidates], resource=existing)
        if kind in ('text', 'voice'):
            row['signature'].extend(('parent', wire(parent)) for r in candidates
                                    if (parent := guild.get_channel(getattr(r, 'category_id', None))))
            row['signature'].append(('destination', wire(expected_parent) if expected_parent else None))
            row['signature'].append(('desired', db.get_setting(f"managed_channel_state:{guild.id}:{row['name']}")))
    warnings = []
    from services.channel_adoption_service import order_plans
    from services.community_structure_service import event_order
    known_channels = {r['resource'].id for r in rows if r['kind'] == 'text' and r['resource']}
    known_categories = {r['resource'].id: r['resource'] for r in rows if r['kind'] == 'category' and r['resource']}
    try:
        plans = order_plans(guild, guild.channels) + [event_order(guild, guild.channels)]
        for current, ordered in plans:
            if not current or current[0].category_id not in known_categories: continue
            category = known_categories[current[0].category_id]
            # Preserve unknown siblings' relative order; submit positions for managed IDs only.
            if [c.id for c in current if c.id not in known_channels] != [c.id for c in ordered if c.id not in known_channels]: continue
            positions = [{'id': c.id, 'position': i} for i, c in enumerate(ordered) if c.id in known_channels]
            if not positions: continue
            rows.append(dict(key=f'order:{category.id}', kind='order', name='order', label=category.name + ' channel order',
                             status='EXACT_MATCH', resource=category, raw=str(category.id), candidates=[], safe=[],
                             signature=[wire(c) for c in current] + [positions], positions=positions,
                             changed=[c.id for c in current] != [c.id for c in ordered]))
    except ServerMessageError:
        warnings.append('Channel order: link ambiguous categories/channels before repair.')
    defaults = managed.canonical_boards(guild, bot, warnings=warnings, defaults=True)
    for key, (channel, content) in managed.canonical_boards(guild, bot, warnings=warnings).items():
        if channel not in guild.text_channels or content is None: continue
        row = dict(key=key, name=key, label=content.split('\n', 1)[0].lstrip('# '), kind='message', channel=channel,
                   content=defaults.get(key, (None, content))[1], parent=None, resource=None, safe=[], candidates=[], raw=None, status='MISSING')
        if row['content'] is None:
            warnings.append(row['label'] + ': default content unavailable; review its channel mappings first.')
            continue
        try:
            data = await messages.inspect_board(guild, key, channel, content)
            view = message_view(guild, key)
            expected = view.to_components() if view else []
            candidates = data['matches']
            raw = messages.mapping(key)
            if raw and raw.isdigit() and not managed.load(key) and all(str(m.id) != raw for m in candidates):
                try:
                    existing = await channel.fetch_message(int(raw))
                    # Persisted generated guides may predate the current command inventory.
                    if (existing.author.id == guild.me.id and existing.type == discord.MessageType.default
                            and existing.content.split('\n', 1)[0] == content.split('\n', 1)[0]):
                        candidates.append(existing)
                except discord.NotFound:
                    pass
            safe = [m.id for m in candidates if (not hasattr(m, 'components') or
                    ([p.to_dict() for p in m.components] == expected and not m.embeds and not m.attachments))]
            existing = next((m for m in candidates if str(m.id) == raw), None)
            status = 'EXACT_MATCH' if existing else 'SAFE_ADOPTION' if len(candidates) == len(safe) == 1 else 'AMBIGUOUS' if candidates else 'STALE' if raw else 'MISSING'
            if data['state'] and (data['state'].get('pending') or data['state']['channel_id'] != channel.id
                                  or data['state'].get('guild_id') != guild.id or data['state'].get('key') != key
                                  or managed.digest(data['state']['content']) != data['state'].get('content_hash')):
                safe, status = [], 'AMBIGUOUS'
            row.update(candidates=candidates, safe=safe, raw=raw, resource=existing, status=status)
        except (ServerMessageError, discord.HTTPException, ValueError):
            row['status'] = 'AMBIGUOUS'
        row['signature'] = [wire(r) for r in row['candidates']]
        row['signature'].append(('state', json.dumps(managed.load(key), sort_keys=True), row['content']))
        rows.append(row)
    for warning in dict.fromkeys(warnings):
        rows.append(dict(key=warning, kind='warning', name=warning, label=warning, status='AMBIGUOUS',
                         candidates=[], safe=[], raw=None, resource=None, signature=[]))
    return rows


def needs_repair(guild, row):
    resource = row['resource']
    if row['status'] != 'EXACT_MATCH' or resource is None: return False
    if row['kind'] == 'order': return row['changed']
    if row['kind'] == 'message':
        if len(row['candidates']) != 1 or resource.id not in row['safe']: return False
        state = managed.load(row['key'])
        return (not resource.pinned or (not state and row['key'] in managed.specs(guild))
                or (not state or not state.get('customized')) and resource.content != row['content'])
    if row['kind'] in ('text', 'voice'):
        parent = target(guild, row)
        return ((parent and resource.category_id != parent.id) or rights(guild, row, resource) != resource.overwrites
                or (not row.get('existing_only') and resource.name != display_name(guild, row)))
    if row['kind'] == 'category':
        return rights(guild, row, resource) != resource.overwrites
    if row['kind'] == 'bot-role':
        from services.bot_group_service import GROUPS, member, assigned
        return not resource.hoist or any(not assigned(guild, bot, resource) for name in GROUPS[row['name']][1]
                                         if (bot := member(guild, name)))
    return False


async def preview(guild, actor, mode, bot=None):
    managed.require_admin(guild, actor)
    if mode == 'setup' and actor.id != guild.owner_id:
        raise ServerMessageError('Only the owner can create missing server resources.')
    rows = await scan(guild, bot)
    return dict(guild_id=guild.id, actor_id=actor.id, mode=mode, created=time.time(), rows=rows)


def persist(guild, row, resource):
    key = row['key']
    if row['kind'] == 'role':
        db.upsert_managed_role(role_id=resource.id, role_kind='base', role_key=row['name'], role_group=row['group'])
        return
    if key.startswith(messages.SECTION_PREFIX):
        values = json.loads(db.get_setting('choose_games_section_message_ids') or '{}')
        values[key[len(messages.SECTION_PREFIX):]] = resource.id
        db.set_setting('choose_games_section_message_ids', json.dumps(values))
    else:
        db.set_setting(key, resource.id)
    aliases = {'welcome': f'onboarding:{guild.id}:welcome', 'bot-commands': 'server_community_commands_channel_id',
               'mod-commands': 'server_staff_commands_channel_id'}
    if row['kind'] == 'text' and row['name'] in aliases:
        db.set_setting(aliases[row['name']], resource.id)
    if row['kind'] == 'message':
        state = managed.load(key)
        if state:
            state.update(message_id=resource.id, version=state['version'] + 1)
            managed.store(state)
        elif key in managed.specs(guild):
            buttons = managed.serialize_view(message_view(guild, key))
            managed.store(dict(key=key, guild_id=guild.id, channel_id=resource.channel.id,
                               message_id=resource.id, label=row['label'], content=resource.content, buttons=buttons,
                               default_content=row['content'], default_buttons=buttons, customized=False, version=1,
                               content_hash=managed.digest(resource.content), pending=False))


async def apply(guild, actor, draft, bot=None, *, confirmed=False, choices=None):
    managed.require_admin(guild, actor)
    if (not confirmed or draft['actor_id'] != actor.id or draft['guild_id'] != guild.id
            or time.time() - draft['created'] > 240):
        raise ServerMessageError('Open a fresh preview and explicitly confirm your changes.')
    mode = draft['mode']
    if mode == 'setup' and actor.id != guild.owner_id: raise ServerMessageError('Owner required.')
    choices = choices or {}
    done, skipped = [], []
    async with managed.lock(f'operations:{guild.id}'), AsyncExitStack() as locks:
        if mode != 'setup':
            keys = {(f"choose_games_refresh:{r['channel'].id}" if r['key'].startswith('choose_games_') else r['key'])
                    for r in draft['rows'] if r['kind'] == 'message'}
            for key in sorted(keys): await locks.enter_async_context(managed.lock(key))
        fresh = {r['key']: r for r in await scan(guild, bot)}
        created_resources = {}
        for old in draft['rows']:
            row = fresh.get(old['key'])
            if not row or (row['raw'], row['signature']) != (old['raw'], old['signature']):
                skipped.append(old['label'] + ': changed; scan again')
                continue
            managed.require_admin(guild, actor)
            key, kind = row['key'], row['kind']
            if mode == 'reconcile':
                selected = choices.get(key)
                if selected is None and row['status'] == 'SAFE_ADOPTION': selected = row['safe'][0]
                if selected is None or selected not in row['safe'] or str(selected) == row['raw']: continue
                resource = next(r for r in row['candidates'] if r.id == selected)
                if kind == 'message':
                    latest = await row['channel'].fetch_message(selected)
                    if wire(latest) != wire(resource): raise ServerMessageError('Message changed; reopen reconciliation.')
                    messages.references(selected, key)
                elif kind in ('role', 'bot-role'):
                    if any(g.get('role_id') == selected for g in db.get_all_games(active_only=False)):
                        skipped.append(row['label'] + ': belongs to a game'); continue
                    if any(str(r['role_id']) == str(selected) and ('base:' + r['role_key']) != key for r in db.get_managed_roles()):
                        skipped.append(row['label'] + ': already linked elsewhere'); continue
                if any(r['key'] != key and r['raw'] == str(selected) for r in fresh.values()):
                    skipped.append(row['label'] + ': already linked elsewhere'); continue
                persist(guild, row, resource)
                row['raw'] = str(selected)
            elif mode == 'setup':
                if row.get('existing_only') or row['status'] not in ('MISSING', 'STALE') or row['candidates']: continue
                if kind in ('role', 'bot-role'):
                    resource = await guild.create_role(name=row['label'], permissions=discord.Permissions.none(), reason='Confirmed GamerHQ setup')
                elif kind == 'category':
                    blank = SimpleNamespace(guild=guild, overwrites={}, overwrites_for=lambda _: discord.PermissionOverwrite())
                    policy = rights(guild, row, blank)
                    resource = await guild.create_category(row['label'], overwrites=policy, reason='Confirmed GamerHQ setup')
                elif kind in ('text', 'voice'):
                    parent = target(guild, row)
                    if parent is None:
                        raw_parent = db.get_setting(row['parent'])
                        parent = created_resources.get(int(raw_parent)) if raw_parent and raw_parent.isdigit() else None
                    if not parent: skipped.append(row['label'] + ': link/create parent first'); continue
                    blank = SimpleNamespace(guild=guild, name=row['label'], id=0, category=parent, overwrites={}, overwrites_for=lambda _: discord.PermissionOverwrite())
                    policy = rights(guild, row, blank)
                    create = parent.create_voice_channel if kind == 'voice' else parent.create_text_channel
                    resource = await create(row['label'], overwrites=policy, reason='Confirmed GamerHQ setup')
                elif kind == 'message':
                    async with managed.lock(key):
                        if await messages.candidates(row['channel'], content=row['content']):
                            skipped.append(row['label'] + ': an existing message needs linking'); continue
                        view = message_view(guild, key)
                        resource = await row['channel'].send(content=row['content'], view=view, allowed_mentions=discord.AllowedMentions.none())
                        persist(guild, row, resource)
                        await resource.pin(reason='Confirmed GamerHQ setup')
                else: continue
                persist(guild, row, resource)
                created_resources[resource.id] = resource
            elif mode == 'repair':
                if not needs_repair(guild, row): continue
                resource = row['resource']
                if kind == 'order':
                    from services.channel_change_service import bulk_positions
                    await bulk_positions(guild, row['positions'], reason='Confirmed GamerHQ managed channel order')
                elif kind == 'message':
                    if resource.id not in row['safe']: skipped.append(row['label'] + ': unknown controls'); continue
                    # Repair never calls an upsert that could send a replacement.
                    latest = await row['channel'].fetch_message(resource.id)
                    if wire(latest) != wire(resource): raise ServerMessageError('Message changed; scan again.')
                    messages.references(latest.id, key)
                    if latest.content != row['content']:
                        updated = await latest.edit(content=row['content'], view=message_view(guild, key), allowed_mentions=discord.AllowedMentions.none())
                        if updated is not None: latest = updated
                        state = managed.load(key)
                        if state:
                            state.update(content=row['content'], content_hash=managed.digest(row['content']), version=state['version'] + 1)
                            managed.store(state)
                    if not latest.pinned: await latest.pin(reason='Confirmed GamerHQ pin repair')
                    if not managed.load(key) and key in managed.specs(guild): persist(guild, row, latest)
                elif kind in ('text', 'voice'):
                    parent = target(guild, row)
                    if not row['private'] and private(resource):
                        skipped.append(row['label'] + ': private resource retained'); continue
                    changes = dict(overwrites=rights(guild, row, resource))
                    if not row.get('existing_only'): changes['name'] = display_name(guild, row)
                    if parent and resource.category_id != parent.id: changes.update(category=parent, sync_permissions=False)
                    from services.channel_change_service import edit
                    await edit(resource, **changes, reason='Confirmed GamerHQ repair')
                elif kind == 'category':
                    if not row['private'] and private(resource):
                        skipped.append(row['label'] + ': private category retained'); continue
                    known = {r['resource'].id for r in fresh.values() if r['resource'] and r['kind'] in ('text', 'voice')}
                    if any(getattr(c, 'category_id', None) == resource.id and c.id not in known
                           and c.overwrites == resource.overwrites for c in await guild.fetch_channels()):
                        skipped.append(row['label'] + ': unknown permission-synced children retained'); continue
                    from services.channel_change_service import edit
                    await edit(resource, overwrites=rights(guild, row, resource), reason='Confirmed GamerHQ category repair')
                elif kind == 'bot-role':
                    from services.bot_group_service import GROUPS, member, assigned, safe
                    if not safe(resource) or resource.permissions.value or any(not m.bot for m in resource.members):
                        skipped.append(row['label'] + ': unsafe/shared bot role; review manually'); continue
                    if not resource.hoist: await resource.edit(hoist=True, reason='Confirmed GamerHQ bot grouping')
                    for name in GROUPS[row['name']][1]:
                        bot_member = member(guild, name)
                        if bot_member and not assigned(guild, bot_member, resource):
                            await bot_member.add_roles(resource, reason='Confirmed GamerHQ bot grouping')
                else: continue
            else: raise ServerMessageError('Unknown operation.')
            done.append(row['label'])
            log.info('Managed operation mode=%s guild=%s actor=%s key=%s resource=%s', mode, guild.id, actor.id, key, resource.id)
    return done, skipped
