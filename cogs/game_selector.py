"""Actor-bound, ephemeral game selection. Public boards never carry member state."""
import logging
import discord
from services.response_service import SafeView
from services.game_catalog_service import member_counts, sections
from services.role_service import set_game_selection

log = logging.getLogger(__name__)


class GameSelectionSession(SafeView):
    def __init__(self, member, games):
        super().__init__(timeout=300)
        self.member_id, self.guild_id = member.id, member.guild.id
        self.guild, self.games = member.guild, games
        self.role_ids = {g['id']: g['role_id'] for g in games}
        held = {r.id for r in member.roles}
        self.selected = {g['id'] for g in games if g['role_id'] in held}
        self.games_by_group = sections(games, member_counts(member.guild, games))
        self.current_group = next(iter(self.games_by_group), None)
        self.page = self.group_page = 0
        self.busy = False
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.guild and interaction.guild.id == self.guild_id and interaction.user.id == self.member_id:
            return True
        await interaction.response.send_message('This selector belongs to another member.', ephemeral=True)
        return False

    def status_text(self):
        return ('# 🎮 Your Games\nChoose a game to add or remove it immediately. '
                '✅ Selected · ➕ Add\nYour game roles never enable notifications automatically.\n\n'
                f'**{self.current_group or "No games available"}** · Page {self.page + 1}')

    def rebuild(self):
        self.clear_items()
        groups = list(self.games_by_group)
        if not groups:
            return
        nav = discord.ui.Select(placeholder='Popular / A–Z', row=0, options=[
            discord.SelectOption(label=k, value=k, default=k == self.current_group)
            for k in groups[self.group_page * 25:(self.group_page + 1) * 25]])
        async def choose(interaction):
            if not await self.interaction_check(interaction): return
            self.current_group, self.page = nav.values[0], 0
            self.rebuild()
            await interaction.response.edit_message(content=self.status_text(), view=self)
        nav.callback = choose
        self.add_item(nav)
        games = self.games_by_group[self.current_group]
        picker = discord.ui.Select(placeholder='Choose one game to toggle', row=1, options=[
            discord.SelectOption(label=(('✅ ' if g['id'] in self.selected else '➕ ') + g['name'])[:100], value=str(g['id']))
            for g in games[self.page * 25:(self.page + 1) * 25]])
        async def toggle(interaction):
            await self.toggle(interaction, int(picker.values[0]))
        picker.callback = toggle
        self.add_item(picker)
        for label, field, delta, total in [('Previous', 'page', -1, (len(games)+24)//25),
                                          ('Next', 'page', 1, (len(games)+24)//25),
                                          ('Earlier letters', 'group_page', -1, (len(groups)+24)//25),
                                          ('More letters', 'group_page', 1, (len(groups)+24)//25)]:
            if total < 2: continue
            button = discord.ui.Button(label=label, row=2, disabled=not 0 <= getattr(self, field)+delta < total)
            async def turn(interaction, field=field, delta=delta, total=total):
                if not await self.interaction_check(interaction): return
                setattr(self, field, max(0, min(total-1, getattr(self, field)+delta)))
                self.rebuild()
                await interaction.response.edit_message(content=self.status_text(), view=self)
            button.callback = turn
            self.add_item(button)

    async def toggle(self, interaction, game_id):
        if not await self.interaction_check(interaction): return
        if self.busy or game_id not in self.role_ids:
            return await interaction.response.send_message('Please wait, then choose a game again.', ephemeral=True)
        self.busy = True
        await interaction.response.defer()
        try:
            from database import db
            game = db.get_game_by_id(game_id)
            if not game or not game['active'] or not game['selectable']:
                raise ValueError('Game is no longer selectable')
            # Explicit desired state makes stale double delivery idempotent. The shared
            # role service refreshes this member and checks ownership and hierarchy.
            enabled = game_id not in self.selected
            await set_game_selection(interaction.user, [(game_id, self.role_ids[game_id], enabled, False)])
            (self.selected.add if enabled else self.selected.discard)(game_id)
            self.rebuild()
            await interaction.edit_original_response(content=self.status_text(), view=self)
        except (ValueError, discord.HTTPException):
            log.warning('Personal game selection failed for game %s', game_id)
            await interaction.followup.send('This game could not be updated. Reopen Select Games or ask staff for help.', ephemeral=True)
        finally:
            self.busy = False
