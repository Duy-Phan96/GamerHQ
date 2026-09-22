"""Canonical custom content layered onto server_service's existing message IDs.

Only explicitly supported public boards are editable. A single running bot uses
the same per-key lock for refresh and edits; versions reject stale UI sessions.
"""
import asyncio
import copy
import hashlib
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit

import discord
from database import db
from services.server_service import ServerMessageError

_locks = {}
ACTIONS = {
    'HOUSEHOLD_CHECK_REQUEST': ('🔍 Haushaltscheck anfragen', 'gamerhq:offers:household-check'),
    'CREATE_SUPPORT_TICKET': ('Create Support Ticket', 'gamerhq:tickets:create'),
    'SUBMIT_SUGGESTION': ('Submit Suggestion', 'gamerhq:suggestions:submit'),
}


def lock(key):
    return _locks.setdefault(key, asyncio.Lock())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def specs(guild):
    from services.support_service import message_key, section_channel
    result = {}
    for section, label in [('intro', 'Benefits Overview'), ('direct', 'Direct Support'),
                           ('amazon', 'Amazon'), ('household', 'Haushaltscheck'),
                           ('instant_gaming', 'Gaming Deals'), ('pixverse', 'AI Tools')]:
        action = 'HOUSEHOLD_CHECK_REQUEST' if section == 'household' else None
        result[message_key(guild, section)] = (label, f'managed_channel:{guild.id}:{section_channel(section)}', [action] if action else [])
    for key, name, label, action in [('central_guide', 'guide', 'Guide', None),
                                    ('suggestions_entry', 'suggestions', 'Suggestions', 'SUBMIT_SUGGESTION'),
                                    ('ticket_entry', 'need-support', 'Need Support', 'CREATE_SUPPORT_TICKET')]:
        result[f'{key}:{guild.id}'] = (label, f'managed_channel:{guild.id}:{name}', [action] if action else [])
    welcome = db.get_setting(f'onboarding:{guild.id}:welcome')
    if welcome and welcome.isdigit():
        result[f'server_pinned_message_{welcome}'] = ('Welcome', f'onboarding:{guild.id}:welcome', [])
    return result


def authorized(guild, user):
    if not guild or not user:
        return False
    # Re-read cached member state on every component/modal/save interaction.
    member = guild.get_member(user.id)
    return bool(member and (member.id == guild.owner_id or member.guild_permissions.administrator))


def require_admin(guild, user):
    if not authorized(guild, user):
        raise ServerMessageError('Only the server owner or an administrator may edit managed messages.')


def load(key):
    with db.connect() as conn:
        row = conn.execute('SELECT state_json FROM managed_message_content WHERE setting_key=?', (key,)).fetchone()
    return json.loads(row['state_json']) if row else None


def store(state, audit=None):
    with db.connect() as conn:
        conn.execute('INSERT INTO managed_message_content VALUES (?,?,?) ON CONFLICT(setting_key) DO UPDATE SET state_json=excluded.state_json',
                     (state['key'], state['guild_id'], json.dumps(state, ensure_ascii=False)))
        if audit:
            conn.execute('INSERT INTO managed_message_audit (guild_id,actor_id,channel_id,setting_key,content_changed,buttons_changed,before_hash,after_hash,action,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', audit)


def records(guild):
    with db.connect() as conn:
        rows = conn.execute('SELECT state_json FROM managed_message_content WHERE guild_id=?', (guild.id,)).fetchall()
    return [state for row in rows if not (state := json.loads(row['state_json'])).get('retired')]


def validate_url(url):
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname.encode('idna').decode() if parsed.hostname else ''
        valid_host = bool(re.fullmatch(r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', hostname))
        valid = (len(url) <= 512 and parsed.scheme == 'https' and parsed.hostname and '.' in parsed.hostname
                 and valid_host and not parsed.username and not parsed.password and not re.search(r'[\s\\<>]', url))
        parsed.port
        sensitive = re.compile(r'token|secret|password|credential|authorization|api.?key|signature', re.I)
        if not valid or any(sensitive.search(key) for key, _ in parse_qsl(parsed.query) + parse_qsl(parsed.fragment)):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise ServerMessageError('Use a valid public HTTPS URL (maximum 512 characters), without credentials or secret query parameters.') from None


def validate(guild, key, content, buttons):
    spec = specs(guild).get(key)
    if not spec:
        raise ServerMessageError('This message is not an editable GamerHQ board.')
    if not isinstance(content, str) or not content.strip() or len(content.encode('utf-16-le')) // 2 > 2000:
        raise ServerMessageError('Content must contain 1–2000 Discord characters. Nothing was saved.')
    if not isinstance(buttons, list) or len(buttons) > 25:
        raise ServerMessageError('A message supports at most 25 buttons.')
    seen = set()
    for b in buttons:
        if not isinstance(b, dict) or set(b) != {'label', 'emoji', 'type', 'target', 'enabled'}:
            raise ServerMessageError('Malformed button configuration.')
        if not isinstance(b['label'], str) or not b['label'].strip() or len(b['label'].encode('utf-16-le')) // 2 > 80:
            raise ServerMessageError('Button labels must contain 1–80 characters.')
        if type(b['enabled']) is not bool or not isinstance(b['emoji'], str) or len(b['emoji']) > 100:
            raise ServerMessageError('Invalid button enabled/emoji setting.')
        if b['emoji']:
            custom = re.fullmatch(r'<a?:\w{2,32}:(\d{15,22})>', b['emoji'])
            unicode_emoji = (len(b['emoji']) <= 16 and
                             re.fullmatch(r'[\U0001F000-\U0001FAFF\u2190-\u23FF\u2600-\u27BF\u2B00-\u2BFF\u00A9\u00AE\u203C\u2049\u2122\u2139\u3030\u303D\u3297\u3299\uFE0F\u200D\u20E3\d#*]+', b['emoji'])
                             and any(ord(c) > 127 for c in b['emoji']))
            if custom:
                emoji = guild.get_emoji(int(custom.group(1)))
                if not emoji or not emoji.is_usable():
                    raise ServerMessageError('Select a custom emoji from this server that the bot can use.')
            elif not unicode_emoji:
                raise ServerMessageError('Use a Unicode emoji or an available custom emoji from this server.')
        if not isinstance(b['target'], str):
            raise ServerMessageError('Invalid button target.')
        if b['type'] == 'LINK':
            validate_url(b['target'])
        elif b['type'] == 'ACTION' and b['target'] in spec[2] and b['target'] not in seen:
            seen.add(b['target'])
        else:
            raise ServerMessageError('Choose a unique allowlisted action for this message; arbitrary callbacks are not allowed.')


def serialize_view(view):
    result = []
    for item in view.children if view else []:
        action = next((key for key, (_, cid) in ACTIONS.items() if cid == item.custom_id), None)
        if not isinstance(item, discord.ui.Button) or (not item.url and not action):
            raise ServerMessageError('Unsupported managed component; review the board configuration.')
        result.append(dict(label=item.label, emoji=str(item.emoji) if item.emoji else '',
                           type='LINK' if item.url else 'ACTION', target=item.url or action, enabled=not item.disabled))
    return result


def render(buttons, *, preview=False):
    view = discord.ui.View(timeout=180 if preview else None)
    for index, config in enumerate(buttons):
        if config['type'] == 'ACTION' and not preview:
            from cogs.tickets import SupportOffers, TicketEntry
            from cogs.suggestions import SuggestionEntryView
            action = config['target']
            source = TicketEntry() if action == 'CREATE_SUPPORT_TICKET' else SuggestionEntryView() if action == 'SUBMIT_SUGGESTION' else SupportOffers()
            original = next(b for b in source.children if b.custom_id == ACTIONS[action][1])
            item = discord.ui.Button(style=original.style, custom_id=original.custom_id)
            item.callback = original.callback
        elif config['type'] == 'LINK' and not preview:
            item = discord.ui.Button(style=discord.ButtonStyle.link, url=config['target'])
        else:
            # Preview controls cannot open links or trigger real ticket actions.
            item = discord.ui.Button(style=discord.ButtonStyle.secondary, custom_id=f'preview:{index}')
        item.label, item.emoji = config['label'], config['emoji'] or None
        item.disabled, item.row = preview or not config['enabled'], index // 5
        view.add_item(item)
    return view


def owns(state, channel, message):
    return bool(isinstance(state, dict) and state.get('guild_id') == channel.guild.id and state.get('channel_id') == channel.id
                and state.get('message_id') == message.id and channel.guild.me
                and message.author.id == channel.guild.me.id
                and digest(message.content) == state.get('content_hash'))


def mapped(guild, state):
    spec = specs(guild).get(state['key'])
    return bool(spec and str(state['channel_id']) == db.get_setting(spec[1])
                and str(state['message_id']) == db.get_setting(state['key']))


async def inspect(guild, state):
    if state['key'].startswith('partner_message:') and any(db.get_setting(f'{name}:{guild.id}') for name in ('partner_reorder', 'partner_split', 'household_migration')):
        raise ServerMessageError('Partner migration is pending. Complete owner setup Repair before editing.')
    if not mapped(guild, state):
        raise ServerMessageError('Managed mapping changed or is missing. Run health and owner setup Repair.')
    if any(other['key'] != state['key'] and (other['channel_id'], other['message_id']) == (state['channel_id'], state['message_id'])
           for other in records(guild)):
        raise ServerMessageError('Multiple managed keys refer to this message. Manual review required.')
    channel = guild.get_channel(state['channel_id'])
    if channel not in guild.text_channels:
        raise ServerMessageError('Managed channel is missing.')
    message = await channel.fetch_message(state['message_id'])
    if (not owns(state, channel, message) or not message.pinned or state.get('pending')
            or digest(state['content']) != state['content_hash']):
        raise ServerMessageError('Message ownership, fingerprint, pin or pending delivery needs repair. Reopen after health/setup.')
    return message


async def available(guild):
    result = []
    for state in records(guild):
        try:
            validate(guild, state['key'], state['content'], state['buttons'])
            await inspect(guild, state)
        except (ServerMessageError, discord.HTTPException, KeyError, TypeError, ValueError):
            continue
        result.append(state)
    return result


async def health(guild, *, messages=True):
    """Read-only checks; never bootstrap missing registrations during a scan."""
    issues = []
    seen = set()
    try:
        states = records(guild)
        registered = {state['key'] for state in states}
        for key in specs(guild):
            if db.get_setting(key) and key not in registered:
                issues.append('A mapped board is not registered yet; refresh its defaults through owner setup Repair.')
        for state in states:
            try:
                validate(guild, state['key'], state['content'], state['buttons'])
                validate(guild, state['key'], state['default_content'], state['default_buttons'])
                identity = (state['channel_id'], state['message_id'])
                if identity in seen or not mapped(guild, state) or not guild.get_channel(state['channel_id']) or state.get('pending'):
                    raise ServerMessageError('Registry mapping or delivery needs review.')
                seen.add(identity)
                if messages:
                    await inspect(guild, state)
            except (ServerMessageError, discord.HTTPException, KeyError, TypeError, ValueError):
                issues.append('A managed board has invalid configuration, ownership, mapping, pin or pending delivery.')
    except (KeyError, TypeError, ValueError):
        issues.append('Managed content storage is malformed; manual review required.')
    return issues


async def save(guild, user, draft, *, reset=False, confirmed=False):
    require_admin(guild, user)
    if not confirmed:
        raise ServerMessageError('Preview and explicitly confirm before saving or resetting.')
    key = draft['key']
    async with lock(key):
        state = load(key)
        if not state or state['version'] != draft['version']:
            raise ServerMessageError('This edit is stale. Reopen the editor to load the latest version.')
        message = await inspect(guild, state)
        require_admin(guild, user)
        updated = copy.deepcopy(state)
        updated['content'] = state['default_content'] if reset else draft['content']
        updated['buttons'] = copy.deepcopy(state['default_buttons'] if reset else draft['buttons'])
        validate(guild, key, updated['content'], updated['buttons'])
        view = render(updated['buttons'])
        updated.update(customized=not reset, version=state['version'] + 1, pending=True)
        # Durable intent before Discord: failed/uncertain HTTP is recovered by
        # normal refresh, never by recreating a pin from an editor interaction.
        audit = (guild.id, user.id, state['channel_id'], key,
                 state['content'] != updated['content'], state['buttons'] != updated['buttons'],
                 digest([state['content'], state['buttons']]), digest([updated['content'], updated['buttons']]),
                 'reset' if reset else 'edit', int(time.time()))
        store(updated, audit)
        try:
            await message.edit(content=updated['content'], embed=None, view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            if exc.status == 400:
                # A definite validation rejection did not update Discord. Restore
                # the prior canonical state so the editor remains usable.
                state['version'] = updated['version']
                rejection = (guild.id, user.id, state['channel_id'], key, False, False,
                             audit[7], audit[6], 'delivery-rejected', int(time.time()))
                store(state, rejection)
                raise ServerMessageError('Discord rejected the content or components. Previous content was restored; reopen the editor and correct the draft.') from None
            raise ServerMessageError('Changes are stored, but Discord delivery is unconfirmed. Run setup Repair; do not repeat this save.') from None
        updated.update(pending=False, content_hash=digest(updated['content']))
        store(updated)
        return updated
