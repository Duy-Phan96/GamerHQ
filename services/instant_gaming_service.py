"""Persisted Instant Gaming destinations; never infer a bot identity from its name."""
import asyncio
import logging
import discord
from database import db
from services.onboarding_service import alias, is_staff, unique
from services.server_service import ServerMessageError, upsert_fixed_message

async def tracked_edit(channel, **kwargs):
    from services.channel_change_service import edit
    return await edit(channel, **kwargs)


CHANNELS = {
    'gaming-news': ('📰・gaming-news', '# 📰 Gaming News\n\nStay up to date with gaming news, new releases and updates from the gaming world.\n\nNews in this channel may be posted automatically by our connected gaming services.'),
    'gaming-deals': ('🔥・gaming-deals', '# 🔥 Gaming Deals\n\nFind current gaming deals, promotions and special offers here.\n\nAffiliate / referral link'),
    'ig-purchases': ('💸・purchases', '# 💸 Instant Gaming Purchases\n\nInternal channel for Instant Gaming purchase and affiliate notifications.\n\nThis channel is visible to staff only.'),
    'ig-buyer-ranking': ('🏆・buyer-ranking', '# 🏆 Instant Gaming Buyer Ranking\n\nInternal overview of Instant Gaming buyer rankings.\n\nThis channel is visible to staff only.'),
}
PUBLIC = {'gaming-news', 'gaming-deals'}
log = logging.getLogger(__name__)
BOT_RIGHTS = ('view_channel', 'send_messages', 'embed_links', 'attach_files', 'read_message_history')
POSTING = ('send_messages', 'send_messages_in_threads', 'create_public_threads', 'create_private_threads')
_locks = {}
_pin_locks = {}


def channel_key(guild, name): return f'managed_channel:{guild.id}:{name}'
def message_key(guild, name):
    # Reuse the existing partner pin and editor state; never create a second deals pin.
    return f'partner_message:{guild.id}:instant_gaming' if name == 'gaming-deals' else f'instant_gaming_message:{guild.id}:{name}'


def candidates(guild, name):
    names = {name, 'affiliate-purchases', 'purchases'} if name == 'ig-purchases' else {name, 'buyer-ranking'} if name == 'ig-buyer-ranking' else {name}
    return [c for c in guild.text_channels if alias(c.name) in names]


def configured_bot(guild):
    from services.bot_group_service import member
    return member(guild, 'instant-gaming')


def resolve(guild, name, *, mapped_only=False):
    from services.channel_change_service import removed
    if removed(guild, name):
        return None
    raw = db.get_setting(channel_key(guild, name))
    channel = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
    if channel in guild.text_channels:
        return channel
    if mapped_only:
        return None
    matches = candidates(guild, name)
    if len(matches) > 1:
        raise ServerMessageError(f'Multiple {name} candidates; manual review required. No duplicate created.')
    return matches[0] if matches else None


def targets(guild):
    private = affiliate_category(guild)
    if private is None:
        raise ServerMessageError('AFFILIATE STATS is missing; run owner Repair.')
    from services.support_service import resource
    public = resource(guild, 'partners-benefits', True)
    if not public:
        raise ServerMessageError('An existing PARTNERS & BENEFITS category is required; no category was created.')
    return {name: public if name in PUBLIC else private for name in CHANNELS}


def affiliate_category(guild):
    raw = db.get_setting(f'managed_category:{guild.id}:affiliate-stats')
    stored = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
    matches = [c for c in guild.categories if alias(c.name) == 'affiliate-stats']
    if stored in guild.categories:
        if alias(stored.name) != 'affiliate-stats':
            raise ServerMessageError('Affiliate Stats mapping points to another category; preserve it for owner review.')
        if any(c.id != stored.id for c in matches):
            raise ServerMessageError('Conflicting Affiliate Stats category IDs/names; manual review required.')
        return stored
    if len(matches) > 1:
        raise ServerMessageError('Multiple Affiliate Stats categories; no duplicate created.')
    return matches[0] if matches else None


async def ensure_affiliate_category(guild):
    category = affiliate_category(guild)
    rights = overwrites(guild, 'ig-purchases', category.overwrites if category else {})
    if category is None:
        category = await guild.create_category('🔒 AFFILIATE STATS', overwrites=rights,
                                               reason='GamerHQ private affiliate statistics')
    elif category.overwrites != rights:
        await tracked_edit(category, overwrites=rights, reason='GamerHQ private Affiliate Stats access')
    db.set_setting(f'managed_category:{guild.id}:affiliate-stats', category.id)
    return category


def staff_can_post(guild, category, target):
    if hasattr(category, 'permissions_for'):
        return category.permissions_for(target).send_messages
    # Stateful offline fixtures, mirroring the relevant Discord role resolution.
    roles = [target] if target in guild.roles else target.roles
    base = guild.default_role.permissions.send_messages or any(r.permissions.send_messages for r in roles)
    everyone = category.overwrites_for(guild.default_role).send_messages
    grants = [category.overwrites_for(r).send_messages for r in roles]
    return True if True in grants else False if False in grants else everyone if everyone is not None else base


def overwrites(guild, name, existing=None, category=None):
    result = {target: discord.PermissionOverwrite.from_pair(*value.pair())
              for target, value in (existing or {}).items()}
    bot = configured_bot(guild)
    allowed = {guild.me, bot} - {None}
    previous_bot = db.get_setting(f'instant_gaming_bot:{guild.id}')
    if name in PUBLIC:
        allowed.update(target for target, value in result.items() if isinstance(target, discord.Member)
                       and target.bot and str(target.id) != previous_bot
                       and value.view_channel is True and value.send_messages is True)
    allowed.update(role for role in guild.roles if is_staff(role))
    allowed.update(target for target in result if isinstance(target, discord.Member)
                   and any(is_staff(role) for role in target.roles))
    for target in set(result) | allowed | {guild.default_role}:
        value = result.setdefault(target, discord.PermissionOverwrite())
        if target in allowed:
            if isinstance(target, discord.Member) and target.bot and target not in {guild.me, bot} and name in PUBLIC:
                continue  # Retain an already approved posting bot's exact grants.
            rights = BOT_RIGHTS if name in PUBLIC or target in {guild.me, bot} else ('view_channel', 'read_message_history')
            for right in rights:
                setattr(value, right, True)
            if name not in PUBLIC and target not in {guild.me, bot}:
                value.send_messages = staff_can_post(guild, category, target) if category else False
            if target == guild.me:
                value.manage_messages = True
        else:
            if name not in PUBLIC:
                value.view_channel = False
                value.read_message_history = False
            else:
                value.view_channel = True
                value.read_message_history = True
            for right in POSTING:
                setattr(value, right, False)
        if target == bot:
            from services.music_bot_service import DENIED_RIGHTS
            for bit in DENIED_RIGHTS:
                setattr(value, bit, False)
        result[target] = value
    from services.channel_change_service import public_policy
    return public_policy(guild, name, result) if name in PUBLIC else result


async def refresh(guild, *, channels=None):
    """Only registered channels; startup never creates channels or changes permissions."""
    async with _pin_locks.setdefault(guild.id, asyncio.Lock()):
        await _refresh(guild, channels)


async def _refresh(guild, channels=None):
    for name, (_, text) in CHANNELS.items():
        from services.channel_change_service import removed
        if removed(guild, name):
            continue
        channel = channels.get(name) if channels is not None else resolve(guild, name, mapped_only=True)
        if not channel:
            continue
        if name not in PUBLIC and channel.overwrites != overwrites(guild, name, channel.overwrites, channel.category):
            continue  # Do not publish into a channel whose private policy has drifted.
        view = None
        if name == 'gaming-deals':
            from services.support_service import section_view, AFFILIATES
            view = section_view('instant_gaming', AFFILIATES[0])
        previous = db.get_setting(message_key(guild, name))
        await upsert_fixed_message(channel, setting_key=message_key(guild, name), content=text,
                                   pin=True, view=view, recover_match=lambda m, title=text.split('\n')[0]:
                                   (m.content or '').startswith(title) or (title == '# 🔥 Gaming Deals' and (m.content or '').startswith('# 🎮 Gaming Deals')))
        log.info('[InstantGaming] %s %s pinned message', 'Updating' if previous else 'Creating', name)


async def sync(guild):
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        if not configured_bot(guild):
            log.warning('[InstantGaming] Instant Gaming bot ID missing or bot unavailable; channels will be prepared without bot-specific grants')
        channels = {name: resolve(guild, name) for name in CHANNELS}
        if len({c.id for c in channels.values() if c}) != sum(c is not None for c in channels.values()):
            raise ServerMessageError('Conflicting Instant Gaming channel mappings; review manually.')
        await ensure_affiliate_category(guild)
        categories = targets(guild)
        from services.support_service import repair_partner_permissions
        await repair_partner_permissions(guild, categories['gaming-news'], feed_ids=[channels[n].id for n in PUBLIC if channels[n]])
        for name, (display, _) in CHANNELS.items():
            from services.channel_change_service import removed
            if removed(guild, name):
                continue
            from services.channel_adoption_service import placement
            display, categories[name] = placement(guild, name, display, categories[name])
            channel = channels[name]
            rights = overwrites(guild, name, channel.overwrites if channel else None, categories[name])
            if channel is None:
                log.info('[InstantGaming] Creating %s channel', name)
                channel = await categories[name].create_text_channel(display, overwrites=rights, reason='GamerHQ Instant Gaming integration')
                db.set_setting(channel_key(guild, name), channel.id)
            else:
                log.info('[InstantGaming] Reusing existing %s channel', name)
                # Apply privacy atomically with relocation; never inherit category grants.
                changes = {}
                if channel.category_id != categories[name].id:
                    changes.update(category=categories[name], sync_permissions=False)
                if channel.overwrites != rights:
                    changes['overwrites'] = rights
                    log.info('[InstantGaming] Updating permissions for %s', name)
                if channel.name != display:
                    changes['name'] = display
                if changes:
                    channel = await tracked_edit(channel, **changes, reason='GamerHQ Instant Gaming destination repair') or channel
                db.set_setting(channel_key(guild, name), channel.id)
            channels[name] = channel
        from services.support_service import order_partner_channels
        await order_partner_channels(guild)
        # edit() returns a new object before the gateway updates the guild cache.
        await refresh(guild, channels=channels)
        bot = configured_bot(guild)
        db.set_setting(f'instant_gaming_bot:{guild.id}', bot.id if bot else 0)
        rows = await diagnostics(guild, channels=channels)
        lines = ['# Instant Gaming Setup']
        for name in CHANNELS:
            lines.append('\n**' + CHANNELS[name][0] + '**')
            lines.extend(('✅ ' if state == 'PASS' else '⚠️ ') + label.removeprefix(name + ' ') + ': ' + detail
                         for label, state, detail in rows if label == name or label.startswith(name + ' '))
        if not configured_bot(guild):
            lines.append('\n⚠️ Instant Gaming Bot ID not configured or bot unavailable; bot access pending.')
        return '\n'.join(lines)[:1950]


async def diagnostics(guild, *, messages=True, channels=None):
    rows = []
    from services.bot_group_service import member_id
    identity = member_id(guild, 'instant-gaming')
    rows.append(('Instant Gaming bot ID', 'PASS' if identity else 'WARN',
                 'Configured user ID.' if identity else 'INSTANT_GAMING_BOT_ID or stored identity is missing.'))
    if not configured_bot(guild):
        rows.append(('Instant Gaming bot', 'WARN', 'INSTANT_GAMING_BOT_ID is not configured or does not identify an available external bot.'))
    try:
        categories = targets(guild)
        from services.onboarding_service import guide_overwrites
        public = categories['gaming-news']
        category_ok = public.overwrites == guide_overwrites(public)
        rows.append(('Partner category permissions', 'PASS' if category_ok else 'REPAIRABLE',
                     'Public read-only category.' if category_ok else 'Repair partner category permissions; preserve interactive exceptions.'))
        for name in CHANNELS:
            from services.channel_change_service import removed
            if removed(guild, name):
                continue
            channel = channels.get(name) if channels is not None else resolve(guild, name)
            matches = candidates(guild, name)
            if len({c.id for c in matches} | ({channel.id} if channel else set())) > 1:
                rows.append((name + ' duplicates', 'MANUAL_REVIEW', 'Multiple channel candidates; retained for review.'))
            from services.channel_adoption_service import placement
            display, target = placement(guild, name, CHANNELS[name][0], categories[name])
            valid = channel and channel.category_id == target.id and str(channel.id) == db.get_setting(channel_key(guild, name))
            valid = valid and channel.name == display
            rows.append((name, 'PASS' if valid else 'REPAIRABLE', 'Channel/category registered.' if valid else 'Repair channel/category.'))
            if channel:
                valid = channel.overwrites == overwrites(guild, name, channel.overwrites, categories[name])
                rows.append((name + ' permissions', 'PASS' if valid else 'REPAIRABLE', ('Public read-only.' if name in PUBLIC else 'Staff-only.') if valid else 'Repair visibility/permissions.'))
                bot = configured_bot(guild)
                access = bot and all(getattr(channel.overwrites_for(bot), right) is True and getattr(channel.permissions_for(bot), right) for right in BOT_RIGHTS)
                rows.append((name + ' bot access', 'PASS' if access else 'WARN', 'Required rights granted.' if access else 'Bot access pending or incomplete.'))
            if channel and messages:
                msg = None
                raw = db.get_setting(message_key(guild, name))
                try:
                    msg = await channel.fetch_message(int(raw)) if raw and raw.isdigit() else None
                    valid_pin = msg and msg.pinned and guild.me and msg.author.id == guild.me.id
                    if valid_pin and name not in PUBLIC:
                        valid_pin = msg.content == CHANNELS[name][1]
                except discord.HTTPException:
                    valid_pin = False
                rows.append((name + ' pin', 'PASS' if valid_pin else 'REPAIRABLE', 'Managed message pinned.' if valid_pin else 'Managed pin missing/unavailable; run sync.'))
                try:
                    headings = {CHANNELS[name][1].split('\n')[0]}
                    if name == 'gaming-deals': headings.add('# 🎮 Gaming Deals')
                    owned = {m.id async for m in channel.pins(limit=None) if guild.me and m.author.id == guild.me.id
                             and (m.id == (msg.id if msg else None) or (m.content or '').split('\n')[0] in headings)}
                    if len(owned) > 1:
                        rows.append((name + ' duplicate pins', 'MANUAL_REVIEW', 'Multiple managed-looking pins; review before removing.'))
                except discord.HTTPException:
                    rows.append((name + ' pins', 'WARN', 'Could not inspect pinned messages.'))
    except ServerMessageError as exc:
        rows.append(('Instant Gaming channels', 'MANUAL_REVIEW', str(exc)))
    return rows
