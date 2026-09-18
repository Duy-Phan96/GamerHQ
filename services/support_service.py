"""Optional affiliate board. Structural changes run only through owner repair."""
from dataclasses import dataclass
from types import SimpleNamespace

import discord
from database import db
from services.onboarding_service import alias, unique, guide_overwrites, set_read_only
from services.server_service import ServerMessageError, upsert_fixed_message
from services.music_bot_service import blocked_name

CATEGORY_NAME = '💜 SUPPORT GAMERHQ'
CHANNEL_NAME = '💜・support-gamerhq'
TITLE = '# 💜 Support GamerHQ'
TELESON_URL = 'https://kundenportal.teleson.de/index.php?_url=register/karriere&reference=bFFQT1RPUHltMWVJb3REWXJDOWhwbzRNdXp5RTNhMUJWUkg4ckxZMHhVVjd5M0kvTWMvR3YrSkhCNWM4Z3ZiUnh2cDJSbFhEYUtjTHZKUWVlQWUrQnFPdWljOUpIbG5wc1drRk9KcXlhalU9'
GERMANY_TEXT = """## 🇩🇪 For Germany

### ⚡ Strom & Gas

Du wohnst in Deutschland und möchtest deinen Strom- oder Gasvertrag optimieren?

Über den Button erhältst du Zugang zu einem Netzwerk, über das du dir selbst einen passenden Strom- oder Gastarif auswählen kannst.

Wenn du dabei Hilfe brauchst oder Fragen hast, kannst du eine private Support-Anfrage öffnen.

### 📚 Strom & Gas Vertrieb

Wenn du dich im Strom- & Gasvertrieb weiterbilden und damit starten möchtest, gibt es dazu einen kompletten Kurs.

Über **Kurs anfragen** wird eine private Anfrage erstellt. Dort erhältst du die nächsten Schritte und den Zugang zum Kurs.

ℹ️ Der Strom-&-Gas-Link ist ein Empfehlungslink."""


@dataclass(frozen=True)
class Affiliate:
    name: str
    category: str
    description: str
    url: str
    copy: str
    emoji: str
    active: bool = True


AFFILIATES = (
    Affiliate('Instant Gaming', 'Gaming', 'Games & Deals',
              'https://www.instant-gaming.com/?igr=gamer-0a9671a',
              'Use this link when buying games on Instant Gaming.', '🎮'),
    Affiliate('PixVerse', 'AI', 'AI Video Generation',
              'https://motivaiprivatelimited.sjv.io/c/7668488/3811144/49478',
              'Use PixVerse to create AI-generated videos and visual content.', '🤖'),
    Affiliate('Amazon', 'Shopping', 'Shopping & Everyday Products',
              'https://amzn.to/4dnxPXh',
              'Use this link before shopping normally on Amazon.\n\nYou can also save the link as a browser bookmark and use it whenever you shop.', '🛒'),
)


def channel_key(guild):
    from services.community_structure_service import resource_key
    return resource_key(guild, 'support-gamerhq')


def category_key(guild): return f'managed_category:{guild.id}:support-gamerhq'
def message_key(guild): return f'support_message:{guild.id}'


def support_text():
    lines = [TITLE, '', 'Want to support GamerHQ?','', 'You can support the project simply by using one of the affiliate links below.', '',
             'Using these links does not cost you anything extra, but GamerHQ may receive a commission when you purchase or sign up through them.']
    for entry in AFFILIATES:
        if entry.active:
            lines.extend(['', f'## {entry.emoji} {entry.name}', '', f'**{entry.description}**', '', entry.copy, '', f'🔗 {entry.url}'])
    lines.extend(['', GERMANY_TEXT])
    lines.extend(['', '## ℹ️ Transparency', '', 'The links above are affiliate links.', '',
                  'If you purchase or sign up through one of them, GamerHQ may receive a commission at no additional cost to you.', '',
                  'Using them is completely optional.'])
    return '\n'.join(lines)


def resolve(guild, kind):
    collection = guild.categories if kind == 'category' else guild.text_channels
    key = category_key(guild) if kind == 'category' else channel_key(guild)
    raw = db.get_setting(key)
    stored = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    named = unique(collection, 'support-gamerhq')
    if stored in collection:
        if named and named.id != stored.id:
            raise ServerMessageError('Support resources have conflicting IDs/names. Review manually; nothing was merged.')
        return stored
    return named


def guide_reference(guild):
    channel = resolve(guild, 'channel')
    link = channel.mention if channel else '#support-gamerhq'
    return f'## 💜 Support GamerHQ\nWant to support GamerHQ? Check **{link}** for optional affiliate links and additional offers.'


async def refresh_support(guild, channel=None):
    from cogs.tickets import SupportOffers
    # Startup refreshes only a board explicitly adopted by owner setup.
    if channel is None:
        raw = db.get_setting(channel_key(guild))
        channel = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
        if channel not in guild.text_channels:
            return
    return await upsert_fixed_message(channel, setting_key=message_key(guild), content=support_text(), pin=True, view=SupportOffers(),
        recover_match=lambda message: (message.content or '').startswith(TITLE))


async def repair_support(guild, changed):
    # Resolve all collisions before creating, renaming or moving anything.
    category, channel = unique(guild.categories, 'start-here'), resolve(guild, 'channel')
    if category is None:
        raise ServerMessageError('START HERE must exist before affiliate setup.')
    if channel and channel.category and blocked_name(channel.category) and alias(channel.category.name) != 'support-gamerhq':
        raise ServerMessageError('An equivalent support channel is in a protected/private area. Review it manually before making it public.')
    # The shared permission builder provides the same staff/bot posting and
    # normal-member read-only rules as other managed information channels.
    empty = SimpleNamespace(guild=guild, overwrites={}, overwrites_for=lambda target: discord.PermissionOverwrite())
    initial = guide_overwrites(empty)
    if channel is None:
        channel = await category.create_text_channel(CHANNEL_NAME, overwrites=initial, reason='GamerHQ read-only affiliate information')
        changed.append('Created support-gamerhq')
    elif channel.name != CHANNEL_NAME or channel.category_id != category.id:
        channel = await channel.edit(name=CHANNEL_NAME, category=category, sync_permissions=False, reason='GamerHQ support channel name/location')
        changed.append('Reused support-gamerhq; ID/history retained')
    db.set_setting(channel_key(guild), channel.id)
    await set_read_only(channel)
    await refresh_support(guild, channel)
    changed.append('Updated canonical support message and read-only permissions')
