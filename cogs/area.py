from services.response_service import SafeView, command_error
"""Admin-only paginated multi-selection; removal always needs a separate confirmation."""
import io
import discord
from discord import app_commands
from discord.ext import commands
from services import area_management_service as service
from services.game_area_cleanup import authorized


class AdminView(SafeView):
    def __init__(self, guild, actor_id):
        super().__init__(timeout=300)
        self.guild, self.actor_id = guild, actor_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.actor_id or not authorized(self.guild, interaction.user):
            await interaction.response.send_message('Only the owner/admin who opened this panel can use it.', ephemeral=True)
            return False
        return True


class AreaMenu(AdminView):
    async def route(self, interaction, mode):
        view = AreaSelect(self.guild, self.actor_id, mode)
        await interaction.response.edit_message(content=view.content(), view=view)

    @discord.ui.button(label='➕ Add Game Areas', style=discord.ButtonStyle.primary)
    async def add(self, interaction, button):
        await self.route(interaction, 'add')

    @discord.ui.button(label='➖ Remove Game Areas', style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await self.route(interaction, 'remove')

    @discord.ui.button(label='Cancel')
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content='Cancelled. Nothing changed.', view=None)
        self.stop()


class AreaSelect(AdminView):
    def __init__(self, guild, actor_id, mode):
        super().__init__(guild, actor_id)
        self.mode, self.page, self.selected = mode, 0, set()
        self.games = service.candidates(mode)
        self.rebuild()

    def content(self):
        return f'🎮 {self.mode.upper()} GAME AREAS\nPage {self.page+1}/{max(1,(len(self.games)+19)//20)} · Selected: {len(self.selected)}\nSelections are retained across pages. Selecting alone changes nothing.'

    def rebuild(self):
        for child in list(self.children):
            if isinstance(child, discord.ui.Select): self.remove_item(child)
        games = self.games[self.page*20:(self.page+1)*20]
        if games:
            select = discord.ui.Select(placeholder='Select games on this page', min_values=0, max_values=len(games), row=0,
                options=[discord.SelectOption(label=g['name'][:100], value=str(g['id']), default=g['id'] in self.selected) for g in games])
            async def selected(interaction):
                self.selected.difference_update(g['id'] for g in games)
                self.selected.update(int(v) for v in select.values)
                self.rebuild()
                await interaction.response.edit_message(content=self.content(), view=self)
            select.callback = selected
            self.add_item(select)
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page+1)*20 >= len(self.games)
        self.proceed.disabled = not self.selected
        self.proceed.label = 'Create Areas' if self.mode == 'add' else 'Continue to Preview'

    @discord.ui.button(label='Previous', row=1)
    async def previous(self, interaction, button):
        self.page = max(0,self.page-1); self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label='Next', row=1)
    async def next_page(self, interaction, button):
        self.page = min(max(0,(len(self.games)-1)//20),self.page+1); self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label='Continue', style=discord.ButtonStyle.primary, row=1)
    async def proceed(self, interaction, button):
        if not await self.interaction_check(interaction): return
        if self.mode == 'add':
            if getattr(self, 'used', False):
                return await interaction.response.send_message('Already submitted.', ephemeral=True)
            self.used = True
            await interaction.response.defer(ephemeral=True)
            await interaction.edit_original_response(content='Creating selected Game Areas…', view=None)
            results = await service.create_many(self.guild, interaction.user, self.selected)
            await send_results(interaction, 'GAME AREA CREATION', results)
            self.stop()
        else:
            rows = service.preview(self.guild, interaction.user, self.selected)
            view = AreaConfirm(self.guild, self.actor_id, rows, self)
            details = '\n'.join(f'{r["game"]["name"]}: {"SAFE" if r["safe"] else "BLOCKED"} — {"; ".join(r["reasons"])}\nCategory {r["game"]["category_id"]}; children: {", ".join(str(c.id) for c in r["children"])}' for r in rows)
            summary = '\n'.join(f'{"✅" if r["safe"] else "⛔"} {discord.utils.escape_markdown(r["game"]["name"])[:70]} — {"safe" if r["safe"] else r["reasons"][0][:65]}' for r in rows[:8])
            await interaction.response.edit_message(content=f'⚠️ Remove {sum(r["safe"] for r in rows)} safe Game Areas?\n{summary}\n\nManaged categories/channels and their message history will be permanently deleted. Games, roles, selection, LFG availability and historical records remain.\nSafety is rechecked before every deletion. The attachment contains every selected area and its checks.',
                attachments=[discord.File(io.BytesIO(details.encode('utf-8')), filename='area-removal-preview.txt')], view=view, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Back', row=2)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content='🎮 Game Area Management\nWhat do you want to do?', view=AreaMenu(self.guild,self.actor_id), attachments=[])

    @discord.ui.button(label='Cancel', row=2)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='Cancelled. Nothing changed.', view=None, attachments=[])
        self.stop()


async def send_results(interaction, title, results):
    text = '\n'.join(results) or 'Nothing selected.'
    await interaction.edit_original_response(content=f'**{title}**\n' + (text if len(text)<1700 else f'{len(results)} results; see attached report.'),
        attachments=[] if len(text)<1700 else [discord.File(io.BytesIO(text.encode('utf-8')), filename='area-results.txt')], view=None,
        allowed_mentions=discord.AllowedMentions.none())


class AreaConfirm(AdminView):
    def __init__(self, guild, actor_id, rows, previous):
        super().__init__(guild, actor_id)
        self.rows, self.previous, self.used = rows, previous, False
        self.confirm.disabled = not any(r['safe'] for r in rows)

    @discord.ui.button(label='Confirm Removal', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction): return
        if self.used:
            return await interaction.response.send_message('This confirmation was already used.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content='Rechecking and removing confirmed Game Areas…', view=None, attachments=[])
        results = await service.remove_many(self.guild, interaction.user, self.rows)
        await send_results(interaction, 'GAME AREA REMOVAL', results)
        self.stop()

    @discord.ui.button(label='Back')
    async def back(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content=self.previous.content(), view=self.previous, attachments=[])
        self.stop()

    @discord.ui.button(label='Cancel')
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='Cancelled. Nothing deleted.', view=None, attachments=[])
        self.stop()


class Areas(commands.Cog):
    cog_app_command_error = command_error
    area = app_commands.Group(name='area', description='GamerHQ Game Area administration', default_permissions=discord.Permissions(administrator=True))

    @area.command(name='manage', description='Add or safely remove multiple Game Areas.')
    @app_commands.guild_only()
    async def manage(self, interaction: discord.Interaction):
        if not authorized(interaction.guild, interaction.user):
            return await interaction.response.send_message('Owner or administrator required.', ephemeral=True)
        await interaction.response.send_message('🎮 Game Area Management\nWhat do you want to do?', view=AreaMenu(interaction.guild, interaction.user.id), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Areas())
