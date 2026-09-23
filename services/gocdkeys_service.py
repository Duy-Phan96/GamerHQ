"""Optional compact comparisons; fail closed on uncertain product matches."""
import asyncio
import html
from html.parser import HTMLParser
import logging
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation
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
    if not message.author.bot or message.webhook_id:
        return None
    return next((name for name in config.SUPPORTED_DEAL_SOURCES
                 if member_id(message.guild, name) == message.author.id), None)


def price_state(message):
    """Only confidently priced offers qualify. Free signals always win over old prices."""
    fields = [f for e in message.embeds for f in e.fields]
    price_fields = [f.value for f in fields if f.name.casefold().strip() in
                    {'price', 'sale price', 'current price', 'preis', 'offer price', 'deal price'}]
    discounts = [f.value + ' off' for f in fields if f.name.casefold().strip() in {'discount', 'rabatt'}]
    texts = [message.content or '', *[e.description or '' for e in message.embeds],
             *[e.title or '' for e in message.embeds], *price_fields, *discounts]
    positive = False
    for text in texts:
        discount_only = text in discounts
        text = re.sub(r'~~.*?~~', '', text, flags=re.S)
        text = text.replace('**', '').replace('__', '')
        # Remove URLs; prices/discounts in referral paths are not offer prices.
        text = re.sub(r'https?://\S+', '', text)
        if re.search(r'(?im)(?:^|[:\n])\s*(?:FREE|Gratis|Kostenlos)(?:\s*[!.*]|\s*$)|\b(?:now free|free to keep|100\s*%\s*(?:off|discount|rabatt))\b', text):
            return 'free'
        if discount_only:
            continue
        text = re.sub(r'(?im)^\s*(?:save|saving|savings|original price|old price|rrp|msrp)\b[^\n]*', '', text)
        values = re.findall(r'(?:[€$£]\s*(\d+(?:[.,]\d{1,2})?)|(?<![\w.])(\d+(?:[.,]\d{1,2})?)\s*(?:[€$£]|EUR\b|USD\b|GBP\b))', text, re.I)
        numbers = [a or b for a, b in values]
        numbers += re.findall(r'(?i)\b(?:price|preis)\s*[:=]\s*(\d+(?:[.,]\d{1,2})?)\b', text)
        if text in price_fields and re.fullmatch(r'\s*\d+(?:[.,]\d{1,2})?\s*', text):
            numbers.append(text.strip())
        for number in numbers:
            try:
                amount = Decimal(number.replace(',', '.'))
            except InvalidOperation:
                continue
            if amount == 0: return 'free'
            positive = positive or amount > 0
    return 'paid' if positive else 'unknown'


def normalize_game_name(value):
    value = html.unescape(str(value or ''))
    value = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', value)
    value = re.sub(r'https?://\S+|<[^>]+>', '', value)
    value = re.sub(r'(?i)\b(?:sale|deal|buy now)\b\s*[:!–-]?', '', value)
    value = re.sub(r'(?:[$€£]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[$€£]|-?\d+\s*%)', '', value)
    value = re.sub(r'(?i)\s*[-–|]?\s*\d+\s*%\s*OFF\s*$', '', value)
    value = re.sub(r'(?i)(?:\s*[-–|()]?\s*\b(?:steam(?: key)?|pc)\b\s*\)?)+$', '', value)
    value = re.sub(r'(?i)\s*[-–|]?\s*OFF\s*$', '', value)
    value = ''.join(c for c in value if unicodedata.category(c)[0] in 'LN' or c in " '-:.")
    value = ' '.join(value.split()).strip(' -:.')
    if not 2 <= len(value) <= 160 or not any(c.isalpha() for c in value):
        return None
    if value.casefold() in {'price', 'discount', 'platform', 'instant gaming', 'dealgecko', 'game', 'product', 'offer', 'offers', 'free', 'gratis', 'eur', 'usd'}:
        return None
    return value.title() if value.isupper() else value


def extract_game_name(message):
    embeds = message.embeds
    sources = [e.title for e in embeds]
    sources += [f.value for e in embeds for f in e.fields if f.name.casefold().strip() in {'game', 'title', 'product', 'game name'}]
    sources += [f.value for e in embeds for f in e.fields if f.name.casefold().strip() not in {'price', 'discount', 'platform', 'store', 'region', 'edition'}]
    sources += [e.description for e in embeds]
    sources += re.findall(r'\[([^\]]+)\]\(https?://[^)]+\)', message.content or '')
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

    async def fetch_page(self, url):
        # No redirects or arbitrary product-provided hosts (SSRF/affiliate safety).
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8),
                                         headers={'User-Agent': 'GamerHQ-PriceComparison/1.0'}) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200 or 'text/html' not in response.headers.get('Content-Type', ''):
                    log.debug('[gocdkeys] skipped: product HTTP status %s', response.status)
                    return None
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 2_000_000: return None
                return raw.decode('utf-8', errors='replace')

    async def find_game_page(self, title):
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
            if now < self._next_request: return None
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

    async def update_companion(self, message, row, *, enable=False):
        if not row['response_message_id'] or row['guild_id'] != message.guild.id or row['channel_id'] != message.channel.id:
            return
        companion = await message.channel.fetch_message(row['response_message_id'])
        if (companion.author.id != message.guild.me.id
                or getattr(companion.reference, 'message_id', None) != message.id
                or companion.content not in {COPY, DISABLED_COPY}):
            log.warning('[gocdkeys] skipped: companion ownership/content mismatch')
            return
        if enable:
            kwargs = create_comparison_message(row['gocdkeys_url'])
            kwargs['suppress'] = kwargs.pop('suppress_embeds')
            await companion.edit(**kwargs)
        else:
            await companion.edit(content=DISABLED_COPY, view=None, allowed_mentions=discord.AllowedMentions.none(), suppress=True)
        affiliate_deals.finish(message.id, 'posted' if enable else 'disabled')

    async def handle(self, message):
        try:
            if not config.GOCDKEYS_ENABLED or not message.guild or message.guild.id != config.GUILD_ID:
                return
            source = deal_source(message)
            if not source:
                return
            channel = resolve(message.guild, 'gaming-deals', mapped_only=True)
            if not channel or message.channel.id != channel.id:
                return
            lock = _message_locks.setdefault(message.id, asyncio.Lock())
            async with lock:
                await self.process_deal(message, source)
        except Exception as exc:
            log.warning('[gocdkeys] skipped: handler error (%s)', type(exc).__name__)

    async def process_deal(self, message, source):
        row = affiliate_deals.record(message.id)
        state = price_state(message)
        title = extract_game_name(message)
        if row:
            if row['status'] == 'posted' and (state != 'paid' or not title or title != row['normalized_game']):
                await self.update_companion(message, row)
            elif row['status'] == 'disabled' and state == 'paid' and title == row['normalized_game']:
                # Revalidate before re-enabling the SAME companion, never post another.
                page = await self.find_game_page(title)
                if page and build_affiliate_url(page) == row['gocdkeys_url']:
                    await self.update_companion(message, row, enable=True)
            else:
                log.debug('[gocdkeys] skipped: already processed')
            return
        if state != 'paid':
            log.debug('[gocdkeys] skipped: free or unknown price')
            return
        if not title:
            log.debug('[gocdkeys] skipped: no game title found')
            return
        page = await self.find_game_page(title)
        if not page:
            log.debug('[gocdkeys] skipped: game not resolved')
            return
        url = build_affiliate_url(page)
        if not affiliate_deals.claim(message, url, title, source): return
        try:
            result = await message.reply(**create_comparison_message(url), mention_author=False)
        except Exception:
            affiliate_deals.finish(message.id, 'uncertain')
            log.warning('[gocdkeys] skipped: delivery failed; claim retained for manual review')
            return
        affiliate_deals.finish(message.id, 'posted', result.id)
        log.info('[gocdkeys] posted source message %s', message.id)


def create_comparison_message(url):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label='Compare Prices', emoji='🔎', url=url))
    return dict(content=COPY, view=view, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)


def status(guild, bot=None):
    from services.bot_group_service import member_id
    channel = resolve(guild, 'gaming-deals', mapped_only=True)
    configured = bool(channel and any(member_id(guild, source) for source in config.SUPPORTED_DEAL_SOURCES) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', config.GOCDKEYS_REFERRAL_CODE))
    intent = bool(getattr(getattr(bot, 'intents', None), 'message_content', False))
    enabled = config.GOCDKEYS_ENABLED and configured and intent
    detail = (f'Paid-deal watcher: {"Enabled" if enabled else "Disabled"}; '
              f'Gaming Deals: {channel.mention if channel else "Missing"}; '
              f'GoCDKeys: {"Configured" if configured else "Missing"}; '
              f'Referral: {"Configured" if config.GOCDKEYS_REFERRAL_CODE else "Missing"}. '
              'Optional product-page validation; no live lookup in health.')
    return ('GoCDKeys', 'PASS' if enabled else 'WARN' if config.GOCDKEYS_ENABLED else 'INFO', detail)
