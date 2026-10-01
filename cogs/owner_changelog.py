"""Readable STAFF notices with owner-only details and confirmed Undo."""
from __future__ import annotations

import asyncio
import copy
import logging
import time

import discord
from discord.ext import commands, tasks

from database import db
from services.onboarding_service import alias
from services.response_service import SafeView
from services.server_service import ServerMessageError
from services import structure_adoption_service as structure
from services import owner_change_feed as feed
from services import owner_change_display as display

NAME = "owner-changelog"
LABEL = "🕘・owner-changelog"
HEADER = "# 🕘 Owner Change Log"
_locks = {}
_setup_locks = {}
log = logging.getLogger(__name__)


def key(guild):
    return f"managed_channel:{guild.id}:{NAME}"


def board_key(guild):
    return f"owner_changelog_board:{guild.id}"


def owner_rights(guild, resource=None):
    """Compact ACL; Administrator bypass is handled by private owner-only review."""
    if guild.me is None:
        raise ValueError("Bot membership is unavailable; retry after startup.")
    result = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False, read_message_history=False, send_messages=False,
            create_public_threads=False, create_private_threads=False,
            send_messages_in_threads=False,
        ),
    }
    owner = guild.get_member(guild.owner_id)
    if owner:
        result[owner] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False,
            use_application_commands=True,
        )
    result[guild.me] = discord.PermissionOverwrite(
        view_channel=True, read_message_history=True, send_messages=True,
        manage_messages=True, embed_links=True,
    )
    return result


def staff_category(guild):
    raw = db.get_setting(f"managed_category:{guild.id}:staff")
    category = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if not isinstance(category, discord.CategoryChannel):
        matches = [c for c in guild.categories if alias(c.name) == "staff"]
        category = matches[0] if len(matches) == 1 else None
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError("Review the STAFF category first.")
    if category.overwrites_for(guild.default_role).view_channel is not False:
        raise ValueError("STAFF must remain private.")
    return category


def destination(guild):
    raw = db.get_setting(key(guild))
    channel = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if not isinstance(channel, discord.TextChannel) or guild.me is None:
        return None
    try:
        parent = staff_category(guild)
    except ValueError:
        return None
    if channel.category_id != parent.id:
        return None
    owner = guild.get_member(guild.owner_id)
    if channel.overwrites_for(guild.default_role).view_channel is not False:
        return None
    allowed_ids = {guild.owner_id, guild.me.id}
    if any(value.view_channel is True and target.id not in allowed_ids
           for target, value in channel.overwrites.items()):
        return None
    if owner and not channel.permissions_for(owner).view_channel:
        return None
    permissions = channel.permissions_for(guild.me)
    if not (permissions.view_channel and permissions.send_messages and permissions.read_message_history):
        return None
    return channel


def label(change):
    # Only used inside owner-authorized private history.
    return display.history_label(change)


def render(guild):
    return (
        HEADER + "\n\nServer changes appear below automatically. Related channel moves "
        "can be grouped with a category deletion. Open **View details** to see names and what changed.\n\n"
        "Discord Administrators can see these generic notices and this launcher. "
        "Names, detailed changes and actions remain private to the current owner.\n\n"
        "Use **Review / Undo** for earlier history. **Undo** is shown only when available "
        "and always requires confirmation. Deleted channel history cannot be recovered."
    )


async def refresh_board(guild, *, publish=False):
    channel = destination(guild)
    if channel is None:
        return False
    if publish:
        feed.enable(guild)
    else:
        await feed.flush(guild, include_latest=True)
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        channel = destination(guild)
        if channel is None:
            return False
        content = render(guild)
        from services.server_service import upsert_fixed_message
        if publish:
            await upsert_fixed_message(
                channel, setting_key=board_key(guild), content=content, pin=True,
                view=Entry(), allowed_mentions=discord.AllowedMentions.none(),
                recover_match=lambda m: (m.content or "").startswith(HEADER),
            )
            return True
        raw = db.get_setting(board_key(guild))
        if not raw or not str(raw).isdigit():
            return False
        try:
            message = await channel.fetch_message(int(raw))
        except (discord.NotFound, discord.HTTPException):
            return False
        if not guild.me or message.author.id != guild.me.id:
            return False
        if message.content != content:
            await message.edit(content=content, view=Entry(), allowed_mentions=discord.AllowedMentions.none())
        return True


def details(change, guild=None, *, related=(), page=0):
    return display.details(change, guild, related=related, page=page)


def private_panel(guild, owner_id, change):
    """Call only after the caller's current-owner check."""
    related = feed.related_changes(guild, change["id"])
    pages = display.detail_pages(change, guild, related=related)
    if related or len(pages) > 1:
        return pages[0], GroupDetails(guild.id, owner_id, change, related, guild=guild)
    return pages[0], Undo(guild.id, owner_id, change)


class OwnerSession(SafeView):
    def __init__(self, guild_id, owner_id, **kwargs):
        super().__init__(**kwargs)
        self.guild_id, self.owner_id = int(guild_id), int(owner_id)

    async def interaction_check(self, interaction):
        ok = bool(interaction.guild and interaction.guild.id == self.guild_id
                  and interaction.user.id == self.owner_id
                  and interaction.guild.owner_id == interaction.user.id)
        if not ok:
            await interaction.response.send_message("Only the server owner can use this panel.", ephemeral=True)
        return ok


class ChangeNotice(SafeView):
    """Persistent handlers resolve message identity in SQLite, not process memory."""
    def __init__(self, change=None):
        super().__init__(timeout=None)
        if change is not None:
            self.undo.disabled = not feed.can_undo(change)
            if self.undo.disabled:
                self.remove_item(self.undo)

    async def open_change(self, interaction):
        guild = interaction.guild
        if guild is None or interaction.user.id != guild.owner_id:
            return await interaction.response.send_message("Only the server owner can review or undo this change.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        channel = destination(guild)
        message = interaction.message
        if (interaction.user.id != guild.owner_id or channel is None or message is None
                or interaction.channel_id != channel.id or message.author.id != guild.me.id):
            return await interaction.edit_original_response(content="This notice is not available in the configured Owner Change Log.", view=None)
        change = feed.change_for_message(guild, channel.id, message.id)
        if change is None:
            return await interaction.edit_original_response(content="This notice is no longer linked. Open Review / Undo for the saved history.", view=None)
        content, view = private_panel(guild, interaction.user.id, change)
        await interaction.edit_original_response(content=content, view=view,
                                                  allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Undo", emoji="↩️", custom_id="gamerhq:owner_changelog:notice:undo",
                       style=discord.ButtonStyle.primary)
    async def undo(self, interaction, button):
        await self.open_change(interaction)

    @discord.ui.button(label="View details", custom_id="gamerhq:owner_changelog:notice:details",
                       style=discord.ButtonStyle.secondary)
    async def review(self, interaction, button):
        await self.open_change(interaction)


class Entry(SafeView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Review / Undo", emoji="↩️", custom_id="gamerhq:owner_changelog:open",
                       style=discord.ButtonStyle.primary)
    async def open(self, interaction, button):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("Only the server owner can use this panel.", ephemeral=True)
        rows = structure.recent_changes(interaction.guild, 25)
        text = ("Select a change to see what happened." if rows else
                "No tracked changes yet. Supported server changes will appear automatically here.")
        await interaction.response.send_message("# Owner Change History\n" + text,
            view=ChangeList(interaction.guild.id, interaction.user.id, rows), ephemeral=True)


class ChangeList(OwnerSession):
    def __init__(self, guild_id, owner_id, rows):
        super().__init__(guild_id, owner_id, timeout=240)
        if rows:
            select = discord.ui.Select(placeholder="Select a change", options=[
                discord.SelectOption(label=label(row), value=str(row["id"]),
                    description=f"{display.status(row)} · Change #{row['id']}"[:100]) for row in rows[:25]])

            async def choose(interaction):
                if not await self.interaction_check(interaction):
                    return
                change = structure.get_change(interaction.guild, int(select.values[0]))
                if not change:
                    return await interaction.response.send_message("Change no longer available.", ephemeral=True)
                content, view = private_panel(interaction.guild, interaction.user.id, change)
                await interaction.response.send_message(content, view=view, ephemeral=True,
                                                        allowed_mentions=discord.AllowedMentions.none())
            select.callback = choose
            self.add_item(select)


class GroupDetails(OwnerSession):
    """Owner-checked pages for a group or a long individual change."""
    def __init__(self, guild_id, owner_id, change, related, page=0, *, guild=None):
        super().__init__(guild_id, owner_id, timeout=240)
        self.change_id = change["id"]
        self.pages = len(display.detail_pages(change, guild, related=related))
        self.page = min(max(0, page), self.pages - 1)
        if self.pages == 1:
            self.remove_item(self.previous)
            self.remove_item(self.next_page)
        else:
            self.previous.disabled = self.page == 0
            self.next_page.disabled = self.page == self.pages - 1
        if related or not feed.can_undo(change):
            self.remove_item(self.review_undo)

    async def turn_page(self, interaction, offset):
        if not await self.interaction_check(interaction):
            return
        change = structure.get_change(interaction.guild, self.change_id)
        if change is None:
            return await interaction.response.edit_message(content="This change is no longer available.", view=None)
        related = feed.related_changes(interaction.guild, self.change_id)
        pages = display.detail_pages(change, interaction.guild, related=related)
        page = min(max(0, self.page + offset), len(pages) - 1)
        await interaction.response.edit_message(
            content=pages[page],
            view=GroupDetails(self.guild_id, self.owner_id, change, related, page, guild=interaction.guild),
            allowed_mentions=discord.AllowedMentions.none())
        self.stop()

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        await self.turn_page(interaction, -1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction, button):
        await self.turn_page(interaction, 1)

    @discord.ui.button(label="Review Undo", style=discord.ButtonStyle.primary)
    async def review_undo(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        change = structure.get_change(interaction.guild, self.change_id)
        if not change or not feed.can_undo(change) or feed.related_changes(interaction.guild, self.change_id):
            return await interaction.response.edit_message(content="Undo is no longer available. Reopen this change.", view=None)
        await interaction.response.edit_message(
            content=details(change, interaction.guild),
            view=Undo(self.guild_id, self.owner_id, change),
            allowed_mentions=discord.AllowedMentions.none())
        self.stop()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def close(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.edit_message(content="Closed.", view=None)
        self.stop()


class Undo(OwnerSession):
    def __init__(self, guild_id, owner_id, change):
        super().__init__(guild_id, owner_id, timeout=120)
        self.change_id, self.snapshot = change["id"], copy.deepcopy(change)
        self.used, self.expires = False, time.monotonic() + 120
        available = feed.can_undo(change)
        button = discord.ui.Button(label="Confirm Undo", emoji="↩️", style=discord.ButtonStyle.danger,
                                   disabled=not available)

        async def callback(interaction):
            if not await self.claim(interaction):
                return
            await interaction.response.defer(ephemeral=True)
            try:
                self.check_current(interaction.guild)
                from services import server_change_observer as observer
                if observer.is_observation(self.snapshot):
                    await observer.undo(interaction.guild, interaction.user, self.change_id, interaction.client)
                else:
                    await observer.undo_legacy(interaction.guild, interaction.user, self.change_id)
                await refresh_board(interaction.guild)
                await interaction.edit_original_response(content=f"✅ Change #{self.change_id} was undone.", view=None)
            except (ValueError, discord.HTTPException, ServerMessageError) as exc:
                await interaction.edit_original_response(content=str(exc), view=None)
            finally:
                self.stop()
        button.callback = callback
        if available:
            self.add_item(button)

        if change["action"] == "channel_delete" and change["status"] == "APPLIED":
            restore_button = discord.ui.Button(label="Confirm Replacement", emoji="♻️", style=discord.ButtonStyle.secondary)

            async def restore_callback(interaction):
                if not await self.claim(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    self.check_current(interaction.guild)
                    from services import resource_restore_service as restore_service
                    draft = restore_service.preview(interaction.guild, interaction.user, change["logical_key"])
                    resource = await restore_service.restore(interaction.guild, interaction.user, draft)
                    with db.connect() as conn:
                        conn.execute("UPDATE structure_change_log SET status='RESTORED',undone_at=?,undone_by=? "
                                     "WHERE guild_id=? AND id=? AND status='APPLIED'",
                                     (int(time.time()), interaction.user.id, interaction.guild.id, self.change_id))
                    await refresh_board(interaction.guild)
                    await interaction.edit_original_response(content=f"✅ Replacement restored: {resource.mention}\n"
                        "The deleted Discord channel's old message history cannot be recovered.", view=None)
                except (ValueError, discord.HTTPException, ServerMessageError) as exc:
                    await interaction.edit_original_response(content=str(exc), view=None)
                finally:
                    self.stop()
            restore_button.callback = restore_callback
            self.add_item(restore_button)
        cancel = discord.ui.Button(label="Cancel" if self.children else "Close", style=discord.ButtonStyle.secondary)

        async def cancel_callback(interaction):
            if not await self.interaction_check(interaction):
                return
            self.used = True
            await interaction.response.edit_message(content="Closed. No changes made.", view=None)
            self.stop()
        cancel.callback = cancel_callback
        self.add_item(cancel)

    async def claim(self, interaction):
        if not await self.interaction_check(interaction):
            return False
        if self.used or time.monotonic() > self.expires:
            await interaction.response.send_message("This confirmation expired or was used. Open the notice again.", ephemeral=True)
            return False
        self.used = True
        return True

    def check_current(self, guild):
        if guild.owner_id != self.owner_id:
            raise ValueError("Server ownership changed. Reopen the review.")
        current = structure.get_change(guild, self.change_id)
        if not current or current != self.snapshot:
            raise ValueError("This change was already handled or changed. Open a fresh review.")


async def open_management(interaction):
    guild = interaction.guild
    if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Only the server owner can configure the Owner Change Log.", ephemeral=True)
    try:
        parent = staff_category(interaction.guild)
        if interaction.guild.me is None:
            raise ValueError("Bot membership is unavailable; retry after startup.")
        raw = db.get_setting(key(interaction.guild))
        current = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
        if raw and not isinstance(current, discord.TextChannel):
            raise ValueError("The saved Owner Change Log channel is unavailable. Review its mapping; no duplicate was created.")
    except ValueError as exc:
        return await interaction.response.send_message(str(exc), ephemeral=True)
    await interaction.response.send_message(
        f"{HEADER}\nCreate or repair **{LABEL}** under **{parent.name}**? "
        "New tracked changes get notices with View details and available Undo buttons. "
        "Administrators can see generic notices; details and actions are private to you. "
        "This replaces only this channel's access entries with a compact owner/bot allowlist.",
        view=Setup(interaction.guild.id, interaction.user.id, parent.id, current.id if current else None), ephemeral=True)


class Setup(OwnerSession):
    def __init__(self, guild_id, owner_id, parent_id, current_id):
        super().__init__(guild_id, owner_id, timeout=120)
        self.parent_id, self.current_id = parent_id, current_id
        self.used, self.expires = False, time.monotonic() + 120

    @discord.ui.button(label="Confirm Owner Log", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        if self.used or time.monotonic() > self.expires:
            return await interaction.response.send_message("This setup review expired or was used. Open a new review.", ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            async with _setup_locks.setdefault(guild.id, asyncio.Lock()):
                if interaction.user.id != guild.owner_id:
                    raise ValueError("Server ownership changed. Reopen setup as the current owner.")
                raw = db.get_setting(key(guild))
                if str(raw or "") != str(self.current_id or ""):
                    raise ValueError("The saved channel changed. Reopen setup; no duplicate was created.")
                parent = staff_category(guild)
                if parent.id != self.parent_id:
                    raise ValueError("STAFF changed. Reopen this setup.")
                current = guild.get_channel(self.current_id) if self.current_id else None
                if self.current_id and not isinstance(current, discord.TextChannel):
                    raise ValueError("The saved channel disappeared. Review its mapping; no replacement was created.")
                if current is None:
                    matches = [c for c in guild.text_channels if alias(c.name) == NAME]
                    if matches:
                        raise ValueError("An unmapped owner-changelog channel already exists. Review it before creating another.")
                    rights = owner_rights(guild)
                    reservation = f"owner_changelog_creation:{guild.id}"
                    with db.connect() as conn:
                        if not conn.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (reservation, 'reserved')).rowcount:
                            raise ValueError("A previous creation needs review. No duplicate was created.")
                    try:
                        current = await guild.create_text_channel(
                            LABEL, category=parent, overwrites=rights,
                            topic="Automatic change notices. Details and confirmed Undo are owner-only private responses.",
                            reason="Confirmed GamerHQ owner change log setup")
                    except discord.HTTPException as exc:
                        if exc.status in (400, 403):
                            with db.connect() as conn:
                                conn.execute('DELETE FROM settings WHERE key=?', (reservation,))
                        raise
                    db.set_setting(key(guild), current.id)
                    with db.connect() as conn:
                        conn.execute('DELETE FROM settings WHERE key=?', (reservation,))
                else:
                    updated = await current.edit(category=parent, sync_permissions=False,
                        overwrites=owner_rights(guild, current), reason="Confirmed GamerHQ owner change log repair")
                    if updated is not None:
                        current = updated
                from services.server_operations import GuildSnapshot
                snapshot = GuildSnapshot(guild, [c for c in guild.channels if c.id != current.id] + [current], guild.roles)
                if not await refresh_board(snapshot, publish=True):
                    raise ValueError("The channel was saved, but its private launcher needs review. Reopen Owner Change Log to retry the same channel.")
            await interaction.edit_original_response(content=f"✅ Owner Change Log ready: {current.mention}", view=None)
        except (ValueError, discord.HTTPException, ServerMessageError) as exc:
            text = str(exc) if isinstance(exc, (ValueError, ServerMessageError)) else "Discord could not finish the Owner Change Log setup. Reopen the panel to review the saved state."
            await interaction.edit_original_response(content=text, view=None)
        finally:
            self.stop()


class OwnerChangeLog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(Entry())
        self.bot.add_view(ChangeNotice())
        self.feed_updates.start()

    def cog_unload(self):
        self.feed_updates.cancel()
        feed.cancel_pending_flushes()

    @tasks.loop(seconds=60)
    async def feed_updates(self):
        for guild in self.bot.guilds:
            try:
                await feed.flush(guild)
            except Exception:
                log.warning("Owner change feed recovery deferred guild=%s", guild.id)

    @feed_updates.before_loop
    async def before_feed_updates(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                if destination(guild) is not None:
                    feed.enable(guild)
                    await refresh_board(guild)
            except Exception:
                log.warning("Owner change feed initialization deferred guild=%s", guild.id)


async def setup(bot):
    await bot.add_cog(OwnerChangeLog(bot))
