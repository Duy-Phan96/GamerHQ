import discord
from discord.ext import commands

from services.role_service import ROLE_GROUPS, configured_group


def _channel_alias(name: str) -> str:
    import re
    value = name.lower().strip()
    value = re.sub(r"[^a-z0-9_-]+", "", value)
    return value.strip("-_")


class RoleCategorySelect(discord.ui.Select):
    def __init__(self, session, group: str):
        self.session = session
        self.group = group
        configured, missing = configured_group(session.guild, group)
        self.configured = configured
        self.missing = missing

        options = [
            discord.SelectOption(
                label=option.label[:100],
                value=str(role.id),
                emoji=option.emoji,
                default=role.id in session.pending_role_ids,
            )
            for option, role in configured
        ]

        # Discord selects require at least one option. A disabled informational
        # option keeps the session usable while making missing setup visible.
        if not options:
            options = [discord.SelectOption(label="No configured roles yet", value="__none__", emoji="⚠️")]

        super().__init__(
            placeholder=f"Choose roles from {group}",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            row=2,
            disabled=not configured,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.member_id:
            await interaction.response.send_message("This role selector belongs to another user.", ephemeral=True)
            return

        configured_ids = {role.id for _, role in self.configured}
        selected_ids = {int(value) for value in self.values if value != "__none__"}
        self.session.pending_role_ids.difference_update(configured_ids)
        self.session.pending_role_ids.update(selected_ids)
        self.session.rebuild()
        await interaction.response.edit_message(content=self.session.status_text(), view=self.session)


class RoleCategoryButton(discord.ui.Button):
    def __init__(self, session, group: str, row: int):
        self.session = session
        self.group = group
        super().__init__(
            label=group.split(" ", 1)[1] if " " in group else group,
            emoji=group.split(" ", 1)[0] if " " in group else None,
            style=discord.ButtonStyle.primary if group == session.current_group else discord.ButtonStyle.secondary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.member_id:
            await interaction.response.send_message("This role selector belongs to another user.", ephemeral=True)
            return
        self.session.current_group = self.group
        self.session.rebuild()
        await interaction.response.edit_message(content=self.session.status_text(), view=self.session)


class RoleSelectionSession(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=300)
        self.member_id = member.id
        self.guild = member.guild
        self.original_role_ids = {role.id for role in member.roles}
        self.pending_role_ids = set(self.original_role_ids)
        self.current_group = next(iter(ROLE_GROUPS))
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        for index, group in enumerate(ROLE_GROUPS):
            self.add_item(RoleCategoryButton(self, group, row=index // 4))
        self.add_item(RoleCategorySelect(self, self.current_group))

        confirm = discord.ui.Button(label="Confirm Selection", emoji="✅", style=discord.ButtonStyle.success, row=4)
        confirm.callback = self.confirm_selection
        self.add_item(confirm)
        cancel = discord.ui.Button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary, row=4)
        cancel.callback = self.cancel_selection
        self.add_item(cancel)

    def managed_ids(self):
        ids = set()
        for group in ROLE_GROUPS:
            configured, _ = configured_group(self.guild, group)
            ids.update(role.id for _, role in configured)
        return ids

    def status_text(self):
        configured, missing = configured_group(self.guild, self.current_group)
        managed = self.managed_ids()
        original = self.original_role_ids & managed
        pending = self.pending_role_ids & managed
        added = len(pending - original)
        removed = len(original - pending)

        text = (
            "👤 **Choose Your Roles**\n"
            "Choose a category, then select the roles that fit you.\n"
            "You can switch categories freely — your choices stay saved until you confirm.\n\n"
            f"**Current category:** {self.current_group}\n"
            f"**Selected overall:** {len(pending)}\n"
            f"**Pending changes:** +{added} / -{removed}\n\n"
            "Nothing changes until you press **Confirm Selection**."
        )
        if missing:
            text += "\n\n⚠️ **Not configured yet:** " + ", ".join(option.label for option in missing)
        return text

    async def confirm_selection(self, interaction: discord.Interaction):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This role selector belongs to another user.", ephemeral=True)
            return

        await interaction.response.edit_message(content="⏳ **Saving your role selection…**", view=None)
        managed = self.managed_ids()
        original = self.original_role_ids & managed
        pending = self.pending_role_ids & managed
        add_ids = pending - original
        remove_ids = original - pending

        add_roles = [self.guild.get_role(role_id) for role_id in add_ids]
        remove_roles = [self.guild.get_role(role_id) for role_id in remove_ids]
        add_roles = [role for role in add_roles if role]
        remove_roles = [role for role in remove_roles if role]

        try:
            if add_roles:
                await interaction.user.add_roles(*add_roles, reason="GamerHQ confirmed general role selection")
            if remove_roles:
                await interaction.user.remove_roles(*remove_roles, reason="GamerHQ confirmed general role selection")
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="❌ I could not update your roles. Please ask staff to check the GamerHQ Bot role position/permissions.",
                view=None,
            )
            self.stop()
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ Discord could not save the selection: `{exc}`", view=None)
            self.stop()
            return

        lines = ["✅ **Your roles have been updated.**"]
        if add_roles:
            lines.append("\n**Added:** " + ", ".join(sorted(role.name for role in add_roles)))
        if remove_roles:
            lines.append("\n**Removed:** " + ", ".join(sorted(role.name for role in remove_roles)))
        if not add_roles and not remove_roles:
            lines.append("\nNo roles needed to be changed.")
        await interaction.edit_original_response(content="".join(lines), view=None)
        self.stop()

    async def cancel_selection(self, interaction: discord.Interaction):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This role selector belongs to another user.", ephemeral=True)
            return
        await interaction.response.edit_message(content="✖️ Role selection cancelled. Nothing was changed.", view=None)
        self.stop()


class SuggestRoleModal(discord.ui.Modal, title="💡 Suggest a Role"):
    role_type = discord.ui.TextInput(
        label="Type",
        placeholder="Language, Platform, Playstyle, Notification or Other",
        required=True,
        max_length=40,
    )
    suggestion = discord.ui.TextInput(
        label="Suggestion",
        placeholder="e.g. French",
        required=True,
        max_length=100,
    )
    reason = discord.ui.TextInput(
        label="Why would this help GamerHQ? (optional)",
        placeholder="A short reason or use case",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
            return
        from cogs.suggestions import submit
        await submit(interaction, f'Role: {str(self.suggestion).strip()}'[:100],
                     f'Type: {str(self.role_type).strip()}\nRole: {str(self.suggestion).strip()}', str(self.reason))


class ChooseRolesHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Select Roles",
        emoji="👤",
        style=discord.ButtonStyle.primary,
        custom_id="gamerhq:roles:select",
    )
    async def select_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
            return
        session = RoleSelectionSession(interaction.user)
        await interaction.response.send_message(session.status_text(), view=session, ephemeral=True)

    @discord.ui.button(
        label="Suggest Role",
        emoji="💡",
        style=discord.ButtonStyle.secondary,
        custom_id="gamerhq:roles:suggest",
    )
    async def suggest_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SuggestRoleModal())


def build_choose_roles_view() -> discord.ui.View:
    return ChooseRolesHubView()


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Persistent custom IDs keep the buttons working after bot restarts.
        self.bot.add_view(ChooseRolesHubView())


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
