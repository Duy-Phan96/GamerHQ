"""Actor-bound, ephemeral game selection. Public boards never carry member state."""
import logging
import discord
from services.response_service import SafeView
from services.game_catalog_service import member_counts, sections
from services.role_service import set_game_selection

log = logging.getLogger(__name__)
PAGE_SIZE = 20  # Four rows of games, one row reserved for navigation.
POPULAR = '🔥 Popular'
LETTER_RANGES = ('A–E', 'F–J', 'K–O', 'P–T', 'U–Z')


class GameSelectionSession(SafeView):
    def __init__(self, member, games):
        super().__init__(timeout=300)
        self.member_id, self.guild_id = member.id, member.guild.id
        self.guild, self.games = member.guild, games
        self.role_ids = {g['id']: g['role_id'] for g in games}
        held = {r.id for r in member.roles}
        self.selected = {g['id'] for g in games if g['role_id'] in held}
        ranked = sections(games, member_counts(member.guild, games))
        self.games_by_group = {POPULAR: ranked[POPULAR]} if POPULAR in ranked else {}
        for label in (*LETTER_RANGES, '#'):
            entries = [g for letter, rows in ranked.items() if letter != POPULAR
                       and (letter == '#' if label == '#' else label[0] <= letter <= label[-1])
                       for g in rows]
            if entries:
                self.games_by_group[label] = sorted(entries, key=lambda g: (g['name'].casefold(), g['id']))
        self.current_group = next(iter(self.games_by_group), None)
        self.page = 0
        self.browsing = False
        self.busy = False
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.guild and interaction.guild.id == self.guild_id and interaction.user.id == self.member_id:
            return True
        await interaction.response.send_message('This selector belongs to another member.', ephemeral=True)
        return False

    def status_text(self):
        group_label = '0–9 / Other' if self.current_group == '#' else self.current_group
        heading = 'Browse A–Z' if self.browsing else (
            f"{group_label if self.current_group == POPULAR else '🎮 Games ' + (group_label or '')}"
            f" · {self.page + 1}/{self.page_count()}")
        return ('# 🎮 Your Games\n\nChoose a game to add or remove it immediately.\n'
                '✅ Selected · ➕ Add\n\n## ' + heading)

    def page_count(self):
        return max(1, (len(self.games_by_group.get(self.current_group, [])) + PAGE_SIZE - 1) // PAGE_SIZE)

    def navigation(self, label, callback, *, row, disabled=False):
        button = discord.ui.Button(label=label, row=row, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def navigate(self, interaction, *, group=None, delta=0, browse=False):
        if not await self.interaction_check(interaction): return
        if self.busy:
            return await interaction.response.send_message('Please wait a moment, then try again.', ephemeral=True)
        self.browsing = browse
        if group is not None:
            self.current_group, self.page = group, 0
        self.page = max(0, min(self.page_count() - 1, self.page + delta))
        self.rebuild()
        await interaction.response.edit_message(content=self.status_text(), view=self)

    def rebuild(self):
        self.clear_items()
        if not self.games_by_group:
            return
        async def popular(interaction): await self.navigate(interaction, group=POPULAR)
        async def browse(interaction): await self.navigate(interaction, browse=True)
        if self.browsing:
            for index, group in enumerate(g for g in self.games_by_group if g != POPULAR):
                async def choose(interaction, group=group): await self.navigate(interaction, group=group)
                self.navigation('0–9 / Other' if group == '#' else group, choose, row=index // 5)
            self.navigation('Back to Popular', popular, row=2)
            return
        games = self.games_by_group[self.current_group]
        for index, game in enumerate(games[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]):
            selected = game['id'] in self.selected
            # Discord buttons permit 80 characters; bound UTF-16 too for emoji names.
            label = (('✅ ' if selected else '➕ ') + game['name']).encode('utf-16-le')[:160].decode('utf-16-le', errors='ignore')
            button = discord.ui.Button(label=label, row=index // 5,
                style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
                custom_id=f'gamerhq:personal_game:{game["id"]}')
            async def toggle(interaction, game_id=game['id']): await self.toggle(interaction, game_id)
            button.callback = toggle
            self.add_item(button)
        async def previous(interaction): await self.navigate(interaction, delta=-1)
        async def next_page(interaction): await self.navigate(interaction, delta=1)
        self.navigation('◀ Previous', previous, row=4, disabled=self.page == 0)
        self.navigation('Next ▶', next_page, row=4, disabled=self.page + 1 >= self.page_count())
        self.navigation('Browse A–Z' if self.current_group == POPULAR else 'Other Letter Ranges', browse, row=4)
        if self.current_group != POPULAR:
            self.navigation('Back to Popular', popular, row=4)

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
        except (ValueError, discord.HTTPException) as exc:
            log.warning('Personal game unavailable: guild=%s game=%s reviewed_role=%s error=%s',
                        self.guild_id, game_id, self.role_ids[game_id], type(exc).__name__)
            await interaction.followup.send('This game is temporarily unavailable.', ephemeral=True)
        finally:
            self.busy = False
