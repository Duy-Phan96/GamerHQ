"""Private, read-only review of the Host's existing setup definitions."""
from collections import Counter
import json

import discord

import config
from database import db
from services import managed_message_service as managed, message_reconciliation as messages
from services import server_operations as operations
from services.game_channel_service import legacy_hints
from services.operation_context import gather_reads, read_once, read_scope
from services.role_service import game_role
from services.server_service import ServerMessageError
from tools.game_catalog_import import manifest, plan


def require_owner(guild, actor):
    if guild.id != config.GUILD_ID or actor.id != guild.owner_id:
        raise ServerMessageError('Only the owner of the configured guild can review its configuration.')
    managed.require_admin(guild, actor)


def skill_configuration(guild):
    protected = set()
    rows = []
    with db.connect() as conn:
        skills = conn.execute(
            'SELECT skill_id, configured, enabled FROM skill_guild_state WHERE guild_id=? AND installed=1 ORDER BY skill_id',
            (guild.id,),
        ).fetchall()
        owned = conn.execute(
            "SELECT json_extract(state_json, '$.channelId') FROM skill_managed_resources WHERE guild_id=?",
            (guild.id,),
        ).fetchall()
        authority = conn.execute(
            "SELECT skill_id FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'",
            (guild.id,),
        ).fetchone()
    protected.update(int(row[0]) for row in owned if row[0])
    if any(skill['skill_id'] == 'event-manager' for skill in skills):
        authoritative = authority is not None and authority[0] == 'event-manager'
        rows.append(('skill', 'event-manager.authority', 'Leave unchanged' if authoritative else 'Conflict',
                     'Installed Skill owns the domain' if authoritative else 'Verify authority in existing Host diagnostics'))
    for skill in skills:
        key = skill['skill_id']
        state = 'Enabled' if skill['enabled'] else 'Configured' if skill['configured'] else 'Unconfigured'
        rows.append(('skill', key, 'Leave unchanged', state))
        policy = json.loads(db.get_setting(f'skill_resources:{guild.id}:{key}') or '{}')
        for slot, label in (('categoryId', 'text-area'), ('voiceCategoryId', 'voice-area')):
            if slot not in policy and key != 'event-manager':
                continue
            identifier = int(policy.get(slot) or 0)
            if identifier:
                protected.add(identifier)
            target = guild.get_channel(identifier)
            ready = isinstance(target, discord.CategoryChannel)
            rows.append(('skill', f'{key}.{label}', 'Reuse' if ready else 'Conflict',
                         'Host category present' if ready else 'Check the existing Host integration configuration'))
    return protected, rows


def structure_row(guild, row, protected):
    kind, name = row['kind'], row['name']
    if kind == 'warning':
        return ('structure', 'managed placement', 'Conflict', 'Review existing links')
    if kind == 'order':
        return ('structure', 'managed ordering', 'Leave unchanged', 'Review ordering separately')
    resources = row['candidates']
    if any(r.id in protected or getattr(r, 'category_id', None) in protected for r in resources):
        return (kind, name, 'Conflict', 'Skill-owned resource or destination')
    status = row['status']
    if status == 'SAFE_ADOPTION':
        return (kind, name, 'Reuse', 'Review link to existing resource')
    if status in {'AMBIGUOUS', 'STALE'}:
        return (kind, name, 'Conflict', 'Ambiguous or unavailable identity')
    if status == 'MISSING':
        action = 'Leave unchanged' if row.get('existing_only') else 'Create'
        return (kind, name, action, 'No matching resource')
    try:
        resource = row['resource']
        if not operations.eligible(guild, row, resource):
            return (kind, name, 'Conflict', 'Visibility, role safety or hierarchy needs review')
        if kind in {'category', 'text', 'voice'} and operations.rights(guild, row, resource) != resource.overwrites:
            return (kind, name, 'Update', 'Managed permission overwrite differences')
        if kind in {'role', 'bot-role'} and resource.permissions.value:
            return (kind, name, 'Conflict', 'Existing role has permissions beyond the setup defaults')
        if operations.needs_repair(guild, row):
            return (kind, name, 'Update', 'Managed configuration differs')
    except (ServerMessageError, ValueError):
        return (kind, name, 'Conflict', 'Existing policy or mapping needs review')
    return (kind, name, 'Reuse', 'Existing managed identity')


def game_rows(guild, games, protected):
    counts = Counter()
    rows = []
    saved = [(game, legacy_hints(game)) for game in games]
    chat_owners = Counter(int(identifier) for game, hints in saved for identifier in
                          {game['channel_id'], hints.get('chat_channel_id')} if identifier)
    for game, hints in saved:
        canonical = game['channel_id']
        legacy = hints.get('chat_channel_id')
        counts['active'] += bool(game['active'])
        counts['visible'] += bool(game['active'] and game['selectable'])
        counts['saved roles'] += bool(game['role_id'])
        counts['saved chats'] += bool(canonical)
        counts['legacy areas'] += bool(hints)
        if not (game['active'] or game['role_id'] or canonical or hints):
            continue
        bad = False
        if game['role_id']:
            try:
                game_role(guild, game['id'], game['role_id'], selectable_only=False)
                counts['live roles'] += 1
            except ValueError:
                bad = True
        if canonical or legacy:
            chat = guild.get_channel(int(canonical or legacy))
            if (not isinstance(chat, discord.TextChannel) or chat.id in protected
                    or chat.category_id in protected or chat_owners[chat.id] > 1
                    or (canonical and legacy and int(canonical) != int(legacy))):
                bad = True
            else:
                counts['live chats'] += 1
        if bad:
            rows.append(('game', game['name'], 'Conflict', 'Review saved role or chat identity'))
        elif hints and not canonical:
            rows.append(('game', game['name'], 'Leave unchanged', 'Legacy area retained for Games recovery review'))
        elif game['active'] and not (game['role_id'] and canonical):
            rows.append(('game', game['name'], 'Leave unchanged', 'Game provisioning is reviewed separately'))
    return counts, rows


async def preview(guild, actor, bot=None):
    require_owner(guild, actor)
    if not guild.me or getattr(guild, 'unavailable', False):
        raise ServerMessageError('The configured Discord guild is unavailable.')
    with read_scope(), db.read_only():
        channels, roles = await gather_reads([
            read_once(('channels', guild.id), guild.fetch_channels),
            read_once(('roles', guild.id), guild.fetch_roles),
        ])
        require_owner(guild, actor)
        if any(resource.guild.id != guild.id for resource in (*channels, *roles)):
            raise ServerMessageError('Discord inventory does not belong to the configured guild.')
        snapshot = operations.GuildSnapshot(guild, channels, roles)
        protected, skill_rows = skill_configuration(snapshot)
        definitions = await operations._scan(snapshot, bot, message_keys=())
        rows = [structure_row(snapshot, row, protected) for row in definitions]
        matched = {r.id for row in definitions for r in row['candidates']}
        extra = sum(channel.id not in matched for channel in channels)
        boards = managed.canonical_boards(snapshot, bot)
        specs = managed.specs(snapshot)
        board_keys = set(boards) | set(specs) | {'choose_games_message_id'}
        for key in sorted(board_keys):
            channel = boards.get(key, (None,))[0]
            if key in specs:
                name = specs[key][0]
            elif key.startswith('server_pinned_message_'):
                name = operations.alias(channel.name) if channel else 'pinned board'
            else:
                name = key.replace(f':{guild.id}:', ':').removesuffix(f':{guild.id}')
            recorded = messages.mapping(key)
            if recorded:
                action, detail = 'Leave unchanged', 'Recorded; live message unverified'
            else:
                action, detail = 'Conflict', 'No saved message link; verify in Managed Messages'
            rows.append(('message', name, action, detail))
        for name in ('welcome', 'choose-your-roles', 'looking-for-group'):
            if not any(r['name'] == name and r['resource'] for r in definitions):
                rows.append(('message', f'{name} board', 'Conflict', 'Review the destination channel first'))
        from services.member_onboarding_service import load_config
        customized = load_config(guild.id)['questions']
        rows.append(('onboarding', 'member-questions', 'Leave unchanged',
                     f'{len(customized)} locally customized question records; native Discord settings unverified'))
        games = db.get_all_games()
        counts, game_details = game_rows(snapshot, games, protected)
        source = config.BASE_DIR / 'data' / 'game_catalog_expansion.json'
        with db.connect() as conn:
            catalog = plan(conn, manifest(source), source)
        rows.extend(game_details + skill_rows)
    totals = Counter((kind, action) for kind, _, action, _ in rows)
    summary = [
        f'Games: {len(games)} catalogued, {counts["active"]} active, {counts["visible"]} visible.',
        f'Game mappings: {counts["saved roles"]} saved roles, {counts["saved chats"]} canonical chats, '
        f'{counts["legacy areas"]} games with legacy hints; {counts["live roles"]} verified roles, '
        f'{counts["live chats"]} verified chats.',
        f'Catalog preview: {len(catalog["existing"])} matches, {len(catalog["insert"])} proposed additions, '
        f'{len(catalog["alias_updates"])} alias updates, {len(catalog["ambiguous"])} ambiguous, '
        f'{len(catalog["retired"])} retired. Separate approval required.',
        f'Unmatched Discord channels/categories: {extra}. Leave unchanged.',
    ]
    for kind in ('category', 'text', 'voice', 'role', 'bot-role', 'message'):
        breakdown = ', '.join(f'{totals[kind, action]} {action.lower()}'
                              for action in ('Reuse', 'Create', 'Update', 'Conflict', 'Leave unchanged')
                              if totals[kind, action])
        if breakdown:
            summary.append(f'{kind.capitalize()}: {breakdown}.')
    return summary, rows
