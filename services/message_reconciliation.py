"""Read-only discovery and explicitly confirmed canonical-message cleanup."""
import logging
import re
import time

import discord
from database import db
from services.server_service import ServerMessageError

log = logging.getLogger(__name__)
SCAN_LIMIT = 1000


async def candidates(channel, *, content, recover_match=None, require_complete=True):
    if not channel.guild.me:
        raise ServerMessageError('MANUAL_REVIEW: bot identity unavailable.')
    found = {m.id: m async for m in channel.pins(limit=None)}
    count = 0
    async for message in channel.history(limit=SCAN_LIMIT + 1):
        count += 1
        found[message.id] = message
    if count > SCAN_LIMIT and require_complete:
        raise ServerMessageError('MANUAL_REVIEW: history scan limit reached; import the runtime DB or review this channel. No replacement posted.')
    return sorted((m for m in found.values()
                   if m.author.id == channel.guild.me.id
                   and m.type == discord.MessageType.default
                   and (m.content == content or (recover_match and recover_match(m)))), key=lambda m: m.id)


def assert_unreferenced(message_id, key, *, equivalent_keys=()):
    """Conservatively check all runtime references, including JSON; never print data."""
    # IDs embedded in hexadecimal fingerprints are not runtime references.
    pattern = re.compile(r'(?<![A-Za-z0-9])' + str(message_id) + r'(?![A-Za-z0-9])')
    with db.connect() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if table.startswith('sqlite_') or table.endswith('_audit'):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            for row in conn.execute('SELECT * FROM ' + quoted):
                data = dict(row)
                if table == 'settings' and data['key'] in (key, *equivalent_keys):
                    continue
                if table == 'managed_message_content' and data['setting_key'] == key:
                    continue
                if any(pattern.search(str(value)) for value in data.values() if value is not None):
                    raise ServerMessageError('MANUAL_REVIEW: message is referenced elsewhere; nothing removed or adopted.')


def boards(guild):
    """Existing owning renderers supply defaults; no second copy of canonical text."""
    from services import managed_message_service as managed
    from services.community_structure_service import core_channel, guide_text, EVENTS_INTRO
    from services.support_service import support_sections, section_channel, resource, message_key, support_text, PARTNER_CHANNELS
    from services.instant_gaming_service import CHANNELS, resolve, message_key as ig_key
    from cogs.server import future_community_copies
    result = {}
    for name, content in future_community_copies().items():
        result[f'server_future_{name}_message_id'] = (core_channel(guild, name), content)
    for name, key, content in [('guide', f'central_guide:{guild.id}', guide_text(guild)),
                               ('community-events', f'community_events:{guild.id}', EVENTS_INTRO)]:
        result[key] = (core_channel(guild, name), content)
    for section, content, _ in support_sections():
        if section == 'intro':
            content = support_text({name: target for name in ['support-gamerhq', *PARTNER_CHANNELS]
                                    if (target := resource(guild, name))})
        result[message_key(guild, section)] = (resource(guild, section_channel(section)), content)
    for name, (_, content) in CHANNELS.items():
        if name != 'gaming-deals':
            result[ig_key(guild, name)] = (resolve(guild, name), content)
    from services import role_panel_service as panels
    board = panels.channel(guild)
    if board:
        keys = panels.message_keys(guild, board)
        result[keys['intro']] = (board, panels.INTRO)
        for section, group, text in panels.SECTIONS:
            result[keys[section]] = (board, panels.panel_text(group, text))
    from services.onboarding_service import unique, welcome_text
    welcome = unique(guild.text_channels, 'welcome')
    if welcome and welcome.category and welcome.category.name:
        result[f'server_pinned_message_{welcome.id}'] = (welcome, welcome_text(guild))
    from cogs.suggestions import ENTRY_TEXT
    result[f'suggestions_entry:{guild.id}'] = (core_channel(guild, 'suggestions'), ENTRY_TEXT)
    from services.ticket_service import ENTRY_TEXT as ticket_text
    result[f'ticket_entry:{guild.id}'] = (core_channel(guild, 'need-support'), ticket_text)
    for state in managed.records(guild):
        if state['key'] in managed.specs(guild):
            result[state['key']] = (guild.get_channel(state['channel_id']), state['content'])
    return result


def fingerprint(message):
    from services.managed_message_service import digest
    return digest([message.content, [e.to_dict() for e in message.embeds],
                   [c.to_dict() for c in getattr(message, 'components', [])],
                   [a.id for a in getattr(message, 'attachments', [])]])


async def preview(guild, user, key):
    from services import managed_message_service as managed
    managed.require_admin(guild, user)
    channel, content = boards(guild).get(key, (None, None))
    if channel not in guild.text_channels:
        raise ServerMessageError('Select a known canonical message key with an existing target channel.')
    matches = await candidates(channel, content=content)
    if len(matches) != 2:
        raise ServerMessageError(f'MANUAL_REVIEW: {len(matches)} exact candidates. Cleanup supports one reviewed pair at a time; nothing changed.')
    if fingerprint(matches[0]) != fingerprint(matches[1]):
        raise ServerMessageError('MANUAL_REVIEW: bodies/buttons/embeds/attachments differ; automatic pair cleanup is unsafe.')
    for message in matches:
        assert_unreferenced(message.id, key)
    managed.require_admin(guild, user)
    return dict(guild_id=guild.id, actor_id=user.id, key=key, channel_id=channel.id,
                ids=[m.id for m in matches], hashes=[fingerprint(m) for m in matches],
                mapping=db.get_setting(key), state=managed.load(key), created=time.time())


async def confirm(guild, user, draft, keep_id, *, confirmed=False):
    from services import managed_message_service as managed
    managed.require_admin(guild, user)
    if (not confirmed or draft['guild_id'] != guild.id or draft['actor_id'] != user.id
            or keep_id not in draft['ids'] or time.time() - draft['created'] > 180):
        raise ServerMessageError('Fresh explicit confirmation by the preview author is required.')
    async with managed.lock(draft['key']):
        current = await preview(guild, user, draft['key'])
        for field in ('channel_id', 'ids', 'hashes', 'mapping', 'state'):
            if current[field] != draft[field]:
                raise ServerMessageError('State changed. Reopen duplicate review; nothing removed.')
        channel = guild.get_channel(draft['channel_id'])
        messages = [await channel.fetch_message(mid) for mid in draft['ids']]
        for message, expected in zip(messages, draft['hashes']):
            if message.author.id != guild.me.id or fingerprint(message) != expected:
                raise ServerMessageError('Message ownership/content changed; nothing removed.')
            assert_unreferenced(message.id, draft['key'])
        managed.require_admin(guild, user)
        remove = next(m for m in messages if m.id != keep_id)
        # Persist canonical identity BEFORE deletion. Failed/uncertain deletion is never retried here.
        with db.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (draft['key'], str(keep_id)))
            state = managed.load(draft['key'])
            if state:
                import json
                state.update(message_id=keep_id, version=state['version'] + 1)
                conn.execute('UPDATE managed_message_content SET state_json=? WHERE setting_key=?',
                             (json.dumps(state), draft['key']))
            conn.execute('INSERT INTO managed_message_audit (guild_id,actor_id,channel_id,setting_key,content_changed,buttons_changed,before_hash,after_hash,action,created_at) VALUES (?,?,?,?,0,0,?,?,?,?)',
                         (guild.id,user.id,channel.id,draft['key'],draft['hashes'][0],draft['hashes'][0],
                          f'duplicate_cleanup_requested:keep={keep_id}:remove={remove.id}',int(time.time())))
        try:
            await remove.delete()
        except discord.HTTPException:
            raise ServerMessageError('Canonical ID saved; deletion unconfirmed. Inspect health and open a fresh review; do not automatically retry.') from None
        log.info('Confirmed managed duplicate cleanup guild=%s actor=%s key=%s kept=%s removed=%s',
                 guild.id, user.id, draft['key'], keep_id, remove.id)
        return keep_id


async def diagnostics(guild):
    rows = []
    for key, (channel, content) in boards(guild).items():
        if channel not in guild.text_channels:
            continue
        try:
            raw = db.get_setting(key)
            current = None
            if raw and raw.isdigit():
                try:
                    current = await channel.fetch_message(int(raw))
                except discord.NotFound:
                    pass
            valid = current and guild.me and current.author.id == guild.me.id and current.content == content
            matches = await candidates(channel, content=content, require_complete=not valid)
            if valid and all(m.id != current.id for m in matches):
                matches.append(current)
            if len(matches) > 1:
                rows.append((key, 'MANUAL_REVIEW', f'Duplicate detected in channel {channel.id}: {len(matches)} candidates. Use /server message-duplicates.'))
            elif matches and raw != str(matches[0].id):
                rows.append((key, 'REPAIRABLE', 'Adoption available: missing/stale mapping. Owner Repair reuses the existing message.'))
            elif not matches:
                rows.append((key, 'REPAIRABLE', 'No exact default candidate; review stored/custom/legacy identity before repair.'))
        except (ServerMessageError, discord.HTTPException) as exc:
            rows.append((key, 'MANUAL_REVIEW', str(exc) if isinstance(exc, ServerMessageError) else 'Cannot inspect message history.'))
    return rows
