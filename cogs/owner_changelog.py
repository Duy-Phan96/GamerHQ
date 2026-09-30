"""Per-change STAFF notices with owner-only details and confirmed Undo."""
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
    """At most three explicit child overwrites; never enumerate all server roles.

    Administrator bypasses Discord ACLs. Notices must therefore contain only
    generic metadata; private details/actions are protected in the application.
    """
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
    action = {
        "channel_update": "Channel changed", "channel_delete": "Channel deleted",
        "category_update": "Category changed", "category_delete": "Category deleted",
        "message_delete": "Managed message deleted", "security_review": "Security review",
    }.get(change["action"], change["action"].replace("_", " ").title())
    return f"#{change['id']} · {action}"


def render(guild):
    # Administrator can read channel messages. Never put detailed history here.
    return (
        HEADER + "\n\nNew tracked server changes appear below as **individual messages** with "
        "**Undo** and **Details** buttons. Undo opens a private review before confirmation.\n\n"
        "Discord Administrators can see these generic notices and this launcher. "
        "Resource names, actors, before/after details and actions are available only to the current owner.\n\n"
        "Use **Review / Undo** for earlier history. Deleted channel history cannot be recovered; "
        "some deleted resources offer a replacement instead."
    )


async def refresh_board(guild, *, publish=False):
    channel = destination(guild)
    if channel is None:
        return False
    if publish:
        feed.enable(guild)  # Setup enables future notices, not a historical message dump.
    else:
        # Existing structure._announce calls this immediately after recording a change.
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


def details(change):
    def safe(value):
        return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:240]
    def describe(state):
        if state.get("deleted"):
            return "Deleted"
        parts = [safe(state.get("name", state.get("message_id", "n/a")))]
        if "category_id" in state:
            parts.append("Category ID: " + safe(state["category_id"]))
        if "position" in state:
            parts.append("Position: " + safe(state["position"]))
        return " · ".join(parts)
    actor = str(change["actor_id"]) if change.get("actor_id") else "unknown"
    return (
        f"# {safe(label(change))}\nResource: {safe(change['logical_key'])}\n"
        f"Status: **{safe(change['status'])}**\nActor ID: {safe(actor)}\n\n"
        f"**Before:** {describe(change['before'])}\n**After:** {describe(change['after'])}\n\n"
        "Review before confirming. Later conflicting edits block Undo. "
        "A replacement does not recover deleted channel history."
    )


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
    """Shared persistent handlers; message identity is resolved in SQLite, not RAM."""
    def __init__(self, change=None):
        super().__init__(timeout=None)
        if change is not None:
            self.undo.disabled = not feed.can_undo(change)
            if change.get("status") in {"UNDONE", "RESTORED"}:
                self.undo.label = "Handled"

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
        await interaction.edit_original_response(content=details(change),
            view=Undo(guild.id, interaction.user.id, change), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Undo", emoji="↩️", custom_id="gamerhq:owner_changelog:notice:undo",
                       style=discord.ButtonStyle.primary)
    async def undo(self, interaction, button):
        await self.open_change(interaction)

    @discord.ui.button(label="Details", custom_id="gamerhq:owner_changelog:notice:details",
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
        text = ("Select a tracked change to review." if rows else
                "No tracked changes yet. New managed-resource changes will appear automatically in this channel.")
        await interaction.response.send_message("# Owner Change History\n" + text,
            view=ChangeList(interaction.guild.id, interaction.user.id, rows), ephemeral=True)


class ChangeList(OwnerSession):
    def __init__(self, guild_id, owner_id, rows):
        super().__init__(guild_id, owner_id, timeout=240)
        if rows:
            select = discord.ui.Select(placeholder="Select a change", options=[
                discord.SelectOption(label=label(row)[:100], value=str(row["id"]),
                    description=(row["logical_key"] + " · " + row["status"])[:100]) for row in rows[:25]])

            async def choose(interaction):
                if not await self.interaction_check(interaction):
                    return
                change = structure.get_change(interaction.guild, int(select.values[0]))
                if not change:
                    return await interaction.response.send_message("Change no longer available.", ephemeral=True)
                await interaction.response.send_message(details(change),
                    view=Undo(interaction.guild.id, interaction.user.id, change),
                    ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            select.callback = choose
            self.add_item(select)


class Undo(OwnerSession):
    def __init__(self, guild_id, owner_id, change):
        super().__init__(guild_id, owner_id, timeout=120)
        self.change_id, self.snapshot = change["id"], copy.deepcopy(change)
        self.used, self.expires = False, time.monotonic() + 120
        button = discord.ui.Button(label="Confirm Undo", emoji="↩️", style=discord.ButtonStyle.danger,
                                   disabled=not feed.can_undo(change))

        async def callback(interaction):
            if not await self.claim(interaction):
                return
            await interaction.response.defer(ephemeral=True)
            try:
                self.check_current(interaction.guild)
                await structure.undo_change(interaction.guild, interaction.user, self.change_id)
                await refresh_board(interaction.guild)
                await interaction.edit_original_response(content=f"✅ Change #{self.change_id} was undone.", view=None)
            except (ValueError, discord.HTTPException, ServerMessageError) as exc:
                await interaction.edit_original_response(content=str(exc), view=None)
            finally:
                self.stop()
        button.callback = callback
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
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

        async def cancel_callback(interaction):
            if not await self.interaction_check(interaction):
                return
            self.used = True
            await interaction.response.edit_message(content="Cancelled. No changes made.", view=None)
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
    if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Only the server owner can configure the Owner Change Log.", ephemeral=True)
    try:
        parent = staff_category(interaction.guild)
        if interaction.guild.me is None:
            raise ValueError("Bot membership is unavailable; retry after startup.")
        raw = db.get_setting(key(interaction.guild))
        current = interaction.guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
        if raw and not isinstance(current, discord.TextChannel):
            raise ValueError("The saved Owner Change Log channel is unavailable. Review its mapping; no duplicate was created.")
    except ValueError as exc:
        return await interaction.response.send_message(str(exc), ephemeral=True)
    await interaction.response.send_message(
        f"{HEADER}\nCreate or repair **{LABEL}** under **{parent.name}**? "
        "New tracked changes get individual notices with Undo/Details buttons. "
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
