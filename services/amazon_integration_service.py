"""Least-privilege publishing access for the separately deployed Amazon bot."""
import discord

from services.music_bot_service import DENIED_RIGHTS
from services.instant_gaming_service import BOT_RIGHTS
from services.onboarding_service import guide_overwrites


def configured_bot(guild):
    from services.bot_group_service import member
    return member(guild, 'amazon')


def publisher_overwrite(existing=None):
    value = discord.PermissionOverwrite.from_pair(*existing.pair()) if existing else discord.PermissionOverwrite()
    for bit in DENIED_RIGHTS:
        setattr(value, bit, False)
    for bit in BOT_RIGHTS:
        setattr(value, bit, True)
    return value


def channel_overwrites(channel, *, bot=None):
    """Public read-only board plus one verified Amazon publisher."""
    rights = guide_overwrites(channel)
    bot = bot or configured_bot(channel.guild)
    if bot:
        rights[bot] = publisher_overwrite(rights.get(bot))
    return rights


def category_overwrites(category, rights, *, bot=None):
    """Allow the verified publisher to see Marketplace without category-wide posting."""
    bot = bot or configured_bot(category.guild)
    if bot:
        value = discord.PermissionOverwrite.from_pair(*rights.get(bot, category.overwrites_for(bot)).pair())
        for bit in DENIED_RIGHTS:
            setattr(value, bit, False)
        value.view_channel = True
        value.read_message_history = True
        value.send_messages = False
        value.embed_links = None
        value.attach_files = None
        rights[bot] = value
    return rights


async def repair(channel):
    """Repair only #amazon; identity may use one exact member fetch."""
    from services.bot_group_service import fetch_member
    from services.channel_change_service import edit
    bot = await fetch_member(channel.guild, 'amazon')
    rights = channel_overwrites(channel, bot=bot)
    if channel.overwrites != rights:
        await edit(channel, overwrites=rights, reason='GamerHQ Amazon publisher access')
    return bot


def diagnostics(guild):
    from services.support_service import resource
    from services.bot_group_service import member
    from services.server_service import ServerMessageError
    bot = member(guild, 'amazon')
    try:
        channel = resource(guild, 'amazon')
    except ServerMessageError as exc:
        return ('Amazon bot #amazon access', 'MANUAL_REVIEW', str(exc))
    if not bot:
        return ('Amazon bot #amazon access', 'WARN',
                'Select the installed Amazon bot in /server manage → Integrations, then run Fix Common Issues.')
    if not channel:
        return ('Amazon bot #amazon access', 'WARN', 'Managed #amazon channel is unavailable; review Marketplace first.')
    explicit = all(getattr(channel.overwrites_for(bot), bit) is True for bit in BOT_RIGHTS)
    effective = all(getattr(channel.permissions_for(bot), bit) for bit in BOT_RIGHTS)
    safe = not any(getattr(channel.overwrites_for(bot), bit) is True for bit in DENIED_RIGHTS)
    category_ok = bool(channel.category and channel.category.overwrites_for(bot).view_channel is not False)
    return ('Amazon bot #amazon access', 'PASS' if explicit and effective and safe and category_ok else 'REPAIRABLE',
            'Verified bot has posting/embed access only on the managed Amazon board.' if explicit and effective and safe and category_ok
            else 'Amazon publisher permissions drifted; Fix Common Issues can restore the scoped overwrite.')
