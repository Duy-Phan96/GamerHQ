"""Bounded manual imports using existing provider validation and curated delivery."""
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import time
from weakref import WeakValueDictionary

import discord
from database import affiliate_deals
from services import curated_deal_service as curated
from services.gocdkeys_service import import_identity, promotion_link
from services.server_service import ServerMessageError

MAX_LINKS = 10
MAX_INPUT = 4000
_locks = WeakValueDictionary()
_next_send = {}


@dataclass(frozen=True)
class Entry:
    line: int
    source_url: str
    url: str = ''
    normalized_url: str = ''
    title: str = ''
    note: str = ''
    state: str = 'new'
    reason: str = ''
    promotion_type: str = 'DEAL'


@dataclass(frozen=True)
class Plan:
    channel_id: int
    entries: tuple[Entry, ...]
    giveaway_channel_id: int = 0


def known_links(guild_id):
    found = {}
    for row in affiliate_deals.curated_links(guild_id):
        data = json.loads(row['data_json'])
        key = data.get('normalized_url') or import_identity(data['url'])
        state = 'posted' if row['status'] == 'posted' else 'retained'
        if found.get(key) != 'posted':
            found[key] = state
    return found


def preview(guild, actor, text, selected_type='AUTO'):
    curated.authorize(guild, actor)
    targets = {'DEAL': 0, 'GIVEAWAY': 0}
    lines = [(index, line.strip()) for index, line in enumerate(text.splitlines(), 1) if line.strip()]
    if not lines or len(lines) > MAX_LINKS or len(text) > MAX_INPUT:
        raise ServerMessageError('Paste 1–10 links, one per line (maximum 4000 characters).')
    known, seen, entries = known_links(guild.id), set(), []
    for index, url in lines:
        try:
            validated, normalized, title, kind = promotion_link(url, selected_type)
        except (ServerMessageError, ValueError) as exc:
            # Do not echo rejected input: it could contain credentials.
            entries.append(Entry(index, '', state='invalid', reason=str(exc)))
            continue
        state = 'duplicate' if normalized in seen else known.get(normalized, 'new')
        seen.add(normalized)
        entries.append(Entry(index, url, validated, normalized, title or '', state=state, promotion_type=kind))
    for kind in {e.promotion_type for e in entries if e.state == 'new'}:
        targets[kind] = curated.target(guild, actor, kind).id
    return Plan(targets['DEAL'], tuple(entries), targets['GIVEAWAY'])


def draft_id(guild_id, normalized_url):
    return f'gocdkeys-import:{guild_id}:' + hashlib.sha256(normalized_url.encode()).hexdigest()


def render(entry):
    if not entry.title.strip() or len(entry.title) > 200 or '\n' in entry.title or len(entry.note) > 120:
        raise ServerMessageError('Each new link needs a title of 1–200 characters and an optional note up to 120 characters.')
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label='Compare Prices', emoji='💰', url=entry.url))
    text = f'🎮 **{discord.utils.escape_markdown(entry.title)}**\n\nCompare current game-key prices on GoCDKeys.'
    if entry.promotion_type == 'GIVEAWAY':
        view.children[0].label = 'Enter Giveaway'
        view.children[0].emoji = '🎁'
        text = f'# 🎁 {discord.utils.escape_markdown(entry.title)}\n\nA new partner giveaway is available.'
    if entry.note:
        text += '\n\n' + discord.utils.escape_markdown(entry.note)
    return dict(content=text + '\n\nAffiliate / referral link', view=view,
                allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)


async def publish(guild, actor, plan):
    curated.authorize(guild, actor)
    if not 1 <= len(plan.entries) <= MAX_LINKS:
        raise ServerMessageError('Target or batch changed; create a new import preview.')
    # Validate all candidate titles/URLs before posting the first one.
    for entry in plan.entries:
        if entry.state == 'new':
            url, normalized, _, _ = promotion_link(entry.url, entry.promotion_type)
            checked_target(guild, actor, plan, entry)
            if url != entry.url or normalized != entry.normalized_url:
                raise ServerMessageError('Referral or URL changed; create a fresh import preview.')
            render(entry)
    results = []
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        for entry in plan.entries:
            if entry.state != 'new':
                results.append((entry, entry.state))
                continue
            channel = checked_target(guild, actor, plan, entry)
            existing = known_links(guild.id).get(entry.normalized_url)
            if existing:
                results.append((entry, existing))
                continue
            delay = _next_send.get(guild.id, 0) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            channel = checked_target(guild, actor, plan, entry)
            data = asdict(entry) | {'partner': 'gocdkeys', 'workflow': 'import-gocdkeys'}
            state = await curated.deliver(guild, actor, draft_id(guild.id, entry.normalized_url), channel, render(entry), data)
            _next_send[guild.id] = time.monotonic() + 1.0
            results.append((entry, state if state != 'posted' else 'created'))
    return results


def checked_target(guild, actor, plan, entry):
    channel = curated.target(guild, actor, entry.promotion_type)
    expected = plan.giveaway_channel_id if entry.promotion_type == 'GIVEAWAY' else plan.channel_id
    if channel.id != expected:
        raise ServerMessageError('Target changed during import. Existing deliveries retained; preview again.')
    return channel
