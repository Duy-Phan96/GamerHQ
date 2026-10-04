"""Private STAFF lobby overview and explicitly confirmed administrator closure.

No production credentials, automatic provisioning, or host impersonation. The
existing event tables, lifecycle cleanup and managed-message helpers are reused.
"""
import asyncio
import json
import logging
import time

import discord
from discord.ext import commands, tasks

from database import db
from services import lobby_service as rules
from services.lobby_dashboard import event_lock, cleanup_ended
from services.onboarding_service import alias
from services.response_service import SafeView

log = logging.getLogger(__name__)
NAME = 'lobby-admin'
LABEL = '📋・lobby-admin'
HEADER = '# 📋 Lobby Administration'
PAGE_SIZE = 6
_OPEN = "(status='scheduled' OR (status IN ('cancelled','completed') AND (voice_channel_id IS NOT NULL OR private_channel_id IS NOT NULL)))"
_locks = {}
_signatures = {}


def key(guild, name=NAME):
    return f'managed_channel:{guild.id}:{name}'


def board_key(guild):
    return f'lobby_admin_board:{guild.id}'


def admin(guild, member):
    return bool(guild and member and not member.bot
                and getattr(getattr(member, 'guild', None), 'id', None) == guild.id
                and (member.id == guild.owner_id or member.guild_permissions.administrator))


async def current_admin(guild, user_id):
    member = await guild.fetch_member(user_id)
    if not admin(guild, member):
        raise ValueError('Only the server owner or an administrator can do this.')
    return member


def allowed_target(guild, target):
    if guild.me and target.id == guild.me.id:
        return True
    if isinstance(target, discord.Role):
        return not target.is_default() and target.permissions.administrator
    return admin(guild, target)


def private_rights(guild, resource=None):
    result = {target: discord.PermissionOverwrite.from_pair(*value.pair())
              for target, value in getattr(resource, 'overwrites', {}).items()}
    for target in set(result) | {guild.default_role} | {r for r in guild.roles if r.permissions.administrator}:
        value = result.setdefault(target, discord.PermissionOverwrite())
        value.view_channel = bool(allowed_target(guild, target))
        value.read_message_history = value.view_channel
        value.send_messages = value.view_channel
        value.use_application_commands = value.view_channel
        value.create_public_threads = value.create_private_threads = value.send_messages_in_threads = False
    if guild.me:
        result[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
            read_message_history=True, manage_messages=True, embed_links=True)
    return result


def staff_category(guild):
    raw = db.get_setting(f'managed_category:{guild.id}:staff')
    if raw:
        found = guild.get_channel(int(raw)) if str(raw).isdigit() else None
        if not isinstance(found, discord.CategoryChannel):
            raise ValueError('The saved STAFF category needs review in Server Structure.')
    else:
        matches = [c for c in guild.categories if alias(c.name) == 'staff']
        if len(matches) != 1:
            raise ValueError('Review the existing STAFF category in Server Structure first.')
        found = matches[0]
    if found.overwrites_for(guild.default_role).view_channel is not False:
        raise ValueError('STAFF must be private before setting up lobby administration.')
    return found


def destination(guild):
    raw = db.get_setting(key(guild))
    channel = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if not isinstance(channel, discord.TextChannel) or not guild.me:
        return None
    try:
        if channel.category_id != staff_category(guild).id:
            return None
    except ValueError:
        return None
    if channel.overwrites_for(guild.default_role).view_channel is not False:
        return None
    if any(rights.view_channel is True and not allowed_target(guild, target)
           for target, rights in channel.overwrites.items()):
        return None
    permissions = channel.permissions_for(guild.me)
    return channel if permissions.view_channel and permissions.send_messages and permissions.read_message_history else None


def inventory(guild, page=0):
    """Bounded SQL and gateway state only. No member fetches or history scans."""
    with db.read_only(), db.connect() as conn:
        total = conn.execute(f'SELECT count(*) FROM lfg_events WHERE guild_id=? AND {_OPEN}', (guild.id,)).fetchone()[0]
        page = min(max(0, page), max(0, (total - 1) // PAGE_SIZE))
        rows = [dict(r) for r in conn.execute(
            f'SELECT e.id,e.guild_id,e.host_id,e.title,e.start_at,e.max_players,e.visibility,e.status,'
            f'e.private_channel_id,e.voice_channel_id,'
            f"(SELECT count(*) FROM lfg_event_members m WHERE m.event_id=e.id AND m.status='joined') AS joined "
            f'FROM lfg_events e WHERE e.guild_id=? AND {_OPEN} ORDER BY e.start_at,e.id LIMIT ? OFFSET ?',
            (guild.id, PAGE_SIZE, page * PAGE_SIZE))]
    return rows, total, page


def voice_label(guild, event):
    if not event.get('voice_channel_id'):
        return 'no voice channel'
    voice = guild.get_channel(event['voice_channel_id'])
    if not isinstance(voice, discord.VoiceChannel) or guild.unavailable:
        return 'voice state unavailable — no forced cleanup'
    return f'{len(voice.members)} in voice'


def overview(guild, page=0):
    rows, total, page = inventory(guild, page)
    lines = [HEADER, f'{total} open or awaiting cleanup · Page {page + 1}/{max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}', '']
    for event in rows:
        title = discord.utils.escape_markdown(discord.utils.escape_mentions(event['title'].replace('\n', ' ')))[:60]
        status = 'OPEN' if event['status'] == 'scheduled' else 'CLOSED · cleanup pending'
        lines.append(f"**#{event['id']} · {title}**\n{status} · {event['visibility'].title()} · "
                     f"<@{event['host_id']}> · <t:{event['start_at']}:f>\n"
                     f"{event['joined']}/{event['max_players']} participants · {voice_label(guild, event)}")
    if not rows:
        lines.append('✅ No open events or pending event rooms.')
    lines.append('\nOpen the private overview to select an event. Closing requires confirmation. Occupied voice rooms are never forcibly deleted.')
    return '\n\n'.join(lines), rows, total, page


def fingerprint(event):
    fields = ('id', 'guild_id', 'host_id', 'title', 'start_at', 'max_players', 'visibility', 'private_channel_id', 'voice_channel_id')
    return tuple(event.get(field) for field in fields)


def close_record(guild, actor, event_id, expected):
    """Administrative exception, not a bypass of the ordinary host-only API."""
    if not admin(guild, actor):
        raise ValueError('Administrator access is required.')
    with db.connect() as conn:
        event = rules._event(conn, event_id, guild.id)
        if fingerprint(event) != expected:
            raise ValueError('The event changed. Open a new review before closing it.')
        now = int(time.time())
        conn.execute("UPDATE lfg_events SET status='cancelled',ended_at=?,share_enabled=0 WHERE id=? AND guild_id=?", (now, event_id, guild.id))
        conn.execute("UPDATE lfg_time_proposals SET status='EXPIRED' WHERE event_id=? AND status='PENDING'", (event_id,))
        # Atomic audit survives delivery failures; no titles, notes or invite secrets.
        conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
            (f'lobby_admin_close:{guild.id}:{event_id}', json.dumps({'actor_id': actor.id, 'event_id': event_id,
              'closed_at': now, 'action': 'admin_close', 'previous_status': event['status']})))
    return db.get_lfg_event(event_id)


async def refresh_board(guild, *, publish=False):
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        channel = destination(guild)
        if channel is None:
            return False
        content, _, _, _ = overview(guild)
        signature = (channel.id, db.get_setting(board_key(guild)), content)
        if not publish and _signatures.get(guild.id) == signature:
            return True
        if publish:
            from services.server_service import upsert_fixed_message
            message = await upsert_fixed_message(channel, setting_key=board_key(guild), content=content,
                pin=True, view=AdminEntry(), allowed_mentions=discord.AllowedMentions.none(),
                recover_match=lambda m: (m.content or '').startswith(HEADER))
        else:
            raw = db.get_setting(board_key(guild))
            if not raw or not str(raw).isdigit():
                return False
            try:
                message = await channel.fetch_message(int(raw))
            except discord.NotFound:
                return False
            if message.author.id != guild.me.id or not (message.content or '').startswith(HEADER):
                return False
            if message.content != content:
                if destination(guild) is None:
                    return False
                await message.edit(content=content, view=AdminEntry(), allowed_mentions=discord.AllowedMentions.none())
        _signatures[guild.id] = (channel.id, str(message.id), content)
        return True


class AdminSession(SafeView):
    def __init__(self, guild_id, actor_id, **kwargs):
        super().__init__(**kwargs)
        self.guild_id, self.actor_id = guild_id, actor_id

    async def interaction_check(self, interaction):
        valid = (interaction.guild and interaction.guild.id == self.guild_id
                 and interaction.user.id == self.actor_id and admin(interaction.guild, interaction.user))
        if not valid:
            await interaction.response.send_message('This private administration panel is not available to you.', ephemeral=True)
        return bool(valid)


async def open_overview(interaction):
    if not admin(interaction.guild, interaction.user):
        return await interaction.response.send_message('Only the server owner or an administrator can open this overview.', ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    content, rows, total, page = overview(interaction.guild)
    await interaction.edit_original_response(content=content,
        view=AdminList(interaction.guild.id, interaction.user.id, rows, total, page), allowed_mentions=discord.AllowedMentions.none())


class AdminEntry(SafeView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='View / Manage Lobbies', emoji='📋', custom_id='gamerhq:lobby_admin:open', style=discord.ButtonStyle.primary)
    async def open(self, interaction, button):
        await open_overview(interaction)


class AdminList(AdminSession):
    def __init__(self, guild_id, actor_id, rows, total, page):
        super().__init__(guild_id, actor_id, timeout=240)
        self.page = page
        active = [r for r in rows if r['status'] == 'scheduled']
        if active:
            picker = discord.ui.Select(placeholder='Select an event to close', options=[discord.SelectOption(
                label=f"#{r['id']} · {r['title']}"[:100], value=str(r['id'])) for r in active])
            async def choose(interaction):
                if not await self.interaction_check(interaction): return
                event = db.get_lfg_event(int(picker.values[0]))
                if not event or event['guild_id'] != self.guild_id or event['status'] != 'scheduled':
                    return await interaction.response.send_message('This event is no longer open. Refresh the overview.', ephemeral=True)
                title = discord.utils.escape_markdown(discord.utils.escape_mentions(event['title']))
                await interaction.response.send_message(
                    f"Close **#{event['id']} · {title}** hosted by <@{event['host_id']}>?\n"
                    'This ends the event and disables its event invite. Its private chat and history will be deleted once its voice is empty. '
                    'Occupied voice and the associated chat are retained until everyone leaves. Shared community channels are never deleted.',
                    view=CloseConfirm(self.guild_id, self.actor_id, event), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            picker.callback = choose
            self.add_item(picker)
        for label, delta in [('Previous', -1), ('Refresh', 0), ('Next', 1)]:
            button = discord.ui.Button(label=label, disabled=page + delta < 0 or (delta == 1 and (page + 1) * PAGE_SIZE >= total))
            async def navigate(interaction, delta=delta):
                if not await self.interaction_check(interaction): return
                await interaction.response.defer()
                content, new_rows, new_total, new_page = overview(interaction.guild, self.page + delta)
                await interaction.edit_original_response(content=content,
                    view=AdminList(self.guild_id, self.actor_id, new_rows, new_total, new_page), allowed_mentions=discord.AllowedMentions.none())
            button.callback = navigate
            self.add_item(button)


class CloseConfirm(AdminSession):
    def __init__(self, guild_id, actor_id, event):
        super().__init__(guild_id, actor_id, timeout=120)
        self.event_id, self.expected = event['id'], fingerprint(event)
        self.expires, self.used = time.monotonic() + 120, False

    @discord.ui.button(label='Confirm Close Event', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction): return
        if self.used or time.monotonic() > self.expires:
            return await interaction.response.send_message('This confirmation was used or expired. Open a new review.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            async with event_lock(self.event_id):
                actor = await current_admin(interaction.guild, interaction.user.id)
                event = close_record(interaction.guild, actor, self.event_id, self.expected)
        except (ValueError, discord.HTTPException) as exc:
            text = str(exc) if isinstance(exc, ValueError) else 'Administrator access could not be verified. Nothing was closed.'
            return await interaction.edit_original_response(content=text, view=None)
        from cogs.lfg import refresh_event_posts
        for action in (lambda: refresh_event_posts(interaction.guild, self.event_id),
                       lambda: cleanup_ended(interaction.guild, event_id=self.event_id),
                       lambda: refresh_board(interaction.guild)):
            try:
                await action()
            except Exception:
                log.warning('Admin event close saved; presentation/cleanup pending guild=%s event=%s', self.guild_id, self.event_id)
        from services.server_log_service import emit
        await emit(interaction.guild, f'lobby-admin-close:{self.event_id}', 'Lobby Closed by Administrator',
            f'Event #{self.event_id} closed by <@{actor.id}>. Occupied voice rooms are retained.', management=False)
        await interaction.edit_original_response(content='✅ Event closed. Any occupied voice and its private chat remain until the voice is empty; cleanup retries automatically.', view=None)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if not await self.interaction_check(interaction): return
        self.used = True
        await interaction.response.edit_message(content='Cancelled. Nothing was closed.', view=None)
        self.stop()


async def open_management(interaction):
    if not admin(interaction.guild, interaction.user):
        return await interaction.response.send_message('Administrator access is required.', ephemeral=True)
    try:
        if interaction.guild.me is None:
            raise ValueError('Bot membership is unavailable; try again after startup.')
        parent = staff_category(interaction.guild)
    except ValueError as exc:
        return await interaction.response.send_message(str(exc), ephemeral=True)
    raw = db.get_setting(key(interaction.guild))
    current = interaction.guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if raw and not isinstance(current, discord.TextChannel):
        return await interaction.response.send_message('The saved lobby-admin channel is unavailable. Review its mapping; no duplicate was created.', ephemeral=True)
    from services.server_operations import wire
    view = SetupConfirm(interaction.guild.id, interaction.user.id, parent.id, wire(parent),
                        current.id if current else None, wire(current) if current else None)
    text = (f'{HEADER}\nCreate or update **{LABEL}** in **{parent.name}**?\n'
            'Only the owner, administrators and GamerHQ can read it. The pinned overview refreshes every minute. '
            'This setup never closes events or deletes channels. Unknown same-name channels are not adopted.')
    await interaction.response.send_message(text, view=view, ephemeral=True)


class SetupConfirm(AdminSession):
    def __init__(self, guild_id, actor_id, parent_id, parent_wire, channel_id, channel_wire):
        super().__init__(guild_id, actor_id, timeout=120)
        self.parent_id, self.parent_wire = parent_id, parent_wire
        self.channel_id, self.channel_wire = channel_id, channel_wire
        self.expires, self.used = time.monotonic() + 120, False

    @discord.ui.button(label='Confirm Admin Channel', style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction): return
        if self.used or time.monotonic() > self.expires:
            return await interaction.response.send_message('This setup review expired or was used.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            await current_admin(guild, interaction.user.id)
            from services.server_operations import wire
            async with _locks.setdefault(guild.id, asyncio.Lock()):
                channels = await guild.fetch_channels()
                parent = next((c for c in channels if c.id == self.parent_id), None)
                current = next((c for c in channels if c.id == self.channel_id), None)
                if not isinstance(parent, discord.CategoryChannel) or wire(parent) != self.parent_wire:
                    raise ValueError('STAFF changed. Open a new setup review.')
                raw = db.get_setting(key(guild))
                if str(raw or '') != str(self.channel_id or ''):
                    raise ValueError('The channel mapping changed. Open a new review.')
                if current and (not isinstance(current, discord.TextChannel) or wire(current) != self.channel_wire):
                    raise ValueError('The channel changed. Open a new review.')
                if self.channel_id and current is None:
                    raise ValueError('The existing channel disappeared. No replacement was created.')
                if any(alias(c.name) == NAME and c.id != self.channel_id for c in channels):
                    raise ValueError('Another lobby-admin channel exists. Review it before creating or linking anything.')
                await current_admin(guild, interaction.user.id)
                if current:
                    current = await current.edit(category=parent, sync_permissions=False,
                        overwrites=private_rights(guild, current), reason='Confirmed private lobby administration setup')
                else:
                    reservation = f'lobby_admin_creation:{guild.id}'
                    with db.connect() as conn:
                        if not conn.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (reservation, 'reserved')).rowcount:
                            raise ValueError('A previous channel creation needs review. No duplicate was created.')
                    current = await guild.create_text_channel(LABEL, category=parent, overwrites=private_rights(guild),
                        topic='GamerHQ private lobby administration. Owner and administrators only.', reason='Confirmed lobby administration setup')
                db.set_setting(f'managed_category:{guild.id}:staff', parent.id)
                db.set_setting(key(guild), current.id)
            # Discord may not have delivered the new channel to the gateway yet.
            # Publish using the returned object after validating its exact permissions.
            from services.server_operations import GuildSnapshot
            snapshot = GuildSnapshot(guild, [c for c in channels if c.id != current.id] + [current], guild.roles)
            await refresh_board(snapshot, publish=True)
            await interaction.edit_original_response(content=f'✅ Lobby administration ready: {current.mention}', view=None)
        except Exception as exc:
            log.warning('Lobby admin setup needs review guild=%s; no automatic duplicate creation.', guild.id)
            text = str(exc) if isinstance(exc, ValueError) else 'Setup could not be completed. Review the existing channel before retrying; no automatic duplicates will be created.'
            await interaction.edit_original_response(content=text, view=None)
        self.stop()

    @discord.ui.button(label='Cancel')
    async def cancel(self, interaction, button):
        if not await self.interaction_check(interaction): return
        self.used = True
        await interaction.response.edit_message(content='Cancelled. No channel changes.', view=None)
        self.stop()


class LobbyAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(AdminEntry())
        self.refresh.start()

    def cog_unload(self):
        self.refresh.cancel()

    @tasks.loop(seconds=60)
    async def refresh(self):
        for guild in self.bot.guilds:
            if guild.unavailable:
                continue
            try:
                await refresh_board(guild)
            except Exception:
                log.warning('Lobby admin overview unavailable guild=%s; use Server Management to review.', guild.id)

    @refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(LobbyAdmin(bot))
