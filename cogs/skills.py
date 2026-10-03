"""Friendly administration for portable GamerHQ Skills."""
from __future__ import annotations

import uuid

import discord

from services.response_service import SafeView, check_admin


def _runtime(interaction):
    return getattr(interaction.client, "skill_runtime", None)


def _manifest_text(manifest, *, enabled: bool, running: bool) -> str:
    state = "🟢 Enabled & running" if enabled and running else "🟡 Enabled · not running" if enabled else "⚪ Disabled"
    permissions = "\n".join(f"• `{value}`" for value in manifest.permissions) or "• None"
    emits = "\n".join(f"• `{item.id}`" for item in manifest.events.emits) or "• None"
    consumes = "\n".join(f"• `{item.id}`" for item in manifest.events.consumes) or "• None"
    exposed = "\n".join(f"• `{item.id}`" for item in manifest.public_apis.exposes) or "• None"
    used = "\n".join(f"• `{item.id}`" for item in manifest.public_apis.consumes) or "• None"
    return (
        f"# 🧩 {discord.utils.escape_markdown(manifest.name)}\n"
        f"{state}\n"
        f"Version **{manifest.version}** · Runtime API **{manifest.runtime_api_version}**\n\n"
        f"{discord.utils.escape_markdown(manifest.description)[:500]}\n\n"
        f"**Capabilities**\n{permissions}\n\n"
        f"**Events emitted**\n{emits}\n\n"
        f"**Events consumed**\n{consumes}\n\n"
        f"**Public APIs exposed**\n{exposed}\n\n"
        f"**Public APIs consumed**\n{used}"
    )[:1900]


async def dashboard_text(guild, runtime) -> str:
    skills = runtime.registry.all()
    if not skills:
        return (
            "# 🧩 Skills\n"
            "No Skills are included in this build yet.\n\n"
            "The portable Skill Runtime is available; first-party Skills will appear here once added."
        )
    enabled = set(await runtime.state.enabled_skill_ids(guild_id=guild.id))
    lines = [
        "# 🧩 Skills",
        "Enable, disable and inspect modular GamerHQ functionality.",
        "",
    ]
    for skill in skills[:20]:
        on = skill.manifest.id in enabled
        running = runtime.manager.is_running(guild_id=guild.id, skill_id=skill.manifest.id)
        icon = "🟢" if on and running else "🟡" if on else "⚪"
        state = "Running" if on and running else "Enabled" if on else "Disabled"
        lines.append(f"{icon} **{discord.utils.escape_markdown(skill.manifest.name)[:70]}** — {state}")
    if len(skills) > 20:
        lines.append(f"… {len(skills)-20} more Skills.")
    lines += [
        "",
        "Select a Skill to inspect its capabilities, Events and Public APIs.",
        "Only the server owner can change Skill enablement.",
    ]
    return "\n".join(lines)[:1900]


class SkillSelect(discord.ui.Select):
    def __init__(self, view):
        options = [
            discord.SelectOption(
                label=skill.manifest.name[:100],
                value=skill.manifest.id,
                description=(skill.manifest.description or "No description")[:100],
                default=skill.manifest.id == view.selected_skill_id,
            )
            for skill in view.runtime.registry.all()[:25]
        ]
        super().__init__(
            placeholder="Choose a Skill",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.selected_skill_id = self.values[0]
        await self.view.refresh_state()
        self.view.rebuild()
        await interaction.response.edit_message(
            content=self.view.text(),
            view=self.view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class SkillsView(SafeView):
    def __init__(self, guild, actor_id, runtime, *, selected_skill_id=None):
        super().__init__(timeout=240)
        self.guild = guild
        self.actor_id = actor_id
        self.runtime = runtime
        self.selected_skill_id = selected_skill_id
        self.enabled = False
        self.running = False
        self.health = None
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.guild_id != self.guild.id or interaction.user.id != self.actor_id:
            await interaction.response.send_message("This Skills panel belongs to another session.", ephemeral=True)
            return False
        return await check_admin(interaction, self.guild)

    async def refresh_state(self):
        if not self.selected_skill_id:
            self.enabled = self.running = False
            self.health = None
            return
        self.enabled = await self.runtime.state.is_enabled(
            guild_id=self.guild.id,
            skill_id=self.selected_skill_id,
        )
        self.running = self.runtime.manager.is_running(
            guild_id=self.guild.id,
            skill_id=self.selected_skill_id,
        )
        try:
            self.health = await self.runtime.manager.health(
                guild_id=self.guild.id,
                skill_id=self.selected_skill_id,
            )
        except Exception:
            self.health = None

    def text(self):
        if not self.selected_skill_id:
            # Initial text is supplied by open_management because this method is sync.
            return "# 🧩 Skills"
        skill = self.runtime.registry.get(self.selected_skill_id)
        text = _manifest_text(skill.manifest, enabled=self.enabled, running=self.running)
        if self.health is not None:
            detail = discord.utils.escape_markdown(str(getattr(self.health, "detail", "")))[:300]
            state = discord.utils.escape_markdown(str(getattr(self.health, "state", "UNKNOWN")))[:40]
            text += f"\n\n**Health**\n{state}" + (f" — {detail}" if detail else "")
        return text[:1900]

    def rebuild(self):
        self.clear_items()
        skills = self.runtime.registry.all()
        if skills:
            self.add_item(SkillSelect(self))
        if self.selected_skill_id:
            owner = self.actor_id == self.guild.owner_id
            action = discord.ui.Button(
                label="Disable Skill" if self.enabled else "Enable Skill",
                style=discord.ButtonStyle.danger if self.enabled else discord.ButtonStyle.success,
                row=1,
                disabled=not owner,
            )
            action.callback = self.change
            self.add_item(action)

            overview = discord.ui.Button(label="Overview", row=1)
            overview.callback = self.overview
            self.add_item(overview)

        refresh = discord.ui.Button(label="Refresh", emoji="🔄", row=2)
        refresh.callback = self.refresh
        self.add_item(refresh)

        back = discord.ui.Button(label="Back to Management", row=2)
        back.callback = self.back
        self.add_item(back)

    async def change(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can enable or disable Skills.",
                ephemeral=True,
            )
        await self.refresh_state()
        skill = self.runtime.registry.get(self.selected_skill_id)
        view = ConfirmSkillStateView(
            self.guild,
            self.actor_id,
            self.runtime,
            self.selected_skill_id,
            enable=not self.enabled,
        )
        await interaction.response.edit_message(
            content=view.text(skill.manifest),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def overview(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.selected_skill_id = None
        await self.refresh_state()
        self.rebuild()
        await interaction.response.edit_message(
            content=await dashboard_text(self.guild, self.runtime),
            view=self,
        )

    async def refresh(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await self.refresh_state()
        self.rebuild()
        content = self.text() if self.selected_skill_id else await dashboard_text(self.guild, self.runtime)
        await interaction.response.edit_message(content=content, view=self)

    async def back(self, interaction):
        if not await self.interaction_check(interaction):
            return
        from cogs.server_management import ManagementView, TITLE
        await interaction.response.edit_message(
            content=TITLE,
            view=ManagementView(self.guild, self.actor_id),
        )


class ConfirmSkillStateView(SafeView):
    def __init__(self, guild, actor_id, runtime, skill_id, *, enable):
        super().__init__(timeout=180)
        self.guild = guild
        self.actor_id = actor_id
        self.runtime = runtime
        self.skill_id = skill_id
        self.enable = enable
        self.finished = False

    async def interaction_check(self, interaction):
        if self.finished or interaction.guild_id != self.guild.id or interaction.user.id != self.actor_id:
            await interaction.response.send_message("This Skill confirmation is no longer available.", ephemeral=True)
            return False
        if not await check_admin(interaction, self.guild):
            return False
        if interaction.user.id != self.guild.owner_id:
            await interaction.response.send_message("Only the server owner can change Skill enablement.", ephemeral=True)
            return False
        return True

    def text(self, manifest):
        verb = "Enable" if self.enable else "Disable"
        permissions = "\n".join(f"• `{value}`" for value in manifest.permissions) or "• None"
        consequence = (
            "The Skill will be enabled and started for this server."
            if self.enable
            else "The Skill will stop receiving runtime work for this server. Persisted Skill data is retained."
        )
        return (
            f"# {verb} {discord.utils.escape_markdown(manifest.name)}?\n"
            f"{consequence}\n\n"
            f"**Requested capabilities**\n{permissions}\n\n"
            "Review before confirming. Existing Skill data is not deleted."
        )[:1900]

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        skill = self.runtime.registry.get(self.skill_id)
        current = await self.runtime.state.is_enabled(guild_id=self.guild.id, skill_id=self.skill_id)
        try:
            if self.enable:
                if not current:
                    await self.runtime.enable(guild_id=self.guild.id, skill_id=self.skill_id)
                await self.runtime.start(guild_id=self.guild.id, skill_id=self.skill_id)
                result = "enabled"
            else:
                await self.runtime.disable(guild_id=self.guild.id, skill_id=self.skill_id)
                result = "disabled"
        except Exception:
            await interaction.edit_original_response(
                content="The Skill state could not be changed safely. Review Skill health/logs before retrying.",
                view=None,
            )
            self.finished = True
            return

        from services.server_log_service import emit
        await emit(
            self.guild,
            "skill-state:" + uuid.uuid4().hex,
            "🧩 Skill Updated",
            f"**{skill.manifest.name}** was {result} for this server.",
        )
        self.finished = True
        view = SkillsView(self.guild, self.actor_id, self.runtime, selected_skill_id=self.skill_id)
        await view.refresh_state()
        await interaction.edit_original_response(content=view.text(), view=view)

    @discord.ui.button(label="Cancel")
    async def cancel(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        self.finished = True
        view = SkillsView(self.guild, self.actor_id, self.runtime, selected_skill_id=self.skill_id)
        await view.refresh_state()
        await interaction.response.edit_message(content=view.text(), view=view)


async def open_management(interaction, *, replace=False):
    if not await check_admin(interaction, interaction.guild):
        return
    runtime = _runtime(interaction)
    if runtime is None:
        return await interaction.response.send_message(
            "Skill Runtime is not available in this build.",
            ephemeral=True,
        )
    view = SkillsView(interaction.guild, interaction.user.id, runtime)
    content = await dashboard_text(interaction.guild, runtime)
    if replace:
        await interaction.response.edit_message(content=content, view=view)
    else:
        await interaction.response.send_message(content, view=view, ephemeral=True)
