"""Owner/admin read-only diagnostics and friendly server-check dashboard."""
import io

import discord

from services.response_service import SafeView
from services.game_area_cleanup import authorized
from services import health_service


GROUPS = (
    "Core",
    "Games",
    "Community",
    "LFG & Voice",
    "Support",
    "Integrations",
    "Skills",
    "Security & Privacy",
    "Other",
)

SEVERITY = {
    "CRITICAL": 6,
    "MANUAL_REVIEW": 5,
    "REPAIRABLE": 4,
    "RECONCILE": 4,
    "WARN": 3,
    "INFO": 1,
    "PASS": 0,
}


def group_for(finding):
    name = finding.name.lower()
    if any(word in name for word in ("game channel", "game role", "game area", "games log", "gaming category")):
        return "Games"
    if any(word in name for word in ("lfg", "voice", "lobby", "temporary voice", "create voice")):
        return "LFG & Voice"
    if any(word in name for word in ("ticket", "support", "electricity")):
        return "Support"
    if any(word in name for word in (
        "amazon", "instant gaming", "dealgecko", "affiliate", "gocdkeys",
        "music bot", "partner", "marketplace", "gaming-deals",
    )):
        return "Integrations"
    if "skill runtime" in name or name.startswith("skill "):
        return "Skills"
    # Security/privacy wins over functional keywords such as "suggestions".
    # Example: "Staff suggestions privacy" is a privacy finding, not a Community feature finding.
    if any(word in name for word in ("privacy", "permission", "unknown categor", "staff")):
        return "Security & Privacy"
    if any(word in name for word in (
        "suggestion", "community", "role settings", "newbies", "general",
        "introductions", "tournament", "giveaway",
    )):
        return "Community"
    if any(word in name for word in (
        "database", "runtime", "server log", "persistent controls", "managed message",
        "start-here", "events order", "welcome", "rules", "announcement",
        "choose-your-", "guide", "bot-commands",
    )):
        return "Core"
    return "Other"


def grouped(findings):
    result = {name: [] for name in GROUPS}
    for finding in findings:
        result[group_for(finding)].append(finding)
    return result


def group_state(rows):
    if not rows:
        return "PASS"
    return max(rows, key=lambda item: SEVERITY.get(item.state, 2)).state


def state_label(state):
    return {
        "PASS": ("✅", "Ready"),
        "INFO": ("ℹ️", "Info"),
        "WARN": ("⚠️", "Check"),
        "REPAIRABLE": ("🔧", "Repair available"),
        "RECONCILE": ("🔗", "Needs linking"),
        "MANUAL_REVIEW": ("⚠️", "Needs review"),
        "CRITICAL": ("❌", "Critical"),
    }.get(state, ("⚠️", "Check"))


def dashboard(findings):
    groups = grouped(findings)
    actionable = sum(
        group_state(rows) not in {"PASS", "INFO"}
        for rows in groups.values() if rows
    )
    lines = [
        "# 🩺 GamerHQ Server Check",
        "**Read-only. Nothing is changed by this check.**",
        "",
        f"{len([g for g in GROUPS if groups[g]])} areas checked · "
        + ("✅ everything looks ready" if not actionable else f"⚠️ {actionable} areas need attention"),
        "",
    ]
    for name in GROUPS:
        rows = groups[name]
        if not rows:
            continue
        state = group_state(rows)
        emoji, label = state_label(state)
        problems = sum(row.state not in {"PASS", "INFO"} for row in rows)
        suffix = f" · {problems} item{'s' if problems != 1 else ''}" if problems else ""
        lines.append(f"{emoji} **{name}** — {label}{suffix}")
    lines += [
        "",
        "Choose an area below for the important findings. **Deep Check** additionally verifies managed messages and recovery mappings.",
    ]
    return "\n".join(lines)[:1900]


def group_detail(findings, name):
    rows = grouped(findings).get(name, [])
    state = group_state(rows)
    emoji, label = state_label(state)
    lines = [
        f"# {emoji} {name} · {label}",
        "**Read-only. No repair is performed here.**",
        "",
    ]
    issues = [row for row in rows if row.state not in {"PASS", "INFO"}]
    if not issues:
        lines.append(f"✅ All {len(rows)} checks in this area passed.")
    else:
        for row in issues[:8]:
            icon, state_name = state_label(row.state)
            lines.append(f"{icon} **{discord.utils.escape_markdown(row.name)[:80]}** — {state_name}")
            lines.append(discord.utils.escape_markdown(row.detail)[:220])
        if len(issues) > 8:
            lines.append(f"… {len(issues)-8} more findings are available in the attached Deep Check report.")
        passed = sum(row.state == "PASS" for row in rows)
        if passed:
            lines.append(f"\n✅ {passed} additional checks passed.")
    lines.append("\nUse **Open Area** to go to the existing management tools; changes there still require their normal preview/confirmation.")
    return "\n".join(lines)[:1900]


class AreaSelect(discord.ui.Select):
    def __init__(self, view):
        groups = grouped(view.findings)
        options = []
        for name in GROUPS:
            rows = groups[name]
            if not rows:
                continue
            emoji, label = state_label(group_state(rows))
            problems = sum(row.state not in {"PASS", "INFO"} for row in rows)
            options.append(discord.SelectOption(
                label=name,
                value=name,
                emoji=emoji,
                description=(label + (f" · {problems} need attention" if problems else ""))[:100],
                default=name == view.selected,
            ))
        super().__init__(placeholder="Choose an area", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction):
        if not await self.view.interaction_check(interaction):
            return
        self.view.selected = self.values[0]
        self.view.rebuild()
        await interaction.response.edit_message(
            content=group_detail(self.view.findings, self.view.selected),
            view=self.view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class HealthView(SafeView):
    """Friendly read-only server check; /server health and /server manage share it."""

    def __init__(self, guild, actor_id, findings, *, selected=None):
        super().__init__(timeout=240)
        self.guild = guild
        self.actor_id = actor_id
        self.findings = findings
        self.selected = selected
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.actor_id or not authorized(self.guild, interaction.user):
            await interaction.response.send_message("❌ Owner or administrator access required.", ephemeral=True)
            return False
        return True

    def text(self):
        return group_detail(self.findings, self.selected) if self.selected else dashboard(self.findings)

    def rebuild(self):
        self.clear_items()
        available = grouped(self.findings)
        if self.selected and not available.get(self.selected):
            self.selected = None
        if any(available.values()):
            self.add_item(AreaSelect(self))

        back = discord.ui.Button(label="Overview", style=discord.ButtonStyle.secondary, row=1,
                                 disabled=self.selected is None)
        back.callback = self.overview
        self.add_item(back)

        refresh = discord.ui.Button(label="Refresh", emoji="🔄", row=1)
        refresh.callback = self.refresh
        self.add_item(refresh)

        details = discord.ui.Button(label="Deep Check", emoji="🔎", style=discord.ButtonStyle.primary, row=1)
        details.callback = self.details
        self.add_item(details)

        area = discord.ui.Button(label="Open Area", emoji="➡️", row=1, disabled=self.selected is None)
        area.callback = self.open_area
        self.add_item(area)

        close = discord.ui.Button(label="Close", row=2)
        close.callback = self.close
        self.add_item(close)

    async def overview(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.selected = None
        self.rebuild()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def details(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(
            content="🔄 Running Deep Check…\nChecking managed messages and recovery mappings.",
            view=None,
        )
        try:
            self.findings = await health_service.scan(self.guild, interaction.client, messages=True)
        except (TimeoutError, discord.HTTPException):
            self.rebuild()
            await interaction.edit_original_response(
                content="Detailed checks could not finish. Nothing was changed; retry later.",
                view=self,
            )
            return
        report = "\n".join(
            f'{"WARNING" if f.state == "WARN" else f.state.replace("_", " ")}: {f.name} — {f.detail}'
            for f in self.findings
        )
        report += "\n\nRegistered commands\n" + "\n".join(
            "/" + name + " — " + description
            for name, description in health_service.command_inventory(interaction.client, self.guild)
        )
        self.rebuild()
        await interaction.edit_original_response(
            content=self.text() + "\n\n📎 Complete technical results are attached.",
            view=self,
            attachments=[discord.File(io.BytesIO(report.encode("utf-8")), filename="gamerhq-server-check.txt")],
        )

    async def refresh(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="🔄 Checking GamerHQ…", view=None)
        try:
            self.findings = await health_service.scan(self.guild, interaction.client, messages=False)
        except (TimeoutError, discord.HTTPException):
            self.rebuild()
            return await interaction.edit_original_response(
                content="The Server Check could not finish. Nothing was changed; retry shortly.",
                view=self,
            )
        self.rebuild()
        await interaction.edit_original_response(content=self.text(), view=self, attachments=[])

    async def open_area(self, interaction):
        if not await self.interaction_check(interaction) or not self.selected:
            return
        from cogs.server_management import (
            ManagementView, StructureView, IntegrationsView, integration_text, TITLE
        )

        if self.selected == "Games":
            from cogs.game_channels import GamesMenu
            view = GamesMenu(self.guild, self.actor_id)
            return await interaction.response.edit_message(content=view.text(), view=view)
        if self.selected == "Support":
            if self.actor_id == self.guild.owner_id:
                from cogs.ticket_entry_repair import open_management
                return await open_management(interaction)
            return await interaction.response.send_message(
                "Support-entry repair is owner-only. Use the normal support channels for testing.",
                ephemeral=True,
            )
        if self.selected == "Integrations":
            return await interaction.response.edit_message(
                content=integration_text(self.guild),
                view=IntegrationsView(self.guild, self.actor_id),
            )
        if self.selected in {"Core", "Security & Privacy"}:
            return await interaction.response.edit_message(
                content="# 🧱 Server Structure\nReview existing resources before applying any changes.",
                view=StructureView(self.guild, self.actor_id),
            )
        if self.selected in {"Community", "LFG & Voice"}:
            return await interaction.response.edit_message(
                content="# Features\nUse the existing GamerHQ feature controls. No Server Check result changes anything automatically.",
                view=ManagementView(self.guild, self.actor_id),
            )
        await interaction.response.edit_message(content=TITLE, view=ManagementView(self.guild, self.actor_id))

    async def close(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.edit_message(content="Server Check closed. Nothing was changed.", view=None)
        self.stop()


async def open_server_check(interaction):
    if not interaction.guild or not authorized(interaction.guild, interaction.user):
        return await interaction.response.send_message("❌ Owner or administrator access required.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        content="🔄 Checking GamerHQ…\nReading stored mappings, current server structure and effective access.",
        view=None,
    )
    try:
        findings = await health_service.scan(interaction.guild, interaction.client, messages=False)
    except (TimeoutError, discord.HTTPException):
        return await interaction.edit_original_response(
            content="The Server Check could not finish. Nothing was changed; retry shortly.",
            view=None,
        )
    view = HealthView(interaction.guild, interaction.user.id, findings)
    await interaction.edit_original_response(content=view.text(), view=view)
