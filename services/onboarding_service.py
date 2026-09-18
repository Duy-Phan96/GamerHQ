"""Focused, retryable onboarding migration; never deletes channels or conversations."""
import asyncio
import re

import discord

from database import db
from services.server_service import ServerMessageError, upsert_fixed_message

WELCOME_COPY = (
    "# 👋 Welcome to GamerHQ!\n\n"
    "🎮 Choose the games you play in {choose-your-games}\n"
    "👤 Select your platform & playstyle in {choose-your-roles}\n"
    "📅 Find or create gaming sessions in {looking-for-group}\n"
    "🤖 Use bot commands in {bot-commands}\n\n"
    "Have fun & see you in game! 🚀"
)
COMMAND_TOPIC = 'Use bot commands here to keep the other channels clean.'
_locks = {}


def alias(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def key(guild, name):
    return f'onboarding:{guild.id}:{name}'


def unique(channels, name):
    found = [c for c in channels if alias(c.name) == name]
    if len(found) > 1:
        raise ServerMessageError(f'Multiple #{name} channels found; review manually. No channels were merged.')
    return found[0] if found else None


def welcome_text(guild):
    # Prefer core channels over similarly named per-game LFG channels.
    core = [c for c in guild.text_channels if c.category and alias(c.category.name) in {'start-here', 'community'}]
    def replace(match):
        found = [c for c in core if alias(c.name) == match[1]]
        return found[0].mention if len(found) == 1 else '#' + match[1]
    return re.sub(r'\{([a-z-]+)\}', replace, WELCOME_COPY)


def old_onboarding(message):
    text = (message.content or '') + '\n' + '\n'.join((e.title or '') + '\n' + (e.description or '') for e in message.embeds)
    text = text.lower()
    return (
        ('introduce yourself' in text and ('tell us what games' in text or 'gamerhq' in text))
        or ('welcome to gamerhq' in text and any(term in text for term in (
            'find games. find mates. play together.', 'gamerhq is a community for gamers',
            'choose-your-games', 'choose the games you play', 'new here?',
        )))
    )


async def remove_obsolete_pins(channel):
    """Only recognized bot-authored onboarding copy; never a user's pinned message."""
    if not channel.guild.me:
        raise ServerMessageError('Bot member is unavailable; cannot identify managed pins.')
    keys = [f'server_pinned_message_{channel.id}']
    if alias(channel.name) == 'introductions':
        keys.append('server_introductions_message_id')
    candidates = {m.id: m async for m in channel.pins(limit=None)}
    for setting in keys:
        raw = db.get_setting(setting)
        if raw and str(raw).isdigit():
            try:
                message = await channel.fetch_message(int(raw))
                candidates[message.id] = message
            except discord.NotFound:
                db.set_setting(setting, '')
    removed = []
    for message in candidates.values():
        if message.author.id != channel.guild.me.id or not old_onboarding(message):
            continue
        try:
            await message.delete()
        except discord.NotFound:
            pass
        removed.append(message.id)
        for setting in keys:
            if str(db.get_setting(setting)) == str(message.id):
                db.set_setting(setting, '')
    return removed


def is_staff(role):
    return not role.is_default() and not role.managed and any((role.permissions.administrator, role.permissions.manage_guild, role.permissions.manage_messages, role.permissions.moderate_members))


def guide_overwrites(channel):
    """Change posting bits only, retaining custom visibility and interactive features."""
    guild = channel.guild
    result = dict(channel.overwrites)
    for target in set(result) | {guild.default_role} | {r for r in guild.roles if is_staff(r)}:
        overwrite = channel.overwrites_for(target)
        staff = target in guild.roles and is_staff(target)
        bot = guild.me and target.id == guild.me.id
        for field in ('send_messages', 'send_messages_in_threads', 'create_public_threads', 'create_private_threads'):
            setattr(overwrite, field, bool(staff or bot))
        if target == guild.default_role:
            overwrite.view_channel = True
            overwrite.read_message_history = True
        if staff or bot:
            overwrite.view_channel = True
            overwrite.read_message_history = True
            overwrite.manage_messages = True
        result[target] = overwrite
    if guild.me:
        overwrite = channel.overwrites_for(guild.me)
        overwrite.view_channel = overwrite.read_message_history = overwrite.send_messages = overwrite.manage_messages = True
        result[guild.me] = overwrite
    return result


async def set_read_only(channel):
    overwrites = guide_overwrites(channel)
    if overwrites != channel.overwrites:
        await channel.edit(overwrites=overwrites, reason='GamerHQ START HERE guide permissions')


async def set_writable(channel):
    # Keep custom role/member restrictions; only undo the default read-only bit.
    overwrite = channel.overwrites_for(channel.guild.default_role)
    if overwrite.send_messages is not True:
        overwrite.send_messages = True
        await channel.set_permissions(channel.guild.default_role, overwrite=overwrite, reason='GamerHQ community interaction channel')


async def canonical_welcome(channel):
    content = welcome_text(channel.guild)
    def match(message):
        return old_onboarding(message) or ((message.content or '').startswith('# 👋 Welcome to GamerHQ!') and 'Choose the games you play' in message.content)
    return await upsert_fixed_message(channel, setting_key=f'server_pinned_message_{channel.id}', content=content, pin=True, recover_match=match)


async def refresh_onboarding(guild):
    """Startup refresh does not rename/move channels or turn old welcome into the guide."""
    for name in ('introductions', 'newbies'):
        channel = unique(guild.text_channels, name)
        if channel:
            await remove_obsolete_pins(channel)
    raw = db.get_setting(key(guild, 'welcome'))
    channel = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if channel and alias(channel.name) == 'welcome' and channel.category and alias(channel.category.name) == 'start-here':
        await canonical_welcome(channel)
    from services.community_structure_service import refresh_boards
    await refresh_boards(guild)
    from services.support_service import refresh_support
    await refresh_support(guild)


async def migrate_onboarding(guild, bot=None):
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        changed, failed = [], []
        try:
            from services.community_structure_service import recover_core_ids
            await recover_core_ids(guild)
            start = unique(guild.categories, 'start-here')
            community = unique(guild.categories, 'community')
            if not start or not community:
                raise ServerMessageError('START HERE and COMMUNITY must already exist; unrelated categories were not created.')
            # Detect collisions before the first mutation.
            newbies = unique(guild.text_channels, 'newbies')
            welcome = unique(guild.text_channels, 'welcome')
            commands = unique(guild.text_channels, 'bot-commands')
            legacy_commands = unique(guild.text_channels, 'community-commands')
            old_id = db.get_setting(key(guild, 'old_welcome'))
            new_id = db.get_setting(key(guild, 'welcome'))
            if welcome and not newbies and str(welcome.id) != str(new_id):
                # Persist identity before moving, so retries never migrate the replacement welcome.
                db.set_setting(key(guild, 'old_welcome'), welcome.id)
                newbies = await welcome.edit(name='👋・newbies', category=community, sync_permissions=False, reason='GamerHQ old welcome becomes community newbies')
                changed.append('Renamed/moved old welcome → COMMUNITY/newbies (history retained)')
                welcome = None
            elif welcome and newbies and not old_id and not new_id:
                failed.append('Both welcome and newbies already exist: adopted them without merging history; review their intended use.')
            if newbies is None:
                newbies = await community.create_text_channel('👋・newbies', reason='GamerHQ newcomer interaction channel')
                changed.append('Created COMMUNITY/newbies')
            elif newbies.category_id != community.id:
                newbies = await newbies.edit(category=community, sync_permissions=False, reason='GamerHQ newcomer area belongs in COMMUNITY')
                changed.append('Moved newbies to COMMUNITY')
            db.set_setting(key(guild, 'newbies'), newbies.id)
            await set_writable(newbies)
            if welcome is None:
                overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False, send_messages_in_threads=False, create_public_threads=False, create_private_threads=False)}
                for role in guild.roles:
                    if is_staff(role):
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True)
                if guild.me:
                    overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True)
                welcome = await start.create_text_channel('👋・welcome', overwrites=overwrites, reason='GamerHQ clean onboarding guide')
                changed.append('Created START HERE/welcome')
            elif welcome.category_id != start.id:
                welcome = await welcome.edit(category=start, sync_permissions=False, reason='GamerHQ welcome guide belongs in START HERE')
                changed.append('Moved welcome to START HERE')
            db.set_setting(key(guild, 'welcome'), welcome.id)
            if commands is None and legacy_commands:
                commands = await legacy_commands.edit(name='🤖・bot-commands', category=community, sync_permissions=False, reason='GamerHQ central bot commands')
                changed.append('Renamed community-commands → bot-commands')
            elif commands is None:
                commands = await community.create_text_channel('🤖・bot-commands', reason='GamerHQ central bot commands')
                changed.append('Created COMMUNITY/bot-commands')
            elif legacy_commands:
                failed.append('Both bot-commands and community-commands exist: using bot-commands; legacy channel/history preserved for manual review.')
            if commands.category_id != community.id:
                commands = await commands.edit(category=community, sync_permissions=False, reason='GamerHQ bot commands in COMMUNITY')
            if commands.topic != COMMAND_TOPIC:
                commands = await commands.edit(topic=COMMAND_TOPIC, reason='GamerHQ bot command guidance')
            await set_writable(commands)
            db.set_setting('server_community_commands_channel_id', commands.id)
            for channel in start.text_channels:
                if alias(channel.name) in {'welcome', 'rules', 'announcements', 'choose-your-games', 'choose-your-roles'}:
                    await set_read_only(channel)
            for channel in (newbies, unique(community.text_channels, 'introductions')):
                if channel:
                    removed = await remove_obsolete_pins(channel)
                    changed.extend(f'Removed obsolete managed pin {mid} in {channel.name}' for mid in removed)
            from services.community_structure_service import migrate_boards
            await migrate_boards(guild, changed, failed)
            await canonical_welcome(welcome)
            if bot:
                from services.command_guide_service import refresh_community_command_guide
                await refresh_community_command_guide(bot, guild, channel=commands)
            changed.append('Updated canonical guides and focused posting permissions')
        except (discord.HTTPException, ServerMessageError) as exc:
            failed.append(str(exc))
        return changed, failed
