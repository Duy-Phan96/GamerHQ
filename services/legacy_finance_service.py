"""Read-only finance retirement checks; deletion is called only by owner Repair."""
import json

import discord

from database import db
from services.onboarding_service import alias
from services.music_bot_service import blocked_name


def candidates(guild):
    ids = {int(raw) for key in ('managed_channel', 'retired_partner_channel')
           if (raw := db.get_setting(f'{key}:{guild.id}:finanzberatung')) and raw.isdigit()}
    household = db.get_setting(f'managed_channel:{guild.id}:haushaltscheck')
    return [c for c in guild.text_channels
            if (c.id in ids or alias(c.name) == 'finanzberatung') and str(c.id) != household]


async def inspect(guild, channel):
    from services import managed_message_service as managed
    from services.support_service import RETIRED_HEADINGS
    identity_keys = {f'{prefix}:{guild.id}:finanzberatung'
                     for prefix in ('managed_channel', 'retired_partner_channel')}
    if not any(db.get_setting(key) == str(channel.id) for key in identity_keys):
        return 'managed channel identity is not recorded'
    if any(raw and raw.isdigit() and int(raw) != channel.id and guild.get_channel(int(raw))
           for key in identity_keys if (raw := db.get_setting(key))):
        return 'conflicting active and retired channel identities'
    if alias(channel.name) != 'finanzberatung':
        return 'recorded channel was renamed; owner intent is uncertain'
    if channel.category and blocked_name(channel.category):
        return 'channel is in a protected/private category'
    if db.get_setting(f'household_migration:{guild.id}'):
        return 'household migration is still pending'
    permissions = channel.permissions_for(guild.me)
    if not (permissions.view_channel and permissions.read_message_history and permissions.manage_threads):
        return 'insufficient permissions to inspect all history and private threads'
    with db.connect() as conn:
        # Channel IDs are globally unique. Preserve all resource references,
        # including closed ticket history; audit tables are historical only.
        for table in ('games', 'support_tickets', 'suggestions', 'lfg_events',
                      'lfg_event_messages', 'temp_voice_channels',
                      'streamer_profiles', 'streamer_channels'):
            fields = [r['name'] for r in conn.execute(f'PRAGMA table_info({table})')
                      if r['name'].endswith('channel_id') or r['name'] == 'category_id']
            for field in fields:
                if conn.execute(f'SELECT 1 FROM {table} WHERE {field}=?', (channel.id,)).fetchone():
                    return f'stored resource dependency: {table}.{field}'
        for row in conn.execute('SELECT key,value FROM settings'):
            if row['value'] == str(channel.id) and row['key'] not in identity_keys | {f'partner_manual_review:{guild.id}'}:
                return f'stored setting dependency: {row["key"]}'
        states = [json.loads(r['state_json']) for r in conn.execute('SELECT state_json FROM managed_message_content')]
    finance_key = f'partner_message:{guild.id}:finance'
    for state in states:
        if state.get('channel_id') == channel.id and state.get('key') != finance_key:
            return 'another managed message depends on this channel'
    state = managed.load(finance_key)
    ids = {int(raw) for key in (finance_key, f'retired_partner_message:{guild.id}:finance')
           if (raw := db.get_setting(key)) and raw.isdigit()}
    if state and state.get('channel_id') == channel.id:
        ids.add(state['message_id'])
    try:
        if any(t.parent_id == channel.id for t in await guild.active_threads()):
            return 'active threads exist'
        for private in (False, True):
            async for thread in channel.archived_threads(limit=None, private=private):
                return 'archived threads exist'
        async for message in channel.history(limit=None):
            if (message.type == discord.MessageType.pins_add and message.author.id == guild.me.id
                    and getattr(getattr(message, 'reference', None), 'message_id', None) in ids):
                continue
            if message.id not in ids or message.author.id != guild.me.id:
                return 'contains unexpected/manual content'
            if getattr(message, 'attachments', None) or message.embeds:
                return 'recorded message contains unexpected attachments/embeds'
            if state and state.get('message_id') == message.id:
                if state.get('customized') or state.get('pending') or not managed.owns(state, channel, message):
                    return 'contains customized or changed managed content'
            elif (message.content or '').split('\n', 1)[0] not in RETIRED_HEADINGS:
                return 'recorded message has an unrecognized fingerprint'
    except discord.HTTPException as exc:
        return f'cannot inspect all history/threads: {type(exc).__name__}'
    return None


async def repair(guild, changed):
    for candidate in candidates(guild):
        # Fetch current topology immediately before the read-only safety check.
        channel = next((c for c in await guild.fetch_channels() if c.id == candidate.id), None)
        if channel is None:
            continue
        reason = await inspect(guild, channel)
        if reason:
            changed.append(f'MANUAL_REVIEW: ⚠️ finanzberatung — {reason}')
            continue
        try:
            await channel.delete(reason='GamerHQ owner setup Repair: retire managed finanzberatung')
        except discord.HTTPException as exc:
            changed.append(f'MANUAL_REVIEW: ⚠️ finanzberatung — deletion failed: {type(exc).__name__}; retry Repair')
            continue
        with db.connect() as conn:
            for key in (f'managed_channel:{guild.id}:finanzberatung', f'partner_message:{guild.id}:finance'):
                conn.execute('DELETE FROM settings WHERE key=?', (key,))
            review_key = f'household_review:{guild.id}'
            row = conn.execute('SELECT value FROM settings WHERE key=?', (review_key,)).fetchone()
            if row:
                ids = [cid for cid in json.loads(row['value']) if int(cid) != channel.id]
                conn.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(ids), review_key))
            conn.execute('DELETE FROM settings WHERE key=? AND value=?',
                         (f'partner_manual_review:{guild.id}', str(channel.id)))
        changed.append('Deleted legacy channels: ✅ finanzberatung')
