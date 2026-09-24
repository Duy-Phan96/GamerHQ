import discord
from discord.ext import commands

from services.role_service import ROLE_GROUPS, configured_group


def _channel_alias(name: str) -> str:
    import re
    value = name.lower().strip()
    value = re.sub(r"[^a-z0-9_-]+", "", value)
    return value.strip("-_")


class RoleCategorySelect(discord.ui.Select):
    def __init__(self, session):
        from services.role_service import PROFILE_STEPS
        self.session = session
        label, groups = PROFILE_STEPS[session.step]
        self.keys = {o.key for group in groups for o in ROLE_GROUPS[group]}
        options = [discord.SelectOption(label=o.label, value=o.key, emoji=o.emoji,
                   default=o.key in session.selected_keys)
                   for group in groups for o in ROLE_GROUPS[group]]
        if label == 'Gender':
            options.append(discord.SelectOption(label='Prefer not to say', value='__private__',
                           emoji='⚪', default=not (self.keys & session.selected_keys)))
        super().__init__(placeholder=label, min_values=0,
                         max_values=1 if label in ('Gender', 'Age') else len(options), options=options)

    async def callback(self, interaction):
        if not await self.session.interaction_check(interaction):
            return
        selected = set(self.values)
        if not selected <= self.keys | {'__private__'} or ('__private__' in selected and len(selected) > 1):
            return await interaction.response.send_message('Invalid profile choice.', ephemeral=True)
        if self.session.step in (0, 1) and len(selected) > 1:
            return await interaction.response.send_message('Choose at most one option.', ephemeral=True)
        self.session.selected_keys.difference_update(self.keys)
        self.session.selected_keys.update(selected - {'__private__'})
        self.session.rebuild()
        await interaction.response.edit_message(content=self.session.status_text(), view=self.session)


class RoleSelectionSession(discord.ui.View):
    """Member-bound, temporary profile draft. Only reviewed Save changes roles."""
    def __init__(self, member):
        from services.role_service import profile_roles
        super().__init__(timeout=300)
        self.guild, self.member_id = member.guild, member.id
        mapping = profile_roles(self.guild)
        self.mapping_ids = {key: role.id for key, role in mapping.items()}
        self.original_role_ids = {r.id for r in member.roles} & set(self.mapping_ids.values())
        self.selected_keys = {key for key, role in mapping.items()
                              if role.id in self.original_role_ids and key != 'gender-unspecified'}
        self.step, self.used = 0, False
        self.rebuild()

    async def interaction_check(self, interaction):
        if (self.used or not interaction.guild or interaction.guild.id != self.guild.id
                or interaction.user.id != self.member_id):
            await interaction.response.send_message('This profile session is closed or belongs to another member.', ephemeral=True)
            return False
        return True

    def rebuild(self):
        from services.role_service import PROFILE_STEPS
        self.clear_items()
        if self.step < len(PROFILE_STEPS) and PROFILE_STEPS[self.step][1]:
            self.add_item(RoleCategorySelect(self))
        for label, callback, disabled in [
            ('Save Profile' if self.step == 6 else 'Review' if self.step == 5 else 'Next',
             self.confirm_selection if self.step == 6 else self.next_step, False),
            ('Back', self.back, self.step == 0), ('Cancel', self.cancel_selection, False)]:
            button = discord.ui.Button(label=label, row=1, disabled=disabled,
                        style=discord.ButtonStyle.success if label == 'Save Profile' else discord.ButtonStyle.secondary)
            button.callback = callback
            self.add_item(button)

    def status_text(self):
        from services.role_service import PROFILE_STEPS
        if self.step < 6:
            name, groups = PROFILE_STEPS[self.step]
            selected = [o.label for group in groups for o in ROLE_GROUPS[group] if o.key in self.selected_keys]
            detail = 'No active playstyle options are configured. Continue to interests.' if not groups else (
                'Current selection: ' + (', '.join(selected) or 'Not specified'))
            return (f'# 👤 Update Profile · {self.step + 1}/6 — {name}\n\n{detail}\n\n'
                    'Choices are optional and visible as server roles. Clear the selection to remove a choice. '
                    'Nothing changes until Review → Save Profile. Games belong in #choose-your-games.')
        lines = ['# 👤 Profile Review']
        for heading, indexes in [('👤 About You', (0, 1, 2)), ('🎮 Gaming Setup', (3, 4)),
                                 ('🔔 Interests & Notifications', (5,))]:
            rows = []
            for index in indexes:
                label, groups = PROFILE_STEPS[index]
                values = [o.label for group in groups for o in ROLE_GROUPS[group] if o.key in self.selected_keys]
                if values:
                    rows.append(f'{label}: ' + ', '.join(values))
            if rows:
                lines.extend(['', '**' + heading + '**', *rows])
        if len(lines) == 1:
            lines.extend(['', 'No optional profile roles selected.'])
        lines.extend(['', 'Save Profile applies only these profile settings. Your game and unrelated roles stay unchanged.'])
        return '\n'.join(lines)

    async def next_step(self, interaction):
        if await self.interaction_check(interaction):
            self.step = min(6, self.step + 1)
            self.rebuild()
            await interaction.response.edit_message(content=self.status_text(), view=self)

    async def back(self, interaction):
        if await self.interaction_check(interaction):
            self.step = max(0, self.step - 1)
            self.rebuild()
            await interaction.response.edit_message(content=self.status_text(), view=self)

    async def confirm_selection(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if self.step != 6:
            return await interaction.response.send_message('Review your profile before saving.', ephemeral=True)
        self.used = True
        self.stop()
        await interaction.response.defer()
        from services.role_service import save_profile
        try:
            added, removed = await save_profile(interaction.user, self.mapping_ids,
                                               self.original_role_ids, self.selected_keys)
            text = f'✅ Profile saved. Added: {added}; removed: {removed}.'
        except ValueError as exc:
            text = str(exc)
        except discord.HTTPException:
            text = ('Discord could not confirm the complete save. Some confirmed changes may already be applied. '
                    'Reopen Update Profile to review your current roles; ask staff to check bot permissions if needed.')
        await interaction.edit_original_response(content=text, view=None)

    async def cancel_selection(self, interaction):
        if await self.interaction_check(interaction):
            self.used = True
            self.stop()
            await interaction.response.edit_message(content='Profile cancelled. Nothing changed.', view=None)

    async def on_timeout(self):
        self.used = True


async def open_profile(interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message('Use this inside GamerHQ.', ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        member = await interaction.guild.fetch_member(interaction.user.id)
        session = RoleSelectionSession(member)
        await interaction.followup.send(session.status_text(), view=session, ephemeral=True)
    except (ValueError, discord.HTTPException):
        await interaction.followup.send('Profile settings are unavailable. Ask staff to run /server health.', ephemeral=True)


class SuggestRoleModal(discord.ui.Modal, title="💡 Suggest a Role"):
    role_type = discord.ui.TextInput(
        label="Type",
        placeholder="Language, Platform, Notification or Other",
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
        import config
        if config.STREAMER_ROLE_SELECTION_ENABLED:
            button = discord.ui.Button(label='Streamer', emoji='🎥', custom_id='gamerhq:roles:streamer')
            button.callback = self.toggle_streamer
            self.add_item(button)

    async def toggle_streamer(self, interaction):
        from services.streamer_hub_service import toggle_role
        await interaction.response.defer(ephemeral=True)
        try:
            enabled = await toggle_role(interaction.guild, interaction.user.id)
            await interaction.followup.send(f'Streamer role {"enabled" if enabled else "disabled"}.', ephemeral=True)
        except (ValueError, discord.HTTPException):
            await interaction.followup.send('Streamer role selection is unavailable.', ephemeral=True)


    @discord.ui.button(
        label="Update Profile",
        emoji="👤",
        style=discord.ButtonStyle.primary,
        custom_id="gamerhq:roles:select",
    )
    async def select_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_profile(interaction)


class RoleSuggestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

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


class RoleToggleView(discord.ui.View):
    def __init__(self, group):
        super().__init__(timeout=None)
        from services.role_panel_service import PANEL_GROUPS
        if group == '💡 Missing something?':
            button = RoleSuggestionView().children[0]
            self.add_item(button)
            return
        groups = PANEL_GROUPS.get(group, (group,))
        options = [o for name in groups for o in ROLE_GROUPS[name]]
        if 'Gender' in groups:
            from services.role_service import RoleOption
            options.insert(3, RoleOption('gender-unspecified', 'Prefer not to say', '⚪'))
        for option in options:
            button = discord.ui.Button(label=option.label, emoji=option.emoji,
                custom_id=f'gamerhq:preference:base:{option.key}')
            async def callback(interaction, key=option.key, label=option.label):
                from services.role_service import toggle_preference
                await interaction.response.defer(ephemeral=True)
                try:
                    if not interaction.guild or not isinstance(interaction.user, discord.Member):
                        raise ValueError('Use these settings inside GamerHQ.')
                    enabled = await toggle_preference(interaction.user, 'base', key)
                    text = 'Gender hidden. No visible gender role is selected.' if key == 'gender-unspecified' else f'{"✅" if enabled else "❌"} {label} {"enabled" if enabled else "disabled"}.'
                    await interaction.followup.send(text, ephemeral=True)
                except (ValueError, discord.HTTPException) as exc:
                    await interaction.followup.send(f'Could not update your preference: {exc}', ephemeral=True)
            button.callback = callback
            self.add_item(button)


class OnboardingEntry(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Get Started', emoji='👋', custom_id='gamerhq:onboarding:start')
    async def start(self, interaction, button):
        await open_profile(interaction)


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Persistent custom IDs keep the buttons working after bot restarts.
        self.bot.add_view(ChooseRolesHubView())
        self.bot.add_view(OnboardingEntry())
        from services.role_panel_service import SECTIONS
        for _, group, _ in SECTIONS:
            self.bot.add_view(RoleToggleView(group))


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
