"""Independent fixed role boards, using the existing message persistence/pin path."""
import asyncio
import discord
from database import db
from services.server_service import upsert_fixed_message, ServerMessageError
from services.onboarding_service import unique, set_read_only

INTRO = '# 👤 Optional Settings\n\nCustomize your GamerHQ experience.\n\nUse the sections below to choose notifications and optional profile settings. You can change these anytime.'
SECTIONS = (
    ('notifications', '🔔 Notifications', 'Choose which GamerHQ notifications you want to receive.'),
    ('gaming_content', '📰 Gaming Content', 'Choose which gaming updates you want to receive.'),
    ('language', '🗣️ Language', 'Choose the languages relevant to you.'),
    ('platform', '🖥️ Platform', 'Optional profile settings: choose the platforms you play on.'),
)
_locks = {}


def channel(guild):
    raw = db.get_setting(f'managed_channel:{guild.id}:choose-your-roles')
    if raw and raw.isdigit():
        found = guild.get_channel(int(raw))
        return found if found in guild.text_channels else None
    try:
        return unique(guild.text_channels, 'choose-your-roles')
    except ServerMessageError:
        return None  # Ambiguity never authorizes adoption or writes.


def message_keys(guild, board):
    return {'intro': f'server_pinned_message_{board.id}',
            **{key: f'role_message:{guild.id}:{key}' for key, _, _ in SECTIONS}}


async def refresh(guild, *, section=None):
    from cogs.roles import ChooseRolesHubView, RoleToggleView
    board = channel(guild)
    if not board:
        return
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        db.set_setting(f'managed_channel:{guild.id}:choose-your-roles', board.id)
        await set_read_only(board)
        keys = message_keys(guild, board)
        panels = [('intro', INTRO, ChooseRolesHubView())] + [
            (key, f'# {group}\n\n{text}\n\nClick to enable or disable. Your choices are optional.', RoleToggleView(group))
            for key, group, text in SECTIONS]
        for key, content, view in panels:
            if section is not None and key != section:
                continue
            title = content.split('\n')[0]
            await upsert_fixed_message(board, setting_key=keys[key], content=content, pin=True, view=view,
                allowed_mentions=discord.AllowedMentions.none(),
                recover_match=lambda m, title=title, key=key: (m.content or '').startswith(title)
                or (key == 'intro' and (m.content or '').startswith('# 👤 Choose Your Roles')))


async def sync(guild):
    from services.role_service import ensure_base_roles, ensure_lfg_roles
    await ensure_base_roles(guild)
    await ensure_lfg_roles(guild)
    await refresh(guild)


async def diagnostics(guild, *, messages=False):
    from services.role_service import ROLE_GROUPS, preference_role, normalize_role_name
    issues = []
    seen = set()
    for options in ROLE_GROUPS.values():
        for option in options:
            try:
                role = preference_role(guild, 'base', option.key)
                if role.id in seen:
                    issues.append('Multiple preferences reference the same role.')
                seen.add(role.id)
                if sum(normalize_role_name(r.name) == normalize_role_name(role.name) for r in guild.roles) > 1:
                    issues.append(f'Duplicate role: {option.label}')
            except ValueError:
                issues.append(f'Missing/unsafe role mapping: {option.label}')
    for game in db.get_selectable_games():
        try:
            role = preference_role(guild, 'lfg', str(game['id']))
            if role.id in seen:
                issues.append(f'Duplicate LFG mapping: {game["name"]}')
            seen.add(role.id)
        except ValueError:
            issues.append(f'Missing/unsafe LFG mapping: {game["name"]}')
    for row in db.get_managed_roles('lfg'):
        if not str(row['role_key']).isdigit() or db.get_game_by_id(int(row['role_key'])) is None:
            issues.append(f'Orphan LFG role mapping: {row["role_key"]}; owner review required.')
    board = channel(guild)
    if not board:
        return issues + ['Optional settings channel missing or ambiguous.']
    ids = set()
    for key, value in message_keys(guild, board).items():
        raw = db.get_setting(value)
        if not raw or not raw.isdigit():
            issues.append(f'Missing role message mapping: {key}')
            continue
        if raw in ids:
            issues.append(f'Duplicate role message mapping: {key}')
        ids.add(raw)
        if messages:
            try:
                msg = await board.fetch_message(int(raw))
                if msg.author.id != guild.me.id or not msg.pinned:
                    issues.append(f'Invalid/unpinned role message: {key}')
            except discord.HTTPException:
                issues.append(f'Role message unavailable: {key}')
    if messages:
        titles = [INTRO.split('\n')[0]] + [f'# {group}' for _, group, _ in SECTIONS]
        counts = dict.fromkeys(titles, 0)
        try:
            async for msg in board.history(limit=100):
                if msg.author.id == guild.me.id:
                    title = (msg.content or '').split('\n')[0]
                    if title in counts:
                        counts[title] += 1
        except discord.HTTPException:
            issues.append('Role message history unavailable; duplicate inspection incomplete.')
        issues.extend(f'Duplicate role messages: {title}' for title, count in counts.items() if count > 1)
    return issues
