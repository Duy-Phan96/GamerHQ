"""Read-only discovery and explicitly confirmed canonical-message cleanup."""
import json
import logging
import re
import time

import discord
from database import db
from services.server_service import ServerMessageError

log = logging.getLogger(__name__)
SCAN_LIMIT = 1000


async def history_snapshot(channel):
    if not channel.guild.me:
        raise ServerMessageError('MANUAL_REVIEW: bot identity unavailable.')
    found = {m.id: m async for m in channel.pins(limit=None)}
    count = 0
    async for message in channel.history(limit=SCAN_LIMIT + 1):
        count += 1
        found[message.id] = message
    return found, count


async def candidates(channel, *, content, recover_match=None, require_complete=True, snapshot=None):
    found, count = snapshot if snapshot is not None else await history_snapshot(channel)
    if count > SCAN_LIMIT and require_complete:
        raise ServerMessageError('MANUAL_REVIEW: history scan limit reached; import the runtime DB or review this channel. No replacement posted.')
    return sorted((m for m in found.values()
                   if m.author.id == channel.guild.me.id
                   and m.type == discord.MessageType.default
                   and (canonical_equal(m.content, content) or (recover_match and recover_match(m)))), key=lambda m: m.id)


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


def boards(guild, bot=None, *, warnings=None):
    from services.managed_message_service import canonical_boards
    return canonical_boards(guild, bot, warnings=warnings)


def fingerprint(message):
    from services.managed_message_service import digest
    return digest([message.content, [e.to_dict() for e in message.embeds],
                   [c.to_dict() for c in getattr(message, 'components', [])],
                   [a.id for a in getattr(message, 'attachments', [])]])


SECTION_PREFIX = 'choose_games_section_message_ids:'


def canonical_equal(left, right):
    # Only line-ending differences are accepted; no fuzzy prose/heading match.
    return left.replace('\r\n', '\n').rstrip('\n') == right.replace('\r\n', '\n').rstrip('\n')


def mapping(key):
    if key.startswith(SECTION_PREFIX):
        raw = db.get_setting('choose_games_section_message_ids') or '{}'
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ServerMessageError('MANUAL_REVIEW: legacy selector mapping needs owner repair before cleanup.')
        return str(value.get(key[len(SECTION_PREFIX):], ''))
    return db.get_setting(key)


def references(message_id, key):
    if key.startswith(SECTION_PREFIX):
        values = json.loads(db.get_setting('choose_games_section_message_ids') or '{}')
        slot = key[len(SECTION_PREFIX):]
        if not isinstance(values, dict) or any(str(v) == str(message_id) for k, v in values.items() if k != slot):
            raise ServerMessageError('MANUAL_REVIEW: selector message referenced elsewhere.')
        assert_unreferenced(message_id, key, equivalent_keys=('choose_games_section_message_ids',))
    else:
        assert_unreferenced(message_id, key, equivalent_keys=equivalent_keys(key))


def equivalent_keys(key):
    # Existing support migration alias refers to the same overview, not a second board.
    match = re.fullmatch(r'partner_message:(\d+):intro', key)
    return (f'support_message:{match.group(1)}',) if match else ()


def controls(message):
    from services.managed_message_service import digest
    return digest([[e.to_dict() for e in message.embeds],
                   [c.to_dict() for c in getattr(message, 'components', [])],
                   [a.id for a in getattr(message, 'attachments', [])]])


async def inspect_board(guild, key, channel, content, *, snapshot=None):
    from services import managed_message_service as managed
    raw = mapping(key)
    matches = await candidates(channel, content=content, snapshot=snapshot)
    if raw and raw.isdigit() and all(str(m.id) != raw for m in matches):
        try:
            current = await channel.fetch_message(int(raw))
            if (guild.me and current.author.id == guild.me.id and current.type == discord.MessageType.default
                    and canonical_equal(current.content, content)):
                matches.append(current)
        except discord.NotFound:
            pass
    matches.sort(key=lambda m: m.id)
    state = managed.load(key)
    safe = len(matches) > 1 and len({controls(m) for m in matches}) == 1
    reason = 'MANUAL_REVIEW: bodies/buttons/embeds/attachments differ.'
    # Never overwrite unresolved customization or accept a moved state record.
    if state and (state.get('pending') or state.get('channel_id') != channel.id
                  or state.get('guild_id') != guild.id or state.get('key') != key
                  or managed.digest(state['content']) != state.get('content_hash')
                  or any(m.content != state['content'] for m in matches)):
        safe = False
        reason = 'MANUAL_REVIEW: customization state needs reconciliation.'
    recommended = None
    if safe:
        persisted = next((m for m in matches if str(m.id) == raw), None)
        if persisted and (not state or managed.owns(state, channel, persisted)):
            recommended, reason = persisted.id, 'Current valid persisted DB mapping.'
        else:
            exact = [m for m in matches if m.content == content]
            if exact:
                recommended, reason = exact[0].id, 'Exact canonical content and matching controls; oldest exact candidate on a tie.'
            else:
                recommended, reason = matches[0].id, 'Only line endings differ from canonical; retain the older original.'
    label = managed.specs(guild).get(key, (content.split('\n', 1)[0].lstrip('# '),))[0]
    return dict(key=key, label=label, channel_id=channel.id, matches=matches, mapping=raw,
                state=state, safe=safe, recommended=recommended, reason=reason)


async def audit(guild, user=None, *, bot=None, managed_key=None):
    """Read-only global inventory. Bounded history reads in expected channels only."""
    from services import managed_message_service as managed
    if user is not None:
        managed.require_admin(guild, user)
    warnings = []
    definitions = boards(guild, bot, warnings=warnings)
    if managed_key is not None:
        if managed_key not in definitions:
            raise ServerMessageError('Select a known canonical managed key.')
        definitions = {managed_key: definitions[managed_key]}
    rows = [dict(key='structure', label=warning, channel_id=None, matches=[], status='MANUAL_REVIEW', reason=warning) for warning in dict.fromkeys(warnings)]
    snapshots = {}
    for key, (channel, content) in definitions.items():
        if channel not in guild.text_channels or content is None:
            continue
        try:
            if channel.id not in snapshots:
                snapshots[channel.id] = await history_snapshot(channel)
            row = await inspect_board(guild, key, channel, content, snapshot=snapshots[channel.id])
            row['status'] = 'DUPLICATE' if len(row['matches']) > 1 else ('UNIQUE' if row['matches'] else 'MISSING')
        except (ServerMessageError, discord.HTTPException, ValueError) as exc:
            row = dict(key=key, label=key, channel_id=channel.id, matches=[], status='MANUAL_REVIEW',
                       reason=str(exc) if isinstance(exc, ServerMessageError) else 'Cannot completely inspect this board.')
        rows.append(row)
    if user is not None:
        managed.require_admin(guild, user)
    return rows


async def preview(guild, user, key, *, bot=None):
    from services import managed_message_service as managed
    managed.require_admin(guild, user)
    channel, content = boards(guild, bot).get(key, (None, None))
    if channel not in guild.text_channels or content is None:
        raise ServerMessageError('Select a known canonical message key with an existing target channel.')
    row = await inspect_board(guild, key, channel, content)
    matches = row['matches']
    if len(matches) < 2:
        raise ServerMessageError(f'MANUAL_REVIEW: {len(matches)} candidates; nothing to remove.')
    if not row['safe']:
        raise ServerMessageError(row['reason'])
    # Larger identical groups are reviewed one pair at a time, never bulk deleted.
    preferred = next(m for m in matches if m.id == row['recommended'])
    pair = sorted([preferred, next(m for m in matches if m.id != preferred.id)], key=lambda m: m.id)
    for message in pair:
        references(message.id, key)
    managed.require_admin(guild, user)
    metadata = [dict(id=m.id, created=discord.utils.snowflake_time(m.id).isoformat(),
                     edited=m.edited_at.isoformat() if getattr(m, 'edited_at', None) else 'Never',
                     mapped=str(m.id) == row['mapping'], preview=m.content[:180]) for m in pair]
    return dict(guild_id=guild.id, actor_id=user.id, key=key, channel_id=channel.id,
                ids=[m.id for m in pair], hashes=[fingerprint(m) for m in pair],
                group_ids=[m.id for m in matches], group_hashes=[fingerprint(m) for m in matches],
                mapping=row['mapping'], aliases={k: db.get_setting(k) for k in equivalent_keys(key)},
                state=row['state'], created=time.time(), label=row['label'],
                recommended=row['recommended'], reason=row['reason'], metadata=metadata)


async def confirm(guild, user, draft, keep_id, *, confirmed=False, bot=None):
    from services import managed_message_service as managed
    managed.require_admin(guild, user)
    if (not confirmed or draft['guild_id'] != guild.id or draft['actor_id'] != user.id
            or keep_id not in draft['ids'] or time.time() - draft['created'] > 180):
        raise ServerMessageError('Fresh explicit confirmation by the preview author is required.')
    lock_key = (f"choose_games_refresh:{draft['channel_id']}" if draft['key'].startswith(('choose_games_section_message_ids:', 'choose_games_message_id')) else draft['key'])
    async with managed.lock(lock_key):
        current = await preview(guild, user, draft['key'], bot=bot)
        for field in ('channel_id', 'ids', 'hashes', 'group_ids', 'group_hashes', 'mapping', 'aliases', 'state'):
            if current[field] != draft[field]:
                raise ServerMessageError('State changed. Reopen duplicate review; nothing removed.')
        channel = guild.get_channel(draft['channel_id'])
        messages = [await channel.fetch_message(mid) for mid in draft['ids']]
        for message, expected in zip(messages, draft['hashes']):
            if message.channel.id != channel.id or message.author.id != guild.me.id or fingerprint(message) != expected:
                raise ServerMessageError('Message ownership/content changed; nothing removed.')
            references(message.id, draft['key'])
        managed.require_admin(guild, user)
        remove = next(m for m in messages if m.id != keep_id)
        kept_hash = draft['hashes'][draft['ids'].index(keep_id)]
        # Persist canonical identity BEFORE deletion. Failed/uncertain deletion is never retried here.
        with db.connect() as conn:
            if draft['key'].startswith(SECTION_PREFIX):
                values = json.loads(db.get_setting('choose_games_section_message_ids') or '{}')
                values[draft['key'][len(SECTION_PREFIX):]] = keep_id
                conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('choose_games_section_message_ids', json.dumps(values)))
            else:
                conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (draft['key'], str(keep_id)))
            for key, value in draft['aliases'].items():
                if value in {str(mid) for mid in draft['ids']}:
                    conn.execute('UPDATE settings SET value=? WHERE key=?', (str(keep_id), key))
            state = managed.load(draft['key'])
            if state:
                state.update(message_id=keep_id, version=state['version'] + 1)
                conn.execute('UPDATE managed_message_content SET state_json=? WHERE setting_key=?',
                             (json.dumps(state), draft['key']))
            conn.execute('INSERT INTO managed_message_audit (guild_id,actor_id,channel_id,setting_key,content_changed,buttons_changed,before_hash,after_hash,action,created_at) VALUES (?,?,?,?,0,0,?,?,?,?)',
                         (guild.id,user.id,channel.id,draft['key'],kept_hash,kept_hash,
                          f'duplicate_cleanup_requested:keep={keep_id}:remove={remove.id}',int(time.time())))
        try:
            await remove.delete()
        except discord.HTTPException:
            raise ServerMessageError('Canonical ID saved; deletion unconfirmed. Inspect health and open a fresh review; do not automatically retry.') from None
        log.info('Confirmed managed duplicate cleanup guild=%s actor=%s key=%s kept=%s removed=%s',
                 guild.id, user.id, draft['key'], keep_id, remove.id)
        return keep_id


async def diagnostics(guild, bot=None):
    rows = []
    inventory = await audit(guild, bot=bot)
    duplicates = sum(row['status'] == 'DUPLICATE' for row in inventory)
    if duplicates:
        rows.append(('Managed Messages', 'MANUAL_REVIEW', f'{duplicates} duplicate groups detected. Run /server message-duplicates.'))
    for row in inventory:
        key, status = row['key'], row['status']
        if status == 'DUPLICATE':
            rows.append((key, 'MANUAL_REVIEW', f"Duplicate detected in channel {row['channel_id']}: {len(row['matches'])} candidates. Use /server message-duplicates."))
        elif status == 'MANUAL_REVIEW':
            rows.append((key, status, row['reason']))
        elif status == 'UNIQUE' and row['mapping'] != str(row['matches'][0].id):
            rows.append((key, 'RECONCILE', 'Adoption available: existing message needs linking. Run /server reconcile.'))
        elif status == 'MISSING':
            rows.append((key, 'REPAIRABLE', 'No exact default candidate; review stored/custom/legacy identity before repair.'))
    return rows
