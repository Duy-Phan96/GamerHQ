"""Explicit adoption of public managed boards, using the existing settings store."""
import asyncio
import json

from database import db
from services.server_service import ServerMessageError
from services.onboarding_service import alias, unique

PROPERTIES = ('name', 'category', 'position')
_locks = {}


def supported():
    from services.support_service import CHANNEL_NAME, PARTNER_CHANNELS
    return {'support-gamerhq': CHANNEL_NAME, **PARTNER_CHANNELS}


def key(guild, name):
    return f'managed_channel_state:{guild.id}:{name}'


def stored(guild, name):
    raw = db.get_setting(key(guild, name))
    state = json.loads(raw) if raw else {}
    if state and str(state['channel_id']) != db.get_setting(f'managed_channel:{guild.id}:{name}'):
        raise ServerMessageError('Adopted state has a different channel ID; manual review required.')
    return state


def defaults(guild, name):
    from services.support_service import resource
    category = unique(guild.categories, 'start-here') if name == 'support-gamerhq' else resource(guild, 'partners-benefits', True)
    return {'name': supported()[name], 'category': category.id if category else None,
            'position': None if name == 'support-gamerhq' else list(supported())[1:].index(name)}


def desired(guild, name):
    result = defaults(guild, name)
    state = stored(guild, name)
    if 'category' in state and state['category'] != result['category'] and 'position' not in state:
        result['position'] = None  # No default ordering contract outside the default category.
    result.update({k: v for k, v in state.items() if k in PROPERTIES})
    return result


def placement(guild, name, display, category):
    if name not in supported():
        return display, category
    state = stored(guild, name)
    if 'category' in state:
        category = guild.get_channel(state['category'])
        if category not in guild.categories:
            raise ServerMessageError('Adopted category is missing; restore it or explicitly adopt another category.')
        from services.music_bot_service import blocked_name
        if blocked_name(category) or category.overwrites_for(guild.default_role).view_channel is False:
            raise ServerMessageError('Adopted public category is now protected/private; manual review required.')
    return state.get('name', display), category


async def apply_layout(guild):
    """Explicit sync only: reconcile existing public board IDs, without permissions."""
    snapshot = await guild.fetch_channels()
    for name in supported():
        raw = db.get_setting(f'managed_channel:{guild.id}:{name}')
        channel = next((c for c in snapshot if str(c.id) == raw), None)
        if not channel:
            continue
        target = desired(guild, name)
        category = guild.get_channel(target['category']) if target['category'] else None
        _, category = placement(guild, name, target['name'], category)
        if category not in guild.categories:
            raise ServerMessageError('Desired category is missing; run owner setup or review adopted state.')
        changes = {}
        if channel.name != target['name']:
            changes['name'] = target['name']
        if channel.category_id != category.id:
            from services.music_bot_service import blocked_name
            if channel.category and blocked_name(channel.category):
                raise ServerMessageError('Public board is in a protected category; review before moving it.')
            changes.update(category=category, sync_permissions=False)
        if changes:
            from services.channel_change_service import edit as tracked_edit
            await tracked_edit(channel, **changes, reason='GamerHQ persisted channel layout')


def identify(guild, channel_id):
    with db.connect() as conn:
        rows = conn.execute('SELECT key FROM settings WHERE key LIKE ? AND value=?',
                            (f'managed_channel:{guild.id}:%', str(channel_id))).fetchall()
    if len(rows) != 1:
        raise ServerMessageError('Select a channel with exactly one existing GamerHQ managed ID mapping. Nothing registered.')
    name = rows[0]['key'].split(':', 2)[2]
    if name not in supported():
        raise ServerMessageError('Initial adoption supports Support GamerHQ and the five public partner/feed channels only.')
    return name


def children(snapshot, category_id):
    return sorted((c for c in snapshot if getattr(c, 'category_id', None) == category_id
                   and getattr(c, '_sorting_bucket', 0) == 0), key=lambda c: (c.position, c.id))


async def actual(guild, channel_id):
    snapshot = await guild.fetch_channels()
    channel = next((c for c in snapshot if c.id == channel_id and hasattr(c, 'category_id')), None)
    if channel is None:
        raise ServerMessageError('Managed channel no longer exists; preview again after repair.')
    siblings = children(snapshot, channel.category_id)
    return {'name': channel.name, 'category': channel.category_id,
            'position': next(i for i, c in enumerate(siblings) if c.id == channel.id)}, snapshot


def authorize(guild, user):
    from services.managed_message_service import require_admin
    require_admin(guild, user)


def validate(guild, name, current, snapshot, fields):
    if 'category' in fields:
        category = next((c for c in snapshot if c.id == current['category']), None)
        from services.support_service import resource
        allowed = {c.id for c in guild.categories if alias(c.name) in {'start-here', 'community'}}
        partners = resource(guild, 'partners-benefits', True)
        if partners:
            allowed.add(partners.id)
        if (not category or category.id not in allowed
                or category.overwrites_for(guild.default_role).view_channel is False):
            raise ServerMessageError('Only existing public START HERE, COMMUNITY or PARTNERS & BENEFITS categories may be adopted.')
    if 'position' in fields and 'category' not in fields and current['category'] != desired(guild, name)['category']:
        raise ServerMessageError('Category differs from desired state. Adopt category first or select all.')
    if 'position' in fields:
        for other in supported():
            if other != name and stored(guild, other).get('position') == current['position'] and desired(guild, other)['category'] == current['category']:
                raise ServerMessageError('Another adopted channel reserves this position; update that channel first.')
    if 'name' in fields:
        cid = int(db.get_setting(f'managed_channel:{guild.id}:{name}'))
        from services.server_setup_service import SERVER_BLUEPRINT
        reserved = {alias(ch.name) for group in SERVER_BLUEPRINT for ch in group.channels} - {name}
        if (any(c.id != cid and alias(c.name) == alias(current['name']) for c in snapshot)
                or alias(current['name']) in reserved):
            raise ServerMessageError('Channel name conflicts with another channel; nothing adopted.')


async def preview(guild, user, channel_id, aspect='all'):
    authorize(guild, user)
    if aspect not in (*PROPERTIES, 'all'):
        raise ServerMessageError('Supported aspects: name, category, position, all. Permissions are not imported.')
    name = identify(guild, channel_id)
    fields = PROPERTIES if aspect == 'all' else (aspect,)
    current, snapshot = await actual(guild, channel_id)
    validate(guild, name, current, snapshot, fields)
    return dict(guild_id=guild.id, actor_id=user.id, channel_id=channel_id, resource=name,
                fields=fields, stored=stored(guild, name), desired=desired(guild, name), actual=current)


async def confirm(guild, user, draft, *, confirmed=False):
    authorize(guild, user)
    if not confirmed or draft['actor_id'] != user.id or draft['guild_id'] != guild.id:
        raise ServerMessageError('Explicit confirmation by the preview author is required.')
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        name = identify(guild, draft['channel_id'])
        current, snapshot = await actual(guild, draft['channel_id'])
        authorize(guild, user)
        if (name != draft['resource'] or stored(guild, name) != draft['stored']
                or current != draft['actual']):
            raise ServerMessageError('Discord state or stored configuration changed. Run /server adopt for a fresh preview.')
        validate(guild, name, current, snapshot, draft['fields'])
        updated = dict(draft['stored'])
        for field in draft['fields']:
            if current[field] != draft['desired'][field]:
                updated[field] = current[field]
        if updated == draft['stored']:
            return False
        updated['channel_id'] = draft['channel_id']
        db.set_setting(key(guild, name), json.dumps(updated, sort_keys=True))
        return True


def render(guild, draft):
    def value(field, val):
        if field == 'category':
            category = guild.get_channel(val) if val else None
            return f'{category.name} ({val})' if category else str(val)
        return str(val) if val is not None else 'Not managed'
    lines = [f"# Adopt {draft['resource']}", 'GamerHQ desired state → current Discord state']
    for field in draft['fields']:
        label = 'Position (within category, 0-based)' if field == 'position' else field.title()
        lines.append(f"\n**{label}**\nStored/default: {value(field, draft['desired'][field])}\nDiscord: {value(field, draft['actual'][field])}")
    lines.append('\nConfirm saves only these properties to GamerHQ. Discord, permissions and pins are unchanged.')
    return '\n'.join(lines)


def order_plans(guild, snapshot):
    """Read-only order calculation shared by sync and health."""
    from services.support_service import resource, PARTNER_CHANNELS
    partners = resource(guild, 'partners-benefits', True)
    mapped = {name: db.get_setting(f'managed_channel:{guild.id}:{name}') for name in supported()}
    states = {name: stored(guild, name) for name in supported()}
    categories = {partners.id} if partners else set()
    categories.update(desired(guild, name)['category'] for name, state in states.items() if 'position' in state)
    plans = []
    for cid in categories:
        current = children(snapshot, cid)
        by_id = {str(c.id): c for c in current}
        managed = [by_id[mapped[name]] for name in PARTNER_CHANNELS if mapped[name] in by_id]
        if len({c.id for c in managed}) != len(managed):
            raise ServerMessageError('Conflicting partner channel mappings; manual review required.')
        ordered = managed + [c for c in current if c not in managed] if partners and cid == partners.id else list(current)
        anchors = [(state['position'], mapped[name]) for name, state in states.items()
                   if 'position' in state and desired(guild, name)['category'] == cid and mapped[name] in by_id]
        anchor_ids = {channel_id for _, channel_id in anchors}
        ordered = [c for c in ordered if str(c.id) not in anchor_ids]
        for pos, channel_id in sorted(anchors):
            channel = by_id[channel_id]
            ordered.insert(min(pos, len(ordered)), channel)
        if partners and cid == partners.id:
            deals = by_id.get(mapped.get('gaming-deals'))
            free = by_id.get(mapped.get('free-games'))
            if deals and free:
                ordered.remove(free)
                ordered.insert(ordered.index(deals) + 1, free)
        plans.append((current, ordered))
    return plans


async def apply_order(guild):
    for current, ordered in order_plans(guild, await guild.fetch_channels()):
        if [c.id for c in current] != [c.id for c in ordered]:
            from services.channel_change_service import bulk_positions
            await bulk_positions(guild,
                [{'id': c.id, 'position': index} for index, c in enumerate(ordered)],
                reason='GamerHQ persisted managed channel order')


def diagnostics(guild, snapshot):
    rows = []
    for name in supported():
        state = stored(guild, name)
        if not state:
            continue
        channel = next((c for c in snapshot if c.id == state['channel_id']), None)
        valid = channel is not None
        if channel:
            current = dict(name=channel.name, category=channel.category_id,
                           position=next(i for i, c in enumerate(children(snapshot, channel.category_id)) if c.id == channel.id))
            valid = all(current[field] == state[field] for field in PROPERTIES if field in state)
        rows.append((f'Adopted {name}', 'PASS' if valid else 'REPAIRABLE',
                     'Explicit desired state matches Discord.' if valid else 'Adopted state drift; sync repairs it, /server adopt keeps intentional changes.'))
    return rows
