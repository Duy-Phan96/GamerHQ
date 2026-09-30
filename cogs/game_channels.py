"""Normal game administration and persistent private candidate decisions."""
import discord
import config
from database import db
from services.response_service import SafeView, check_admin
from services import game_channel_service as service
from cogs.server_management import Menu


class ConfirmChannel(Menu):
    def __init__(self, guild, actor_id, plan):
        super().__init__(guild, actor_id)
        self.plan, self.used = plan, False
        self.override = plan['count'] >= config.GAME_CHANNEL_SOFT_LIMIT
        self.action('Confirm Removal' if plan['action'] == 'remove' else 'Create Anyway' if self.override else 'Confirm Changes', self.confirm)
        self.action('Cancel', self.cancel)

    async def confirm(self, interaction):
        if not await self.interaction_check(interaction): return
        if self.used:
            return await interaction.response.send_message('Already submitted. Reopen Games to review the result.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            await service.apply(self.guild, interaction.user, self.plan, override=self.override)
            text = '✅ Game channel updated. Game roles and selection are preserved.'
        except (ValueError, discord.HTTPException) as exc:
            text = str(exc) if isinstance(exc, ValueError) else 'Discord could not confirm this operation. Review the stored channel before retrying.'
        await interaction.edit_original_response(content=text, view=None)

    async def cancel(self, interaction):
        await interaction.response.edit_message(content='Cancelled. Nothing was changed.', view=None)


async def review(interaction, game_id, action):
    if not await check_admin(interaction): return
    try:
        plan = service.preview(interaction.guild, interaction.user, game_id, action)
    except ValueError as exc:
        return await interaction.response.send_message(str(exc), ephemeral=True)
    await interaction.response.send_message(service.description(plan),
        view=ConfirmChannel(interaction.guild, interaction.user.id, plan), ephemeral=True)


class CandidateView(SafeView):
    def __init__(self, game_id):
        super().__init__(timeout=None)
        self.game_id = game_id
        for label, action in [('Create Channel', 'create'), ('Ignore', 'ignore')]:
            button = discord.ui.Button(label=label, custom_id=f'gamerhq:game_candidate:{game_id}:{action}')
            async def callback(interaction, action=action):
                if not await check_admin(interaction): return
                if action == 'create':
                    return await review(interaction, self.game_id, action)
                service.ignore(interaction.guild, interaction.user, self.game_id)
                await interaction.response.send_message('Candidate ignored. It will not be posted again automatically.', ephemeral=True)
            button.callback = callback
            self.add_item(button)


class GamesMenu(Menu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        self.action('Game System Migration', self.migration)
        for label, mode in [('Game Library', 'library'), ('Game Channels', 'channels'),
                            ('Channel Candidates', 'candidates'), ('Create Game Channel', 'create'),
                            ('Migrate Legacy Game Chats', 'legacy'), ('Remove Game Channel', 'remove')]:
            async def open_list(interaction, mode=mode):
                view = GamesList(self.guild, self.admin_id, mode)
                await interaction.response.edit_message(content=view.text(), view=view)
            self.action(label, open_list)
        self.action('Suggested Games', self.suggestions)
        self.action('Back', self.back)

    def text(self):
        games = db.get_all_games()
        return ('# 🎮 Game Management\n'
                f'Selectable Games: {len(db.get_selectable_games())}\n'
                f'Games with Channels: {sum(bool(g.get("channel_id")) for g in games)}\n'
                f'Channel Candidates: {sum(c["status"] == "PENDING" for c in service.candidates(self.guild))}\n'
                'Game roles work with or without a dedicated channel. Create channels deliberately; the member threshold never creates them automatically.')

    async def migration(self, interaction):
        from services import game_system_migration as migration
        try:
            draft = migration.preview(self.guild, interaction.user)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.send_message(
            migration.render(draft),
            view=GameMigrationConfirm(self.guild, self.admin_id, draft),
            ephemeral=True,
        )

    async def suggestions(self, interaction):
        await interaction.response.send_message('Review game suggestions in the existing staff suggestion inbox. Approve a game using `/game-admin create`, or enable an existing game with `/game-admin set-visible`. Approval adds a selectable role; channels remain optional.', ephemeral=True)

    async def back(self, interaction):
        from cogs.server_management import ManagementView, TITLE
        await interaction.response.edit_message(content=TITLE, view=ManagementView(self.guild, self.admin_id))


class GamesList(Menu):
    def __init__(self, guild, actor_id, mode, page=0):
        super().__init__(guild, actor_id)
        self.mode, self.page = mode, page
        rows = db.get_all_games() if mode in {'library', 'legacy', 'remove', 'channels'} else db.get_selectable_games()
        pending = {c['game_id'] for c in service.candidates(guild) if c['status'] == 'PENDING'}
        self.rows = sorted([g for g in rows if
            (mode not in {'remove','channels'} or g.get('channel_id')) and
            (mode != 'candidates' or g['id'] in pending) and
            (mode != 'legacy' or service.legacy_hints(g))], key=lambda g: g['name'].casefold())
        if self.rows:
            picker = discord.ui.Select(placeholder='Choose a game', options=[discord.SelectOption(label=g['name'][:100], value=str(g['id'])) for g in self.rows[page*25:(page+1)*25]])
            async def choose(interaction):
                gid = int(picker.values[0])
                if mode == 'library':
                    game = db.get_game_by_id(gid)
                    return await interaction.response.send_message(f'**{discord.utils.escape_markdown(game["name"])}**\nShow/hide this game in the personal selector. Showing creates or reuses its game role. Channels remain optional.', view=LibraryGame(self.guild, self.admin_id, gid), ephemeral=True)
                if mode == 'candidates':
                    return await interaction.response.send_message('Review this game channel candidate.', view=CandidateView(gid), ephemeral=True)
                if mode == 'channels':
                    game = db.get_game_by_id(gid)
                    return await interaction.response.send_message(f'Game channel: <#{game["channel_id"]}>', ephemeral=True)
                await review(interaction, gid, 'migrate' if mode == 'legacy' else mode)
            picker.callback = choose
            self.add_item(picker)
        for label, delta in [('Previous', -1), ('Next', 1)]:
            if 0 <= page+delta < (len(self.rows)+24)//25:
                async def turn(interaction, delta=delta):
                    view = GamesList(guild, actor_id, mode, page+delta)
                    await interaction.response.edit_message(content=view.text(), view=view)
                self.action(label, turn)
        self.action('Back to Games', self.back)

    def text(self):
        return f'# 🎮 Games — {self.mode.title()}\n{len(self.rows)} games · Page {self.page+1}\n' + ('Preview Migration reuses the stored chat. Remaining category/LFG/create-voice resources require manual review; no automatic deletion.' if self.mode == 'legacy' else 'Choose a game. Changes require a separate review and confirmation.')

    async def back(self, interaction):
        view = GamesMenu(self.guild, self.admin_id)
        await interaction.response.edit_message(content=view.text(), view=view)


class LibraryGame(Menu):
    def __init__(self, guild, actor_id, game_id):
        super().__init__(guild, actor_id)
        self.game_id = game_id
        for label, visible in [('Show Game', True), ('Hide Game', False)]:
            async def update(interaction, visible=visible):
                if not await self.interaction_check(interaction): return
                await interaction.response.defer(ephemeral=True)
                from services.game_area_safety import area_lock
                from cogs.games import resolve_or_adopt_game_role
                async with area_lock(self.game_id):
                    game = db.get_game_by_id(self.game_id)
                    if not game:
                        return await interaction.edit_original_response(content='This game is no longer available.', view=None)
                    if visible:
                        role, status, error = await resolve_or_adopt_game_role(self.guild, game, create_if_missing=True)
                        if error or role is None:
                            return await interaction.edit_original_response(content='The game role needs staff review; no selection state changed.', view=None)
                        from services.role_service import assignable
                        if not assignable(role, self.guild):
                            return await interaction.edit_original_response(content='This role is unavailable or unsafe. Ask the owner to review it.', view=None)
                    changed = bool(game['selectable']) != visible
                    db.set_game_selectable(self.game_id, visible)
                if changed:
                    await service.audit(self.guild, 'Game Available' if visible else 'Game Hidden', game['name'])
                await interaction.edit_original_response(content='Game selection updated. Existing channels and member roles are preserved.', view=None)
            self.action(label, update)


class GameMigrationConfirm(Menu):
    def __init__(self, guild, actor_id, draft):
        super().__init__(guild, actor_id)
        self.draft = draft
        self.action('Confirm Chat Migration', self.confirm)
        self.action('Preview Legacy Cleanup', self.cleanup_preview)
        self.action('Cancel', self.cancel)

    async def confirm(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        from services import game_system_migration as migration
        try:
            moved, skipped = await migration.apply(self.guild, interaction.user, self.draft)
            text = f'✅ Migrated {len(moved)} game chat(s).'
            if skipped:
                text += '\n⚠️ ' + '\n⚠️ '.join(skipped[:8])
            text += '\nReopen Game System Migration to preview safe cleanup of obsolete legacy areas.'
            await interaction.edit_original_response(content=text, view=None)
        except (ValueError, discord.HTTPException) as exc:
            await interaction.edit_original_response(content=str(exc), view=None)

    async def cleanup_preview(self, interaction):
        if not await self.interaction_check(interaction):
            return
        from services import game_system_migration as migration
        try:
            draft = migration.cleanup_preview(self.guild, interaction.user)
            await interaction.response.edit_message(
                content=migration.cleanup_text(draft),
                view=GameCleanupConfirm(self.guild, self.admin_id, draft),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    async def cancel(self, interaction):
        await interaction.response.edit_message(content='Cancelled. Nothing changed.', view=None)


class GameCleanupConfirm(Menu):
    def __init__(self, guild, actor_id, draft):
        super().__init__(guild, actor_id)
        self.draft = draft
        self.action('Confirm Legacy Cleanup', self.confirm)
        self.action('Cancel', self.cancel)

    async def confirm(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        from services import game_system_migration as migration
        try:
            removed = await migration.cleanup(self.guild, interaction.user, self.draft)
            await interaction.edit_original_response(
                content=f'✅ Removed {len(removed)} obsolete legacy game area(s). Unknown or active resources were preserved.',
                view=None,
            )
        except (ValueError, discord.HTTPException) as exc:
            await interaction.edit_original_response(content=str(exc), view=None)

    async def cancel(self, interaction):
        await interaction.response.edit_message(content='Cancelled. Nothing deleted.', view=None)
