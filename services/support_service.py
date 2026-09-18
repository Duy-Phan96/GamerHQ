"""Optional affiliate board. Structural changes run only through owner repair."""
import asyncio
import logging
import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import discord
from database import db
from services.onboarding_service import alias, unique, guide_overwrites, set_read_only
from services.server_service import ServerMessageError, upsert_fixed_message, pin_managed_message
from services.music_bot_service import blocked_name

CHANNEL_NAME = '💜・support-gamerhq'
TITLE = '# 💜 Support GamerHQ'
TELESON_URL = 'https://kundenportal.teleson.de/index.php?_url=register/karriere&reference=bFFQT1RPUHltMWVJb3REWXJDOWhwbzRNdXp5RTNhMUJWUkg4ckxZMHhVVjd5M0kvTWMvR3YrSkhCNWM4Z3ZiUnh2cDJSbFhEYUtjTHZKUWVlQWUrQnFPdWljOUpIbG5wc1drRk9KcXlhalU9'
INTRO_TEXT = TITLE + """

GamerHQ ist kostenlos nutzbar.

Wenn du den Server unterstützen möchtest, findest du unter **PARTNERS & BENEFITS** verschiedene Partnerangebote und Empfehlungslinks.

Wenn du einen dieser Links nutzt, kann GamerHQ oder der jeweilige Partner eine Provision erhalten.

Für dich entstehen **keine zusätzlichen Kosten allein durch die Nutzung eines Empfehlungslinks**.

## 🛒 Amazon

Du kannst GamerHQ auch unterstützen, indem du vor deinem normalen Amazon-Einkauf unseren Link verwendest.

Tipp: Speichere den Link einfach als Lesezeichen im Browser und nutze ihn vor deinem nächsten Einkauf.

## 💜 Direct Support

Eine Möglichkeit zur direkten freiwilligen Unterstützung von GamerHQ folgt später.

**Coming Soon**"""
GERMANY_TEXT = """# ⚡ Strom & Gas

Du wohnst in Deutschland und möchtest deinen Strom- oder Gasvertrag optimieren?

Über den Button erhältst du Zugang zu einem Netzwerk, über das du dir selbst einen passenden Strom- oder Gastarif auswählen kannst.

Brauchst du dabei Unterstützung oder hast Fragen?"""
SALES_TEXT = """# 🎓 Strom & Gas Vertrieb

Du möchtest dich im Strom- & Gasvertrieb weiterbilden und selbst damit starten?

Dafür steht ein kompletter kostenloser Kurs zur Verfügung.

Über **Kurs anfragen** wird eine private Anfrage erstellt. Dort erhältst du Zugang zum kostenlosen Kurs."""
FINANCE_TEXT = """# 💶 Finanzcheck & Planung

Du möchtest deine Finanzen einmal strukturiert überprüfen und langfristig besser aufstellen?

Ein persönlicher Finanzcheck kann dabei helfen, Einnahmen und Ausgaben besser zu überblicken, bestehende Strukturen zu prüfen und finanzielle Ziele sinnvoll zu planen.

Ein besonderer Fokus kann dabei auf langfristigem Vermögensaufbau, Investments und Immobilien liegen.

Eine feste Ansprechperson für Finanzfragen an der Seite zu haben, kann bei langfristigen Entscheidungen sehr hilfreich sein."""
PARTNER_CATEGORY = '🤝 PARTNERS & BENEFITS'
PARTNER_CHANNELS = {'germany-services': '🇩🇪・germany-services', 'gaming-deals': '🎮・gaming-deals', 'ai-tools': '🤖・ai-tools'}
LEGACY_SECTIONS = ('intro','transparency','instant_gaming','pixverse','amazon','energy','energy_sales')
LEGACY_HEADINGS = {TITLE, '## ℹ️ Transparency', '## 🎮 Instant Gaming', '## 🤖 PixVerse', '## 🛒 Amazon', '## 🇩🇪 For Germany', '## 🇩🇪 For Germans', '## 🎓 Strom & Gas Vertrieb'}
_locks = {}
log = logging.getLogger(__name__)


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


def channel_key(guild, name='support-gamerhq'):
    from services.community_structure_service import resource_key
    return resource_key(guild, name)


def category_key(guild): return f'managed_category:{guild.id}:support-gamerhq'
def message_key(guild, section='intro'): return f'partner_message:{guild.id}:{section}'
def legacy_message_key(guild, section='intro'):
    return f'support_message:{guild.id}' if section == 'intro' else f'support_message:{guild.id}:{section}'


def section_channel(section):
    return {'intro':'support-gamerhq', 'energy':'germany-services', 'energy_sales':'germany-services',
            'finance':'germany-services', 'instant_gaming':'gaming-deals', 'pixverse':'ai-tools'}[section]


def support_sections(channel_name=None):
    sections = [('intro', INTRO_TEXT, AFFILIATES[2]), ('energy', GERMANY_TEXT, None),
                ('energy_sales', SALES_TEXT, None), ('finance', FINANCE_TEXT, None),
                ('instant_gaming', '# 🎮 Gaming Deals\n\n## Instant Gaming\n\nGames & Deals\n\n' + AFFILIATES[0].copy + '\n\nℹ️ Affiliate Link', AFFILIATES[0]),
                ('pixverse', '# 🤖 AI & Creator Tools\n\n## PixVerse\n\nAI Video Generation\n\n' + AFFILIATES[1].copy + '\n\nℹ️ Affiliate Link', AFFILIATES[1])]
    return [row for row in sections if channel_name is None or section_channel(row[0]) == channel_name]


def support_text(): return INTRO_TEXT


def section_view(section, affiliate):
    from cogs.tickets import SupportOffers
    if affiliate:
        view = discord.ui.View(timeout=None)
        label = '🛒 Amazon öffnen' if section == 'intro' else f'{affiliate.emoji} Open {affiliate.name}'
        view.add_item(discord.ui.Button(label=label, url=affiliate.url))
        return view
    return SupportOffers(section)


def resource(guild, name, category=False):
    collection = guild.categories if category else guild.text_channels
    key = f'managed_category:{guild.id}:{name}' if category else channel_key(guild, name)
    raw = db.get_setting(key)
    stored = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    named = unique(collection, name)
    if stored in collection:
        if named and named.id != stored.id:
            raise ServerMessageError('Conflicting partner/support IDs and names; review before repair.')
        return stored
    return named


def resolve(guild, kind):
    return resource(guild, 'support-gamerhq', kind == 'category')


def guide_reference(guild):
    channel = resolve(guild, 'channel')
    link = channel.mention if channel else '#support-gamerhq'
    return f'## 💜 Support GamerHQ\nWant to support GamerHQ? Check **{link}** for optional ways to support the server and find partner offers.'


def matches_section(message, content):
    return (message.content or '').split('\n', 1)[0] == content.split('\n', 1)[0]


async def remove_empty_legacy_category(guild):
    # Fetch all channel types, not just cached text channels. Never delete children.
    categories = [c for c in guild.categories if alias(c.name) == 'support-gamerhq']
    if not categories:
        return []
    channels = await guild.fetch_channels()
    retained = []
    for category in categories:
        current = next((c for c in channels if c.id == category.id), None)
        if current is None:
            continue
        if alias(current.name) != 'support-gamerhq' or any(
                getattr(c, 'category_id', None) == current.id for c in channels):
            retained.append(current.id)
            log.warning('Support legacy category retained: nonempty or renamed id=%s', current.id)
            continue
        await current.delete(reason='Remove empty obsolete Support GamerHQ category')
        if db.get_setting(category_key(guild)) == str(current.id):
            db.set_setting(category_key(guild), '')
        log.info('Support empty legacy category deleted id=%s', current.id)
    return retained


async def rebuild_order(guild, channel, journal):
    """Resume a persisted create/pin → atomic ID switch → old-message cleanup."""
    sections = support_sections("germany-services")
    journal_key = f'partner_reorder:{guild.id}'
    result = {'messages': [], 'pin_failures': []}
    if journal['channel'] != channel.id:
        raise ServerMessageError('Support channel changed during reorder; review the saved migration first.')
    if journal['phase'] == 'create':
        staged = {}
        for section, content, affiliate in sections:
            message = await upsert_fixed_message(
                channel, setting_key=f'{journal_key}:{journal["generation"]}:{section}',
                content=content, view=section_view(section, affiliate), pin=False,
                allowed_mentions=discord.AllowedMentions.none(),
                recover_match=lambda m, content=content: m.id not in journal['old'] and matches_section(m, content))
            staged[section] = message.id
            log.info('Support reorder staged guild=%s section=%s message=%s', guild.id, section, message.id)
            try:
                await pin_managed_message(message, reason='GamerHQ ordered support messages')
            except discord.HTTPException:
                result['pin_failures'].append(section)
                log.exception('Support reorder pin failed section=%s', section)
        result['messages'] = list(staged.values())
        if result['pin_failures']:
            return result  # Keep original IDs and messages until every replacement is pinned.
        if result['messages'] != sorted(result['messages']):
            # A staged message was deleted between retries. Start a fresh ordered
            # generation; retain every superseded ID for eventual cleanup.
            journal['old'] = list(set(journal['old'] + result['messages']))
            journal['generation'] = uuid.uuid4().hex
            db.set_setting(journal_key, json.dumps(journal))
            raise ServerMessageError('Staged support messages changed. Retry sync to complete ordered recovery.')
        journal['phase'] = 'cleanup'
        journal['new'] = staged
        with db.connect() as conn:
            for section, mid in staged.items():
                conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             (message_key(guild, section), str(mid)))
            conn.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(journal), journal_key))
    for mid in journal['old']:
        try:
            message = await channel.fetch_message(mid)
            if message.author.id != guild.me.id or not any(matches_section(message, content) for _, content, _ in sections):
                raise ServerMessageError('An old managed support message changed ownership/content; cleanup requires review.')
            await message.delete()
            log.info('Support reorder removed old message=%s guild=%s', mid, guild.id)
        except discord.NotFound:
            pass
    db.set_setting(journal_key, '')
    result['messages'] = list(journal['new'].values())
    return result


async def retire_legacy_messages(guild, channel, active_ids):
    """Retire only recorded legacy IDs, after every destination is ready."""
    keys = [legacy_message_key(guild, section) for section in LEGACY_SECTIONS]
    old = {int(raw) for key in keys if (raw := db.get_setting(key)) and raw.isdigit()}
    journal_key = f'support_reorder:{guild.id}'
    raw = db.get_setting(journal_key)
    if raw:
        journal = json.loads(raw)
        if journal['channel'] != channel.id:
            raise ServerMessageError('Legacy support reorder belongs to another channel; review it first.')
        old.update(journal['old'])
        old.update(journal.get('new', {}).values())
    with db.connect() as conn:
        rows = conn.execute('SELECT key,value FROM settings WHERE key LIKE ?', (journal_key + ':%',)).fetchall()
    keys += [r['key'] for r in rows]
    old.update(int(r['value']) for r in rows if r['value'].isdigit())
    retained = []
    for mid in old - set(active_ids):
        try:
            message = await channel.fetch_message(mid)
            if message.author.id != guild.me.id or (message.content or '').split('\n',1)[0] not in LEGACY_HEADINGS:
                retained.append(mid)
                log.warning('Legacy support mapping is unrelated; preserving message=%s', mid)
                continue
            await message.delete()
            log.info('Support migrated legacy message=%s', mid)
        except discord.NotFound:
            pass
    with db.connect() as conn:
        for key in keys + [journal_key]:
            conn.execute('DELETE FROM settings WHERE key=?', (key,))
    return retained


async def sync_support_messages(guild, channel=None):
    """Refresh adopted channels only; owner repair creates the structure."""
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        channels = {}
        for name in ['support-gamerhq', *PARTNER_CHANNELS]:
            raw = db.get_setting(channel_key(guild, name))
            target = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
            if target not in guild.text_channels:
                return None
            channels[name] = target
        result = {'messages': [], 'pin_failures': [], 'retained_messages': []}
        pending = db.get_setting(f'partner_reorder:{guild.id}')
        if pending:
            resumed = await rebuild_order(guild, channels['germany-services'], json.loads(pending))
            if resumed['pin_failures']:
                return resumed
        grouped = {}
        for section, content, affiliate in support_sections():
            target = channels[section_channel(section)]
            if section == 'intro':
                content += '\n\n' + ' · '.join(channels[name].mention for name in PARTNER_CHANNELS)
            previous = db.get_setting(message_key(guild, section))
            message = await upsert_fixed_message(
                target, setting_key=message_key(guild, section), content=content, pin=False,
                view=section_view(section, affiliate), allowed_mentions=discord.AllowedMentions.none(),
                recover_match=lambda item, content=content: matches_section(item, content))
            log.info('Partner %s guild=%s section=%s message=%s', 'updated' if previous == str(message.id) else 'recreated' if previous else 'created', guild.id, section, message.id)
            grouped.setdefault(section_channel(section), []).append(message.id)
            result['messages'].append(message.id)
            try:
                if not message.pinned:
                    await pin_managed_message(message, reason='GamerHQ support and partner information')
                    log.info('Partner pinned guild=%s section=%s message=%s', guild.id, section, message.id)
            except discord.HTTPException:
                result['pin_failures'].append(section)
                log.exception('Partner pin failed section=%s', section)
        germany = grouped['germany-services']
        if germany != sorted(germany):
            journal = {'channel':channels['germany-services'].id, 'phase':'create', 'generation':uuid.uuid4().hex, 'old':germany}
            db.set_setting(f'partner_reorder:{guild.id}', json.dumps(journal))
            ordered = await rebuild_order(guild, channels['germany-services'], journal)
            result['pin_failures'].extend(ordered['pin_failures'])
            result['messages'] = [int(db.get_setting(message_key(guild,k))) for k,_,_ in support_sections()]
        if not result['pin_failures']:
            result['retained_messages'] = await retire_legacy_messages(guild, channels['support-gamerhq'], result['messages'])
        result['retained_categories'] = await remove_empty_legacy_category(guild)
        return result


async def refresh_support(guild, channel=None):
    return await sync_support_messages(guild, channel)


async def repair_support(guild, changed):
    # Resolve every collision/private target before creating or moving resources.
    start = unique(guild.categories, 'start-here')
    if start is None:
        raise ServerMessageError('START HERE must exist before support setup.')
    partners = resource(guild, 'partners-benefits', True)
    channels = {name:resource(guild,name) for name in ['support-gamerhq', *PARTNER_CHANNELS]}
    if partners and blocked_name(partners):
        raise ServerMessageError('Mapped partner category is protected/private; review manually.')
    for channel in channels.values():
        if channel and channel.category and blocked_name(channel.category) and alias(channel.category.name) != 'support-gamerhq':
            raise ServerMessageError('A partner/support channel is in a protected/private area; review before making it public.')
    empty = SimpleNamespace(guild=guild, overwrites={}, overwrites_for=lambda target:discord.PermissionOverwrite())
    overwrites = guide_overwrites(empty)
    if partners is None:
        partners = await guild.create_category(PARTNER_CATEGORY, overwrites=overwrites, reason='GamerHQ partner information')
        changed.append('Created PARTNERS & BENEFITS')
    else:
        await partners.edit(name=PARTNER_CATEGORY, reason='GamerHQ partner category naming')
    db.set_setting(f'managed_category:{guild.id}:partners-benefits', partners.id)
    for name, channel in channels.items():
        target = start if name == 'support-gamerhq' else partners
        display = CHANNEL_NAME if name == 'support-gamerhq' else PARTNER_CHANNELS[name]
        if channel is None:
            channel = await target.create_text_channel(display, overwrites=overwrites, reason='GamerHQ read-only partner board')
        elif channel.name != display or channel.category_id != target.id:
            channel = await channel.edit(name=display, category=target, sync_permissions=False, reason='GamerHQ partner board placement')
        db.set_setting(channel_key(guild,name), channel.id)
        await set_read_only(channel)
    result = await sync_support_messages(guild)
    changed.append('Updated Support and Partners & Benefits; existing channels/history retained')
    if result and result['pin_failures']:
        raise ServerMessageError('Partner messages saved, but pinning failed: ' + ', '.join(result['pin_failures']) + '. Restore permissions and retry.')
