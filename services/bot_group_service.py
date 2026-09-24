"""Explicit bot identities and cosmetic groups; no blanket private-channel grants."""
import asyncio
import time
import discord
from database import db
from services.onboarding_service import alias, is_staff
from services.server_service import ServerMessageError
from services.music_bot_service import role_key as music_key, DENIED_RIGHTS

GROUPS = {'music': ('🎵 Music Bots', ('jockie', 'pancake')),
          'gaming': ('🤖 Gaming Bots', ('instant-gaming', 'dealgecko'))}
CONFIG = {'jockie': 'JOCKIE_MUSIC_BOT_ID', 'pancake': 'PANCAKE_BOT_ID',
          'instant-gaming': 'INSTANT_GAMING_BOT_ID', 'dealgecko': 'DEALGECKO_BOT_ID'}
_locks = {}
LABELS = {'jockie': 'Jockie Music', 'pancake': 'Pancake', 'instant-gaming': 'Instant Gaming', 'dealgecko': 'DealGecko'}
_members = {}
_assigned = {}


def key(guild, group):
    return music_key(guild) if group == 'music' else f'gaming_bots_role:{guild.id}'


def member_id(guild, name):
    import config
    configured = getattr(config, CONFIG[name])
    legacy = db.get_setting(f'instant_gaming_bot:{guild.id}') if name == 'instant-gaming' else None
    raw = str(configured or db.get_setting(f'bot_member:{guild.id}:{name}') or legacy or '')
    return int(raw) if raw.isdigit() and int(raw) else None


def member(guild, name):
    uid = member_id(guild, name)
    result = guild.get_member(uid) if uid else None
    cached = _members.get((guild.id, uid))
    if result is None and cached and cached[0] is guild and cached[1] > time.monotonic():
        result = cached[2]
    return result if result and result.bot and result != guild.me else None


async def fetch_member(guild, name):
    """Repair-only fallback; cache positive/negative results briefly, never fetch in health."""
    result = member(guild, name)
    uid = member_id(guild, name)
    if result or not uid:
        return result
    cached = _members.get((guild.id, uid))
    if cached and cached[0] is guild and cached[1] > time.monotonic():
        return None
    fetch = getattr(guild, 'fetch_member', None)
    if not callable(fetch):
        return None
    try:
        result = await fetch(uid)
    except discord.NotFound:
        result = None
    except discord.HTTPException:
        raise ServerMessageError(f'{LABELS[name]} member lookup failed; check bot access and retry Repair.') from None
    if result is not None and (result.id != uid or not result.bot or result == guild.me):
        result = None
    if len(_members) >= 256:
        _members.clear()
    _members[(guild.id, uid)] = (guild, time.monotonic() + 60, result)
    return result


def assigned(guild, bot, role):
    return role in bot.roles or _assigned.get((id(guild), bot.id, role.id), 0) > time.monotonic()


def resolve(guild, group):
    raw = db.get_setting(key(guild, group))
    if raw:
        result = guild.get_role(int(raw)) if raw.isdigit() else None
        if result:
            return result
    matches = [r for r in guild.roles if alias(r.name) == group + '-bots']
    if len(matches) > 1:
        raise ServerMessageError(f'Ambiguous {group} bot roles; review before repair.')
    return matches[0] if matches else None


def safe(role):
    return not role.is_default() and not role.managed and not any(getattr(role.permissions, p) for p in DENIED_RIGHTS)


async def sync(guild):
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        notes, resolved = [], {}
        for group, (display, names) in GROUPS.items():
            try:
                role = resolve(guild, group)
                if role is None:
                    role = await guild.create_role(name=display, permissions=discord.Permissions.none(), hoist=True,
                                                   reason='GamerHQ bot grouping; no administrative grants')
                    db.set_setting(key(guild, group), role.id)
                if not safe(role) or (group == 'gaming' and role.permissions.value):
                    raise ServerMessageError(f'{display} has unsafe/shared permissions; owner review required.')
                if any(not m.bot for m in getattr(role, 'members', [])):
                    raise ServerMessageError(f'{display} contains non-bot members; preserve and review manually.')
                if guild.me is None or not role < guild.me.top_role:
                    raise ServerMessageError(f'{display} is not below GamerHQ; owner must repair hierarchy.')
                db.set_setting(key(guild, group), role.id)
                resolved[group] = role
                if role.name != display or not role.hoist:
                    await role.edit(name=display, hoist=True, reason='GamerHQ bot member-list grouping')
                for name in names:
                    try:
                        bot = await fetch_member(guild, name)
                        if bot is None:
                            notes.append(f'⚠️ {LABELS[name]} not found in guild or unconfigured — role assignment skipped.')
                            continue
                        if not assigned(guild, bot, role):
                            await bot.add_roles(role, reason='GamerHQ verified bot grouping')
                            if len(_assigned) >= 256:
                                _assigned.clear()
                            _assigned[(id(guild), bot.id, role.id)] = time.monotonic() + 60
                        notes.append(f'✅ {LABELS[name]} → {display}')
                    except ServerMessageError as exc:
                        notes.append(f'⚠️ {exc}')
                    except discord.HTTPException:
                        notes.append(f'⚠️ {LABELS[name]} assignment failed; check Manage Roles and role hierarchy.')
            except ServerMessageError as exc:
                notes.append(str(exc))
            except discord.HTTPException:
                notes.append(f'⚠️ {display} could not be repaired; check Manage Roles and hierarchy.')
        # Only move our two group roles, preserving relative Staff/Admin ordering.
        staff = [r for r in guild.roles if is_staff(r) and r not in resolved.values()]
        boundaries = [r.position for r in staff] + ([guild.me.top_role.position] if guild.me else [])
        if len(resolved) == 2 and boundaries and all(isinstance(p, int) for p in boundaries):
            ceiling = min(boundaries)
            if ceiling >= 3:
                positions = {resolved['music']: ceiling - 2, resolved['gaming']: ceiling - 1}
                if any(r.position != pos for r, pos in positions.items()):
                    try:
                        await guild.edit_role_positions(positions=positions, reason='GamerHQ groups below Staff and bot hierarchy')
                    except discord.HTTPException:
                        notes.append('Bot-group ordering requires owner review.')
            else:
                notes.append('Insufficient hierarchy space below Staff; owner must position bot groups.')
        for group, role in resolved.items():
            for name in GROUPS[group][1]:
                bot = member(guild, name)
                if bot and any(r.hoist and r.position > role.position for r in bot.roles):
                    notes.append(f'⚠️ {LABELS[name]} has a higher hoisted role; review member-list grouping manually. Integration roles were preserved.')
        return notes


def diagnostics(guild):
    rows = []
    for group, (display, names) in GROUPS.items():
        try:
            role = resolve(guild, group)
            valid = role and safe(role) and role.hoist and (group != 'gaming' or role.permissions.value == 0)
            rows.append((display, 'PASS' if valid else 'WARN', 'Grouping role checked.' if valid else 'Run owner Repair or review role permissions/hoist.'))
            for name in names:
                bot = member(guild, name)
                correct = bool(bot and role and assigned(guild, bot, role))
                rows.append((f'{LABELS[name]} → {display}', 'PASS' if correct else 'WARN',
                             'Exact user-ID membership checked.' if correct else 'Optional bot absent, uncached or missing role; run owner Repair.'))
            if role:
                staff = [r for r in guild.roles if is_staff(r) and r != role]
                if any(role.position >= r.position for r in staff):
                    rows.append((display + ' order', 'WARN', 'Group must be below every Staff/Admin role.'))
                for name in names:
                    bot = member(guild, name)
                    if not bot:
                        rows.append((name, 'WARN', f'Optional integration absent; configure {CONFIG[name]}.'))
                        continue
                    higher = [r for r in bot.roles if r.hoist and r.position > role.position]
                    if not assigned(guild, bot, role) or higher:
                        rows.append((name + ' grouping', 'WARN', 'Check group assignment/higher hoisted roles; unrelated roles were not edited.'))
                    if any(getattr(bot.guild_permissions, bit) for bit in DENIED_RIGHTS):
                        rows.append((name + ' permissions', 'WARN', 'External bot has broad permissions from other roles; owner should remove unnecessary grants.'))
                if group == 'gaming':
                    for channel in guild.channels:
                        if channel.overwrites_for(guild.default_role).view_channel is False and channel.overwrites_for(role).view_channel is True:
                            rows.append(('Gaming Bots privacy', 'WARN', 'Existing private-channel group grant requires manual review.'))
                            break
        except ServerMessageError as exc:
            rows.append((display, 'WARN', str(exc)))
    # A display-name match is only a recommendation, never identity/permission authority.
    if any(getattr(m, 'bot', False) and alias(m.name) in {'carl-bot', 'carlbot'} for m in getattr(guild, 'members', [])):
        rows.append(('Carl-bot', 'WARN', 'Carl-bot still installed; no code dependency found. Review live use, then remove manually.'))
    return rows
