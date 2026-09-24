"""Incremental core-board migration, invoked only by owner setup."""
from database import db
from services.onboarding_service import alias, unique, set_read_only, set_writable
from services.server_service import ServerMessageError, upsert_fixed_message


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
        '## 🏆 Events\n**Coming Soon** — tournaments, giveaways and upcoming community events in **EVENTS**.\n\n'
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


async def migrate_boards(guild, changed, failed):
    from cogs.suggestions import STAFF_ALIASES, private_overwrites
    start, community = unique(guild.categories, 'start-here'), unique(guild.categories, 'community')
    # Resolve all targets before mutating; never adopt a per-game LFG channel.
    channels = {name: core_channel(guild, name) for name in ('looking-for-group', 'guide', 'suggestions', 'tournaments', 'giveaways', 'introductions')}
    for name in ('guide', 'suggestions'):
        if not channels[name] and unique(guild.text_channels, name):
            raise ServerMessageError(f'#{name} exists outside the core categories; review its location before setup. No duplicate was created.')
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
    for name, target in [('guide', start), ('suggestions', community)]:
        channel = channels[name]
        if not channel:
            # Visible but read-only from creation, including bot access.
            import discord
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False, send_messages_in_threads=False, create_public_threads=False, create_private_threads=False)}
            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            channel = await target.create_text_channel('📘・guide' if name == 'guide' else name, overwrites=overwrites, reason='GamerHQ managed board')
            channels[name] = channel
            changed.append(f'Created {name}')
        elif channel.category_id != target.id:
            channels[name] = await channel.edit(category=target, sync_permissions=False, reason='GamerHQ managed board location')
    if channels['guide'].name != '📘・guide':
        channels['guide'] = await channels['guide'].edit(name='📘・guide', reason='GamerHQ guide naming')
    if channels['looking-for-group']:
        await channels['looking-for-group'].edit(name='🎯・looking-for-group', reason='GamerHQ LFG naming')
    for name in ('looking-for-group', 'guide', 'suggestions'):
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
    for name in ('guide', 'suggestions', 'looking-for-group', 'tournaments', 'giveaways', 'bot-commands', 'choose-your-games', 'choose-your-roles'):
        channel = channels.get(name) or core_channel(guild, name)
        if channel:
            db.set_setting(resource_key(guild, name), channel.id)
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
