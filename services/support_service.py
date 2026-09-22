"""Optional affiliate board. Structural changes run only through owner repair."""
import asyncio
import logging
import json
from dataclasses import dataclass
from types import SimpleNamespace

import discord
from database import db
from services.onboarding_service import alias, unique, guide_overwrites, set_read_only
from services.server_service import ServerMessageError, upsert_fixed_message, pin_managed_message
from services.music_bot_service import blocked_name

CHANNEL_NAME = '💜・support-gamerhq'
TITLE = '# 🤝 Partners & Benefits'
INTRO_TEXT = TITLE + "\n\nLooking for useful deals, tools or services?\n\nUnder **PARTNERS & BENEFITS** you'll find selected offers and resources that may be useful to you."
DISCLOSURE = 'Some links may be affiliate or referral links.'
PARTNER_NAVIGATION = (
    ('direct-support', '💜', 'Direct Support'), ('amazon', '🛒', 'Amazon'),
    ('haushaltscheck', '🇩🇪', 'Haushaltscheck'),
    ('gaming-deals', '🎮', 'Gaming Deals'), ('ai-tools', '🤖', 'AI Tools'),
)
DIRECT_TEXT = """# 💜 Direct Support

Want to support GamerHQ directly?

A direct support option will be available here soon.

**Coming Soon**"""
AMAZON_TEXT = """# 🛒 Amazon

Use the link below when shopping on Amazon.

Tip: Save it as a browser bookmark with `Ctrl + D` so it's easy to find later.

Affiliate / referral link"""
HOUSEHOLD_TEXT = """# 🇩🇪 Haushaltscheck

Nur für Nutzer in Deutschland.

Viele Themen rund um Verträge, Tarife und laufende Kosten werden einem im Alltag kaum erklärt – und in der Schule meistens auch nicht.

Wenn du möchtest, kannst du deinen Haushalt kostenlos und unverbindlich prüfen lassen.

Dabei können zum Beispiel Bereiche wie:

- 🚗 KFZ
- ⚡ Strom & Gas
- 📄 laufende Verträge & Tarife

gecheckt werden.

Du bekommst mehrere passende Tarife übersichtlich zusammengestellt und als PDF zum Vergleichen.

So kannst du Preis und Leistung in Ruhe vergleichen und selbst entscheiden, ob und welches Angebot für dich sinnvoll ist."""
GAMING_TEXT = """# 🎮 Gaming Deals

Find current gaming deals, promotions and releases here.

Affiliate / referral link"""
PARTNER_CATEGORY = '🤝 PARTNERS & BENEFITS'
PARTNER_CHANNELS = {'direct-support':'💜・direct-support', 'amazon':'🛒・amazon', 'haushaltscheck':'🇩🇪・haushaltscheck', 'gaming-deals':'🎮・gaming-deals', 'ai-tools':'🤖・ai-tools'}
LEGACY_SECTIONS = ('intro','transparency','instant_gaming','pixverse','amazon','energy','energy_sales')
LEGACY_HEADINGS = {TITLE, '# 💜 Support GamerHQ', '## ℹ️ Transparency', '## 🎮 Instant Gaming', '## 🤖 PixVerse', '## 🛒 Amazon', '## 🇩🇪 For Germany', '## 🇩🇪 For Germans', '## 🎓 Strom & Gas Vertrieb'}
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
              'Create AI-generated videos and visual content with PixVerse.', '🤖'),
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
    return {'intro':'support-gamerhq', 'direct':'direct-support', 'amazon':'amazon', 'household':'haushaltscheck', 'instant_gaming':'gaming-deals', 'pixverse':'ai-tools'}[section]


def support_sections(channel_name=None):
    sections = [('intro', INTRO_TEXT, None), ('direct', DIRECT_TEXT, None), ('amazon', AMAZON_TEXT, AFFILIATES[2]),
                ('household', HOUSEHOLD_TEXT, None),
                ('instant_gaming', GAMING_TEXT, AFFILIATES[0]),
                ('pixverse', '# 🤖 AI & Creator Tools\n\n## PixVerse\n\n' + AFFILIATES[1].copy + '\n\nAffiliate / referral link', AFFILIATES[1])]
    return [row for row in sections if channel_name is None or section_channel(row[0]) == channel_name]


def support_text(channels=None):
    """Render only channels already resolved from persisted managed IDs."""
    channels = channels or {}
    bullets = [f'- {emoji} {channels[name].mention} — {label}'
               for name, emoji, label in PARTNER_NAVIGATION if name in channels]
    parts = [INTRO_TEXT]
    if bullets:
        parts.append('\n'.join(bullets))
    if len(bullets) != len(PARTNER_NAVIGATION):
        parts.append('More partner channels are being set up.')
    parts.append(DISCLOSURE)
    return '\n\n'.join(parts)


def section_view(section, affiliate):
    from cogs.tickets import SupportOffers
    if affiliate:
        view = discord.ui.View(timeout=None)
        label = f'{affiliate.emoji} Open {affiliate.name}'
        view.add_item(discord.ui.Button(label=label, url=affiliate.url))
        return view
    return None if section in ('intro', 'direct') else SupportOffers(section)


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
    return f'## 🤝 Partners & Benefits\nFind useful deals, tools and services in **{link}**.'


def matches_section(message, content):
    heading = (message.content or '').split('\n', 1)[0]
    expected = content.split('\n', 1)[0]
    return heading == expected or (expected == TITLE and heading == '# 💜 Support GamerHQ')


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


LEGACY_CHANNELS = ('strom-gas', 'germany-services', 'finanzberatung')
RETIRED_HEADINGS = {'# ⚡ Strom & Gas', '# 🎓 Strom & Gas Vertrieb',
                    '# 💶 Finanzcheck & Planung', '# 💶 Finanzberatung'}


def legacy_review_channels(guild):
    active = db.get_setting(channel_key(guild, 'haushaltscheck'))
    ids = set(json.loads(db.get_setting(f'household_review:{guild.id}') or '[]'))
    previous_review = db.get_setting(f'partner_manual_review:{guild.id}')
    if previous_review and previous_review.isdigit():
        ids.add(int(previous_review))
    for name in LEGACY_CHANNELS:
        retired = db.get_setting(f'retired_partner_channel:{guild.id}:{name}')
        if retired and retired.isdigit() and retired != active:
            ids.add(int(retired))
        channel = resource(guild, name)
        if channel and str(channel.id) != active:
            ids.add(channel.id)
    return [guild.get_channel(cid) for cid in sorted(ids) if guild.get_channel(cid)]


def legacy_review_channel(guild):
    return next(iter(legacy_review_channels(guild)), None)


def retire_partner_mappings(guild):
    """Remove obsolete active mappings, retaining identities for historical review."""
    if not db.get_setting(f'household_migrated:{guild.id}') or db.get_setting(f'household_migration:{guild.id}'):
        return
    from services import managed_message_service as managed
    for section in ('energy', 'energy_sales', 'finance'):
        state = managed.load(message_key(guild, section))
        if state and not state.get('retired'):
            state['retired'] = True
            managed.store(state)
    mappings = [(channel_key(guild, name), f'retired_partner_channel:{guild.id}:{name}')
                for name in LEGACY_CHANNELS]
    mappings += [(message_key(guild, section), f'retired_partner_message:{guild.id}:{section}')
                 for section in ('energy', 'energy_sales', 'finance')]
    with db.connect() as conn:
        for current, retired in mappings:
            row = conn.execute('SELECT value FROM settings WHERE key=?', (current,)).fetchone()
            if row:
                conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (retired, row['value']))
                conn.execute('DELETE FROM settings WHERE key=?', (current,))


async def prepare_household_migration(guild, legacy_channels):
    """Snapshot persisted identities before renames; never discover pins by heading alone."""
    key = f'household_migration:{guild.id}'
    if db.get_setting(key) or db.get_setting(f'household_migrated:{guild.id}'):
        return
    sources = {channel.id: set() for channel in legacy_channels if channel}
    for section in ('energy', 'energy_sales', 'finance'):
        raw = db.get_setting(message_key(guild, section))
        if raw and raw.isdigit():
            for ids in sources.values():
                ids.add(int(raw))
    # Capture resumable migrations from earlier versions, including staged IDs.
    for prefix in ('partner_reorder', 'partner_split'):
        old_key = f'{prefix}:{guild.id}'
        raw = db.get_setting(old_key)
        if raw:
            journal = json.loads(raw)
            ids = sources.setdefault(journal['channel'], set())
            ids.update(journal['old'])
            ids.update(journal.get('new', {}).values())
            with db.connect() as conn:
                rows = conn.execute('SELECT value FROM settings WHERE key LIKE ?', (old_key + ':%',)).fetchall()
            ids.update(int(row['value']) for row in rows if row['value'].isdigit())
    db.set_setting(key, json.dumps({str(cid): sorted(ids) for cid, ids in sources.items()}))


async def finish_household_migration(guild):
    """Retire recognized defaults only after all replacement boards are pinned."""
    from services import managed_message_service as managed
    key = f'household_migration:{guild.id}'
    raw = db.get_setting(key)
    if not raw:
        return
    review = set(json.loads(db.get_setting(f'household_review:{guild.id}') or '[]'))
    states = [managed.load(message_key(guild, section)) for section in ('energy', 'energy_sales', 'finance')]
    for cid, ids in json.loads(raw).items():
        channel = guild.get_channel(int(cid))
        if channel not in guild.text_channels:
            continue
        # Unmapped lookalikes are evidence for review only, never deletion.
        async for message in channel.pins(limit=None):
            if message.id not in ids and (message.content or '').split('\n', 1)[0] in RETIRED_HEADINGS:
                review.add(channel.id)
                db.set_setting(f'household_review:{guild.id}', json.dumps(sorted(review)))
        for mid in ids:
            try:
                message = await channel.fetch_message(mid)
            except discord.NotFound:
                continue
            state = next((s for s in states if s and s['channel_id'] == channel.id and s['message_id'] == mid), None)
            if (message.author.id != guild.me.id or
                    (state and (state['customized'] or state.get('pending') or not managed.owns(state, channel, message))) or
                    (not state and (message.content or '').split('\n', 1)[0] not in RETIRED_HEADINGS)):
                review.add(channel.id)
                # Preserve all custom/manual copy. Old callbacks cannot create new tickets.
                db.set_setting(f'household_review:{guild.id}', json.dumps(sorted(review)))
                continue
            await message.delete()
    # Historical customization/audit stays in SQLite, outside the active editor registry.
    for state in states:
        if state:
            state['retired'] = True
            managed.store(state)
    with db.connect() as conn:
        for prefix in ('partner_reorder', 'partner_split'):
            conn.execute('DELETE FROM settings WHERE key=? OR key LIKE ?',
                         (f'{prefix}:{guild.id}', f'{prefix}:{guild.id}:%'))
        conn.execute('DELETE FROM settings WHERE key=?', (key,))
        conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (f'household_migrated:{guild.id}', '1'))


async def sync_support_messages(guild, channel=None):
    """Refresh adopted channels only; owner repair creates the structure."""
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        channels = {}
        for name in ['support-gamerhq', *PARTNER_CHANNELS]:
            raw = db.get_setting(channel_key(guild, name))
            target = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
            if target in guild.text_channels:
                channels[name] = target
        if len(channels) != 1 + len(PARTNER_CHANNELS):
            # Never retain dangling navigation or adopt a name-only lookalike.
            # Repair remains owner-controlled; sync reports the incomplete setup.
            if 'support-gamerhq' in channels:
                await upsert_fixed_message(
                    channels['support-gamerhq'], setting_key=message_key(guild),
                    content=support_text(channels), view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                    recover_match=lambda item: matches_section(item, INTRO_TEXT))
            return None
        result = {'messages': [], 'pin_failures': [], 'retained_messages': []}
        for section, content, affiliate in support_sections():
            target = channels[section_channel(section)]
            if section == 'intro':
                content = support_text(channels)
            previous = db.get_setting(message_key(guild, section))
            message = await upsert_fixed_message(
                target, setting_key=message_key(guild, section), content=content, pin=False,
                view=section_view(section, affiliate), allowed_mentions=discord.AllowedMentions.none(),
                recover_match=lambda item, content=content: matches_section(item, content))
            log.info('Partner %s guild=%s section=%s message=%s', 'updated' if previous == str(message.id) else 'recreated' if previous else 'created', guild.id, section, message.id)
            result['messages'].append(message.id)
            try:
                if not message.pinned:
                    await pin_managed_message(message, reason='GamerHQ support and partner information')
                    log.info('Partner pinned guild=%s section=%s message=%s', guild.id, section, message.id)
            except discord.HTTPException:
                result['pin_failures'].append(section)
                log.exception('Partner pin failed section=%s', section)
        if not result['pin_failures']:
            await finish_household_migration(guild)
            retire_partner_mappings(guild)
            result['retained_messages'] = await retire_legacy_messages(guild, channels['support-gamerhq'], result['messages'])
        result['retained_categories'] = await remove_empty_legacy_category(guild)
        result['manual_review_channels'] = [c.id for c in legacy_review_channels(guild)]
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
    legacy = [resource(guild, name) for name in LEGACY_CHANNELS]
    if channels['haushaltscheck'] is None:
        channels['haushaltscheck'] = next((c for name, c in zip(LEGACY_CHANNELS, legacy)
            if c and db.get_setting(channel_key(guild, name)) == str(c.id)), None)
    if partners and blocked_name(partners):
        raise ServerMessageError('Mapped partner category is protected/private; review manually.')
    # Once retired, a legacy channel may have been archived privately by its owner.
    # Only targets that could be moved/made public require this preflight then.
    inspect_channels = list(channels.values())
    if not db.get_setting(f'household_migrated:{guild.id}'):
        inspect_channels.extend(legacy)
    for channel in inspect_channels:
        if channel and channel.category and blocked_name(channel.category) and alias(channel.category.name) != 'support-gamerhq':
            raise ServerMessageError('A partner/support channel is in a protected/private area; review before making it public.')
    await prepare_household_migration(guild, legacy)
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
        if name == 'gaming-deals':
            from config import INSTANT_GAMING_BOT_ID
            external = guild.get_member(INSTANT_GAMING_BOT_ID) if INSTANT_GAMING_BOT_ID else None
            if external and external.bot and external.id != guild.me.id:
                await channel.set_permissions(external, overwrite=discord.PermissionOverwrite(
                    view_channel=True, read_message_history=True, send_messages=True,
                    embed_links=True, attach_files=True, manage_channels=False,
                    manage_roles=False, manage_messages=False, mention_everyone=False),
                    reason='Configured external Instant Gaming publisher, gaming-deals only')
    result = await sync_support_messages(guild)
    changed.append('Updated Partners & Benefits messages and navigation')
    if result and result['pin_failures']:
        raise ServerMessageError('Partner messages saved, but pinning failed: ' + ', '.join(result['pin_failures']) + '. Restore permissions and retry.')
    from services import legacy_finance_service as finance
    await finance.repair(guild, changed)
    finance_ids = {c.id for c in finance.candidates(guild)}
    if any(c.id not in finance_ids for c in legacy_review_channels(guild)):
        changed.append('MANUAL_REVIEW: other legacy partner channels/custom content retained; inspect manually.')
