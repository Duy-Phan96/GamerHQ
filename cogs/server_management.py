"""Friendly entry points over the existing reviewed server operations."""
import uuid

import discord
from database import db
from services.response_service import SafeView, check_admin
from services.server_service import ServerMessageError
from cogs.server import RoleAdminSession

TITLE = '# ⚙️ GamerHQ Server Management\nManage the most important GamerHQ settings.'
SECTIONS = ('Core Server', 'Integrations', 'Roles & Permissions', 'Features', 'Review', 'Finish')


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
        actions = [('Server Structure', self.structure), ('Roles & Permissions', self.roles),
                   ('Integrations', self.integrations), ('Managed Messages', self.messages),
                   ('Games', self.games), ('Features', self.features), ('Server Log', self.server_log),
                   ('Lobby Admin', self.lobby_admin)]
        if actor_id == guild.owner_id:
            actions.append(('Owner Change Log', self.owner_changelog))
        for label, callback in actions:
            self.action(label, callback)

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
