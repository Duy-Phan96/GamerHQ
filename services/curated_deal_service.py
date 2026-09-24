"""Admin-supplied deals; no product scraping or automatic pricing."""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
import ipaddress
import logging
import re
from urllib.parse import urlsplit

import discord
import config
from database import affiliate_deals
from services.game_area_cleanup import authorized
from services.instant_gaming_service import resolve
from services.managed_message_service import validate_url
from services.server_service import ServerMessageError
from services.gocdkeys_service import build_affiliate_url, valid_page_url

PARTNERS = {
    'amazon': ('Amazon', '🛒', 'View on Amazon'),
    'instant-gaming': ('Instant Gaming', '🎮', 'View on Instant Gaming'),
    'gocdkeys': ('GoCDKeys', '💰', 'Compare / View on GoCDKeys'),
    'other': ('Partner', '🔗', 'View Deal'),
}
AMAZON_HOSTS = {'amazon.' + suffix for suffix in ('de', 'com', 'co.uk', 'fr', 'it', 'es', 'nl',
                'pl', 'se', 'com.be', 'com.au', 'ca', 'co.jp', 'in', 'com.br', 'com.mx')}


def public_url(value):
    value = value.strip()
    validate_url(value)
    host = urlsplit(value).hostname.lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not host.endswith(('.local', '.localhost', '.internal')):
            return value
    raise ServerMessageError('Use a public HTTPS domain, not a local address or IP.')


def partner_url(partner, value):
    value = public_url(value)
    host = urlsplit(value).hostname.lower()
    domains = {'amazon': AMAZON_HOSTS | {'amzn.to', 'amzn.eu'},
               'instant-gaming': {'instant-gaming.com'}, 'gocdkeys': {'gocdkeys.com'}}
    if partner in domains and not any(host == d or host.endswith('.' + d) for d in domains[partner]):
        raise ServerMessageError('The URL domain does not match the selected partner.')
    # Reuse the established referral contract only for its supported product URLs.
    # Other manually verified provider URLs remain exact, with no guessed paths.
    if partner == 'gocdkeys' and valid_page_url(value):
        value = build_affiliate_url(value)
    return value


def price(value, *, optional=False):
    value = value.strip()
    if optional and not value:
        return None
    if not re.fullmatch(r'\d{1,7}(?:[.,]\d{1,2})?', value):
        raise ServerMessageError('Enter a non-negative EUR amount, e.g. 99.99 (no currency symbol or thousands separators).')
    return Decimal(value.replace(',', '.')).quantize(Decimal('0.01'))


@dataclass(frozen=True)
class Deal:
    partner: str
    title: str
    current_price: str
    regular_price: str
    url: str
    note: str = ''
    image_url: str = ''


def validate(deal):
    if deal.partner not in PARTNERS or not 1 <= len(deal.title.strip()) <= 200 or len(deal.note) > 500:
        raise ServerMessageError('Choose a supported partner, a title of 1–200 characters and a note up to 500 characters.')
    current, regular = price(deal.current_price), price(deal.regular_price, optional=True)
    if regular is not None and (regular <= 0 or current > regular):
        raise ServerMessageError('Regular price must be above zero and at least the current price, or leave it empty.')
    return Deal(deal.partner, deal.title.strip(), str(current), str(regular) if regular is not None else '',
                partner_url(deal.partner, deal.url), deal.note.strip(), public_url(deal.image_url) if deal.image_url else '')


def render(deal):
    deal = validate(deal)
    label, emoji, button = PARTNERS[deal.partner]
    embed = discord.Embed(title=f'{emoji} {label} Deal', description=discord.utils.escape_markdown(deal.title),
                          color=0x5865F2)
    embed.add_field(name='Now', value=deal.current_price + ' €')
    if deal.regular_price:
        regular, current = Decimal(deal.regular_price), Decimal(deal.current_price)
        embed.add_field(name='Regular', value=deal.regular_price + ' €')
        if current < regular:
            discount = ((regular-current) / regular * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            embed.add_field(name='Save', value=f'≈ {discount}%')
    if deal.note:
        embed.add_field(name='Note', value=discord.utils.escape_markdown(deal.note), inline=False)
    if deal.image_url:
        embed.set_image(url=deal.image_url)
    embed.set_footer(text='Affiliate / referral link')
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=button, emoji=emoji, url=deal.url))
    return dict(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


def target(guild, actor):
    if not guild or guild.id != config.GUILD_ID or not authorized(guild, actor):
        raise ServerMessageError('Owner or administrator access required in the configured server.')
    channel = resolve(guild, 'gaming-deals', mapped_only=True)
    if not channel or not guild.me or not all(getattr(channel.permissions_for(guild.me), bit)
            for bit in ('view_channel', 'send_messages', 'embed_links')):
        raise ServerMessageError('Managed gaming-deals or GamerHQ posting permissions missing. Run /server health.')
    return channel


async def publish(guild, actor, draft_id, expected_channel_id, deal):
    channel = target(guild, actor)
    if channel.id != expected_channel_id:
        raise ServerMessageError('The managed target changed. Create a fresh preview.')
    deal = validate(deal)
    payload = render(deal)
    if not affiliate_deals.claim_curated(draft_id, guild.id, channel.id, actor.id, asdict(deal)):
        return 'retained'
    try:
        message = await channel.send(**payload)
    except Exception as exc:
        affiliate_deals.finish_curated(draft_id, 'uncertain')
        logging.getLogger(__name__).warning('Curated deal delivery uncertain (%s); do not blindly retry.', type(exc).__name__)
        return 'uncertain'
    affiliate_deals.finish_curated(draft_id, 'posted', message.id)
    return 'posted'
