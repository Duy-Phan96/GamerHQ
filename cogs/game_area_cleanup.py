"""Ephemeral cleanup preview; every deletion needs a separate explicit confirmation."""
import discord

from database import db
from services import game_area_cleanup as cleanup


class CleanupPreview(discord.ui.View):
    def __init__(self, guild, actor_id, rows, unknown, page=0):
        super().__init__(timeout=300)
        self.guild, self.actor_id = guild, actor_id
        self.rows, self.unknown, self.page = rows, unknown, page
        self.rebuild()

    def rebuild(self):
        for item in list(self.children):
            if isinstance(item, discord.ui.Select): self.remove_item(item)
        visible = self.rows[self.page * 10:(self.page + 1) * 10]
        candidates = [r for r in visible if r['safe']]
        if candidates:
            select = discord.ui.Select(placeholder='Select ONE safe area to review', options=[discord.SelectOption(label=r['game']['name'][:100], value=str(r['game']['id'])) for r in candidates])
            async def selected(interaction):
                row = next(r for r in candidates if r['game']['id'] == int(select.values[0]))
                await interaction.response.edit_message(
                    content=f"**Delete the area for {row['game']['name']}?**\nCategory: <#{row['game']['category_id']}>\n"
                            f"Channels: {len(row['children'])}\nHidden/inactive, no events or temporary resources.\n"
                            "Channel message history is permanently deleted. Game record and role are kept.\n"
                            "Conditions will be checked again immediately before each deletion.",
                    view=CleanupConfirm(self.guild, self.actor_id, row))
            select.callback = selected
            self.add_item(select)
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 10 >= len(self.rows)

    def content(self):
        lines = ['# GAME AREA CLEANUP PREVIEW', 'Select a safe candidate, then review and confirm. No deletion happens on this screen.', '']
        for row in self.rows[self.page * 10:(self.page + 1) * 10]:
            label = 'Candidate' if row['safe'] else 'Keep'
            lines.append(f"**{label}: {discord.utils.escape_markdown(row['game']['name'])[:80]}** — {'; '.join(row['reasons'])[:110]}")
        lines.append(f"\nKeep: {len(self.unknown)} unlinked categories — not confidently GamerHQ game areas.")
        lines.append(f'Page {self.page + 1}/{max(1, (len(self.rows) + 9) // 10)}')
        return '\n'.join(lines)[:2000]

    async def interaction_check(self, interaction):
        if interaction.user.id != self.actor_id or not cleanup.authorized(self.guild, interaction.user):
            await interaction.response.send_message('Only the owner/admin who opened this preview may use it.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Previous', row=1)
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1); self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label='Next', row=1)
    async def next_page(self, interaction, button):
        self.page = min(max(0, (len(self.rows)-1)//10), self.page + 1); self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label='Cancel', row=1)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content='Cleanup cancelled. Nothing deleted.', view=None)
        self.stop()


class CleanupConfirm(discord.ui.View):
    def __init__(self, guild, actor_id, snapshot):
        super().__init__(timeout=120)
        self.guild, self.actor_id, self.snapshot = guild, actor_id, snapshot
        self.running = False

    @discord.ui.button(label='Confirm Cleanup', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.actor_id or not cleanup.authorized(self.guild, interaction.user):
            return await interaction.response.send_message('Only the owner/admin who opened this confirmation may delete the area.', ephemeral=True)
        if self.running:
            return await interaction.response.send_message('This confirmation has already been used.', ephemeral=True)
        self.running = True
        await interaction.response.defer(ephemeral=True)
        try:
            result = await cleanup.delete_confirmed_area(self.guild, interaction.user, self.snapshot)
        except ValueError as exc:
            result = str(exc)
        await interaction.edit_original_response(content=result, view=None)
        self.stop()

    @discord.ui.button(label='Cancel')
    async def cancel(self, interaction, button):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message('This confirmation belongs to another user.', ephemeral=True)
        await interaction.response.edit_message(content='Cleanup cancelled. Nothing deleted.', view=None)
        self.stop()
