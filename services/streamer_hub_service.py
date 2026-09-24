"""Small managed Twitch beta; legacy profile records are never rewritten."""
import asyncio
import discord
import config
from database import db, twitch
from services.onboarding_service import alias, unique, is_staff
from services.server_service import ServerMessageError, upsert_fixed_message

DENIED = 'This feature is currently available to approved GamerHQ streamers.'
NAMES = {'streamer-guide': '📖・streamer-guide', 'stream-updates': '🔴・stream-updates',
         'streamer-commands': '📱・streamer-commands', 'choose-streamers': '🎬・choose-streamers'}
_locks = {}


def channel(guild, name):
    raw = db.get_setting(f'managed_channel:{guild.id}:{name}')
    found = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
    return found if found in guild.text_channels else None


def role(guild):
    raw = db.get_setting(f'managed_role:{guild.id}:streamer')
    return guild.get_role(int(raw)) if raw and raw.isdigit() else None


def approved(member):
    return bool(member and member.guild.id == config.GUILD_ID and
                (member.id == member.guild.owner_id or any(is_staff(r) for r in member.roles)
                 or (role(member.guild) and role(member.guild) in member.roles)))


async def authorize(guild, user_id):
    if not config.STREAMER_HUB_ENABLED or not guild or guild.id != config.GUILD_ID:
        return False
    try:
        return approved(await guild.fetch_member(user_id))
    except discord.HTTPException:
        return False


async def ensure_role(guild):
    from services.role_service import assignable
    found = role(guild)
    if found is None:
        matches = [r for r in guild.roles if alias(r.name) == 'streamer']
        if len(matches) > 1:
            raise ServerMessageError('Ambiguous Streamer roles; owner review required.')
        found = matches[0] if matches else await guild.create_role(name='🎥 Streamer', permissions=discord.Permissions.none(), reason='GamerHQ approved streamer beta')
    if not assignable(found, guild):
        raise ServerMessageError('Streamer role has unsafe permissions/hierarchy; preserve for owner review.')
    db.set_setting(f'managed_role:{guild.id}:streamer', found.id)
    return found


def overwrites(resource, *, public=False, streamer=None):
    guild = resource.guild
    result = {target: discord.PermissionOverwrite.from_pair(*value.pair()) for target, value in resource.overwrites.items()}
    for target in set(result) | {guild.default_role, guild.me} | {r for r in guild.roles if is_staff(r)} | ({streamer} if streamer else set()):
        staff = target == guild.me or (target in guild.roles and is_staff(target))
        if isinstance(target, discord.Member):
            staff = staff or any(is_staff(r) for r in target.roles)
        value = result.setdefault(target, discord.PermissionOverwrite())
        value.view_channel = bool(public or staff or target == streamer)
        value.read_message_history = value.view_channel
        value.send_messages = bool(staff)
        value.send_messages_in_threads = bool(staff)
        value.create_public_threads = bool(staff)
        value.create_private_threads = bool(staff)
        if target == guild.me:
            value.embed_links = True
        if public and target != guild.default_role and not staff:
            # Explicit read-only overrides prevent an inherited member grant.
            value.send_messages = False
    return result


async def legacy_channel(guild, name):
    found = channel(guild, name)
    if found:
        return found
    if name == 'streamer-commands':
        raw = db.get_setting('server_streamer_commands_channel_id')
        legacy = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
        if legacy in guild.text_channels and alias(legacy.name) == name:
            db.set_setting(f'managed_channel:{guild.id}:{name}', legacy.id)
            return legacy
    key = {'streamer-guide': 'streamer_guide_message_id', 'choose-streamers': 'streamer_choose_message_id',
           'streamer-commands': 'server_streamer_commands_message_id'}.get(name)
    candidate = unique([c for c in guild.text_channels if c.category and alias(c.category.name) in {'streamers', 'gamerhq-streamers'}], name)
    raw = db.get_setting(key) if key else None
    if candidate and raw and raw.isdigit():
        try:
            message = await candidate.fetch_message(int(raw))
        except discord.NotFound:
            return None
        prefixes = {'streamer-guide': ('# 🎥 GamerHQ Streamers', '# 🎥 Streamer Hub'),
                    'choose-streamers': ('# 🎬 Choose Streamers',), 'streamer-commands': ('# 🎥', '🎥')}
        if message.author.id == guild.me.id and message.content.startswith(prefixes[name]):
            db.set_setting(f'managed_channel:{guild.id}:{name}', candidate.id)
            return candidate
    return None


async def sync(guild):
    from cogs.twitch_hub import HubView
    if guild.id != config.GUILD_ID:
        return
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        streamer = await ensure_role(guild) if config.STREAMER_HUB_ENABLED or config.STREAMER_ROLE_SELECTION_ENABLED else role(guild)
        resources = {name: await legacy_channel(guild, name) for name in NAMES}
        categories = [c for c in guild.categories if alias(c.name) in {'streamers', 'gamerhq-streamers'}]
        if len(categories) > 1:
            raise ServerMessageError('Ambiguous Streamer categories; owner review required.')
        category = categories[0] if categories else None
        # The old streamer cog created this three-channel bundle but stored only
        # guide/directory message IDs. Its owned guide provides migration evidence.
        guide = resources['streamer-guide']
        if not resources['stream-updates'] and guide and guide.category == category:
            candidate = unique(category.text_channels, 'stream-updates')
            if candidate and candidate.overwrites_for(guild.default_role).view_channel is not False:
                resources['stream-updates'] = candidate
                db.set_setting(f'managed_channel:{guild.id}:stream-updates', candidate.id)
        unresolved = []
        if config.STREAMER_HUB_ENABLED:
            if not category:
                category = await guild.create_category('🎥 STREAMERS', reason='GamerHQ streamer beta')
            for name in ('streamer-guide', 'stream-updates', 'streamer-commands'):
                if resources[name]:
                    continue
                candidates = [c for c in category.text_channels if alias(c.name) == name]
                if candidates:
                    raise ServerMessageError(f'Unmapped {name}; verify ownership and store its managed channel ID before repair. Nothing recreated.')
                resources[name] = await category.create_text_channel(NAMES[name], reason='GamerHQ streamer beta',
                    overwrites=overwrites(category, public=name == 'stream-updates', streamer=streamer if name != 'stream-updates' else None))
                db.set_setting(f'managed_channel:{guild.id}:{name}', resources[name].id)
        for name, resource in resources.items():
            if resource is None:
                if any(alias(c.name) == name for c in guild.text_channels) and name != 'stream-updates':
                    unresolved.append(name)
                continue
            rights = overwrites(resource, public=config.STREAMER_HUB_ENABLED and name == 'stream-updates',
                streamer=streamer if config.STREAMER_HUB_ENABLED and name in {'streamer-guide', 'streamer-commands'} else None)
            if name == 'streamer-guide':
                rights[guild.me].manage_messages = True
            if resource.overwrites != rights:
                from services.channel_change_service import edit
                await edit(resource, overwrites=rights, reason='GamerHQ hidden streamer beta access')
        guide = resources['streamer-guide']
        if guide:
            target = resources['stream-updates']
            content = ('# 🎥 Streamer Hub — Beta\n\nConnect your Twitch account to GamerHQ and automatically appear in '
                       f'{target.mention} when you go live.') if config.STREAMER_HUB_ENABLED and target else '# 🎥 Streamer Hub — Beta\n\nCurrently disabled.'
            await upsert_fixed_message(guide, setting_key='streamer_guide_message_id', content=content, pin=True,
                view=HubView() if config.STREAMER_HUB_ENABLED else None,
                recover_match=lambda m: m.content.startswith(('# 🎥 GamerHQ Streamers', '# 🎥 Streamer Hub')))
        if unresolved:
            raise ServerMessageError('Unmapped Streamer channels need ownership review: ' + ', '.join(unresolved))


def health(guild, bot):
    if not config.STREAMER_HUB_ENABLED:
        return 'Streamer Hub', 'INFO', 'Disabled (hidden beta).'
    missing = []
    if not role(guild): missing.append('Streamer role')
    if not channel(guild, 'stream-updates'): missing.append('managed stream-updates')
    if not config.TWITCH_CLIENT_ID: missing.append('Twitch configuration')
    manager = getattr(bot, 'twitch_hub', None)
    if not manager or not manager.initialized: missing.append('EventSub service')
    try:
        with db.connect() as conn:
            conn.execute('SELECT 1 FROM twitch_connections LIMIT 1')
            conn.execute('SELECT 1 FROM twitch_live_deliveries LIMIT 1')
        if any(not r['notifications_enabled'] for r in twitch.connections() if r['guild_id'] == guild.id):
            missing.append('Twitch reconnection needed')
    except Exception:
        missing.append('persistence')
    return 'Streamer Hub', 'WARN' if missing else 'PASS', ', '.join(missing) if missing else 'Beta ready; Twitch connections persisted. No live health requests.'


async def toggle_role(guild, user_id):
    from services.role_service import assignable, _preference_locks
    if not config.STREAMER_ROLE_SELECTION_ENABLED or not guild or guild.id != config.GUILD_ID:
        raise ValueError('Streamer role selection is disabled.')
    async with _preference_locks.setdefault((guild.id, user_id), asyncio.Lock()):
        member = await guild.fetch_member(user_id)
        target = role(guild)
        if not assignable(target, guild):
            raise ValueError('Streamer role unavailable.')
        enabled = target not in member.roles
        if enabled:
            await member.add_roles(target, reason='GamerHQ explicit Streamer opt-in')
        else:
            await member.remove_roles(target, reason='GamerHQ explicit Streamer opt-out')
        return enabled
