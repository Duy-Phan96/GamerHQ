"""Owner-only STAFF change history and Undo controls."""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from database import db
from services.onboarding_service import alias
from services.response_service import SafeView
from services import structure_adoption_service as structure

NAME = "owner-changelog"
LABEL = "🕘・owner-changelog"
HEADER = "# 🕘 Owner Change Log"
_locks = {}


def key(guild):
    return f"managed_channel:{guild.id}:{NAME}"


def board_key(guild):
    return f"owner_changelog_board:{guild.id}"


def owner_rights(guild, resource=None):
    result = {target: discord.PermissionOverwrite.from_pair(*value.pair())
              for target, value in getattr(resource, "overwrites", {}).items()}
    for target in set(result) | set(guild.roles):
        value = result.setdefault(target, discord.PermissionOverwrite())
        value.view_channel = False
        value.send_messages = False
        value.read_message_history = False
    result[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
    owner = guild.get_member(guild.owner_id)
    if owner:
        result[owner] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False,
            use_application_commands=True,
        )
    if guild.me:
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
    if not isinstance(channel, discord.TextChannel):
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
    if owner and not channel.permissions_for(owner).view_channel:
        return None
    return channel


def label(change):
    action = {
        "channel_update": "Channel changed",
        "channel_delete": "Channel deleted",
        "category_update": "Category changed",
        "category_delete": "Category deleted",
        "message_delete": "Managed message deleted",
        "security_review": "Security review",
    }.get(change["action"], change["action"].replace("_", " ").title())
    return f"#{change['id']} · {action}"


def render(guild):
    rows = structure.recent_changes(guild, 12)
    lines = [HEADER, "Only the server owner can read or undo changes here.", ""]
    if not rows:
        lines.append("✅ No tracked Discord structure changes yet.")
    for row in rows:
        actor = f"<@{row['actor_id']}>" if row.get("actor_id") else "actor unknown"
        undo = " · Undo available" if row["reversible"] and row["status"] == "APPLIED" else ""
        if row["action"] == "channel_delete" and row["status"] == "APPLIED":
            try:
                from services.resource_restore_service import removed_names
                if row["logical_key"] in removed_names(guild):
                    undo = " · Restore replacement available"
            except Exception:
                pass
        lines.append(
            f"**{label(row)}**\n"
            f"{row['resource_type']} · {row['logical_key']} · {row['status']}{undo}\n"
            f"{actor} · <t:{row['created_at']}:R>"
        )
    lines.append(
        "\nUse Review / Undo for details. Safe renames/moves and managed-message deletions can be undone. "
        "A deleted optional channel can create a replacement, but deleted Discord history cannot be recovered."
    )
    return "\n\n".join(lines)[:1950]


async def refresh_board(guild, *, publish=False):
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


class OwnerSession(SafeView):
    def __init__(self, guild_id, owner_id, **kwargs):
        super().__init__(**kwargs)
        self.guild_id, self.owner_id = int(guild_id), int(owner_id)

    async def interaction_check(self, interaction):
        ok = bool(
            interaction.guild
            and interaction.guild.id == self.guild_id
            and interaction.user.id == self.owner_id
            and interaction.guild.owner_id == interaction.user.id
        )
        if not ok:
            await interaction.response.send_message("Only the server owner can use this panel.", ephemeral=True)
        return ok


class Entry(SafeView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Review / Undo", emoji="↩️", custom_id="gamerhq:owner_changelog:open",
        style=discord.ButtonStyle.primary,
    )
    async def open(self, interaction, button):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("Only the server owner can use this panel.", ephemeral=True)
        rows = structure.recent_changes(interaction.guild, 25)
        await interaction.response.send_message(
            "# Owner Change History\nSelect a tracked change. Undo is shown only when the previous state can be restored safely.",
            view=ChangeList(interaction.guild.id, interaction.user.id, rows), ephemeral=True,
        )


class ChangeList(OwnerSession):
    def __init__(self, guild_id, owner_id, rows):
        super().__init__(guild_id, owner_id, timeout=240)
        if rows:
            select = discord.ui.Select(
                placeholder="Select a change",
                options=[
                    discord.SelectOption(
                        label=label(row)[:100], value=str(row["id"]),
                        description=(row["logical_key"] + " · " + row["status"])[:100],
                    )
                    for row in rows[:25]
                ],
            )

            async def choose(interaction):
                if not await self.interaction_check(interaction):
                    return
                change = structure.get_change(interaction.guild, int(select.values[0]))
                if not change:
                    return await interaction.response.send_message("Change no longer available.", ephemeral=True)
                before, after = change["before"], change["after"]
                text = (
                    f"# {label(change)}\n"
                    f"Resource: {change['logical_key']}\n"
                    f"Status: **{change['status']}**\n"
                    f"Actor: {'<@'+str(change['actor_id'])+'>' if change.get('actor_id') else 'unknown'}\n\n"
                    f"Before: {before.get('name', before.get('message_id', 'n/a'))}\n"
                    f"After: {after.get('name', 'deleted' if after.get('deleted') else after.get('message_id', 'n/a'))}"
                )
                await interaction.response.send_message(
                    text, view=Undo(interaction.guild.id, interaction.user.id, change),
                    ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                )

            select.callback = choose
            self.add_item(select)


class Undo(OwnerSession):
    def __init__(self, guild_id, owner_id, change):
        super().__init__(guild_id, owner_id, timeout=120)
        self.change_id = change["id"]
        button = discord.ui.Button(
            label="Undo Change", emoji="↩️", style=discord.ButtonStyle.danger,
            disabled=not (change["reversible"] and change["status"] == "APPLIED"),
        )

        async def callback(interaction):
            if not await self.interaction_check(interaction):
                return
            await interaction.response.defer(ephemeral=True)
            try:
                await structure.undo_change(interaction.guild, interaction.user, self.change_id)
                await refresh_board(interaction.guild)
                await interaction.edit_original_response(
                    content=f"✅ Change #{self.change_id} was undone.", view=None
                )
            except (ValueError, discord.HTTPException) as exc:
                await interaction.edit_original_response(content=str(exc), view=None)

        button.callback = callback
        self.add_item(button)

        if change["action"] == "channel_delete" and change["status"] == "APPLIED":
            restore_button = discord.ui.Button(
                label="Restore Replacement", emoji="♻️", style=discord.ButtonStyle.secondary,
            )

            async def restore_callback(interaction):
                if not await self.interaction_check(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    from services import resource_restore_service as restore_service
                    draft = restore_service.preview(interaction.guild, interaction.user, change["logical_key"])
                    resource = await restore_service.restore(interaction.guild, interaction.user, draft)
                    now = int(__import__("time").time())
                    with db.connect() as conn:
                        conn.execute(
                            "UPDATE structure_change_log SET status='RESTORED',undone_at=?,undone_by=? "
                            "WHERE guild_id=? AND id=? AND status='APPLIED'",
                            (now, interaction.user.id, interaction.guild.id, self.change_id),
                        )
                    await refresh_board(interaction.guild)
                    await interaction.edit_original_response(
                        content=(
                            f"✅ Replacement restored: {resource.mention}\n"
                            "The deleted Discord channel's old message history cannot be recovered."
                        ),
                        view=None,
                    )
                except (ValueError, discord.HTTPException) as exc:
                    await interaction.edit_original_response(content=str(exc), view=None)

            restore_button.callback = restore_callback
            self.add_item(restore_button)


async def open_management(interaction):
    if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(
            "Only the server owner can configure the Owner Change Log.", ephemeral=True
        )
    try:
        parent = staff_category(interaction.guild)
    except ValueError as exc:
        return await interaction.response.send_message(str(exc), ephemeral=True)
    current = destination(interaction.guild)
    await interaction.response.send_message(
        f"{HEADER}\nCreate or repair **{LABEL}** under **{parent.name}**? "
        "Only you and GamerHQ will be able to read it.",
        view=Setup(interaction.guild.id, interaction.user.id, parent.id, current.id if current else None),
        ephemeral=True,
    )


class Setup(OwnerSession):
    def __init__(self, guild_id, owner_id, parent_id, current_id):
        super().__init__(guild_id, owner_id, timeout=120)
        self.parent_id, self.current_id = parent_id, current_id

    @discord.ui.button(label="Confirm Owner Log", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            parent = staff_category(guild)
            if parent.id != self.parent_id:
                raise ValueError("STAFF changed. Reopen this setup.")
            current = guild.get_channel(self.current_id) if self.current_id else None
            if current is not None and not isinstance(current, discord.TextChannel):
                raise ValueError("Stored Owner Change Log mapping is invalid.")
            if current is None:
                matches = [c for c in guild.text_channels if alias(c.name) == NAME]
                if matches:
                    raise ValueError(
                        "An unmapped owner-changelog channel already exists. Review it before creating another."
                    )
                current = await guild.create_text_channel(
                    LABEL, category=parent, overwrites=owner_rights(guild),
                    topic="Owner-only GamerHQ structure history and safe Undo actions.",
                    reason="Confirmed GamerHQ owner change log setup",
                )
            else:
                updated = await current.edit(
                    category=parent, sync_permissions=False,
                    overwrites=owner_rights(guild, current),
                    reason="Confirmed GamerHQ owner change log repair",
                )
                if updated is not None:
                    current = updated
            db.set_setting(key(guild), current.id)
            await refresh_board(guild, publish=True)
            await interaction.edit_original_response(
                content=f"✅ Owner Change Log ready: {current.mention}", view=None
            )
        except (ValueError, discord.HTTPException) as exc:
            await interaction.edit_original_response(content=str(exc), view=None)


class OwnerChangeLog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(Entry())


async def setup(bot):
    await bot.add_cog(OwnerChangeLog(bot))
