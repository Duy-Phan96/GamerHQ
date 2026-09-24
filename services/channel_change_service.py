"""Approval records and exact expected mutations for managed support/IG text channels."""
import asyncio
import json
import logging
import time
import uuid
from types import SimpleNamespace

import discord
from database import db
from services import channel_adoption_service as adoption
from services.server_service import ServerMessageError

log = logging.getLogger(__name__)
DEBOUNCE = 4
EXPECTED_TTL = 90
PENDING_TTL = 7 * 86400
_expected = {}
_timers = {}
_dirty = {}
_locks = {}


def supported():
    from services.instant_gaming_service import CHANNELS
    return {**adoption.supported(), **{k: v[0] for k, v in CHANNELS.items()}}


def removed(guild, name):
    names = (name, 'haushaltscheck') if name == 'electricity' else (name,)
    return any(db.get_setting(f'managed_channel_removed:{guild.id}:{key}') == '1' for key in names)


def identify(guild, cid):
    matches = [name for name in supported() if not removed(guild, name)
               and db.get_setting(f'managed_channel:{guild.id}:{name}') == str(cid)]
    return matches[0] if len(matches) == 1 else None


def permissions(overwrites):
    return {str(t.id): [o.pair()[0].value, o.pair()[1].value] for t, o in overwrites.items()}


def wire(channel):
    return dict(name=channel.name, category=channel.category_id, position=channel.position,
                permissions=permissions(channel.overwrites))


def expect(guild, cid, **fields):
    now = time.monotonic()
    for key in list(_expected):
        _expected[key] = [(v, expiry) for v, expiry in _expected[key] if expiry > now]
        if not _expected[key]:
            del _expected[key]
    for field, value in fields.items():
        _expected.setdefault((guild.id, cid, field), []).append((value, now + EXPECTED_TTL))


def consume(guild, cid, changes):
    remaining = set(changes)
    now = time.monotonic()
    for field, value in changes.items():
        key = (guild.id, cid, field)
        candidates = [(v, expiry) for v, expiry in _expected.get(key, []) if expiry > now]
        found = next((i for i, (v, _) in enumerate(candidates) if v == value), None)
        if found is not None:
            candidates.pop(found)
            remaining.remove(field)
        if candidates:
            _expected[key] = candidates
        else:
            _expected.pop(key, None)
    return remaining


async def edit(channel, **kwargs):
    fields = {k: v for k, v in kwargs.items() if k in ('name', 'position')}
    if 'category' in kwargs:
        fields['category'] = kwargs['category'].id if kwargs['category'] else None
    if 'overwrites' in kwargs:
        fields['permissions'] = permissions(kwargs['overwrites'])
    expect(channel.guild, channel.id, **fields)
    propagated = []
    # Category permission synchronization emits child events too.
    if not hasattr(channel, 'category_id') and 'overwrites' in kwargs:
        for child in channel.guild.text_channels:
            if child.category_id == channel.id and child.overwrites == channel.overwrites:
                expect(channel.guild, child.id, permissions=fields['permissions'])
                propagated.append(child.id)
    try:
        return await channel.edit(**kwargs)
    except Exception:
        for field in fields:
            _expected.pop((channel.guild.id, channel.id, field), None)
        for cid in propagated:
            _expected.pop((channel.guild.id, cid, 'permissions'), None)
        raise


async def bulk_positions(guild, payload, **kwargs):
    for entry in payload:
        expect(guild, entry['id'], position=entry['position'])
    try:
        await guild._state.http.bulk_channel_update(guild.id, payload, **kwargs)
    except Exception:
        for entry in payload:
            _expected.pop((guild.id, entry['id'], 'position'), None)
        raise


def public_policy(guild, name, rights):
    state = adoption.stored(guild, name) if name in adoption.supported() else {}
    if 'send_messages' in state:
        value = discord.PermissionOverwrite.from_pair(*rights[guild.default_role].pair())
        value.send_messages = state['send_messages']
        rights[guild.default_role] = value
    return rights


def safe_rights(channel, name):
    from services.instant_gaming_service import CHANNELS, overwrites
    from services.onboarding_service import guide_overwrites
    if name == 'free-games':
        from services.support_service import free_games_overwrites
        result = free_games_overwrites(channel)
    elif name in CHANNELS:
        result = overwrites(channel.guild, name, channel.overwrites, channel.category)
    else:
        result = guide_overwrites(channel)
    return public_policy(channel.guild, name, result)


def target(guild, name):
    if name in adoption.supported():
        return adoption.desired(guild, name)
    from services.instant_gaming_service import targets, CHANNELS
    return dict(name=CHANNELS[name][0], category=targets(guild)[name].id, position=None)


def snapshot(channel, name):
    current = wire(channel)
    siblings = adoption.children(channel.guild.channels, channel.category_id)
    current['position'] = next((i for i, c in enumerate(siblings) if c.id == channel.id), 0)
    desired = target(channel.guild, name)
    desired['permissions'] = permissions(safe_rights(channel, name))
    return desired, current


def risky(channel, name, desired):
    from services.onboarding_service import is_staff
    from services.instant_gaming_service import configured_bot, BOT_RIGHTS
    guild = channel.guild
    private = name in {'ig-purchases', 'ig-buyer-ranking'}
    allowed = {guild.me, configured_bot(guild)} - {None}
    allowed.update(r for r in guild.roles if is_staff(r))
    if private:
        if channel.overwrites_for(guild.default_role).view_channel is not False:
            return True
        for member, value in channel.overwrites.items():
            staff_member = isinstance(member, discord.Member) and any(is_staff(r) for r in member.roles)
            if member not in allowed and not staff_member and value.view_channel is True:
                return True
    bots = {guild.me, configured_bot(guild)} - {None}
    bots.update(target for target in desired if isinstance(target, discord.Member) and target.bot)
    for member in bots:
        expected = desired.get(member)
        if expected and any(getattr(expected, bit) is True and not getattr(channel.permissions_for(member), bit)
                            for bit in BOT_RIGHTS):
            return True
        if expected and any(getattr(expected, bit) is True and getattr(channel.overwrites_for(member), bit) is not True
                            for bit in BOT_RIGHTS):
            return True
    return False


def record_key(gid, cid):
    return f'managed_change:{gid}:{cid}'


def load(gid, cid):
    raw = db.get_setting(record_key(gid, cid))
    return json.loads(raw) if raw else None


def store(record):
    db.set_setting(record_key(record['guild_id'], record['resource_id']), json.dumps(record, sort_keys=True))


def records(gid=None):
    with db.connect() as conn:
        rows = conn.execute('SELECT value FROM settings WHERE key LIKE ?',
                            (f'managed_change:{gid}:%' if gid is not None else 'managed_change:%',)).fetchall()
    return [json.loads(r['value']) for r in rows]


def desired_token(guild, name):
    return dict(layout=target(guild, name), stored=db.get_setting(adoption.key(guild, name)),
                mapping=db.get_setting(f'managed_channel:{guild.id}:{name}'))


async def detect(before, after, notify, *, deleted=False):
    channel = before if deleted else after
    guild = channel.guild
    name = identify(guild, channel.id)
    if not name:
        return
    if deleted:
        changes = {'deleted': True}
    else:
        old, new = wire(before), wire(after)
        changes = {k: v for k, v in new.items() if old[k] != v}
    security = not deleted and risky(channel, name, safe_rights(channel, name))
    unmatched = consume(guild, channel.id, changes)
    if not unmatched and not security:
        return
    key = (guild.id, channel.id)
    if key in _timers:
        _timers.pop(key).cancel()
    if security:
        _dirty.pop(key, None)
        await process(guild, channel, name, notify, deleted=False, security=True)
        return
    _dirty.setdefault(key, set()).update(unmatched)

    async def later():
        try:
            await asyncio.sleep(DEBOUNCE)
            await process(guild, channel, name, notify, deleted=deleted, changed_fields=_dirty.get(key, set()).copy())
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception('Managed change processing failed guild=%s resource=%s', guild.id, channel.id)
        finally:
            if _timers.get(key) is asyncio.current_task():
                _timers.pop(key, None)
                _dirty.pop(key, None)
    _timers[key] = asyncio.create_task(later())


async def process(guild, channel, name, notify, *, deleted=False, security=False, changed_fields=()):
    async with _locks.setdefault((guild.id, channel.id), asyncio.Lock()):
        if identify(guild, channel.id) != name:
            return
        if not deleted:
            try:
                channel = next((c for c in await guild.fetch_channels() if c.id == channel.id), None)
            except discord.HTTPException:
                if not security:
                    raise
                # Do not postpone a known privacy repair solely because the
                # topology refresh failed; the event still supplies the channel.
            if not channel:
                return
        desired, current = snapshot(channel, name)
        if deleted:
            current = {'deleted': True}
        fields = ['deleted'] if deleted else [k for k in desired if desired[k] is not None and desired[k] != current[k]]
        if not deleted and desired['position'] is None and 'position' in changed_fields:
            fields.append('position')
        previous = load(guild.id, channel.id)
        if not fields:
            if previous and previous['status'] in {'pending', 'ignored', 'repair_failed'}:
                previous.update(status='reverted', resolution='Discord now matches desired state; no adoption was performed.')
                store(previous)
                await notify(guild, previous)
            return
        if previous and previous['status'] != 'repair_failed' and previous['current_discord_state'] == current and previous['old_desired_state'] == desired:
            return
        security = not deleted and risky(channel, name, safe_rights(channel, name))
        record = dict(guild_id=guild.id, resource_id=channel.id, resource_key=name, resource_type='text_channel',
                      detected_at=time.time(), revision=uuid.uuid4().hex, risk_level='high' if security else 'medium' if 'permissions' in fields else 'low',
                      change_types=fields, old_desired_state=desired, current_discord_state=current,
                      desired_token=desired_token(guild, name), status='pending',
                      notification_channel_id=(previous or {}).get('notification_channel_id'),
                      message_id=(previous or {}).get('message_id'))
        store(record)
        if security:
            try:
                await edit(channel, overwrites=safe_rights(channel, name), reason='GamerHQ immediate private/bot access repair')
                record['status'] = 'auto_repaired'
            except discord.HTTPException:
                record['status'] = 'repair_failed'
                log.exception('Security repair failed guild=%s channel=%s', guild.id, channel.id)
            store(record)
        await notify(guild, record)


async def reconcile(channel, name, *, permissions_only=False):
    wanted = target(channel.guild, name)
    kwargs = {'overwrites': safe_rights(channel, name)}
    if not permissions_only:
        category = channel.guild.get_channel(wanted['category'])
        if category not in channel.guild.categories:
            raise ServerMessageError('Desired category is unavailable; manual review required.')
        kwargs.update(name=wanted['name'], category=category, sync_permissions=False)
    await edit(channel, **kwargs, reason='GamerHQ approved managed change revert')
    if not permissions_only:
        await adoption.apply_order(channel.guild)


def message_keys(guild, name):
    from services.instant_gaming_service import CHANNELS, message_key as ig_key
    from services.support_service import support_sections, section_channel, message_key
    return ([ig_key(guild, name)] if name in CHANNELS else []) + [message_key(guild, section)
            for section, _, _ in support_sections() if section_channel(section) == name]


async def restore(guild, record):
    name = record['resource_key']
    # Keep custom message state and desired layout while rebinding only the recreated ID.
    wanted = target(guild, name)
    category = guild.get_channel(wanted['category'])
    if category not in guild.categories:
        raise ServerMessageError('Desired category missing; restore/review it first.')
    if record.get('restore_attempt'):
        raise ServerMessageError('A previous creation attempt has an uncertain outcome. Review Discord before retrying; no duplicate will be created.')
    if any(c.name == wanted['name'] for c in await guild.fetch_channels()):
        raise ServerMessageError('A channel with the desired name already exists. Review its identity; no duplicate will be created.')
    stub = SimpleNamespace(guild=guild, category=category, overwrites={},
                           overwrites_for=lambda target: discord.PermissionOverwrite())
    record['restore_attempt'] = True
    store(record)
    try:
        channel = await category.create_text_channel(wanted['name'], overwrites=safe_rights(stub, name),
                                                     reason='GamerHQ approved deleted channel restoration')
    except discord.HTTPException as exc:
        if exc.status in (400, 403):  # Definitive rejection: retry after fixing the cause.
            record.pop('restore_attempt', None)
            store(record)
        raise
    db.set_setting(f'managed_channel:{guild.id}:{name}', channel.id)
    raw = db.get_setting(adoption.key(guild, name))
    if raw:
        state = json.loads(raw)
        state['channel_id'] = channel.id
        db.set_setting(adoption.key(guild, name), json.dumps(state))
    record.update(status='reverted', restored_resource_id=channel.id)
    store(record)  # A pin-delivery retry must never create another channel.
    from services import managed_message_service as managed
    for key in set(message_keys(guild, name)):
        state = managed.load(key)
        if state:
            state['channel_id'] = channel.id
            managed.store(state)
    from services.instant_gaming_service import CHANNELS, refresh
    from services.support_service import sync_support_messages
    try:
        if name in CHANNELS:
            await refresh(guild, channels={name: channel})
        await sync_support_messages(guild, channel=channel)
        await adoption.apply_order(guild)
    except (discord.HTTPException, ServerMessageError):
        record['restore_warning'] = 'Channel restored; message/order delivery incomplete. Run the existing targeted sync.'
        store(record)
        log.exception('Restored channel requires message/order retry guild=%s channel=%s', guild.id, channel.id)
    return channel


def remove_from_setup(guild, name):
    from services import managed_message_service as managed
    for key in set(message_keys(guild, name)):
        state = managed.load(key)
        if state:
            state['retired'] = True
            managed.store(state)
    with db.connect() as conn:
        conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (f'managed_channel_removed:{guild.id}:{name}', '1'))
        for key in {*message_keys(guild, name), f'managed_channel:{guild.id}:{name}', adoption.key(guild, name)}:
            conn.execute('DELETE FROM settings WHERE key=?', (key,))


async def act(guild, user, cid, revision, action):
    adoption.authorize(guild, user)
    async with _locks.setdefault((guild.id, cid), asyncio.Lock()):
        record = load(guild.id, cid)
        if not record or record['revision'] != revision or record['status'] != 'pending':
            raise ServerMessageError('This change is no longer pending. Review the current notification.')
        if time.time() - record['detected_at'] > PENDING_TTL:
            record['status'] = 'expired'; store(record)
            raise ServerMessageError('This approval expired; no Discord or desired-state change was made.')
        name = record['resource_key']
        if desired_token(guild, name) != record['desired_token']:
            raise ServerMessageError('Desired state changed after detection. Review fresh drift before acting.')
        snapshot_channels = await guild.fetch_channels()
        channel = next((c for c in snapshot_channels if c.id == cid), None)
        deleted = 'deleted' in record['change_types']
        if (deleted and channel) or (not deleted and (not channel or snapshot(channel, name)[1] != record['current_discord_state'])):
            raise ServerMessageError('Discord changed after this notification; wait for the updated review.')
        adoption.authorize(guild, user)
        if action == 'ignore':
            record['status'] = 'ignored'
        elif deleted and action == 'restore':
            await restore(guild, record)
            record['status'] = 'reverted'
        elif deleted and action == 'remove':
            remove_from_setup(guild, name)
            record['status'] = 'adopted'
            store(record)
            from services.support_service import sync_support_messages
            await sync_support_messages(guild)
        elif not deleted and action == 'revert':
            if 'position' in record['change_types'] and record['old_desired_state']['position'] is None:
                raise ServerMessageError('No stored position exists for this channel. Apply the current order to Setup or Ignore; no position was invented.')
            await reconcile(channel, name)
            record['status'] = 'reverted'
        elif not deleted and action == 'adopt':
            if name not in adoption.supported() or record['risk_level'] == 'high':
                raise ServerMessageError('This state cannot safely be adopted. Revert or ignore it.')
            layout = {k for k in record['change_types'] if k in adoption.PROPERTIES}
            current, channels = await adoption.actual(guild, cid)
            adoption.validate(guild, name, current, channels, layout)
            state = dict(adoption.stored(guild, name))
            for field in layout:
                state[field] = current[field]
            if 'permissions' in record['change_types']:
                rights = safe_rights(channel, name)
                proposed = channel.overwrites_for(guild.default_role).send_messages
                rights[guild.default_role].send_messages = proposed
                if proposed not in (True, False) or permissions(rights) != permissions(channel.overwrites):
                    raise ServerMessageError('Only @everyone Send Messages on public boards can be adopted. Unknown overwrites are not imported.')
                state['send_messages'] = proposed
            state['channel_id'] = cid
            db.set_setting(adoption.key(guild, name), json.dumps(state, sort_keys=True))
            record['status'] = 'adopted'
        else:
            raise ServerMessageError('Unsupported action for this change.')
        record['resolved_by'] = user.id
        store(record)
        return record


def expire():
    now = time.monotonic()
    for key in list(_expected):
        _expected[key] = [(v, expiry) for v, expiry in _expected[key] if expiry > now]
        if not _expected[key]:
            del _expected[key]
    for record in records():
        if record['status'] == 'pending' and time.time() - record['detected_at'] > PENDING_TTL:
            record['status'] = 'expired'
            store(record)
