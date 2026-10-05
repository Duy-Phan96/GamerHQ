"""Friendly entry points over the existing reviewed server operations."""
import uuid
from types import SimpleNamespace

import discord
from database import db
from services.response_service import SafeView, check_admin
from services.server_service import ServerMessageError
from cogs.server import RoleAdminSession
from skill_runtime.contracts.schedule import DailySchedule, IntervalSchedule, WeeklySchedule, schedule_from_dict

TITLE = '# ⚙️ GamerHQ Server Management\nManage the most important GamerHQ settings.'
SECTIONS = ('Core Server', 'Integrations', 'Roles & Permissions', 'Features', 'Review', 'Finish')

_RECURRING_LIST_API = "recurring-posts.list.v1"
_RECURRING_GET_API = "recurring-posts.get.v1"
_RECURRING_CREATE_API = "recurring-posts.create.v1"
_RECURRING_SET_ACTIVE_API = "recurring-posts.set-active.v1"
_RECURRING_DELETE_API = "recurring-posts.delete.v1"


def friendly_plan(view):
    from services.server_operations import needs_repair
    mode = view.draft['mode']
    title = {'setup': 'Add Missing Resources', 'repair': 'Fix Common Issues', 'reconcile': 'Review Structure'}[mode]
    lines = ['# ' + title, 'Review every page. Changes require confirmation; resources that change during review are skipped.']
    for row in view.draft['rows'][view.page * 8:(view.page + 1) * 8]:
        label = 'Channel settings need review' if row['kind'] == 'warning' else row['label']
        status = {'EXACT_MATCH': 'Already linked', 'SAFE_ADOPTION': 'Link existing resource',
                  'AMBIGUOUS': 'Choose the intended resource', 'MISSING': 'Not found', 'STALE': 'Previously linked resource is unavailable'}[row['status']]
        if mode == 'setup' and row['status'] in ('MISSING', 'STALE') and not row['candidates']:
            status = 'Create missing resource'
        elif mode == 'repair':
            try:
                if needs_repair(view.guild, row): status = 'Update managed placement, permissions or message'
            except ServerMessageError:
                status = 'Needs review before changing'
        if row['key'] in view.choices:
            status = 'Selected for linking' if view.choices[row['key']] else 'Skipped'
        lines.append(f'• {discord.utils.escape_markdown(label)[:80]}: {status}')
    lines.append(f'Page {view.page + 1}/{max(1, (len(view.draft["rows"]) + 7) // 8)}. Preview Changes → Confirm Changes.')
    return '\n'.join(lines)[:1950]


def setup_key(guild):
    return f'server_setup_complete:{guild.id}'


def structure_issues(guild):
    """Cached channels/roles and SQLite only; no message scans on landing pages."""
    from services.server_operations import definitions, mapped, needs_repair
    issues = []
    with db.read_only():
        for row in definitions(guild):
            raw = mapped(row)
            resource = (guild.get_role(int(raw)) if row['kind'] in ('role', 'bot-role') else guild.get_channel(int(raw))) if raw and raw.isdigit() else None
            try:
                issue = resource is None or needs_repair(guild, dict(row, resource=resource, status='EXACT_MATCH'))
            except (ServerMessageError, ValueError):
                issue = True
            if issue:
                issues.append(row['label'])
    return issues


async def open_manage(interaction):
    if await check_admin(interaction, interaction.guild):
        await interaction.response.send_message(TITLE,
            view=ManagementView(interaction.guild, interaction.user.id), ephemeral=True)


async def open_setup(interaction):
    if not await check_admin(interaction, interaction.guild):
        return
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message('Only the server owner can run setup. Use `/server manage` for administration.', ephemeral=True)
    complete = db.get_setting(setup_key(interaction.guild)) == '1'
    view = SetupCompleteView(interaction.guild, interaction.user.id) if complete else SetupWizard(interaction.guild, interaction.user.id)
    await interaction.response.send_message(view.text(), view=view, ephemeral=True)


class OpenManagementView(SafeView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Open Server Management', custom_id='gamerhq:server:manage', style=discord.ButtonStyle.primary)
    async def open(self, interaction, button):
        await open_manage(interaction)


class Menu(RoleAdminSession):
    def __init__(self, guild, actor_id):
        super().__init__(timeout=240)
        self.guild, self.admin_id = guild, actor_id

    def action(self, label, callback, *, row=None):
        button = discord.ui.Button(label=label, row=row)
        button.callback = callback
        self.add_item(button)


class ManagementView(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        actions = [('Server Check', self.server_check), ('Server Structure', self.structure),
                   ('Roles & Permissions', self.roles), ('Integrations', self.integrations),
                   ('Managed Messages', self.messages), ('Games', self.games), ('Member Onboarding', self.member_onboarding), ('Skills', self.skills),
                   ('Features', self.features),
                   ('Server Log', self.server_log), ('Lobby Admin', self.lobby_admin)]
        if actor_id == guild.owner_id:
            actions.append(('Owner Change Log', self.owner_changelog))
            actions.append(('Support & Requests', self.support_entries))
        for label, callback in actions:
            self.action(label, callback)

    async def server_check(self, interaction):
        from cogs.health import open_server_check
        await open_server_check(interaction)

    async def support_entries(self, interaction):
        from cogs.ticket_entry_repair import open_management
        await open_management(interaction)

    async def lobby_admin(self, interaction):
        from cogs.lobby_admin import open_management
        await open_management(interaction)

    async def owner_changelog(self, interaction):
        from cogs.owner_changelog import open_management
        await open_management(interaction)

    async def games(self, interaction):
        from cogs.game_channels import GamesMenu
        view = GamesMenu(self.guild, self.admin_id)
        await interaction.response.edit_message(content=view.text(), view=view)

    async def structure(self, interaction):
        issues = structure_issues(self.guild)
        text = '# 🧱 Server Structure\n' + (f'⚠️ {len(issues)} items need attention.' if issues else '✅ Core structure looks good.')
        text += '\nReview existing resources before applying changes. Message checks run only when requested.'
        await interaction.response.edit_message(content=text, view=StructureView(self.guild, self.admin_id))

    async def roles(self, interaction):
        from cogs.server import RoleAdminView
        await interaction.response.edit_message(content='# Roles & Permissions\nReview and manage GamerHQ roles.',
            view=RoleAdminView(guild=self.guild, admin_id=self.admin_id))

    async def integrations(self, interaction):
        await interaction.response.edit_message(content=integration_text(self.guild), view=IntegrationsView(self.guild, self.admin_id))

    async def messages(self, interaction):
        from cogs.managed_messages import open_editor
        await open_editor(interaction)

    async def member_onboarding(self, interaction):
        await open_member_onboarding(interaction, self.guild, self.admin_id)

    async def skills(self, interaction):
        await open_skills(interaction, self.guild, self.admin_id)

    async def features(self, interaction):
        import config
        await interaction.response.edit_message(content='# Features\n'
            '• Game catalog and visibility: `/game-admin set-visible`\n'
            '• Optional game channels: Games in `/server manage`\n'
            '• Events: `/lfg manage`\n'
            '• Voice rooms: `/voice manage`\n'
            f'• Streamer Hub beta: {"Enabled" if config.STREAMER_HUB_ENABLED else "Disabled"}\n'
            'Each feature keeps its existing access and confirmation controls. Beta enablement is an owner deployment setting.',
            view=ManagementView(self.guild, self.admin_id))

    async def server_log(self, interaction):
        from services.server_log_service import channel
        destination = channel(self.guild)
        text = f'Private operational updates: {destination.mention}' if destination else 'The private STAFF log needs attention. Review existing channels, then preview fixes; the owner can add missing resources.'
        await interaction.response.edit_message(content='# 📜 Server Log\n' + text, view=StructureView(self.guild, self.admin_id))



def _member_onboarding_text(guild):
    from services.member_onboarding_service import profile_status

    status = profile_status(guild)
    missing = status["missing"]
    legacy = status["legacy"]
    questions = status["questions"]

    lines = [
        "# 👤 Member Profile & Onboarding",
        "Manage the profile roles and onboarding design members use to personalize GamerHQ.",
        "",
        f"**Profile roles:** {len(status['present'])} ready · {len(missing)} missing",
        f"**Legacy mappings:** {len(legacy)}",
        f"**Default questions:** {len(questions)}",
        "",
        "**Age groups:** Under 18 · 18–20 · 21–22 · 23–24 · 25+",
        "Gender and age remain optional profile choices.",
        "Games onboarding uses a small popular subset; the full library stays in Choose Your Games.",
        "",
        "Discord onboarding sync is not applied automatically from this screen yet. "
        "Use Preview Questions to review GamerHQ's desired state first.",
    ]
    if legacy:
        lines.append(
            "\n⚠️ Legacy age/language mappings are retained until explicit Repair. "
            "Existing member role assignments are never guessed or silently migrated."
        )
    return "\n".join(lines)[:1950]


def _member_questions_text(guild):
    from services.member_onboarding_service import questions_for_guild

    lines = [
        "# 👋 Onboarding Questions Preview",
        "This is GamerHQ's desired onboarding design. Nothing changes in Discord from this preview.",
        "",
    ]
    for index, question in enumerate(questions_for_guild(guild), start=1):
        flags = []
        flags.append("Required" if question.required else "Optional")
        flags.append("Multiple answers" if question.multiple else "Single answer")
        flags.append("Before join" if question.before_join else "Channels & Roles")
        lines.append(f"**{index}. {question.prompt}**")
        lines.append(" · ".join(flags))
        if question.answers:
            labels = [answer.label for answer in question.answers[:10]]
            lines.append("Answers: " + " · ".join(labels))
            if len(question.answers) > 10:
                lines.append(f"+ {len(question.answers) - 10} more")
        else:
            lines.append("Answers: no selectable games are currently available.")
        lines.append("")
    return "\n".join(lines)[:1950]


async def open_member_onboarding(interaction, guild, actor_id):
    await interaction.response.edit_message(
        content=_member_onboarding_text(guild),
        view=MemberOnboardingView(guild, actor_id),
    )


class MemberOnboardingView(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        self.action("Manage Questions", self.manage_questions)
        self.action("Preview Questions", self.preview_questions)
        self.action("Review Profile Repair", self.review_repair)
        self.action("Discord Setup Guide", self.discord_guide)
        self.action("Back to Management", self.back)

    async def manage_questions(self, interaction):
        await open_onboarding_questions(interaction, self.guild, self.admin_id)

    async def preview_questions(self, interaction):
        await interaction.response.edit_message(
            content=_member_questions_text(self.guild),
            view=MemberOnboardingPreviewView(self.guild, self.admin_id),
        )

    async def discord_guide(self, interaction):
        await interaction.response.edit_message(
            content=(
                "# 🔗 Discord Community Onboarding\n"
                "GamerHQ stores the desired question design, but Discord does not currently expose "
                "a documented bot API that GamerHQ can safely use to publish these native onboarding questions.\n\n"
                "**Apply manually in Discord Desktop:**\n"
                "Server Settings → Onboarding → Questions\n\n"
                "Use **Preview Questions** as the source for prompts and flags. "
                "Age/profile answers map to GamerHQ-managed roles; Games uses a small popular subset.\n\n"
                "Do not use unofficial/private Discord endpoints. A native Sync action can be added later "
                "if Discord publishes a supported API."
            )[:1950],
            view=MemberOnboardingPreviewView(self.guild, self.admin_id),
        )

    async def review_repair(self, interaction):
        from services.member_onboarding_service import profile_status

        status = profile_status(self.guild)
        missing = status["missing"]
        legacy = status["legacy"]
        lines = [
            "# Review Profile Role Repair",
            "This repair only creates/adopts the current GamerHQ profile roles and retires proven legacy mappings.",
            "",
            f"**Missing current roles:** {len(missing)}",
            f"**Legacy mappings to retire:** {len(legacy)}",
            "",
        ]
        if missing:
            lines.extend(
                f"• {group}: {option.emoji} {option.label}"
                for group, option in missing[:15]
            )
        if legacy:
            lines.append("\nLegacy Discord roles and existing member assignments are preserved.")
            lines.append("Only their old GamerHQ profile mappings are retired.")
        if not missing and not legacy:
            lines.append("✅ No profile-role repair is currently needed.")
        lines.append("\nNothing changes until you confirm.")
        await interaction.response.edit_message(
            content="\n".join(lines)[:1950],
            view=MemberOnboardingRepairConfirmView(
                self.guild,
                self.admin_id,
                expected_missing=tuple(option.key for _, option in missing),
                expected_legacy=tuple(legacy),
            ),
        )

    async def back(self, interaction):
        await interaction.response.edit_message(
            content=TITLE,
            view=ManagementView(self.guild, self.admin_id),
        )



def _onboarding_question_detail_text(question):
    state = "Enabled" if question.enabled else "Disabled"
    flags = [
        "Required" if question.required else "Optional",
        "Multiple answers" if question.multiple else "Single answer",
        "Before join" if question.before_join else "Channels & Roles",
    ]
    lines = [
        f"# ❓ {discord.utils.escape_markdown(question.prompt)[:100]}",
        f"**Status:** {state}",
        f"**Mode:** {' · '.join(flags)}",
        f"**Answers:** {len(question.answers)}",
        "",
    ]
    if question.answers:
        lines.extend(
            f"• {discord.utils.escape_markdown(answer.label)[:100]}"
            for answer in question.answers[:12]
        )
        if len(question.answers) > 12:
            lines.append(f"• + {len(question.answers) - 12} more")
    else:
        lines.append("No answers are currently available.")
    lines.extend([
        "",
        "Answer lists are generated from GamerHQ-managed roles or the current popular-game subset, "
        "so role/game changes do not require duplicating the list here.",
    ])
    return "\n".join(lines)[:1950]


async def open_onboarding_questions(interaction, guild, actor_id):
    from services.member_onboarding_service import questions_for_guild
    questions = questions_for_guild(guild, include_disabled=True)
    await interaction.response.edit_message(
        content=(
            "# ❓ Manage Onboarding Questions\n"
            "Edit GamerHQ's desired question settings. Answers stay linked to managed roles/games.\n\n"
            "Choose a question below. Changes affect GamerHQ's saved desired state; native Discord Onboarding "
            "must still be applied manually until Discord exposes a supported bot API."
        ),
        view=OnboardingQuestionsView(guild, actor_id, questions),
    )


class OnboardingQuestionPicker(discord.ui.Select):
    def __init__(self, questions):
        super().__init__(
            placeholder="Choose a question",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=question.prompt[:100],
                    value=question.key,
                    description=(
                        ("Enabled" if question.enabled else "Disabled")
                        + " · "
                        + ("Required" if question.required else "Optional")
                    )[:100],
                )
                for question in questions[:25]
            ],
        )

    async def callback(self, interaction):
        from services.member_onboarding_service import load_config, questions_for_guild
        question = next(
            q for q in questions_for_guild(self.view.guild, include_disabled=True)
            if q.key == self.values[0]
        )
        revision = load_config(self.view.guild.id)["revision"]
        await interaction.response.edit_message(
            content=_onboarding_question_detail_text(question),
            view=OnboardingQuestionDetailView(
                self.view.guild,
                self.view.admin_id,
                question,
                revision,
            ),
        )


class OnboardingQuestionsView(Menu):
    def __init__(self, guild, actor_id, questions):
        super().__init__(guild, actor_id)
        self.questions = tuple(questions)
        if self.questions:
            self.add_item(OnboardingQuestionPicker(self.questions))
        self.action("Reset Defaults", self.review_reset)
        self.action("Back", self.back)

    async def review_reset(self, interaction):
        from services.member_onboarding_service import load_config
        revision = load_config(self.guild.id)["revision"]
        await interaction.response.edit_message(
            content=(
                "# Reset Onboarding Questions?\n"
                "This removes GamerHQ question overrides and restores the built-in prompts/flags. "
                "Managed roles, member roles and games are not deleted.\n\n"
                "Nothing changes until you confirm."
            ),
            view=OnboardingResetConfirmView(self.guild, self.admin_id, revision),
        )

    async def back(self, interaction):
        await interaction.response.edit_message(
            content=_member_onboarding_text(self.guild),
            view=MemberOnboardingView(self.guild, self.admin_id),
        )


class OnboardingQuestionEditModal(discord.ui.Modal, title="Edit Onboarding Question"):
    prompt = discord.ui.TextInput(
        label="Question",
        placeholder="Short, clear question shown to members",
        required=True,
        min_length=3,
        max_length=100,
    )

    def __init__(self, guild, actor_id, question, revision):
        super().__init__()
        self.guild_id = guild.id
        self.actor_id = actor_id
        self.question_key = question.key
        self.revision = revision
        self.prompt.default = question.prompt

    async def on_submit(self, interaction):
        if (
            not interaction.guild
            or interaction.guild.id != self.guild_id
            or interaction.user.id != self.actor_id
            or not interaction.user.guild_permissions.administrator
        ):
            return await interaction.response.send_message(
                "This editor is no longer authorized. Reopen Member Onboarding.",
                ephemeral=True,
            )
        from services.member_onboarding_service import (
            load_config,
            questions_for_guild,
            save_question_override,
        )
        try:
            save_question_override(
                self.guild_id,
                self.question_key,
                prompt=str(self.prompt.value),
                expected_revision=self.revision,
            )
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        question = next(
            q for q in questions_for_guild(interaction.guild, include_disabled=True)
            if q.key == self.question_key
        )
        revision = load_config(self.guild_id)["revision"]
        await interaction.response.edit_message(
            content=_onboarding_question_detail_text(question),
            view=OnboardingQuestionDetailView(
                interaction.guild,
                self.actor_id,
                question,
                revision,
            ),
        )


class OnboardingQuestionDetailView(Menu):
    def __init__(self, guild, actor_id, question, revision):
        super().__init__(guild, actor_id)
        self.question = question
        self.revision = revision
        self.action("Edit Question", self.edit)
        self.action("Disable" if question.enabled else "Enable", self.toggle_enabled)
        self.action("Make Optional" if question.required else "Make Required", self.toggle_required)
        self.action(
            "Move to Channels & Roles" if question.before_join else "Ask Before Join",
            self.toggle_before_join,
        )
        self.action(
            "Single Answer" if question.multiple else "Allow Multiple",
            self.toggle_multiple,
        )
        self.action("Back", self.back)

    async def _toggle(self, interaction, **change):
        from services.member_onboarding_service import (
            load_config,
            questions_for_guild,
            save_question_override,
        )
        try:
            save_question_override(
                self.guild.id,
                self.question.key,
                expected_revision=self.revision,
                **change,
            )
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        question = next(
            q for q in questions_for_guild(self.guild, include_disabled=True)
            if q.key == self.question.key
        )
        revision = load_config(self.guild.id)["revision"]
        await interaction.response.edit_message(
            content=_onboarding_question_detail_text(question),
            view=OnboardingQuestionDetailView(
                self.guild,
                self.admin_id,
                question,
                revision,
            ),
        )

    async def edit(self, interaction):
        await interaction.response.send_modal(
            OnboardingQuestionEditModal(
                self.guild,
                self.admin_id,
                self.question,
                self.revision,
            )
        )

    async def toggle_enabled(self, interaction):
        await self._toggle(interaction, enabled=not self.question.enabled)

    async def toggle_required(self, interaction):
        await self._toggle(interaction, required=not self.question.required)

    async def toggle_before_join(self, interaction):
        await self._toggle(interaction, before_join=not self.question.before_join)

    async def toggle_multiple(self, interaction):
        await self._toggle(interaction, multiple=not self.question.multiple)

    async def back(self, interaction):
        await open_onboarding_questions(interaction, self.guild, self.admin_id)


class OnboardingResetConfirmView(Menu):
    def __init__(self, guild, actor_id, revision):
        super().__init__(guild, actor_id)
        self.revision = revision
        self.used = False
        self.action("Confirm Reset", self.confirm)
        self.action("Cancel", self.cancel)

    async def confirm(self, interaction):
        if self.used:
            return await interaction.response.send_message(
                "This reset review was already used.",
                ephemeral=True,
            )
        self.used = True
        from services.member_onboarding_service import reset_config
        try:
            reset_config(self.guild.id, expected_revision=self.revision)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await open_onboarding_questions(interaction, self.guild, self.admin_id)

    async def cancel(self, interaction):
        await open_onboarding_questions(interaction, self.guild, self.admin_id)


class MemberOnboardingPreviewView(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        self.action("Back", self.back)

    async def back(self, interaction):
        await interaction.response.edit_message(
            content=_member_onboarding_text(self.guild),
            view=MemberOnboardingView(self.guild, self.admin_id),
        )


class MemberOnboardingRepairConfirmView(Menu):
    def __init__(self, guild, actor_id, *, expected_missing, expected_legacy):
        super().__init__(guild, actor_id)
        self.expected_missing = tuple(expected_missing)
        self.expected_legacy = tuple(expected_legacy)
        self.used = False
        self.action("Confirm Repair", self.confirm)
        self.action("Cancel", self.cancel)

    async def confirm(self, interaction):
        if self.used:
            return await interaction.response.send_message(
                "This repair review was already used. Reopen Member Onboarding.",
                ephemeral=True,
            )
        from services.member_onboarding_service import profile_status
        current = profile_status(self.guild)
        current_missing = tuple(option.key for _, option in current["missing"])
        current_legacy = tuple(current["legacy"])
        if current_missing != self.expected_missing or current_legacy != self.expected_legacy:
            return await interaction.response.edit_message(
                content="Profile-role state changed while this review was open. Reopen Member Onboarding before repairing.",
                view=MemberOnboardingView(self.guild, self.admin_id),
            )
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            from services.role_panel_service import sync
            await sync(self.guild, repair=True)
            notice = "✅ Profile roles repaired. Existing member roles were preserved."
        except (ValueError, ServerMessageError, discord.HTTPException):
            notice = "❌ Profile role repair could not be completed safely. Review Roles & Permissions and try again."
        await interaction.edit_original_response(
            content=(notice + "\n\n" + _member_onboarding_text(self.guild))[:1950],
            view=MemberOnboardingView(self.guild, self.admin_id),
        )

    async def cancel(self, interaction):
        await interaction.response.edit_message(
            content=_member_onboarding_text(self.guild),
            view=MemberOnboardingView(self.guild, self.admin_id),
        )


def _skill_status_label(status):
    if status.missing_capabilities or status.health in ('UNAVAILABLE', 'ERROR', 'FAIL'):
        return '🔴 Unavailable'
    if status.enabled:
        return '🟢 Enabled'
    return '⚪ Disabled'


def _skills_overview_text(statuses):
    lines = [
        '# 🧩 Skills',
        'Manage portable GamerHQ Skills for this server. Enablement is stored per guild; Discord remains an integration, not the source of truth.',
    ]
    if not statuses:
        lines.append('')
        lines.append('No portable Skills are registered in this build yet.')
        return '\n'.join(lines)
    lines.append('')
    for status in statuses[:25]:
        lines.append(
            f'• **{discord.utils.escape_markdown(status.name)[:80]}** '
            f'v{status.version} — {_skill_status_label(status)}'
        )
    if len(statuses) > 25:
        lines.append(f'\n{len(statuses) - 25} additional Skills are not shown in this Discord selector.')
    lines.append('\nSelect a Skill to review capabilities and activation.')
    return '\n'.join(lines)[:1950]


def _skill_detail_text(status):
    source = 'Built-in GamerHQ Skill' if status.source_kind == 'built-in' else f'External package: {discord.utils.escape_markdown(status.source_distribution or "unknown")[:100]}'
    capabilities = ', '.join(f'`{value}`' for value in status.required_capabilities) or 'None'
    lines = [
        f'# 🧩 {discord.utils.escape_markdown(status.name)[:80]}',
        discord.utils.escape_markdown(status.description)[:500],
        '',
        f'**Version:** {status.version}',
        f'**Source:** {source}',
        f'**Status:** {_skill_status_label(status)}',
        f'**Runtime:** {"Running" if status.running else "Stopped"}',
        f'**Configuration:** {"Available" if status.management_available else "No configuration surface registered."}',
        f'**Required capabilities:** {capabilities}',
    ]
    if status.missing_capabilities:
        lines.append('**Unavailable capabilities:** ' + ', '.join(
            f'`{value}`' for value in status.missing_capabilities
        ))
    if status.health_detail:
        lines.append(f'**Health:** {discord.utils.escape_markdown(status.health_detail)[:300]}')
    return '\n'.join(lines)[:1950]


async def open_skills(interaction, guild, actor_id):
    runtime = getattr(interaction.client, 'skill_runtime', None)
    if runtime is None:
        return await interaction.response.edit_message(
            content='# 🧩 Skills\nSkill Runtime is unavailable in this process.',
            view=ManagementView(guild, actor_id),
        )
    statuses = await runtime.statuses(guild_id=guild.id)
    await interaction.response.edit_message(
        content=_skills_overview_text(statuses),
        view=SkillsView(guild, actor_id, statuses),
    )


class SkillPicker(discord.ui.Select):
    def __init__(self, statuses):
        super().__init__(
            placeholder='Choose a Skill',
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=status.name[:100],
                    value=status.skill_id,
                    description=(f'{_skill_status_label(status)} • v{status.version}')[:100],
                )
                for status in statuses[:25]
            ],
        )

    async def callback(self, interaction):
        runtime = getattr(interaction.client, 'skill_runtime', None)
        if runtime is None:
            return await interaction.response.edit_message(
                content='# 🧩 Skills\nSkill Runtime is unavailable in this process.',
                view=ManagementView(self.view.guild, self.view.admin_id),
            )
        status = await runtime.status(guild_id=self.view.guild.id, skill_id=self.values[0])
        await interaction.response.edit_message(
            content=_skill_detail_text(status),
            view=SkillDetailsView(self.view.guild, self.view.admin_id, status),
        )


class SkillsView(Menu):
    def __init__(self, guild, actor_id, statuses):
        super().__init__(guild, actor_id)
        self.statuses = tuple(statuses)
        if self.statuses:
            self.add_item(SkillPicker(self.statuses))
        self.action('Back to Management', self.back)

    async def back(self, interaction):
        await interaction.response.edit_message(content=TITLE, view=ManagementView(self.guild, self.admin_id))


class SkillDetailsView(Menu):
    def __init__(self, guild, actor_id, status):
        super().__init__(guild, actor_id)
        self.skill_id = status.skill_id
        if not status.missing_capabilities and status.health not in ('UNAVAILABLE', 'ERROR'):
            self.action(
                'Review Disable' if status.enabled else 'Review Enable',
                self.review_toggle,
            )
        if status.skill_id == 'recurring-posts' and status.enabled and status.management_available:
            self.action('Configure', self.configure)
        self.action('Back to Skills', self.back)

    async def configure(self, interaction):
        await open_recurring_posts(interaction, self.guild, self.admin_id)

    async def review_toggle(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                'Only the server owner can enable or disable Skills. Admins can still review Skill status.',
                ephemeral=True,
            )
        runtime = getattr(interaction.client, 'skill_runtime', None)
        if runtime is None:
            return await interaction.response.send_message('Skill Runtime is unavailable.', ephemeral=True)
        status = await runtime.status(guild_id=self.guild.id, skill_id=self.skill_id)
        action = 'Disable' if status.enabled else 'Enable'
        effect = (
            'Future Skill execution will be blocked, while configuration and persisted scheduler jobs are retained.'
            if status.enabled else
            'Required host capabilities will be rechecked before the Skill starts for this server.'
        )
        await interaction.response.edit_message(
            content=(
                f'# {action} {discord.utils.escape_markdown(status.name)[:80]}?\n'
                f'{effect}\n\nNothing changes until you confirm.'
            ),
            view=SkillToggleConfirmView(self.guild, self.admin_id, status),
        )

    async def back(self, interaction):
        runtime = getattr(interaction.client, 'skill_runtime', None)
        statuses = await runtime.statuses(guild_id=self.guild.id) if runtime is not None else ()
        await interaction.response.edit_message(
            content=_skills_overview_text(statuses),
            view=SkillsView(self.guild, self.admin_id, statuses),
        )


class SkillToggleConfirmView(Menu):
    def __init__(self, guild, actor_id, status):
        super().__init__(guild, actor_id)
        self.skill_id = status.skill_id
        self.expected_enabled = status.enabled
        self.action('Confirm Disable' if status.enabled else 'Confirm Enable', self.confirm)
        self.action('Cancel', self.cancel)

    async def confirm(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                'Only the server owner can enable or disable Skills.',
                ephemeral=True,
            )
        runtime = getattr(interaction.client, 'skill_runtime', None)
        if runtime is None:
            return await interaction.response.send_message('Skill Runtime is unavailable.', ephemeral=True)

        current = await runtime.status(guild_id=self.guild.id, skill_id=self.skill_id)
        if current.enabled != self.expected_enabled:
            return await interaction.response.edit_message(
                content='Skill state changed while this confirmation was open. Review the current state before trying again.',
                view=SkillDetailsView(self.guild, self.admin_id, current),
            )

        await interaction.response.defer(ephemeral=True)
        try:
            if current.enabled:
                changed = await runtime.disable_skill(guild_id=self.guild.id, skill_id=self.skill_id)
                notice = '✅ Skill disabled.' if changed else 'ℹ️ Skill was already disabled.'
            else:
                changed = await runtime.enable_skill(guild_id=self.guild.id, skill_id=self.skill_id)
                notice = '✅ Skill enabled.' if changed else 'ℹ️ Skill was already enabled.'
        except (PermissionError, ValueError, KeyError):
            notice = '❌ This Skill cannot be activated with the current host capabilities.'
        except Exception:
            notice = '❌ Skill activation failed. Check the private server log.'

        status = await runtime.status(guild_id=self.guild.id, skill_id=self.skill_id)
        await interaction.edit_original_response(
            content=(notice + '\n\n' + _skill_detail_text(status))[:1950],
            view=SkillDetailsView(self.guild, self.admin_id, status),
        )

    async def cancel(self, interaction):
        runtime = getattr(interaction.client, 'skill_runtime', None)
        status = await runtime.status(guild_id=self.guild.id, skill_id=self.skill_id)
        await interaction.response.edit_message(
            content=_skill_detail_text(status),
            view=SkillDetailsView(self.guild, self.admin_id, status),
        )


async def _recurring_call(interaction, guild, contract_id, payload):
    runtime = getattr(interaction.client, 'skill_runtime', None)
    if runtime is None:
        raise RuntimeError('Skill Runtime is unavailable.')
    return await runtime.call_management(
        guild_id=guild.id,
        skill_id='recurring-posts',
        contract_id=contract_id,
        payload=payload,
    )


def _recurring_post(value):
    if not isinstance(value, dict):
        try:
            value = dict(value)
        except Exception as exc:
            raise ValueError('Invalid Recurring Posts management response.') from exc
    try:
        schedule = dict(value['schedule'])
        return SimpleNamespace(
            id=str(value['id']),
            name=str(value['name']),
            channel_id=int(value['channelId']),
            content=str(value['content']),
            schedule=schedule,
            active=bool(value['active']),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Invalid Recurring Posts management response.') from exc


def _recurring_schedule_label(value):
    schedule = schedule_from_dict(value)
    if isinstance(schedule, IntervalSchedule):
        minutes = schedule.seconds // 60
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"Every {hours} hour{'s' if hours != 1 else ''}"
        return f"Every {minutes} minutes"
    if isinstance(schedule, DailySchedule):
        return f"Daily at {schedule.hour:02d}:{schedule.minute:02d} · {schedule.timezone}"
    if isinstance(schedule, WeeklySchedule):
        days = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
        return (
            f"{days[schedule.weekday]} at {schedule.hour:02d}:{schedule.minute:02d} "
            f"· {schedule.timezone}"
        )
    raise ValueError('Unsupported Recurring Posts schedule.')


def _recurring_overview(posts):
    lines = [
        '# 🔁 Recurring Posts',
        'Create automatic server posts using the shared Skill Scheduler.',
        'Minimum interval: **15 minutes**. Daily and weekly schedules use an explicit timezone.',
        '',
    ]
    if not posts:
        lines.append('No recurring posts configured yet.')
    else:
        for post in posts[:20]:
            state = '🟢 Active' if post.active else '⏸️ Paused'
            lines.append(
                f'• **{discord.utils.escape_markdown(post.name)[:70]}** — {state}\n'
                f'  <#{post.channel_id}> · {_recurring_schedule_label(post.schedule)}'
            )
    return '\n'.join(lines)[:1950]


async def open_recurring_posts(interaction, guild, actor_id):
    try:
        response = await _recurring_call(interaction, guild, _RECURRING_LIST_API, {})
        posts = tuple(_recurring_post(value) for value in response.get('posts', ()))
    except Exception:
        return await interaction.response.edit_message(
            content='# 🔁 Recurring Posts\nConfiguration is currently unavailable.',
            view=ManagementView(guild, actor_id),
        )
    await interaction.response.edit_message(
        content=_recurring_overview(posts),
        view=RecurringPostsView(guild, actor_id, posts),
    )


class RecurringPostPicker(discord.ui.Select):
    def __init__(self, posts):
        super().__init__(
            placeholder='Choose a recurring post',
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=post.name[:100],
                    value=post.id,
                    description=(('Active' if post.active else 'Paused') + ' · ' + _recurring_schedule_label(post.schedule))[:100],
                )
                for post in posts[:20]
            ],
        )

    async def callback(self, interaction):
        response = await _recurring_call(
            interaction,
            self.view.guild,
            _RECURRING_GET_API,
            {'postId': self.values[0]},
        )
        post = _recurring_post(response['post'])
        await interaction.response.edit_message(
            content=_recurring_post_detail(post),
            view=RecurringPostDetailView(self.view.guild, self.view.admin_id, post),
        )


class RecurringPostsView(Menu):
    def __init__(self, guild, actor_id, posts):
        super().__init__(guild, actor_id)
        self.posts = tuple(posts)
        if self.posts:
            self.add_item(RecurringPostPicker(self.posts))
        self.action('Add Post', self.add, row=2)
        self.action('Back to Skill', self.back, row=2)

    async def add(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                'Only the server owner can create recurring posts.',
                ephemeral=True,
            )
        await interaction.response.edit_message(
            content='# 🔁 New Recurring Post\nChoose the destination channel first.',
            view=RecurringPostChannelView(self.guild, self.admin_id),
        )

    async def back(self, interaction):
        runtime = interaction.client.skill_runtime
        status = await runtime.status(guild_id=self.guild.id, skill_id='recurring-posts')
        await interaction.response.edit_message(
            content=_skill_detail_text(status),
            view=SkillDetailsView(self.guild, self.admin_id, status),
        )


class RecurringChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder='Choose destination channel',
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        )

    async def callback(self, interaction):
        channel_id = int(self.values[0].id)
        await interaction.response.edit_message(
            content='# 🔁 New Recurring Post\nChoose a schedule type.',
            view=RecurringPostScheduleView(self.view.guild, self.view.admin_id, channel_id),
        )


class RecurringPostChannelView(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        self.add_item(RecurringChannelSelect())
        self.action('Cancel', self.cancel, row=2)

    async def cancel(self, interaction):
        await open_recurring_posts(interaction, self.guild, self.admin_id)


class RecurringPostScheduleView(Menu):
    def __init__(self, guild, actor_id, channel_id):
        super().__init__(guild, actor_id)
        self.channel_id = channel_id
        self.action('Interval', self.interval)
        self.action('Daily', self.daily)
        self.action('Weekly', self.weekly)
        self.action('Cancel', self.cancel)

    async def interval(self, interaction):
        await interaction.response.send_modal(
            RecurringPostModal(self.guild, self.admin_id, self.channel_id, 'interval')
        )

    async def daily(self, interaction):
        await interaction.response.send_modal(
            RecurringPostModal(self.guild, self.admin_id, self.channel_id, 'daily')
        )

    async def weekly(self, interaction):
        await interaction.response.send_modal(
            RecurringPostModal(self.guild, self.admin_id, self.channel_id, 'weekly')
        )

    async def cancel(self, interaction):
        await open_recurring_posts(interaction, self.guild, self.admin_id)


class RecurringPostModal(discord.ui.Modal):
    def __init__(self, guild, actor_id, channel_id, kind):
        super().__init__(title={'interval': 'Interval Post', 'daily': 'Daily Post', 'weekly': 'Weekly Post'}[kind])
        self.guild, self.actor_id, self.channel_id, self.kind = guild, actor_id, channel_id, kind
        self.name = discord.ui.TextInput(label='Name', max_length=80, placeholder='Rules reminder')
        self.content = discord.ui.TextInput(
            label='Message',
            style=discord.TextStyle.paragraph,
            max_length=2000,
            placeholder='Message to post automatically',
        )
        self.add_item(self.name)
        self.add_item(self.content)
        if kind == 'interval':
            self.schedule = discord.ui.TextInput(
                label='Every N minutes',
                placeholder='180',
                default='180',
                max_length=6,
            )
            self.add_item(self.schedule)
        elif kind == 'daily':
            self.schedule = discord.ui.TextInput(
                label='Time (HH:MM)',
                placeholder='09:00',
                default='09:00',
                max_length=5,
            )
            self.timezone = discord.ui.TextInput(
                label='IANA timezone',
                placeholder='Europe/Berlin',
                default='Europe/Berlin',
                max_length=64,
            )
            self.add_item(self.schedule)
            self.add_item(self.timezone)
        else:
            self.weekday = discord.ui.TextInput(
                label='Weekday',
                placeholder='Monday',
                default='Monday',
                max_length=9,
            )
            self.schedule = discord.ui.TextInput(
                label='Time (HH:MM)',
                placeholder='09:00',
                default='09:00',
                max_length=5,
            )
            self.timezone = discord.ui.TextInput(
                label='IANA timezone',
                placeholder='Europe/Berlin',
                default='Europe/Berlin',
                max_length=64,
            )
            self.add_item(self.weekday)
            self.add_item(self.schedule)
            self.add_item(self.timezone)

    def _schedule_value(self):
        if self.kind == 'interval':
            minutes = int(str(self.schedule.value).strip())
            return {'type': 'interval', 'seconds': minutes * 60}
        hour, minute = [int(value) for value in str(self.schedule.value).strip().split(':', 1)]
        timezone = str(self.timezone.value).strip()
        if self.kind == 'daily':
            return {'type': 'daily', 'hour': hour, 'minute': minute, 'timezone': timezone}
        days = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6,
        }
        weekday = days[str(self.weekday.value).strip().lower()]
        return {
            'type': 'weekly',
            'weekday': weekday,
            'hour': hour,
            'minute': minute,
            'timezone': timezone,
        }

    async def on_submit(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                'Only the server owner can create recurring posts.',
                ephemeral=True,
            )
        try:
            schedule = self._schedule_value()
            await _recurring_call(
                interaction,
                self.guild,
                _RECURRING_CREATE_API,
                {
                    'name': str(self.name.value),
                    'channelId': self.channel_id,
                    'content': str(self.content.value),
                    'schedule': schedule,
                },
            )
            response = await _recurring_call(
                interaction,
                self.guild,
                _RECURRING_LIST_API,
                {},
            )
            posts = tuple(_recurring_post(value) for value in response.get('posts', ()))
        except (ValueError, KeyError):
            return await interaction.response.send_message(
                'That schedule is invalid. Use at least 15 minutes, a valid HH:MM time, and an IANA timezone such as Europe/Berlin.',
                ephemeral=True,
            )
        except Exception:
            return await interaction.response.send_message(
                'Recurring Post could not be saved. Check the private server log.',
                ephemeral=True,
            )
        await interaction.response.edit_message(
            content='✅ Recurring Post created.\n\n' + _recurring_overview(posts),
            view=RecurringPostsView(self.guild, self.actor_id, posts),
        )


def _recurring_post_detail(post):
    state = '🟢 Active' if post.active else '⏸️ Paused'
    preview = discord.utils.escape_markdown(post.content)
    if len(preview) > 700:
        preview = preview[:697] + '...'
    return (
        f'# 🔁 {discord.utils.escape_markdown(post.name)[:80]}\n'
        f'**Status:** {state}\n'
        f'**Channel:** <#{post.channel_id}>\n'
        f'**Schedule:** {_recurring_schedule_label(post.schedule)}\n\n'
        f'**Message**\n{preview}'
    )[:1950]


class RecurringPostDetailView(Menu):
    def __init__(self, guild, actor_id, post):
        super().__init__(guild, actor_id)
        self.post_id = post.id
        self.active = post.active
        self.action('Pause' if post.active else 'Resume', self.toggle)
        self.action('Delete', self.delete)
        self.action('Back', self.back)

    async def toggle(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message('Only the server owner can change recurring posts.', ephemeral=True)
        response = await _recurring_call(
            interaction,
            self.guild,
            _RECURRING_SET_ACTIVE_API,
            {'postId': self.post_id, 'active': not self.active},
        )
        post = _recurring_post(response['post'])
        await interaction.response.edit_message(
            content=_recurring_post_detail(post),
            view=RecurringPostDetailView(self.guild, self.admin_id, post),
        )

    async def delete(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message('Only the server owner can delete recurring posts.', ephemeral=True)
        await interaction.response.edit_message(
            content='# Delete Recurring Post?\nThis removes the configuration and its scheduler job. It does not delete messages that were already posted.',
            view=RecurringPostDeleteConfirmView(self.guild, self.admin_id, self.post_id),
        )

    async def back(self, interaction):
        await open_recurring_posts(interaction, self.guild, self.admin_id)


class RecurringPostDeleteConfirmView(Menu):
    def __init__(self, guild, actor_id, post_id):
        super().__init__(guild, actor_id)
        self.post_id = post_id
        self.action('Confirm Delete', self.confirm)
        self.action('Cancel', self.cancel)

    async def confirm(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message('Only the server owner can delete recurring posts.', ephemeral=True)
        await _recurring_call(
            interaction,
            self.guild,
            _RECURRING_DELETE_API,
            {'postId': self.post_id},
        )
        response = await _recurring_call(
            interaction,
            self.guild,
            _RECURRING_LIST_API,
            {},
        )
        posts = tuple(_recurring_post(value) for value in response.get('posts', ()))
        await interaction.response.edit_message(
            content='✅ Recurring Post deleted.\n\n' + _recurring_overview(posts),
            view=RecurringPostsView(self.guild, self.admin_id, posts),
        )

    async def cancel(self, interaction):
        response = await _recurring_call(
            interaction,
            self.guild,
            _RECURRING_GET_API,
            {'postId': self.post_id},
        )
        post = _recurring_post(response['post'])
        await interaction.response.edit_message(
            content=_recurring_post_detail(post),
            view=RecurringPostDetailView(self.guild, self.admin_id, post),
        )


class StructureView(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        for label, mode in [('Review Structure', 'reconcile'), ('Fix Common Issues', 'repair')]:
            async def run(interaction, selected=mode):
                from cogs.server import open_operation
                await open_operation(interaction, selected, friendly=True)
            self.action(label, run)
        if actor_id == guild.owner_id:
            async def missing(interaction):
                from cogs.server import open_operation
                await open_operation(interaction, 'setup', friendly=True)
            self.action('Preview Missing Resources', missing)
            self.action('Removed Resources', self.removed_resources)
        self.action('Review Duplicate Messages', self.duplicates)
        self.action('Back to Management', self.back)

    async def removed_resources(self, interaction):
        from services import resource_restore_service as restore
        names = restore.removed_names(self.guild)
        if not names:
            return await interaction.response.send_message(
                'No intentionally removed managed channels are waiting for restore.', ephemeral=True)
        await interaction.response.send_message(
            '# Removed Resources\nSelect a resource to preview an explicit restore. Nothing is recreated automatically.',
            view=RemovedResourcesView(self.guild, self.admin_id, names), ephemeral=True)

    async def duplicates(self, interaction):
        from services.message_reconciliation import audit
        from cogs.server import DuplicateAuditView
        await interaction.response.defer(ephemeral=True)
        rows = await audit(self.guild, interaction.user, bot=interaction.client)
        view = DuplicateAuditView(self.guild, self.admin_id, rows, interaction.client, friendly=True)
        await interaction.edit_original_response(content=view.text(), view=view)

    async def back(self, interaction):
        await interaction.response.edit_message(content=TITLE, view=ManagementView(self.guild, self.admin_id))


def integration_text(guild):
    from services.bot_group_service import LABELS, member
    return '# Integrations\nSelect an installed bot, then confirm its assignment.\n' + '\n'.join(
        f'• {label}: {"Available" if member(guild, name) else "Not available / optional"}' for name, label in LABELS.items())


class IntegrationsView(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        from services.bot_group_service import LABELS
        for name, label in LABELS.items():
            async def select(interaction, selected=name):
                await interaction.response.edit_message(content='Select the installed bot member for this integration.',
                    view=SelectBotView(self.guild, self.admin_id, selected))
            self.action(label, select)


class BotPicker(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='Select Bot', min_values=1, max_values=1)

    async def callback(self, interaction):
        guild = self.view.guild
        selected = guild.get_member(self.values[0].id)
        if selected is None:
            try:
                selected = await guild.fetch_member(self.values[0].id)
            except discord.HTTPException:
                selected = None
        if selected is None or not selected.bot or selected.id == guild.me.id:
            return await interaction.response.send_message('Select an external bot that is currently a member of this server.', ephemeral=True)
        await interaction.response.edit_message(content=f'Use **{discord.utils.escape_markdown(selected.display_name)}** for this integration? No permissions change until you review fixes.',
            view=ConfirmBotView(guild, self.view.admin_id, self.view.integration, selected.id), allowed_mentions=discord.AllowedMentions.none())


class SelectBotView(Menu):
    def __init__(self, guild, actor_id, integration):
        super().__init__(guild, actor_id)
        self.integration = integration
        self.add_item(BotPicker())


class ConfirmBotView(Menu):
    def __init__(self, guild, actor_id, integration, member_id):
        super().__init__(guild, actor_id)
        self.integration, self.member_id, self.finished = integration, member_id, False

    @discord.ui.button(label='Confirm Bot', style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if self.finished or not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            member = await self.guild.fetch_member(self.member_id)
        except discord.HTTPException:
            member = None
        if member is None or member.id != self.member_id or not member.bot or member.id == self.guild.me.id:
            return await interaction.edit_original_response(content='That bot is no longer available. Select it again.', view=None)
        if not await self.interaction_check(interaction):
            return
        self.finished = True
        key = f'bot_member:{self.guild.id}:{self.integration}'
        changed = db.get_setting(key) != str(member.id)
        db.set_setting(key, member.id)
        from services.bot_group_service import remember_member
        remember_member(self.guild, member)
        if changed:
            from services.server_log_service import emit
            from services.bot_group_service import LABELS
            await emit(self.guild, 'integration:' + uuid.uuid4().hex, '🔌 Integration Updated',
                LABELS[self.integration] + ' bot assignment saved. Review permissions in `/server manage`.')
        await interaction.edit_original_response(content='Bot assignment saved. Review Structure / Fix Common Issues to preview permissions.', view=StructureView(self.guild, self.admin_id))


class SetupCompleteView(Menu):
    def text(self):
        return '# ✅ GamerHQ Setup Complete\nGamerHQ is already configured.\nUse `/server manage` for normal administration.'

    @discord.ui.button(label='Open Server Management', style=discord.ButtonStyle.primary)
    async def manage(self, interaction, button):
        await interaction.response.edit_message(content=TITLE, view=ManagementView(self.guild, self.admin_id))

    @discord.ui.button(label='Run Setup Again — Advanced')
    async def again(self, interaction, button):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message('Only the owner can rerun setup.', ephemeral=True)
        view = SetupWizard(self.guild, self.admin_id)
        await interaction.response.edit_message(content=view.text(), view=view)


class SetupWizard(Menu):
    def __init__(self, guild, actor_id, page=0):
        super().__init__(guild, actor_id)
        self.page = page
        self.section.label = 'Finish Setup' if page == 5 else 'Open Section'

    async def interaction_check(self, interaction):
        return await super().interaction_check(interaction) and interaction.user.id == self.guild.owner_id

    def text(self):
        descriptions = ('Review existing channels before adding missing core resources.',
            'Choose installed bots. Optional integrations can be left unconfigured.',
            'Review managed roles and private areas before confirming changes.',
            'Configure the game library and optional channels in Games; events remain centralized.',
            'Review links, preview missing resources and fix permissions. Every change requires confirmation.',
            'Finish checks the core structure using stored resources. Unresolved items remain available for review.')
        return f'# GamerHQ Setup — {SECTIONS[self.page]}\nStep {self.page + 1} of 6\n{descriptions[self.page]}\n' + \
            'Use `/server setup` to return here after configuring a section.'

    @discord.ui.button(label='Open Section', style=discord.ButtonStyle.primary)
    async def section(self, interaction, button):
        manager = ManagementView(self.guild, self.admin_id)
        if self.page in (0, 4):
            await manager.structure(interaction)
        elif self.page == 1:
            await manager.integrations(interaction)
        elif self.page == 2:
            await manager.roles(interaction)
        elif self.page == 3:
            await manager.games(interaction)
        else:
            if structure_issues(self.guild):
                return await interaction.response.edit_message(content='Some core items still need review. Open the structure panel to continue.', view=StructureView(self.guild, self.admin_id))
            db.set_setting(setup_key(self.guild), '1')
            await interaction.response.defer(ephemeral=True)
            from services.server_log_service import emit
            await emit(self.guild, 'setup-complete', '✅ GamerHQ Setup Complete', 'Use `/server manage` for normal administration.')
            view = SetupCompleteView(self.guild, self.admin_id)
            await interaction.edit_original_response(content=view.text(), view=view)

    @discord.ui.button(label='Next')
    async def next(self, interaction, button):
        self.page = min(5, self.page + 1)
        self.section.label = 'Finish Setup' if self.page == 5 else 'Open Section'
        await interaction.response.edit_message(content=self.text(), view=self)

    @discord.ui.button(label='Back')
    async def back(self, interaction, button):
        self.page = max(0, self.page - 1)
        self.section.label = 'Finish Setup' if self.page == 5 else 'Open Section'
        await interaction.response.edit_message(content=self.text(), view=self)


class DeveloperView(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        for label, mode in [('Reconcile', 'reconcile'), ('Repair', 'repair'), ('Resource Mappings', 'reconcile')]:
            async def run(interaction, selected=mode):
                from cogs.server import open_operation
                await open_operation(interaction, selected)
            self.action(label, run)
        for label in ('Health', 'Raw Diagnostics'):
            self.action(label, self.health)
        self.action('Duplicate Scan', self.duplicates)
        self.action('Production Doctor', self.doctor)
        self.action('Legacy Game Migration', self.legacy_games)

    async def interaction_check(self, interaction):
        return await super().interaction_check(interaction) and interaction.user.id == self.guild.owner_id

    async def health(self, interaction):
        from cogs.health import HealthView
        from services.health_service import scan, summary
        await interaction.response.defer(ephemeral=True)
        findings = await scan(self.guild, interaction.client, messages=False)
        await interaction.edit_original_response(content=summary(findings), view=HealthView(self.guild, self.admin_id, findings))

    async def duplicates(self, interaction):
        from services.message_reconciliation import audit
        from cogs.server import DuplicateAuditView
        await interaction.response.defer(ephemeral=True)
        rows = await audit(self.guild, interaction.user, bot=interaction.client)
        view = DuplicateAuditView(self.guild, self.admin_id, rows, interaction.client)
        await interaction.edit_original_response(content=view.text(), view=view)

    async def legacy_games(self, interaction):
        from cogs.game_channels import GamesList
        view = GamesList(self.guild, self.admin_id, 'legacy')
        await interaction.response.edit_message(content=view.text(), view=view)

    async def doctor(self, interaction):
        await interaction.response.send_message('Production Doctor runs on the VPS without Discord changes:\n'
            '```sh\npython -m tools.production_doctor --help\n```\n'
            'Follow docs/PRODUCTION_OPERATIONS.md. Never paste `.env` contents into Discord.', ephemeral=True)


class RemovedResourcesView(Menu):
    def __init__(self, guild, actor_id, names):
        super().__init__(guild, actor_id)
        options = [discord.SelectOption(label=name[:100], value=name) for name in names[:25]]
        picker = discord.ui.Select(placeholder='Choose removed resource', options=options)

        async def choose(interaction):
            if interaction.user.id != self.guild.owner_id:
                return await interaction.response.send_message('Only the server owner can restore resources.', ephemeral=True)
            from services import resource_restore_service as restore
            try:
                draft = restore.preview(self.guild, interaction.user, picker.values[0])
            except ServerMessageError as exc:
                return await interaction.response.send_message(str(exc), ephemeral=True)
            row = draft['row']
            parent = self.guild.get_channel(draft['parent_id'])
            await interaction.response.send_message(
                f"# Restore {row['label']}\nCreate a new managed channel under **{parent.name}**? "
                "The previous Discord channel history cannot be recovered.",
                view=RestoreRemovedConfirm(self.guild, interaction.user.id, draft),
                ephemeral=True,
            )

        picker.callback = choose
        self.add_item(picker)


class RestoreRemovedConfirm(Menu):
    def __init__(self, guild, actor_id, draft):
        super().__init__(guild, actor_id)
        self.draft = draft
        self.action('Confirm Restore', self.confirm)
        self.action('Cancel', self.cancel)

    async def confirm(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message('Only the server owner can restore resources.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        from services import resource_restore_service as restore
        try:
            resource = await restore.restore(self.guild, interaction.user, self.draft)
            await interaction.edit_original_response(
                content=f'✅ Restored {resource.mention}. Existing application data was preserved.', view=None)
        except (ServerMessageError, discord.HTTPException) as exc:
            await interaction.edit_original_response(content=str(exc), view=None)

    async def cancel(self, interaction):
        await interaction.response.edit_message(content='Cancelled. Nothing restored.', view=None)
