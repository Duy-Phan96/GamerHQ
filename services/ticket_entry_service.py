"""Read-only support-entry diagnosis and explicitly confirmed identity repair.

Only the two existing public ticket entries are in scope. Never create/delete a
message or channel, relax permissions, or trust an action custom ID by itself.
Canonical content, customization and audit remain in managed_message_service.
"""
from __future__ import annotations

import copy
import json
import logging
import time

import discord
from database import db
from services import managed_message_service as managed
from services import message_reconciliation as reconciliation
from services.operation_context import read_scope
from services.server_service import ServerMessageError

log = logging.getLogger(__name__)
KINDS = ('support', 'electricity')


def definition(guild, kind):
    from services import support_service as support, ticket_service as tickets
    from cogs.tickets import TicketEntry, SupportOffers
    if kind == 'support':
        return dict(kind=kind, label='Need Support', name='need-support',
                    key=f'ticket_entry:{guild.id}', channel_key=tickets.resource_key(guild, 'need-support'),
                    content=tickets.ENTRY_TEXT, view=TicketEntry(), action='CREATE_SUPPORT_TICKET')
    if kind == 'electricity':
        return dict(kind=kind, label='Electricity', name='electricity',
                    key=support.message_key(guild, 'household'),
                    channel_key=support.channel_key(guild, 'electricity'),
                    content=support.ELECTRICITY_TEXT, view=SupportOffers('household'), action='ELECTRICITY_REQUEST')
    raise ValueError('Choose a supported ticket entry.')


def number(value):
    return int(value) if str(value or '').isdigit() and int(value) > 0 else None


def _source_ok(guild, message):
    return bool(guild and guild.me and message
                and getattr(getattr(message, 'author', None), 'id', None) == guild.me.id
                and getattr(message, 'webhook_id', None) is None
                and getattr(message, 'type', None) == discord.MessageType.default
                and getattr(getattr(message, 'guild', None), 'id', None) == guild.id)


def _state(guild, spec):
    state = managed.load(spec['key'])
    if state is not None:
        if not isinstance(state, dict) or state.get('guild_id') != guild.id or state.get('key') != spec['key']:
            raise ServerMessageError('The saved entry belongs to a different server or is invalid.')
        if state.get('retired') or state.get('pending'):
            raise ServerMessageError('The saved entry is retired or has unconfirmed delivery. Review it without replacing it.')
        if managed.digest(state.get('content')) != state.get('content_hash'):
            raise ServerMessageError('The saved content fingerprint is inconsistent. Preserve the entry for review.')
        managed.validate(guild, spec['key'], state['content'], state['buttons'])
        if not any(b['type'] == 'ACTION' and b['target'] == spec['action'] and b['enabled'] for b in state['buttons']):
            raise ServerMessageError('This entry action was disabled or removed in Managed Messages; restore it there only after review.')
    return state


def binding_problem(interaction, kind):
    """Fast callback gate; a healthy support button can still open a modal directly."""
    guild, message = interaction.guild, interaction.message
    if not guild or not _source_ok(guild, message):
        return 'SOURCE'
    spec = definition(guild, kind)
    from services.channel_change_service import removed
    if removed(guild, spec['name']):
        return 'REMOVED'
    if number(db.get_setting(spec['channel_key'])) != interaction.channel_id:
        return 'CHANNEL'
    if number(db.get_setting(spec['key'])) != message.id:
        return 'MESSAGE'
    try:
        state = _state(guild, spec)
        if state is None:
            return 'REGISTRY'
        if not managed.owns(state, message.channel, message):
            return 'FINGERPRINT'
    except (ServerMessageError, KeyError, TypeError, ValueError):
        return 'REGISTRY'
    return None


def _payload_matches(guild, spec, state, channel, message):
    if not _source_ok(guild, message) or message.channel.id != channel.id:
        return False
    if state:
        if not managed.owns(state, channel, message):
            return False
        buttons = state['buttons']
    else:
        if not reconciliation.canonical_equal(message.content, spec['content']):
            return False
        buttons = managed.serialize_view(spec['view'])
    # Rebinding never silently changes customized controls or imports arbitrary ones.
    return (not message.embeds and not getattr(message, 'attachments', ())
            and [part.to_dict() for part in getattr(message, 'components', ())]
            == managed.render(buttons).to_components())


def _snapshot(guild, spec):
    return dict(channel=db.get_setting(spec['channel_key']), message=db.get_setting(spec['key']),
                state=copy.deepcopy(managed.load(spec['key'])), bot_id=guild.me.id if guild.me else None)


def _channel_signature(channel):
    return (channel.id, channel.name, channel.category_id,
            tuple(sorted((target.id, *[p.value for p in overwrite.pair()])
                         for target, overwrite in channel.overwrites.items())))


async def diagnose(guild, kind, *, hint=None):
    """Bounded read-only review. Hint is a clicked channel/message, never a name."""
    with db.read_only(), read_scope():
        return await _diagnose(guild, kind, hint=hint)


async def _diagnose(guild, kind, *, hint=None):
    spec = definition(guild, kind)
    row = dict(kind=kind, label=spec['label'], ready=False, repairable=False,
               reason='', checks=[], hint=hint, candidate=None)
    try:
        if not guild.me or getattr(guild, 'unavailable', False):
            raise ServerMessageError('Bot membership or the server is unavailable. Try again after reconnecting.')
        from services.channel_change_service import removed
        if removed(guild, spec['name']):
            raise ServerMessageError('This entry channel was intentionally removed. Use the reviewed resource-restore flow.')
        row['saved'] = _snapshot(guild, spec)
        state = _state(guild, spec)
        channel_id = number(row['saved']['channel'])
        state_channel = number(state.get('channel_id')) if state else None
        choices = {n for n in (channel_id, state_channel, hint[0] if hint else None) if n}
        row['checks'] = [f'Channel mapping: {"present" if channel_id else "missing / invalid"}',
                         f'Message mapping: {"present" if number(row["saved"]["message"]) else "missing / invalid"}',
                         f'Managed record: {"present" if state else "missing"}']
        if len(choices) != 1:
            raise ServerMessageError('Channel evidence is missing or disagrees. Open this check from the actual entry button; conflicting mappings require owner review.')
        channel = await guild.fetch_channel(choices.pop())
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
            raise ServerMessageError('The recorded entry is not a text channel on this server.')
        permissions = channel.permissions_for(guild.me)
        if not (permissions.view_channel and permissions.read_message_history):
            raise ServerMessageError('The bot needs View Channel and Read Message History on this entry channel.')
        with db.connect() as conn:
            aliases = conn.execute('SELECT key FROM settings WHERE value=? AND key LIKE ?',
                                   (str(channel.id), f'managed_channel:{guild.id}:%')).fetchall()
        if any(r['key'] != spec['channel_key'] for r in aliases):
            raise ServerMessageError('This channel is mapped to another managed resource. Review that conflict first.')
        # Heading lookalikes make the result ambiguous, but are NEVER trusted for repair.
        heading = (state['content'] if state else spec['content']).split('\n', 1)[0]
        action_id = managed.ACTIONS[spec['action']][1]
        def candidate(message):
            components = [part.to_dict() for part in getattr(message, 'components', ())]
            action = any(b.get('custom_id') == action_id for part in components for b in part.get('components', []))
            return (_payload_matches(guild, spec, state, channel, message)
                    or message.content.split('\n', 1)[0] == heading or action)
        matches = await reconciliation.candidates(channel, content=spec['content'],
                                                  recover_match=candidate, require_complete=True)
        ids = {n for n in (number(row['saved']['message']), number(state.get('message_id')) if state else None,
                           hint[1] if hint else None) if n}
        for mid in ids:
            if all(m.id != mid for m in matches):
                try:
                    message = await channel.fetch_message(mid)
                except discord.NotFound:
                    continue
                if _source_ok(guild, message) and candidate(message):
                    matches.append(message)
                elif str(mid) == str(row['saved']['message']):
                    raise ServerMessageError('The message mapping points to unrelated content. No message was changed.')
        if len(matches) != 1:
            raise ServerMessageError(f'Found {len(matches)} possible entries. Review missing or duplicate messages; nothing was created or deleted.')
        message = matches[0]
        if not _payload_matches(guild, spec, state, channel, message):
            raise ServerMessageError('The entry author, content or controls differ from the saved record / known default. Preserve it for manual review.')
        if not message.pinned:
            raise ServerMessageError('The verified entry is not pinned. Review its pin in Managed Messages before binding repair.')
        reconciliation.references(message.id, spec['key'])
        row.update(channel_id=channel.id, message_id=message.id, channel_signature=_channel_signature(channel),
                   fingerprint=reconciliation.fingerprint(message), candidate=message)
        row['ready'] = bool(state and channel_id == channel.id and number(row['saved']['message']) == message.id)
        row['repairable'] = not row['ready']
        row['checks'] += ['Bot author / content / controls: verified', 'Unique pinned entry: verified',
                          'Stored channel/message bindings: ' + ('match' if row['ready'] else 'repair required')]
        row['reason'] = ('This entry is ready. Reopen its button.' if row['ready'] else
                         'Restore only the verified channel/message bindings. Text, buttons, permissions and ticket history stay unchanged.')
    except discord.Forbidden:
        row['reason'] = 'Discord denied this read. Check the bot\'s View Channel / Read Message History permissions.'
    except discord.NotFound:
        row['reason'] = 'Discord reports that the recorded entry channel is missing. No replacement was created.'
    except (discord.HTTPException, TimeoutError):
        row['reason'] = 'Discord could not complete verification. Retry this read later; absence was not assumed.'
    except (ServerMessageError, ValueError, KeyError, TypeError) as exc:
        row['reason'] = str(exc) if isinstance(exc, ServerMessageError) else 'Entry storage or metadata is invalid; owner review required.'
    return row


def require_owner(guild, actor):
    if not guild or not actor or guild.owner_id != actor.id:
        raise ServerMessageError('Only the current server owner can repair entry bindings.')


async def preview(guild, actor, kind, *, hint=None):
    require_owner(guild, actor)
    row = await diagnose(guild, kind, hint=hint)
    require_owner(guild, actor)
    row.update(guild_id=guild.id, actor_id=actor.id, created=time.time())
    return row


async def repair(guild, actor, draft, *, confirmed=False):
    require_owner(guild, actor)
    if (not confirmed or draft['guild_id'] != guild.id or draft['actor_id'] != actor.id
            or time.time() - draft['created'] > 180 or not draft['repairable']):
        raise ServerMessageError('Open a fresh entry check and confirm its proposed repair.')
    spec = definition(guild, draft['kind'])
    async with managed.lock(spec['key']):
        current = await diagnose(guild, draft['kind'], hint=draft['hint'])
        for field in ('saved', 'channel_id', 'message_id', 'channel_signature', 'fingerprint'):
            if not current['repairable'] or current.get(field) != draft.get(field):
                raise ServerMessageError('The entry or its saved state changed. Check it again; nothing was overwritten.')
        # The owner is re-read from Discord before any persistent repair.
        live = await guild._state.http.get_guild(guild.id)
        if int(live['owner_id']) != actor.id:
            raise ServerMessageError('Server ownership changed. Reopen as the current owner.')
        require_owner(guild, actor)
        message = current['candidate']
        state = copy.deepcopy(current['saved']['state'])
        if state is None:
            buttons = managed.serialize_view(spec['view'])
            state = dict(key=spec['key'], guild_id=guild.id, channel_id=message.channel.id,
                         message_id=message.id, label=spec['label'], content=message.content,
                         buttons=buttons, default_content=spec['content'], default_buttons=copy.deepcopy(buttons),
                         customized=False, version=0, content_hash=managed.digest(message.content), pending=False)
        state['version'] += 1  # Invalidate old editor drafts, even if only settings changed.
        # Last compare-and-write under SQLite's write lock. No Discord mutation.
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if _snapshot(guild, spec) != current['saved']:
                raise ServerMessageError('Saved bindings changed. Reopen the check; nothing was overwritten.')
            for key, value in ((spec['channel_key'], message.channel.id), (spec['key'], message.id)):
                conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, str(value)))
            conn.execute('INSERT INTO managed_message_content VALUES(?,?,?) ON CONFLICT(setting_key) DO UPDATE SET state_json=excluded.state_json',
                         (spec['key'], guild.id, json.dumps(state, ensure_ascii=False)))
            signature = managed.digest([state['content'], state['buttons']])
            conn.execute('INSERT INTO managed_message_audit (guild_id,actor_id,channel_id,setting_key,content_changed,buttons_changed,before_hash,after_hash,action,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (guild.id, actor.id, message.channel.id, spec['key'], False, False, signature, signature,
                          'entry-binding-repair', int(time.time())))
        return current


async def current_link(guild, member, kind):
    """Point obsolete controls to a verified canonical entry; never expose other channels."""
    spec = definition(guild, kind)
    channel_id, message_id = number(db.get_setting(spec['channel_key'])), number(db.get_setting(spec['key']))
    if not channel_id or not message_id:
        return None
    try:
        channel = await guild.fetch_channel(channel_id)
        if channel.guild.id != guild.id or not isinstance(channel, discord.TextChannel):
            return None
        permissions = channel.permissions_for(member)
        if not (permissions.view_channel and permissions.read_message_history):
            return None
        message = await channel.fetch_message(message_id)
        from types import SimpleNamespace
        check = SimpleNamespace(guild=guild, message=message, channel_id=channel.id)
        if binding_problem(check, kind) is None and _payload_matches(guild, spec, _state(guild, spec), channel, message):
            return f'https://discord.com/channels/{guild.id}/{channel.id}/{message.id}'
    except (discord.HTTPException, ServerMessageError, ValueError, KeyError, TypeError, TimeoutError):
        pass
    return None
