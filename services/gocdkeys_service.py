"""Optional compact comparisons; fail closed on uncertain product matches."""
import asyncio
import html
from html.parser import HTMLParser
import logging
import re
import time
import unicodedata
from weakref import WeakValueDictionary
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import discord
import config
from database import affiliate_deals
from services.instant_gaming_service import resolve

log = logging.getLogger(__name__)
COPY = '💰 Compare Prices\n\nAffiliate link'
DISABLED_COPY = 'Price comparison unavailable for this offer.'
_message_locks = WeakValueDictionary()


def deal_source(message):
    from services.bot_group_service import member_id
    if not message.author.bot:
        return None
    if message.author.id == getattr(getattr(message.guild, 'me', None), 'id', None):
        return None
    # Discord-supplied application IDs authenticate application/webhook posts;
    # an arbitrary webhook name or author ID is never sufficient.
    source_id = getattr(message, 'application_id', None) if message.webhook_id else message.author.id
    return next((name for name in config.SUPPORTED_DEAL_SOURCES
                 if source_id and member_id(message.guild, name) == source_id), None)


def normalize_game_name(value):
    value = html.unescape(str(value or ''))
    value = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', value)
    value = re.sub(r'https?://\S+|<[^>]+>', '', value)
    value = re.sub(r'(?i)^(?:sale|deal|buy now)\s*[:!–-]\s*', '', value)
    value = re.sub(r'(?:[$€£]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[$€£]|-?\d+\s*%)', '', value)
    value = re.sub(r'(?i)\s*[-–|]?\s*\d+\s*%\s*OFF\s*$', '', value)
    value = re.sub(r'(?i)(?:\s*[-–|()]?\s*\b(?:steam(?: key)?|pc)\b\s*\)?)+$', '', value)
    value = re.sub(r'(?i)\s*[-–|]?\s*OFF\s*$', '', value)
    value = ''.join(c for c in value if unicodedata.category(c)[0] in 'LN' or c in " '-:.")
    value = ' '.join(value.split()).strip(' -:.')
    if not 2 <= len(value) <= 160 or not any(c.isalpha() for c in value):
        return None
    if value.casefold() in {'sale', 'deal', 'buy now', 'price', 'discount', 'platform', 'instant gaming', 'dealgecko', 'game', 'product', 'offer', 'offers', 'free', 'gratis', 'eur', 'usd'}:
        return None
    return value


def extract_game_name(message):
    embeds = message.embeds
    sources = [e.title for e in embeds]
    sources += [f.value for e in embeds for f in e.fields if f.name.casefold().strip() in {'game', 'title', 'product', 'game name'}]
    sources += re.findall(r'\[([^\]]+)\]\(https?://[^)]+\)', message.content or '')
    sources += re.findall(r'(?im)^(?:game|title|product)\s*:\s*(.+)$', message.content or '')
    sources += [e.description for e in embeds]
    sources += [message.content]
    for source in sources:
        for line in (source or '').splitlines():
            title = normalize_game_name(line)
            if title:
                platforms = [f.value for e in embeds for f in e.fields if f.name.casefold().strip() == 'platform']
                if (platforms and re.search(r'(?i)\b(?:xbox|playstation|ps[345]|nintendo|switch)\b', platforms[0])
                        and not re.search(r'(?i)\b(?:xbox|playstation|ps[345]|nintendo|switch)\b', title)):
                    title += ' ' + platforms[0]
                return title
    return None


def identity(title):
    return ''.join(c for c in unicodedata.normalize('NFKC', title).casefold() if c.isalnum())


def valid_page_url(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc == 'gocdkeys.com'
            and not parts.query and bool(re.fullmatch(r'/buy-[a-z0-9-]+-(?:pc-cd-key|xbox-one|ps4|ps5)', parts.path)))


def build_affiliate_url(url):
    if not valid_page_url(url) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', config.GOCDKEYS_REFERRAL_CODE):
        raise ValueError('Invalid comparison URL/referral configuration')
    return urlunsplit(urlsplit(url)._replace(fragment='ref=' + config.GOCDKEYS_REFERRAL_CODE))


class ProductPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'title': self.in_title = True

    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False

    def handle_data(self, data):
        if self.in_title: self.title += data


def product_target(title):
    for pattern, suffix, platform in [(r'\s*[-|]?\s*(?:PlayStation\s*5|PS5)$', 'ps5', 'PS5'),
                                      (r'\s*[-|]?\s*(?:PlayStation\s*4|PS4)$', 'ps4', 'PS4'),
                                      (r'\s*[-|]?\s*Xbox(?: One| Series [XS](?:/S)?)?$', 'xbox-one', 'XBOX')]:
        match = re.search(pattern, title, re.I)
        if match: return title[:match.start()].strip(), suffix, platform
    if re.search(r'(?i)\b(?:xbox|playstation|ps[345]|nintendo|switch)\b', title):
        return None
    return title, 'pc-cd-key', 'PC'


def matches_page(document, expected, platform='PC'):
    page = ProductPage()
    page.feed(document)
    # Observed official product-title format; a changed layout fails closed.
    pattern = (r'Buy (.+?) (?:Steam|PC)(?: CD)? Key.*? at best prices\s*\|\s*Gocdkeys' if platform == 'PC'
               else r'Buy Cheap (.+?) ' + re.escape(platform) + r' Code.*?\|\s*Gocdkeys')
    match = re.fullmatch(pattern, page.title.strip(), re.I)
    return bool(match and identity(match[1]) == identity(expected))


class GoCdKeysService:
    def __init__(self):
        self._cache = {}
        self._gate = asyncio.Lock()
        self._next_request = 0.0

    def backfill_channel(self, guild):
        if not config.GOCDKEYS_AUTOMATIC_SUPPORTED:
            raise ValueError('Automatic GoCDKeys lookup is unsupported (HTTP 403). Use /deals create with a verified link.')
        if not config.GOCDKEYS_ENABLED or not guild or guild.id != config.GUILD_ID:
            raise ValueError('GoCDKeys must be enabled in the configured server.')
        channel = resolve(guild, 'gaming-deals', mapped_only=True)
        if not channel or not guild.me or not all(getattr(channel.permissions_for(guild.me), bit)
                for bit in ('view_channel', 'read_message_history', 'send_messages', 'embed_links')):
            raise ValueError('Managed gaming-deals or GamerHQ read/reply permissions missing. Run /server health.')
        return channel

    async def recent_messages(self, channel, limit):
        messages = []
        async for message in channel.history(limit=limit):
            messages.append(message)
            if len(messages) >= limit:
                break
        return messages

    def companion_sources(self, guild, messages):
        # Existing canonical replies without a DB row are also retained. Never
        # adopt, edit or delete these messages from a read-only preview.
        return {getattr(getattr(m, 'reference', None), 'message_id', None) for m in messages
                if m.author.id == guild.me.id and m.content in {COPY, DISABLED_COPY}}

    async def preview_backfill(self, guild, limit=50):
        if limit not in (25, 50, 100):
            raise ValueError('Choose 25, 50 or 100 messages.')
        channel = self.backfill_channel(guild)
        messages = await self.recent_messages(channel, limit)
        companions = self.companion_sources(guild, messages)
        plan = dict(channel_id=channel.id, scanned=len(messages), supported=0,
                    enriched=0, retained=0, skipped=0, candidates=[])
        for message in messages:
            if (message.channel.id != channel.id or not deal_source(message)
                    or not extract_game_name(message)):
                plan['skipped'] += 1
                continue
            plan['supported'] += 1
            row = affiliate_deals.record(message.id)
            if message.id in companions or (row and row['status'] == 'posted'):
                plan['enriched'] += 1
            elif row:
                plan['retained'] += 1  # Includes uncertain/reserved/deleted claims.
            else:
                plan['candidates'].append(message.id)
        return plan

    async def run_backfill(self, guild, plan):
        channel = self.backfill_channel(guild)
        if channel.id != plan['channel_id'] or len(plan['candidates']) > 100:
            raise ValueError('Managed channel changed; create a new preview.')
        companions = self.companion_sources(guild, await self.recent_messages(channel, 100))
        result = dict(created=0, retained=0, skipped=0, uncertain=0)
        for source_id in plan['candidates']:
            # Re-read both Discord and SQLite at confirmation, including live
            # arrivals since preview. handle() shares the live source lock/claim.
            if source_id in companions or affiliate_deals.processed(source_id):
                result['retained'] += 1
                continue
            try:
                message = await channel.fetch_message(source_id)
                outcome = await self.handle(message, backfill=True)
                result[outcome if outcome in result else 'skipped'] += 1
            except discord.HTTPException:
                result['skipped'] += 1
        log.info('[gocdkeys] backfill processed created=%s retained=%s skipped=%s uncertain=%s',
                 result['created'], result['retained'], result['skipped'], result['uncertain'])
        return result

    async def fetch_page(self, url):
        # Retain the provider seam, but never scrape without approved access.
        log.info('[gocdkeys] automatic lookup unsupported; use verified manual links')
        return None

    async def find_game_page(self, title, *, wait=False):
        if not config.GOCDKEYS_AUTOMATIC_SUPPORTED:
            return None
        # Conservative resolver. Edition/DLC words remain part of the identity.
        # A candidate slug is NEVER considered a result until the page is verified.
        target = product_target(title)
        if not target:
            return None
        name, suffix, platform = target
        slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', name.replace("'", '')).encode('ascii', 'ignore').decode().lower()).strip('-')
        url = 'https://gocdkeys.com/buy-' + slug + '-' + suffix
        async with self._gate:
            now = time.monotonic()
            cached = self._cache.get(title)
            if cached and cached[0] > now: return cached[1]
            # Bounded load; busy bursts can be skipped rather than queued unboundedly.
            if now < self._next_request:
                if not wait: return None
                await asyncio.sleep(self._next_request - now)
                now = time.monotonic()
            self._next_request = now + 2
            try:
                document = await self.fetch_page(url)
                result = url if document and matches_page(document, name, platform) else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                log.debug('[gocdkeys] skipped: product lookup unavailable')
                result = None
            if len(self._cache) >= 256: self._cache.clear()
            self._cache[title] = (now + (3600 if result else 300), result)
            return result

    async def owned_companion(self, channel, guild, source_id, row):
        if not row['response_message_id'] or row['guild_id'] != guild.id or row['channel_id'] != channel.id:
            return None
        companion = await channel.fetch_message(row['response_message_id'])
        if (companion.author.id != guild.me.id
                or getattr(companion.reference, 'message_id', None) != source_id
                or companion.content not in {COPY, DISABLED_COPY}):
            log.warning('[gocdkeys] skipped reason=companion_ownership_mismatch message_id=%s', source_id)
            return None
        return companion

    async def update_companion(self, message, row, *, url=None, title=None):
        companion = await self.owned_companion(message.channel, message.guild, message.id, row)
        if not companion:
            return
        if url:
            kwargs = create_comparison_message(url)
            kwargs['suppress'] = kwargs.pop('suppress_embeds')
            await companion.edit(**kwargs)
            affiliate_deals.update_target(message.id, url, title)
        else:
            await companion.edit(content=DISABLED_COPY, view=None, allowed_mentions=discord.AllowedMentions.none(), suppress=True)
            affiliate_deals.finish(message.id, 'disabled')

    async def handle_delete(self, guild, channel_id, source_id):
        try:
            if not config.GOCDKEYS_AUTOMATIC_SUPPORTED or not config.GOCDKEYS_ENABLED or not guild or guild.id != config.GUILD_ID:
                return
            channel = resolve(guild, 'gaming-deals', mapped_only=True)
            if not channel or channel.id != channel_id:
                return
            lock = _message_locks.setdefault(source_id, asyncio.Lock())
            async with lock:
                row = affiliate_deals.record(source_id)
                if not row or row['status'] not in {'posted', 'disabled'}:
                    return
                try:
                    companion = await self.owned_companion(channel, guild, source_id, row)
                    if not companion:
                        return
                    await companion.delete()
                except discord.NotFound:
                    pass
                affiliate_deals.finish(source_id, 'deleted')
        except Exception as exc:
            log.warning('[gocdkeys] cleanup skipped message_id=%s reason=%s', source_id, type(exc).__name__)

    async def handle(self, message, *, backfill=False):
        try:
            if not config.GOCDKEYS_AUTOMATIC_SUPPORTED or not config.GOCDKEYS_ENABLED or not message.guild or message.guild.id != config.GUILD_ID:
                log.debug('[gocdkeys] skipped reason=disabled_or_wrong_guild message_id=%s', message.id)
                return
            source = deal_source(message)
            if not source:
                log.debug('[gocdkeys] skipped reason=untrusted_source message_id=%s', message.id)
                return
            channel = resolve(message.guild, 'gaming-deals', mapped_only=True)
            if not channel or message.channel.id != channel.id:
                return
            lock = _message_locks.setdefault(message.id, asyncio.Lock())
            async with lock:
                if backfill and affiliate_deals.processed(message.id):
                    log.info('[gocdkeys] duplicate skipped message_id=%s', message.id)
                    return 'retained'
                return await self.process_deal(message, source, backfill=backfill)
        except Exception as exc:
            log.warning('[gocdkeys] skipped: handler error (%s)', type(exc).__name__)

    async def process_deal(self, message, source, *, backfill=False):
        row = affiliate_deals.record(message.id)
        title = extract_game_name(message)
        log.info('[gocdkeys] deal detected source=%s message_id=%s title_found=%s', source, message.id, bool(title))
        if row:
            if row['status'] not in {'posted', 'disabled'}:
                return
            if row['status'] == 'posted' and title and identity(title) == identity(row['normalized_game'] or ''):
                return
            page = await self.find_game_page(title) if title else None
            if page:
                await self.update_companion(message, row, url=build_affiliate_url(page), title=title)
            elif row['status'] == 'posted':
                await self.update_companion(message, row)
            return
        if not title:
            log.info('[gocdkeys] skipped reason=unresolved_title message_id=%s', message.id)
            return
        page = await self.find_game_page(title, wait=True) if backfill else await self.find_game_page(title)
        if not page:
            log.info('[gocdkeys] skipped reason=unresolved_game message_id=%s', message.id)
            return
        url = build_affiliate_url(page)
        if not affiliate_deals.claim(message, url, title, source): return
        try:
            result = await message.reply(**create_comparison_message(url), mention_author=False)
        except Exception:
            affiliate_deals.finish(message.id, 'uncertain')
            log.warning('[gocdkeys] skipped: delivery failed; claim retained for manual review')
            return 'uncertain'
        affiliate_deals.finish(message.id, 'posted', result.id)
        log.info('[gocdkeys] comparison created provider=GoCDKeys source=%s message_id=%s', source, message.id)
        return 'created'


def create_comparison_message(url):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label='Compare Prices', emoji='🔎', url=url))
    return dict(content=COPY, view=view, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)


def status(guild, bot=None):
    if not config.GOCDKEYS_AUTOMATIC_SUPPORTED:
        return ('GoCDKeys', 'INFO', 'Automatic lookup/backfill: Unsupported (HTTP 403). Manual verified links: /deals create. Existing comparisons retained.')
    from services.bot_group_service import member_id
    channel = resolve(guild, 'gaming-deals', mapped_only=True)
    configured = bool(channel and any(member_id(guild, source) for source in config.SUPPORTED_DEAL_SOURCES) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', config.GOCDKEYS_REFERRAL_CODE))
    intent = bool(getattr(getattr(bot, 'intents', None), 'message_content', False))
    permissions = channel.permissions_for(guild.me) if channel and getattr(guild, 'me', None) else None
    missing = [right for right in ('view_channel', 'send_messages', 'read_message_history', 'embed_links')
               if not permissions or not getattr(permissions, right, False)]
    enabled = config.GOCDKEYS_ENABLED and configured and intent and not missing
    detail = (f'Paid-deal watcher: {"Enabled" if enabled else "Disabled"}; '
              f'Gaming Deals: {channel.mention if channel else "Missing"}; '
              f'GoCDKeys: {"Configured" if configured else "Missing"}; '
              f'Referral: {"Configured" if config.GOCDKEYS_REFERRAL_CODE else "Missing"}. '
              f'GamerHQ posting: {"Ready" if not missing else "Missing " + ", ".join(missing)}; '
              f'Message Content intent: {"Enabled" if intent else "Disabled"}. '
              'Optional product-page validation; no live lookup in health.')
    return ('GoCDKeys', 'PASS' if enabled else 'WARN' if config.GOCDKEYS_ENABLED else 'INFO', detail)
