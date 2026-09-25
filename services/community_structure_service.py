"""Incremental core-board migration, invoked only by owner setup."""
from database import db
from services.onboarding_service import alias, unique, set_read_only, set_writable
from services.server_service import ServerMessageError, upsert_fixed_message

EVENT_BOARDS = ('community-events', 'tournaments', 'giveaways')
EVENTS_INTRO = ('# 🎉 Community Events\n\n'
                'Join upcoming GamerHQ community events, game nights and special sessions here.')


def event_order(guild, snapshot):
    """Replace only managed event slots; unrelated children's relative order stays intact."""
    events = unique(guild.categories, 'events')
    current = sorted((c for c in snapshot if getattr(c, 'category_id', None) == getattr(events, 'id', None)
                      and c in guild.text_channels), key=lambda c: (c.position, c.id)) if events else []
    by_id = {c.id: c for c in current}
    # edit() returns a new object before the gateway necessarily updates the cache.
    # Resolve identity from mappings, but placement from the fresh REST snapshot.
    ids = [c.id for name in EVENT_BOARDS if (c := core_channel(guild, name)) and c.id in by_id] if events else []
    managed = iter(by_id[cid] for cid in ids if cid in by_id)
    return current, [next(managed) if c.id in ids else c for c in current]


def resource_key(guild, name):
    return f'managed_channel:{guild.id}:{name}'


def core_channel(guild, name):
    raw = db.get_setting(resource_key(guild, name))
    if raw and str(raw).isdigit():
        channel = guild.get_channel(int(raw))
        if channel in guild.text_channels:
            return channel
    return unique([c for c in guild.text_channels if c.category and alias(c.category.name) in {'start-here', 'community', 'events'}], name)


def mention(guild, name):
    channel = core_channel(guild, name)
    return channel.mention if channel else '#' + name


def guide_text(guild):
    from services.support_service import guide_reference
    from services.ticket_service import guide_reference as ticket_reference
    streamer = unique([c for c in guild.text_channels if c.category and alias(c.category.name) in {'streamers', 'gamerhq-streamers'}], 'streamer-guide')
    streamer_link = streamer.mention if streamer else '#streamer-guide'
    return (
        '# 📘 GamerHQ Guide\n\nHere are GamerHQ’s main features and how to use them.\n\n'
        f'## 🎮 Games & Roles\nChoose games in **{mention(guild, "choose-your-games")}** and optional notifications, languages and profile settings in **{mention(guild, "choose-your-roles")}**. \n\n'
        f'## 🎯 Looking for Group\nFind players and open sessions in **{mention(guild, "looking-for-group")}**.\n'
        '`/lfg create` — create a session\n`/lfg manage` — view/manage your sessions\n`/lfg join-code` — join a private session\nHosts can invite players and change session details.\n\n'
        f'## 🤖 Bot Commands\nUse **{mention(guild, "bot-commands")}** for bot commands.\n\n'
        '## 🎵 Music Bots\n**Jockie Music** — mainly Apple Music · `m!`\n**Pancake** — mainly Spotify · `p!`\nJoin a voice channel, then use a prefix with:\n'
        '`play <song/link>` — play a song/playlist\n`skip` — skip (Pancake may use a vote)\n`pause` / `resume` — pause/continue\n`queue` — show the queue\n'
        '`m!play <Apple Music song or playlist link>`\n`p!play <Spotify song or playlist link>`\n'
        'Disconnect: `m!leave` / `p!stop`. More: `m!help`, `p!help` or official bot documentation.\n\n'
        f'## 💡 Suggestions\nOpen **{mention(guild, "suggestions")}** → **Submit Suggestion**. Ideas go privately to the team for review.\n\n'
        f'## 🏆 Events\nGame nights: **{mention(guild, "community-events")}**. Tournaments & giveaways: **Coming Soon**.\n\n'
        '## 🔊 Voice\nJoin **➕ Create Voice** for your own temporary room. Use `/voice manage` to rename it, set a user limit, lock/unlock, allow or remove players, and close it. Music Bots work there too. Empty rooms are automatically removed.\n\n'
        '## 🎥 Streamer Hub\nBeta — currently hidden.\n\n'
        + ticket_reference(guild) + '\n\n' + guide_reference(guild)
    )


async def refresh_boards(guild):
    from cogs.suggestions import refresh_entry
    guide = core_channel(guild, 'guide')
    if guide:
        await upsert_fixed_message(guide, setting_key=f'central_guide:{guild.id}', content=guide_text(guild), pin=True,
            recover_match=lambda m: (m.content or '').startswith('# 📘 GamerHQ Guide'))
    suggestions = core_channel(guild, 'suggestions')
    if suggestions:
        await refresh_entry(suggestions)
    events = core_channel(guild, 'community-events') if db.get_setting(resource_key(guild, 'community-events')) else None
    if events:
        await upsert_fixed_message(events, setting_key=f'community_events:{guild.id}', content=EVENTS_INTRO, pin=True,
            recover_match=lambda m: (m.content or '').startswith('# 🎉 Community Events'))


async def migrate_boards(guild, changed, failed):
    from cogs.suggestions import STAFF_ALIASES, private_overwrites
    start, community = unique(guild.categories, 'start-here'), unique(guild.categories, 'community')
    # Resolve all targets before mutating; never adopt a per-game LFG channel.
    channels = {name: core_channel(guild, name) for name in ('looking-for-group', 'guide', 'suggestions', *EVENT_BOARDS, 'introductions')}
    for name in ('guide', 'suggestions', 'community-events'):
        if not channels[name] and unique(guild.text_channels, name):
            raise ServerMessageError(f'#{name} exists outside the core categories; review its location before setup. No duplicate was created.')
    event = channels['community-events']
    matches = [c for c in guild.text_channels if alias(c.name) == 'community-events']
    if event and any(c.id != event.id for c in matches):
        raise ServerMessageError('Conflicting community-events IDs/names; review manually. No duplicate was created.')
    if event and (not event.category or alias(event.category.name) not in {'events', 'start-here', 'community'}):
        raise ServerMessageError('community-events is outside public core categories; review before making it public.')
    if event and not db.get_setting(resource_key(guild, 'community-events')) and (
        event.overwrites_for(guild.default_role).view_channel is False
        or event.category.overwrites_for(guild.default_role).view_channel is False
    ):
        raise ServerMessageError('Unmapped community-events is private; review before adopting it as a public board.')
    events = unique(guild.categories, 'events')
    staff_categories = [c for c in guild.categories if alias(c.name) in STAFF_ALIASES]
    if len(staff_categories) > 1:
        raise ServerMessageError('Multiple STAFF categories found; review manually.')
    staff = staff_categories[0] if staff_categories else None
    staff_inbox = unique(guild.text_channels, 'staff-suggestions')
    if not events:
        events = await guild.create_category('🏆 EVENTS', reason='GamerHQ tournaments and giveaways')
        changed.append('Created EVENTS')
    for name, target in [('looking-for-group', start), ('tournaments', events), ('giveaways', events), ('introductions', community)]:
        channel = channels[name]
        if channel and channel.category_id != target.id:
            channels[name] = await channel.edit(category=target, sync_permissions=False, reason='GamerHQ core channel organization')
            changed.append(f'Moved {name} → {target.name}; ID/history/overrides preserved')
        elif not channel:
            failed.append(f'Existing #{name} not found; no replacement/history created.')
    for name, target in [('guide', start), ('suggestions', community), ('community-events', events)]:
        channel = channels[name]
        if not channel:
            # Visible but read-only from creation, including bot access.
            import discord
            from types import SimpleNamespace
            from services.onboarding_service import guide_overwrites
            empty = SimpleNamespace(guild=guild, name=name, overwrites={}, overwrites_for=lambda target: discord.PermissionOverwrite())
            overwrites = guide_overwrites(empty)
            display = {'guide': '📘・guide', 'community-events': '🎉・community-events'}.get(name, name)
            channel = await target.create_text_channel(display, overwrites=overwrites, reason='GamerHQ managed board')
            channels[name] = channel
            db.set_setting(resource_key(guild, name), channel.id)
            changed.append(f'Created {name}')
        elif channel.category_id != target.id:
            channels[name] = await channel.edit(category=target, sync_permissions=False, reason='GamerHQ managed board location')
    if channels['guide'].name != '📘・guide':
        channels['guide'] = await channels['guide'].edit(name='📘・guide', reason='GamerHQ guide naming')
    if channels['community-events'].name != '🎉・community-events':
        channels['community-events'] = await channels['community-events'].edit(name='🎉・community-events', reason='GamerHQ community events naming')
    if channels['looking-for-group']:
        await channels['looking-for-group'].edit(name='🎯・looking-for-group', reason='GamerHQ LFG naming')
    for name in ('looking-for-group', 'guide', 'suggestions', 'community-events'):
        if channels[name]:
            await set_read_only(channels[name])
    if channels['introductions']:
        await set_writable(channels['introductions'])
    if not staff:
        failed.append('No suitable STAFF category exists; staff-suggestions was not created. Create/review STAFF, then rerun setup. Submissions remain unavailable.')
    else:
        if not staff_inbox:
            staff_inbox = await staff.create_text_channel('staff-suggestions', overwrites=private_overwrites(guild), reason='GamerHQ private suggestion inbox')
            changed.append('Created private STAFF/staff-suggestions')
        else:
            # Privacy and move in the same API operation, never temporarily public.
            await staff_inbox.edit(category=staff, sync_permissions=False, overwrites=private_overwrites(guild, staff_inbox), reason='GamerHQ private suggestion inbox')
        db.set_setting(f'staff_suggestions_channel:{guild.id}', staff_inbox.id)
    for name in ('guide', 'suggestions', 'looking-for-group', *EVENT_BOARDS, 'bot-commands', 'choose-your-games', 'choose-your-roles'):
        channel = channels.get(name) or core_channel(guild, name)
        if channel:
            db.set_setting(resource_key(guild, name), channel.id)
    current, ordered = event_order(guild, await guild.fetch_channels())
    if [c.id for c in current] != [c.id for c in ordered]:
        from services.channel_change_service import bulk_positions
        await bulk_positions(guild, [{'id': c.id, 'position': i} for i, c in enumerate(ordered)],
                             reason='GamerHQ community events before tournaments and giveaways')
    from services.support_service import repair_support
    await repair_support(guild, changed)
    from services.ticket_service import repair
    await repair(guild, changed, failed)
    await refresh_boards(guild)


async def recover_core_ids(guild):
    """Explicit repair can recover pre-registry messages by stored ID plus author/marker."""
    import discord
    for name, message_key, prefix in (
        ('guide', f'central_guide:{guild.id}', '# 📘 GamerHQ Guide'),
        ('suggestions', f'suggestions_entry:{guild.id}', '💡 Suggestions'),
    ):
        if db.get_setting(resource_key(guild, name)):
            continue
        raw = db.get_setting(message_key)
        if not raw or not str(raw).isdigit():
            continue
        for channel in guild.text_channels:
            if not channel.category or alias(channel.category.name) not in {'start-here','community'}:
                continue
            try:
                message = await channel.fetch_message(int(raw))
            except discord.NotFound:
                continue
            if guild.me and message.author.id == guild.me.id and (message.content or '').startswith(prefix):
                db.set_setting(resource_key(guild, name), channel.id)
                break
