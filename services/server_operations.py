"""Preview/apply orchestration over existing resource keys, policies and renderers.

Reconciliation only writes mappings. Setup only creates missing resources. Repair
only edits persisted resources. No operation here deletes a Discord resource.
"""
import json
import logging
import sqlite3
import time
from contextlib import AsyncExitStack
from copy import copy
from types import SimpleNamespace

import discord
from database import db
from services import managed_message_service as managed, message_reconciliation as messages
from services.server_service import ServerMessageError
from services.onboarding_service import alias, INTERACTIVE_BOARDS, STATIC_BOARDS
from services.server_setup_service import SERVER_BLUEPRINT
from services.operation_context import read_scope, read_once, gather_reads, measured, operation

log = logging.getLogger(__name__)

SETUP_PHASES = {
    'bulk': frozenset({'category', 'text', 'voice', 'role', 'bot-role'}),
    'structure': frozenset({'category', 'text', 'voice', 'role', 'bot-role'}),
    'foundations': frozenset({'category', 'role', 'bot-role'}),
    'channels': frozenset({'text', 'voice'}),
    'placement': frozenset({'text', 'voice'}),
    'messages': frozenset({'message'}),
}
# The localized Discord defaults can be considered only by explicit selection.
REUSE_ALIASES = {'general': {'allgemein'}, 'chill-lounge': {'allgemein', 'general'}}
PHASE_TITLES = {
    'bulk': 'Setup All Missing',
    'structure': 'Review Existing Structure',
    'foundations': 'Preview Missing Categories & Roles',
    'channels': 'Preview Missing Channels',
    'placement': 'Preview Reused Channel Placement',
    'messages': 'Preview Missing Managed Messages',
}


def in_phase(row, scope):
    return (scope is None or row['kind'] in SETUP_PHASES[scope]
            and (scope != 'placement' or row['name'] in REUSE_ALIASES and not row.get('existing_only')))



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
        row = dict(key=parent, kind='category', name=name, label=category.name,
                   aliases=names, private=category.private, parent=None)
        if name == 'games':
            row['aliases'].add('gaming')
            row['legacy_key'] = f'managed_category:{guild.id}:gaming'
        result.append(row)
        for channel in category.channels:
            child = {'purchases': 'ig-purchases', 'buyer-ranking': 'ig-buyer-ranking'}.get(alias(channel.name), alias(channel.name))
            if removed(guild, child):
                continue
            names = {child, alias(channel.name)}
            if child == 'bot-commands': names.add('community-commands')
            if child == 'electricity': names.add('haushaltscheck')
            result.append(dict(key=f'managed_channel:{guild.id}:{child}', kind=channel.kind, name=child,
                               label=channel.name, aliases=names, private=category.private, parent=parent,
                               optional=channel.optional))
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
    from services.game_channel_service import slug
    for game in sorted(db.get_all_games(), key=lambda g: g['name'].casefold()):
        if game.get('channel_id'):
            result.append(dict(key=f'game_channel:{game["id"]}', kind='text', name=slug(game['name']),
                label=slug(game['name']), aliases=set(), parent=f'managed_category:{guild.id}:games',
                private=True, existing_only=True, game_id=game['id']))
    return result


def mapped(row):
    if row.get('game_id'):
        game = db.get_game_by_id(row['game_id'])
        return str(game['channel_id']) if game and game.get('channel_id') else None
    if row['kind'] == 'role':
        stored = db.get_managed_role_by_key('base', row['name'])
        return str(stored['role_id']) if stored else None
    if row['kind'] == 'message':
        return messages.mapping(row['key'])
    raw = db.get_setting(row['key'])
    return raw or (db.get_setting(row['legacy_key']) if row.get('legacy_key') else None)


def private(resource):
    everyone = resource.guild.default_role
    return (resource.overwrites_for(everyone).view_channel is False or
            (getattr(resource, 'category', None) and resource.category.overwrites_for(everyone).view_channel is False))


def snapshot_permissions(guild, channel, subject):
    detached = copy(channel)
    detached.guild = SimpleNamespace(id=guild.id, owner_id=getattr(guild, 'owner_id', None),
                                     default_role=guild.get_role(guild.id), get_role=guild.get_role)
    return detached.permissions_for(subject)


async def public_message_channel(guild, channel):
    if channel not in guild.text_channels or channel.guild.id != guild.id:
        return False
    everyone = guild.get_role(guild.id)
    parent_id = channel.category_id
    parent = guild.get_channel(parent_id) if parent_id is not None else None
    if everyone is None or guild.me is None or getattr(channel, 'nsfw', False):
        return False
    if parent_id is not None:
        if not isinstance(parent, discord.CategoryChannel):
            return False
        inherited = parent.overwrites_for(everyone)
        if inherited.view_channel is False or inherited.read_message_history is False:
            return False
    overwrite = channel.overwrites_for(everyone)
    access = ('view_channel', 'read_message_history')
    for name in access:
        public = getattr(overwrite, name)
        if public is None:
            public = getattr(everyone.permissions, name)
        if not public:
            return False
    try:
        bot_member = await read_once(('message-permissions-member', guild.id), lambda: guild.fetch_member(guild.me.id))
    except (discord.HTTPException, TimeoutError):
        return False
    if bot_member.id != guild.me.id:
        return False
    # Resolve effective permissions against the REST roles and channel overwrites
    # without changing the Discord gateway cache or reimplementing permission rules.
    bot_permissions = snapshot_permissions(guild, channel, bot_member)
    return all(getattr(bot_permissions, name) for name in access)


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
    games_parent = row['parent'] == f'managed_category:{guild.id}:games'
    if games_parent:
        legacy = db.get_setting(f'managed_category:{guild.id}:gaming')
        if raw and legacy and raw != legacy:
            raise ServerMessageError('Games category mappings conflict. Review Server Structure.')
        raw = raw or legacy
    parent = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
    if games_parent and raw and not isinstance(parent, discord.CategoryChannel):
        raise ServerMessageError('Saved Games category is unavailable. Review Server Structure.')
    from services import structure_adoption_service as runtime
    logical = f"game:{row['game_id']}" if row.get('game_id') else row['name']
    state = runtime.channel_state(guild, logical)
    if state and not state.get('deleted') and state.get('category_id') is not None:
        adopted = guild.get_channel(int(state['category_id']))
        if isinstance(adopted, discord.CategoryChannel):
            parent = adopted
    from services.channel_adoption_service import supported, placement
    if row['name'] in supported() and not state:
        _, parent = placement(guild, row['name'], row['label'], parent)
    if parent is not None and parent not in guild.categories:
        raise ServerMessageError('Recorded destination is not a category. Review /server reconcile.')
    if parent is not None and not row.get('private') and parent.overwrites_for(guild.default_role).view_channel is False:
        raise ServerMessageError('Recorded destination is private. Review the mapping; nothing is exposed.')
    return parent


def display_name(guild, row):
    from services import structure_adoption_service as runtime
    logical = f"game:{row['game_id']}" if row.get('game_id') else row['name']
    state = runtime.channel_state(guild, logical)
    if state and not state.get('deleted') and state.get('name'):
        return state['name']
    from services.channel_adoption_service import supported, placement
    if row['name'] in supported():
        return placement(guild, row['name'], row['label'], target(guild, row))[0]
    return row['label']


def rights(guild, row, resource):
    if row.get('game_id'):
        from services.game_channel_service import overwrites
        from services.role_service import game_role
        game = db.get_game_by_id(row['game_id'])
        try:
            if any(g['id'] != game['id'] and g.get('channel_id') == resource.id for g in db.get_all_games()):
                raise ValueError('Shared game channel mapping')
            role = game_role(guild, game['id'], game.get('role_id'), selectable_only=False)
        except ValueError as exc:
            raise ServerMessageError('Game role/channel ownership needs review; no permissions changed.') from exc
        return overwrites(guild, role, resource)
    from services.channel_change_service import safe_rights
    if row['name'] in {'server-log', 'games-log'}:
        from services.server_log_service import overwrites
        return overwrites(guild, resource)
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
    if key.startswith('community_events:'):
        from cogs.lfg import build_lfg_hub_view
        return build_lfg_hub_view()
    if key == 'streamer_guide_message_id':
        import config
        from cogs.twitch_hub import HubView
        return HubView() if config.STREAMER_HUB_ENABLED else None
    return None


@measured('server_scan')
async def scan(guild, bot=None, *, fresh=False, message_keys=None, affected=None, verify_placement=False, bulk=False):
    with read_scope():
        channels, roles = list(guild.channels), list(guild.roles)
        structure = affected is None or any(r['kind'] in ('text', 'voice', 'category', 'order', 'message') for r in affected)
        role_check = affected is None or any(
            r['kind'] in ('role', 'bot-role', 'message') or 'create_overwrites' in r
            or r['name'] in REUSE_ALIASES for r in affected)
        if not channels or (fresh and structure):
            ids = {r.id for row in affected or [] if row['kind'] in ('text', 'voice') for r in row['candidates']}
            ids.update(r.category_id for row in affected or [] if row['kind'] in ('text', 'voice')
                       for r in row['candidates'] if r.category_id is not None)
            for row in affected or []:
                if row['kind'] == 'message':
                    ids.add(row['channel'].id)
                    if row['channel'].category_id is not None:
                        ids.add(row['channel'].category_id)
            for row in affected or []:
                try: parent = target(guild, row) if row.get('parent') else None
                except ServerMessageError: parent = None
                if parent: ids.add(parent.id)
            targeted = (not verify_placement and affected and ids and len(ids) <= 2 and hasattr(guild, 'fetch_channel')
                        and all((r['kind'] in ('text', 'voice', 'message')
                                and r['resource'] is not None and str(r['resource'].id) == r['raw']) for r in affected))
            if targeted:
                async def fetch(cid):
                    try: return await read_once(('channel', cid), lambda: guild.fetch_channel(cid))
                    except discord.NotFound: return None
                updates = await gather_reads([fetch(cid) for cid in ids])
                channels = [c for c in channels if c.id not in ids] + [c for c in updates if c is not None]
            else:
                # Discovery/adoption, category/order and creation need a fresh candidate inventory.
                channels = await read_once(('channels', guild.id), guild.fetch_channels)
        if (not roles or fresh and role_check) and hasattr(guild, 'fetch_roles'):
            roles = await read_once(('roles', guild.id), guild.fetch_roles)
        snapshot = GuildSnapshot(guild, channels, roles)
        with db.read_only():
            return await _scan(snapshot, bot, message_keys=message_keys, verify_placement=verify_placement, bulk=bulk)


async def _scan(guild, bot, *, message_keys=None, protected=None, verify_placement=False, bulk=False):
    from services.role_service import normalize_role_name
    from services.structure_adoption_service import channel_mapping
    if protected is None:
        from services.server_configuration_preview import skill_configuration
        protected, _ = skill_configuration(guild)
    rows = definitions(guild)
    for row in rows:
        kind = row['kind']
        collection = (guild.roles if kind in ('role', 'bot-role') else guild.categories if kind == 'category'
                      else getattr(guild, 'voice_channels', []) if kind == 'voice' else guild.text_channels)
        raw = mapped(row)
        existing = next((r for r in collection if str(r.id) == raw), None)
        candidates = [existing] if existing else [r for r in collection if (normalize_role_name(r.name) if kind == 'role' else alias(r.name)) in row['aliases']]
        if kind == 'text' and not existing:
            import config
            fallback_keys = {'welcome': f'onboarding:{guild.id}:welcome', 'newbies': f'onboarding:{guild.id}:newbies',
                             'bot-commands': 'server_community_commands_channel_id', 'mod-commands': 'server_staff_commands_channel_id'}
            fallback = db.get_setting(fallback_keys[row['name']]) if row['name'] in fallback_keys else None
            if row['name'] == 'choose-your-games': fallback = str(config.CHOOSE_GAMES_CHANNEL_ID or '')
            found = next((r for r in collection if str(r.id) == fallback), None)
            if found and found not in candidates: candidates.append(found)
        if existing and existing not in candidates: candidates.append(existing)
        review_aliases = REUSE_ALIASES.get(row['name'], ()) if kind in ('text', 'voice') else ()
        review_candidates = [r for r in collection if alias(r.name) in review_aliases] if not existing else []
        candidates.extend(r for r in review_candidates if r not in candidates)
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
        if row.get('legacy_key'):
            canonical = db.get_setting(row['key'])
            legacy = db.get_setting(row['legacy_key'])
            # A recorded category is preserved even when temporarily unavailable.
            # Conflicting records or a tombstone must be resolved before setup.
            removed = any(db.get_setting(f'managed_category_removed:{guild.id}:{name}') == '1'
                          for name in ('games', 'gaming'))
            placement_error |= bool((canonical and legacy and canonical != legacy)
                                    or (raw and existing is None) or (removed and existing is None))
        if (expected_parent and expected_parent.id in protected
                or any(r.id in protected or getattr(r, 'category_id', None) in protected
                       or (getattr(r, 'topic', None) or '').startswith('skill-resource:') for r in candidates)):
            placement_error = True
            row['reason'] = 'Skill-owned resource or destination'
        if existing and review_aliases and alias(existing.name) != row['name'] and any(
                r.id != existing.id and alias(r.name) == row['name'] for r in collection):
            placement_error = True
            row['reason'] = 'A different channel already uses the intended name'
        if review_aliases and candidates and (not existing or verify_placement):
            access_issue = None
            for candidate in candidates:
                if candidate.id not in safe:
                    continue
                everyone = guild.get_role(guild.id)
                permission = 'read_message_history' if kind == 'text' else 'connect'
                parent_id = candidate.category_id
                if everyone is None or parent_id is not None and guild.get_channel(parent_id) is None:
                    access_issue = 'Existing channel parent or @everyone role could not be verified'
                    safe.remove(candidate.id)
                    continue
                access = snapshot_permissions(guild, candidate, everyone)
                if not (access.view_channel and getattr(access, permission)):
                    access_issue = ('Existing channel needs verified @everyone View Channel and '
                                    + ('Read Message History' if kind == 'text' else 'Connect'))
                    safe.remove(candidate.id)
                    continue
                if existing:
                    if channel_mapping(guild, candidate.id) != row['name']:
                        safe.remove(candidate.id)
                else:
                    try:
                        messages.assert_unreferenced(candidate.id, row['key'])
                    except ServerMessageError:
                        access_issue = 'Existing channel has a competing saved mapping'
                        safe.remove(candidate.id)
            if (review_candidates or not safe) and not placement_error:
                status = 'AMBIGUOUS'
                row['reason'] = ('Select the intended existing channel' if safe
                                 else (access_issue if bulk and len(candidates) == 1 and access_issue
                                       else 'Existing channel ownership or access needs review'))
        if placement_error: safe, status = [], 'AMBIGUOUS'
        row.update(raw=raw, candidates=candidates, safe=safe, status=status,
                   signature=[wire(r) for r in candidates], resource=existing)
        if kind in ('text', 'voice'):
            row['destination'] = expected_parent
            row['signature'].extend(('parent', wire(parent)) for r in candidates
                                    if (parent := guild.get_channel(getattr(r, 'category_id', None))))
            row['signature'].append(('destination', wire(expected_parent) if expected_parent else None))
            row['signature'].append(('desired', db.get_setting(f"managed_channel_state:{guild.id}:{row['name']}")))
            if verify_placement and review_aliases and status == 'EXACT_MATCH' and existing.id in safe:
                row['placement_name'] = display_name(guild, row)
                row['signature'].append(('placement_name', row['placement_name']))
        if not bulk and row.get('private') and status == 'MISSING' and not row.get('existing_only'):
            blank = SimpleNamespace(guild=guild, name=row['label'], id=0, category=expected_parent,
                                    overwrites={}, overwrites_for=lambda _: discord.PermissionOverwrite())
            policy = rights(guild, row, blank)
            row['create_overwrites'] = policy
            row['signature'].append(('policy', sorted(
                (principal.id, allow.value, deny.value)
                for principal, overwrite in policy.items() for allow, deny in [overwrite.pair()])))
    if bulk:
        member = (await read_once(('bulk-setup-member', guild.id), lambda: guild.fetch_member(guild.me.id))
                  if guild.me else None)
        return bulk_plan(guild, rows, member)
    warnings = []
    from services.channel_adoption_service import order_plans
    from services.community_structure_service import event_order
    known_channels = {r['resource'].id for r in rows if r['kind'] == 'text' and r['resource']}
    known_categories = {r['resource'].id: r['resource'] for r in rows if r['kind'] == 'category' and r['resource']}
    try:
        from services.game_channel_service import order_plan
        plans = order_plans(guild, guild.channels) + [event_order(guild, guild.channels)] + order_plan(guild)
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
    if message_keys is None or message_keys:
        defaults = managed.canonical_boards(guild, bot, warnings=warnings, defaults=True)
        boards = managed.canonical_boards(guild, bot, warnings=warnings)
        jobs = [inspect_message(guild, key, channel, content, defaults.get(key, (None, content))[1], boards=boards)
                for key, (channel, content) in boards.items()
                if (message_keys is None or key in message_keys) and channel in guild.text_channels]
        rows.extend(await gather_reads(jobs))
    for warning in dict.fromkeys(warnings):
        rows.append(dict(key=warning, kind='warning', name=warning, label=warning, status='AMBIGUOUS',
                         candidates=[], safe=[], raw=None, resource=None, signature=[]))
    return rows


class BulkPolicyError(ServerMessageError):
    """An owner-safe, locally generated permission diagnostic."""


def bulk_policy_issue(policy, permissions):
    # Discord requires every explicit allow AND deny in a create request to
    # be within the bot's guild permissions. Never strip bits and create anyway.
    if permissions.administrator:
        return None
    requested = 0
    for overwrite in policy.values():
        allow, deny = overwrite.pair()
        requested |= allow.value | deny.value
    manage_roles = discord.Permissions(manage_roles=True).value
    if requested & manage_roles:
        return 'Only a Discord administrator can set a Manage Roles overwrite'
    missing = requested & ~permissions.value
    if missing:
        names = [name.replace('_', ' ').title() for name, enabled in discord.Permissions(missing) if enabled]
        return 'Host lacks guild permissions required by the overwrite: ' + ', '.join(names or ['unknown permission'])
    return None


def verify_bulk_private_policy(guild, row, policy):
    if not row.get('private'):
        return
    from services.onboarding_service import is_staff
    allowed = {principal.id for principal, overwrite in policy.items() if overwrite.view_channel is True}
    staff = {role.id for role in guild.roles if is_staff(role)}
    if not allowed & staff:
        raise BulkPolicyError('No verified Staff role has access under the current policy')
    permitted = staff | {guild.me.id}
    if row['name'] in {'affiliate-stats', 'ig-purchases', 'ig-buyer-ranking'}:
        from services.instant_gaming_service import configured_bot
        publisher = configured_bot(guild)
        if publisher is None:
            raise BulkPolicyError('Verify the installed Instant Gaming bot in Server Management → Integrations')
        permitted.add(publisher.id)
    if allowed - permitted:
        raise BulkPolicyError('Private policy grants access to an unverified principal')


def bulk_error_reason(error):
    if isinstance(error, BulkPolicyError):
        return str(error)
    if isinstance(error, discord.RateLimited):
        return 'Discord rate limit reached; open a fresh preview later'
    if isinstance(error, discord.HTTPException):
        status, code = error.status, error.code
        if status == 429:
            detail = 'Discord rate limit reached; open a fresh preview later'
        elif status == 403:
            detail = 'Discord refused access; review the Host permissions and role hierarchy'
        elif status == 400:
            detail = 'Discord rejected the request; review the resource policy and API code'
        elif status >= 500:
            detail = 'Discord service error; check the inventory before retrying'
        else:
            detail = 'Discord rejected the request; check the inventory before retrying'
        return f'{detail} (HTTP {status}, code {code})'
    if isinstance(error, TimeoutError):
        return 'Request timed out; check the inventory before retrying'
    if isinstance(error, sqlite3.Error):
        return 'Database mapping could not be saved; check the inventory before retrying'
    if isinstance(error, ServerMessageError):
        return 'Resource ownership or setup policy needs review'
    return 'Unexpected setup error; review the sanitized Host log before retrying'


def setup_policy(guild, row, parent=None):
    blank = SimpleNamespace(guild=guild, name=row['label'], id=0, category=parent,
                            overwrites={}, overwrites_for=lambda _: discord.PermissionOverwrite())
    policy = rights(guild, row, blank)
    if row.get('private') and (
            policy.get(guild.default_role, discord.PermissionOverwrite()).view_channel is not False
            or guild.me is None
            or policy.get(guild.me, discord.PermissionOverwrite()).view_channel is not True):
        raise ServerMessageError('Private permissions need review')
    return policy


def policy_signature(policy, *, exclude=()):
    return sorted((principal.id, *[value.value for value in overwrite.pair()])
                  for principal, overwrite in policy.items() if principal.id not in exclude)


def bulk_plan(guild, rows, member):
    """Classify the same reconciliation inventory before the owner confirms."""
    order = {'role': 0, 'bot-role': 0, 'category': 1, 'text': 2, 'voice': 2}
    rows = sorted(rows, key=lambda row: (order[row['kind']], row['key']))
    reviewed = {}
    permissions = discord.Permissions.none()
    if member:
        member = copy(member)
        member.guild = SimpleNamespace(id=guild.id, owner_id=guild.owner_id,
                                       default_role=guild.get_role(guild.id), get_role=guild.get_role)
        permissions = member.guild_permissions
    for row in rows:
        row.update(bulk_action='skip', bulk_guild=guild, bulk_permissions=permissions)
        reviewed[row['key']] = row
        if row.get('optional') or row.get('existing_only'):
            row['reason'] = 'Outside bulk setup'
            continue
        kind, status = row['kind'], row['status']
        role = kind in ('role', 'bot-role')
        candidates = row['candidates']
        match = candidates[0] if len(candidates) == 1 else None
        if match and match.id in row['safe']:
            if role and (not permissions.manage_roles or not match < member.top_role):
                row['reason'] = 'GamerHQ cannot manage the existing role'
                continue
            if any(other['key'] != row['key'] and
                   (str(match.id) == other['raw'] or any(item.id == match.id for item in other['candidates']))
                   for other in rows):
                row['reason'] = 'Resource is shared by competing mappings'
                continue
            try:
                if status != 'EXACT_MATCH':
                    messages.assert_unreferenced(match.id, row['key'])
                if kind == 'bot-role' and match.permissions.value:
                    raise ServerMessageError('Bot grouping role has existing grants')
                if row.get('private'):
                    parent = reviewed.get(row['parent'])
                    if row['parent'] and (not parent or parent['bulk_action'] != 'reuse'
                                          or match.category_id != parent.get('bulk_id')):
                        raise ServerMessageError('Existing private parent needs review')
                    if match.overwrites_for(guild.default_role).view_channel is not False:
                        raise BulkPolicyError('Existing private resource needs an explicit @everyone view deny')
                    policy = setup_policy(guild, row, row.get('destination'))
                    verify_bulk_private_policy(guild, row, policy)
                    allowed = {principal.id for principal, overwrite in policy.items() if overwrite.view_channel is True}
                    if any(overwrite.view_channel is True and principal.id not in allowed
                           for principal, overwrite in match.overwrites.items()):
                        raise ServerMessageError('Existing private resource has additional access grants')
            except BulkPolicyError as exc:
                row['reason'] = str(exc)
                continue
            except (ServerMessageError, ValueError):
                row['reason'] = 'Existing ownership or permissions need review'
                continue
            if status in ('EXACT_MATCH', 'SAFE_ADOPTION'):
                row.update(bulk_action='reuse', bulk_id=match.id)
                continue
            if row['name'] in REUSE_ALIASES and alias(match.name) in REUSE_ALIASES[row['name']]:
                row.update(bulk_action='choose', bulk_id=match.id)
                continue
        if status != 'MISSING' or candidates:
            continue
        if role:
            if permissions.manage_roles:
                row['bulk_action'] = 'create'
            else:
                row['reason'] = 'GamerHQ needs Manage Roles'
            continue
        if not permissions.manage_channels or (row.get('private') and not permissions.manage_roles):
            row['reason'] = 'GamerHQ needs permissions to create this resource'
            continue
        parent = reviewed.get(row['parent'])
        if row['parent'] and (not parent or parent['bulk_action'] not in ('reuse', 'create')
                              or (row['destination'] and row['destination'].id != parent.get('bulk_id'))):
            row['bulk_blocked'] = True
            row['reason'] = 'Parent category needs review' + (f": {parent['label']}" if parent else '')
            continue
        if parent and parent.get('bulk_id'):
            destination = guild.get_channel(parent['bulk_id'])
            if hasattr(destination, 'permissions_for') and not snapshot_permissions(guild, destination, member).manage_channels:
                row['reason'] = 'GamerHQ cannot manage the existing category'
                continue
        try:
            policy = setup_policy(guild, row, row.get('destination'))
            verify_bulk_private_policy(guild, row, policy)
            issue = bulk_policy_issue(policy, permissions)
            if issue:
                raise BulkPolicyError(issue)
            row['create_overwrites'] = policy
        except BulkPolicyError as exc:
            row['reason'] = str(exc)
            continue
        except (ServerMessageError, ValueError):
            row['reason'] = 'Creation permissions need review'
            continue
        row['signature'].append(('bulk-policy', policy_signature(row['create_overwrites'])))
        row['bulk_action'] = 'create'
    return rows


def confirmed_setup_policy(row, added_roles, parent=None):
    guild = row['bulk_guild']
    policy = setup_policy(guild, row, parent)
    verify_bulk_private_policy(guild, row, policy)
    issue = bulk_policy_issue(policy, row['bulk_permissions'])
    if issue:
        raise BulkPolicyError(issue)
    if policy_signature(policy, exclude=added_roles) != policy_signature(row['create_overwrites'], exclude=added_roles):
        raise BulkPolicyError('Creation permissions changed during setup; review a fresh preview')
    return policy


def component_payload_matches(actual, expected):
    # Discord assigns numeric IDs to components that the owning renderer leaves
    # unspecified. Custom IDs, link targets, options and ordering still must match.
    if len(actual) != len(expected):
        return False
    for received, configured in zip(actual, expected):
        received = dict(received)
        configured = dict(configured)
        if 'id' not in configured:
            received.pop('id', None)
        if not component_payload_matches(received.pop('components', []), configured.pop('components', [])):
            return False
        if received != configured:
            return False
    return True


async def inspect_message(guild, key, channel, content, default_content, *, boards):
    row = dict(key=key, name=key, label=key, kind='message', channel=channel, guild=guild,
               content=default_content, parent=None, resource=None, safe=[], candidates=[],
               raw=None, status='AMBIGUOUS', signature=[])
    if not await public_message_channel(guild, channel):
        return dict(row, reason='Destination or public history access could not be verified')
    if content is None or default_content is None:
        return dict(row, reason='Default content unavailable; review the destination configuration')
    try:
        state = managed.load(key)
        raw = messages.mapping(key)
        row['raw'] = raw
        spec = managed.specs(guild).get(key)
        row['label'] = spec[0] if spec else key if state else content.split('\n', 1)[0].lstrip('# ')
        linked_channel = db.get_setting(spec[1]) if spec else None
        if linked_channel and linked_channel != str(channel.id):
            return dict(row, reason='Saved destination conflicts with the discovered channel')
        if managed.partner_migration_pending(guild, key):
            return dict(row, reason='Existing partner migration needs review')
        aliases = tuple(db.get_setting(alias_key) for alias_key in messages.equivalent_keys(key))
        if state and (state.get('pending') or state.get('retired') or state.get('channel_id') != channel.id
                      or state.get('guild_id') != guild.id or state.get('key') != key
                      or managed.digest(state.get('content')) != state.get('content_hash')):
            return dict(row, reason='Saved message state needs review')
        view = message_view(guild, key)
        expected = view.to_components() if view else []
        snapshot = await messages.history_snapshot(channel) if not raw else None
        data = await messages.inspect_board(guild, key, channel, content, snapshot=snapshot, mapped_first=True)
        candidates = data['matches']
        existing = next((m for m in candidates if str(m.id) == raw), None)
        known = {value for value in (*aliases, str(state['message_id']) if state else None) if value}
        safe = []
        for message in candidates:
            if (message.channel.id != channel.id or (known and known != {str(message.id)})
                    or (state and not managed.owns(state, channel, message))
                    or not component_payload_matches([p.to_dict() for p in getattr(message, 'components', [])], expected)
                    or message.embeds or getattr(message, 'attachments', [])):
                continue
            if message is not existing:
                try:
                    messages.references(message.id, key)
                except ServerMessageError:
                    continue
            safe.append(message.id)
        if existing:
            status, reason = 'EXACT_MATCH', 'Existing saved message'
        elif len(candidates) == len(safe) == 1:
            heading = content.split('\n', 1)[0]
            found, _ = snapshot if snapshot is not None else await messages.history_snapshot(channel)
            other_board = any(m.id != safe[0] and m.type == discord.MessageType.default
                              and m.content.split('\n', 1)[0] == heading for m in found.values())
            status, reason = ('AMBIGUOUS', 'A second message has the board heading; owner review required') if other_board else (
                'SAFE_ADOPTION', 'Verified existing bot message; link its ID')
        elif candidates or known or (raw and not raw.isdigit()):
            status, reason = 'AMBIGUOUS', 'Saved identity, competing matches or message controls need review'
        else:
            if snapshot is None:
                # A numeric saved ID can reach here only after the exact lookup
                # returned NotFound and the bounded candidate scan completed.
                snapshot = await messages.history_snapshot(channel)
            sibling_ids = {messages.mapping(other) for other, (destination, _) in boards.items()
                           if other != key and destination is not None and destination.id == channel.id}
            heading = content.split('\n', 1)[0]
            unidentified = any(m.type == discord.MessageType.default and str(m.id) not in sibling_ids
                               and (m.author.id == guild.me.id or getattr(m.author, 'bot', False)
                                    or getattr(m, 'webhook_id', None) or m.content.split('\n', 1)[0] == heading)
                               for m in snapshot[0].values())
            status, reason = ('AMBIGUOUS', 'Unlinked messages could include an existing board') if unidentified else (
                'MISSING', 'No managed message found in complete public history')
        row.update(candidates=candidates, safe=safe, resource=existing, status=status, reason=reason)
        row['signature'] = [(channel.id, status, tuple(safe), aliases, state, default_content, expected),
                            *(wire(message) for message in candidates)]
    except (ServerMessageError, discord.HTTPException, ValueError, TimeoutError, KeyError, TypeError):
        row['reason'] = 'Message identity or complete public history could not be verified'
    return row


def needs_repair(guild, row, *, placement=False):
    resource = row['resource']
    if row['status'] != 'EXACT_MATCH' or resource is None: return False
    if row.get('optional'): return False
    if placement:
        parent, name = row.get('destination'), row.get('placement_name')
        return bool(parent and name and (resource.category_id != parent.id or resource.name != name))
    if row['kind'] == 'order': return row['changed']
    if row['kind'] == 'message':
        if len(row['candidates']) != 1 or resource.id not in row['safe']: return False
        state = managed.load(row['key'])
        return (not resource.pinned or (not state and row['key'] in managed.specs(guild))
                or (not state or not state.get('customized')) and resource.content != row['content'])
    if row['kind'] in ('text', 'voice'):
        parent = target(guild, row)
        return ((parent and resource.category_id != parent.id) or rights(guild, row, resource) != resource.overwrites
                or ((not row.get('existing_only') or row.get('game_id')) and resource.name != display_name(guild, row)))
    if row['kind'] == 'category':
        return rights(guild, row, resource) != resource.overwrites
    if row['kind'] == 'bot-role':
        from services.bot_group_service import GROUPS, member, assigned
        return not resource.hoist or any(not assigned(guild, bot, resource) for name in GROUPS[row['name']][1]
                                         if (bot := member(guild, name)))
    return False


async def preview(guild, actor, mode, bot=None, *, scope=None):
    managed.require_admin(guild, actor)
    if (mode == 'setup' or scope in ('placement', 'bulk')) and actor.id != guild.owner_id:
        raise ServerMessageError('Only the owner can review these changes.')
    with operation(f'server_{mode}_preview'):
        rows = await scan(guild, bot, fresh=True, message_keys=None if scope in (None, 'messages') else (),
                          verify_placement=scope == 'placement', bulk=scope == 'bulk')
    rows = [r for r in rows if in_phase(r, scope) or r['kind'] == 'warning']
    if mode == 'reconcile' and actor.id != guild.owner_id:
        for row in rows:
            if ((row['kind'] == 'message' or row['name'] in REUSE_ALIASES)
                    and row['status'] == 'AMBIGUOUS' and row['safe']):
                row.update(safe=[], reason='The owner must review these candidates')
    return dict(guild_id=guild.id, actor_id=actor.id, mode=mode, scope=scope, created=time.time(), rows=rows)


def persist(guild, row, resource):
    if row.get('game_id'):
        # Only an already persisted ID can be repaired; name discovery cannot adopt.
        if mapped(row) != str(resource.id):
            raise ServerMessageError('Game channel ownership changed; review Games.')
        return
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


@measured('server_apply')
async def apply(guild, actor, draft, bot=None, *, confirmed=False, choices=None):
    managed.require_admin(guild, actor)
    if (not confirmed or draft['actor_id'] != actor.id or draft['guild_id'] != guild.id
            or time.time() - draft['created'] > 240):
        raise ServerMessageError('Open a fresh preview and explicitly confirm your changes.')
    mode = draft['mode']
    scope = draft.get('scope')
    if (mode == 'setup' or scope == 'placement') and actor.id != guild.owner_id:
        raise ServerMessageError('Owner required.')
    choices = choices or {}
    bulk = scope == 'bulk'
    if bulk and mode != 'setup':
        raise ServerMessageError('Bulk setup requires the owner setup operation.')
    result = dict(created=[], reused=[], already_configured=[], skipped=[], blocked=[], failed=[],
                  needs_review=[], remaining=[]) if bulk else None
    done, skipped = [], []
    available, added_roles = {}, set()
    labels = {row['key']: row['label'] for row in draft['rows']} if bulk else {}

    def unresolved(bucket, row, reason):
        result[bucket].append(f"{row['label']} · {reason}")
        if bucket != 'skipped':
            result['remaining'].append(row['label'])

    def bulk_action(row):
        action = row.get('bulk_action')
        if action == 'choose':
            return 'reuse' if choices.get(row['key']) == row.get('bulk_id') else 'skip'
        return action

    def actionable(row):
        if bulk:
            return bulk_action(row) in ('create', 'reuse')
        if not in_phase(row, scope) or row.get('optional'):
            return False
        if scope in ('channels', 'placement') and not row.get('destination'):
            return False
        if mode == 'repair':
            try: return needs_repair(guild, row, placement=scope == 'placement')
            except ServerMessageError: return True  # Revalidation reports drift without applying it.
        if mode == 'setup':
            statuses = ('MISSING',) if scope or row['kind'] == 'message' else ('MISSING', 'STALE')
            return row['status'] in statuses and not row['candidates'] and not row.get('existing_only')
        selected = choices.get(row['key'])
        if selected is not None:
            return selected in row['safe'] and str(selected) != row['raw']
        return scope != 'structure' and row['status'] == 'SAFE_ADOPTION'
    planned = [row for row in draft['rows'] if actionable(row)]
    if bulk:
        for row in draft['rows']:
            if not actionable(row):
                reason = row.get('reason') or 'Existing resource needs review'
                if row.get('optional') or row.get('existing_only'):
                    unresolved('skipped', row, reason)
                elif row.get('bulk_blocked'):
                    unresolved('blocked', row, reason)
                else:
                    if row.get('bulk_action') == 'choose':
                        reason = 'Select the verified existing channel in a fresh preview to link it'
                    unresolved('needs_review', row, reason)
    elif not planned:
        return done, skipped
    async with managed.lock(f'operations:{guild.id}'), AsyncExitStack() as locks:
        if bulk and time.time() - draft['created'] > 240:
            raise ServerMessageError('Open a fresh setup preview before confirming.')
        keys = {(f"choose_games_refresh:{r['channel'].id}" if r['key'].startswith('choose_games_') else r['key'])
                for r in planned if r['kind'] == 'message'}
        for key in sorted(keys):
            await locks.enter_async_context(managed.lock(key))
        shared = [r for r in planned if mode != 'repair' or r['kind'] != 'message']
        fresh = {r['key']: r for r in await scan(guild, bot, fresh=True, affected=None if bulk else shared,
                 message_keys={r['key'] for r in shared if r['kind'] == 'message'},
                 verify_placement=scope == 'placement', bulk=bulk)} if shared else {}
        created_resources = {}
        for old in planned:
            row = fresh.get(old['key'])
            if mode == 'repair' and old['kind'] == 'message':
                # Re-read only this board immediately before repairing it.
                rows = await scan(guild, bot, fresh=True, affected=[old], message_keys={old['key']})
                row = next((r for r in rows if r['key'] == old['key']), None)
            if (not row or (row['raw'], row['signature']) != (old['raw'], old['signature'])
                    or bulk and (bulk_action(row), row.get('bulk_id')) != (bulk_action(old), old.get('bulk_id'))):
                if bulk:
                    if row and row['status'] == 'EXACT_MATCH' and row['bulk_action'] == 'reuse':
                        result['already_configured'].append(row['label'])
                    else:
                        reason = row.get('reason') if row and row['bulk_action'] == 'skip' else None
                        unresolved('needs_review', old, reason or 'Changed since preview; open a fresh preview')
                else:
                    skipped.append(old['label'] + ': changed; scan again')
                continue
            managed.require_admin(guild, actor)
            if bulk and actor.id != guild.owner_id:
                raise ServerMessageError('Owner required.')
            key, kind = row['key'], row['kind']
            if bulk and bulk_action(row) == 'reuse' and row['status'] == 'EXACT_MATCH':
                available[key] = row['resource']
                result['already_configured'].append(row['label'])
                continue
            phase = 'link mapping' if bulk and bulk_action(row) == 'reuse' else 'create Discord resource'
            try:
                if mode == 'reconcile' or bulk and bulk_action(row) == 'reuse':
                    if ((kind == 'message' or row['name'] in REUSE_ALIASES)
                            and row['status'] == 'AMBIGUOUS' and actor.id != guild.owner_id):
                        skipped.append(row['label'] + ': owner review required')
                        continue
                    selected = row['bulk_id'] if bulk else choices.get(key)
                    if selected is None and row['status'] == 'SAFE_ADOPTION': selected = row['safe'][0]
                    if selected is None or selected not in row['safe'] or str(selected) == row['raw']: continue
                    resource = next(r for r in row['candidates'] if r.id == selected)
                    if kind == 'message':
                        latest = await row['channel'].fetch_message(selected)
                        if (latest.channel.id != row['channel'].id or latest.author.id != guild.me.id
                                or latest.type != discord.MessageType.default or wire(latest) != wire(resource)):
                            raise ServerMessageError('Message changed; reopen reconciliation.')
                        messages.references(selected, key)
                        managed.require_admin(guild, actor)
                    elif kind in ('role', 'bot-role'):
                        if any(g.get('role_id') == selected for g in db.get_all_games(active_only=False)):
                            if bulk:
                                unresolved('needs_review', row, 'belongs to a game')
                            else:
                                skipped.append(row['label'] + ': belongs to a game')
                            continue
                        if any(str(r['role_id']) == str(selected) and ('base:' + r['role_key']) != key for r in db.get_managed_roles()):
                            if bulk:
                                unresolved('needs_review', row, 'already linked elsewhere')
                            else:
                                skipped.append(row['label'] + ': already linked elsewhere')
                            continue
                    if any(r['key'] != key and r['raw'] == str(selected) for r in fresh.values()):
                        if bulk:
                            unresolved('needs_review', row, 'already linked elsewhere')
                        else:
                            skipped.append(row['label'] + ': already linked elsewhere')
                        continue
                    if bulk:
                        messages.assert_unreferenced(selected, key)
                    persist(guild, row, resource)
                    row['raw'] = str(selected)
                elif mode == 'setup':
                    if not actionable(row): continue
                    if kind in ('role', 'bot-role'):
                        resource = await guild.create_role(name=row['label'], permissions=discord.Permissions.none(),
                                                           reason='Confirmed GamerHQ setup',
                                                           **({'hoist': True} if bulk and kind == 'bot-role' else {}))
                    elif kind == 'category':
                        blank = SimpleNamespace(guild=guild, overwrites={}, overwrites_for=lambda _: discord.PermissionOverwrite())
                        policy = confirmed_setup_policy(row, added_roles) if bulk else (
                            row['create_overwrites'] if scope and 'create_overwrites' in row else rights(guild, row, blank))
                        resource = await guild.create_category(row['label'], overwrites=policy, reason='Confirmed GamerHQ setup')
                    elif kind in ('text', 'voice'):
                        parent = available.get(row['parent']) if bulk else (
                            row['destination'] if scope == 'channels' else target(guild, row))
                        if parent is None and not bulk:
                            raw_parent = db.get_setting(row['parent'])
                            parent = created_resources.get(int(raw_parent)) if raw_parent and raw_parent.isdigit() else None
                        if not parent:
                            if bulk:
                                parent_label = labels.get(row['parent'], 'required category')
                                unresolved('blocked', row, f'Parent unavailable after setup: {parent_label}')
                            else:
                                skipped.append(row['label'] + ': link/create parent first')
                            continue
                        blank = SimpleNamespace(guild=guild, name=row['label'], id=0, category=parent, overwrites={}, overwrites_for=lambda _: discord.PermissionOverwrite())
                        policy = confirmed_setup_policy(row, added_roles, parent) if bulk else (
                            row['create_overwrites'] if scope and 'create_overwrites' in row else rights(guild, row, blank))
                        create = parent.create_voice_channel if kind == 'voice' else parent.create_text_channel
                        resource = await create(row['label'], overwrites=policy, reason='Confirmed GamerHQ setup')
                    elif kind == 'message':
                        with read_scope(), db.read_only():
                            boards = managed.canonical_boards(row['guild'], bot)
                            destination, content = boards.get(key, (None, None))
                            defaults = managed.canonical_boards(row['guild'], bot, defaults=True)
                            default_content = defaults.get(key, (None, content))[1]
                            current = await inspect_message(row['guild'], key, destination, content, default_content, boards=boards)
                        if current['signature'] != row['signature']:
                            skipped.append(row['label'] + ': message evidence changed; scan again')
                            continue
                        managed.require_admin(guild, actor)
                        if actor.id != guild.owner_id:
                            raise ServerMessageError('Owner required.')
                        view = message_view(row['guild'], key)
                        nonce = managed.digest([guild.id, destination.id, key])[:25]
                        resource = await destination.send(content=row['content'], view=view, nonce=nonce,
                                                          allowed_mentions=discord.AllowedMentions.none())
                        persist(guild, row, resource)
                        await resource.pin(reason='Confirmed GamerHQ setup')
                    else: continue
                    if kind != 'message':
                        phase = 'save mapping after Discord creation'
                        persist(guild, row, resource)
                    created_resources[resource.id] = resource
                elif mode == 'repair':
                    if not needs_repair(guild, row, placement=scope == 'placement'): continue
                    resource = row['resource']
                    if kind == 'order':
                        from services.channel_change_service import bulk_positions
                        await bulk_positions(guild, row['positions'], reason='Confirmed GamerHQ managed channel order')
                    elif kind == 'message':
                        if resource.id not in row['safe']: skipped.append(row['label'] + ': unknown controls'); continue
                        # Repair never calls an upsert that could send a replacement.
                        latest = resource  # Exact-ID fetch was the immediately preceding revalidation.
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
                        parent = row['destination'] if scope == 'placement' else target(guild, row)
                        if not row['private'] and private(resource):
                            skipped.append(row['label'] + ': private resource retained'); continue
                        changes = {} if scope == 'placement' else dict(overwrites=rights(guild, row, resource))
                        if scope == 'placement':
                            changes['name'] = row['placement_name']
                        elif not row.get('existing_only') or row.get('game_id'):
                            changes['name'] = display_name(guild, row)
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
            except Exception as exc:
                if not bulk:
                    raise
                reason = bulk_error_reason(exc)
                unresolved('needs_review' if isinstance(exc, BulkPolicyError) else 'failed',
                           row, f'{phase}: {reason}')
                # Avoid raw Discord payloads, exception messages and tracebacks.
                log.warning('Bulk setup guild=%s key=%s phase=%s error=%s reason=%s',
                            guild.id, key, phase, type(exc).__name__, reason)
                continue
            if bulk:
                available[key] = resource
                result['created' if bulk_action(row) == 'create' else 'reused'].append(row['label'])
                if kind in ('role', 'bot-role') and bulk_action(row) == 'create':
                    row['bulk_guild'].roles.append(resource)
                    added_roles.add(resource.id)
            done.append(row['label'])
            log.info('Managed operation mode=%s guild=%s actor=%s key=%s resource=%s', mode, guild.id, actor.id, key, resource.id)
    return result if bulk else (done, skipped)
