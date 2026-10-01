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
        self.action('Manage Visible Games', self.visible_selector)
        self.action('Set up Games Channels', self.visible_setup)
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
                'Manage Visible Games selects several games to show or hide together after review. Set up Games Channels also handles missing chats for already-visible games. Nothing is deleted.')

    async def visible_selector(self, interaction):
        from cogs.game_visibility_selector import open_selector
        await open_selector(interaction)

    async def visible_setup(self, interaction):
        await open_visible_setup(interaction)

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
        await interaction.response.send_message('Review game suggestions in the existing staff suggestion inbox. Approve a game using `/game-admin create`, or enable an existing game with `/game-admin set-visible`. Show Game reviews its game role and shared Games text channel together.', ephemeral=True)

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
                    return await interaction.response.send_message(f'**{discord.utils.escape_markdown(game["name"])}**\nShow this game and its role-gated Games text channel, or hide both without deleting messages. Changes require confirmation.', view=LibraryGame(self.guild, self.admin_id, gid), ephemeral=True)
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
                await open_visibility(interaction, self.game_id, visible)
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


async def open_visibility(interaction, game_id, visible):
    """Shared slash/library/legacy-setup entry; opening never mutates game state."""
    if not await check_admin(interaction):
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    from services import game_visibility_service as visibility
    try:
        plan = await visibility.preview(interaction.guild, interaction.user, game_id, visible)
        await interaction.edit_original_response(content=visibility.description(plan),
            view=VisibilityConfirmation(interaction.guild, interaction.user.id, plan),
            allowed_mentions=discord.AllowedMentions.none())
    except (ValueError, discord.HTTPException) as exc:
        text = str(exc) if isinstance(exc, ValueError) else 'Discord could not read the current channels/roles. Check bot access and try again.'
        await interaction.edit_original_response(content=text, view=None,
                                                allowed_mentions=discord.AllowedMentions.none())


class VisibilityConfirmation(Menu):
    def __init__(self, guild, actor_id, plan):
        super().__init__(guild, actor_id)
        self.plan, self.used = plan, False
        self.action('Create Anyway' if plan['override'] else 'Confirm Show' if plan['visible'] else 'Confirm Hide', self.confirm)
        self.action('Cancel', self.cancel)

    async def confirm(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if self.used:
            return await interaction.response.send_message('This review was used. Reopen Games for the current result.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        from services import game_visibility_service as visibility
        try:
            room = await visibility.apply(interaction.guild, interaction.user, self.plan,
                                          override=self.plan['override'])
            name = discord.utils.escape_markdown(self.plan['game']['name'])[:100]
            if self.plan['visible']:
                text = f'✅ {name} is selectable. Its Games text channel is ready: {room.mention}'
            else:
                text = f'✅ {name} is hidden from selection and its chat is hidden from game members. Staff can still access it.' if room else f'✅ {name} is hidden. No game chat is linked.'
            text += '\nNo messages or member roles were deleted.'
        except (ValueError, discord.HTTPException) as exc:
            text = str(exc) if isinstance(exc, ValueError) else f'Discord could not complete this change (HTTP {exc.status}). Check bot permissions and reopen the review.'
            text += '\nSome steps may already be saved. The previous channel is never deleted as a rollback.'
        await interaction.edit_original_response(content=text, view=None,
                                                allowed_mentions=discord.AllowedMentions.none())
        self.stop()

    async def cancel(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.used = True
        await interaction.response.edit_message(content='Cancelled. No visibility or channel changes made.', view=None)
        self.stop()


async def open_visible_setup(interaction):
    if not await check_admin(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    from services import game_visibility_service as visibility
    try:
        summary = await visibility.sync_preview(interaction.guild, interaction.user, limit=3)
        view = VisibleSetupChoices(interaction.guild, interaction.user.id, summary)
        await interaction.edit_original_response(content=view.text(), view=view,
                                                allowed_mentions=discord.AllowedMentions.none())
    except (ValueError, discord.HTTPException) as exc:
        text = str(exc) if isinstance(exc, ValueError) else 'Discord could not read the current Games setup. Check the bot access.'
        await interaction.edit_original_response(content=text, view=None)


class VisibleSetupChoices(Menu):
    """Three real visible games per pass, each using the exact same reviewed action.

    Later games get fresh previews after an earlier action creates the category;
    no stale bulk plan silently adopts changes that happened in the meantime.
    """
    def __init__(self, guild, actor_id, summary):
        super().__init__(guild, actor_id)
        self.summary = summary
        for plan in summary['plans']:
            game = plan['game']
            async def choose(interaction, game_id=game['id']):
                if not await self.interaction_check(interaction):
                    return
                await open_visibility(interaction, game_id, True)
            self.action('Set up ' + game['name'][:60], choose)
        self.action('Refresh / Next games', self.refresh)
        self.action('Close', self.close)

    def text(self):
        s = self.summary
        text = f"# 🎮 Set up Games Channels\n{ s['ready'] } already ready. {len(s['plans'])} visible games in this pass.\nChoose a game to review category creation, chat reuse and role access. No old categories or channels are deleted.\n"
        for plan in s['plans']:
            game = discord.utils.escape_markdown(plan['game']['name'])[:80]
            text += f"\n• {game}: " + ('reuse/move saved chat' if plan['channel_id'] else 'create text chat')
        if not s['plans'] and not s['blocked']:
            text += '\nNo pending visible games. Show a hidden game from the library to set up its chat.'
        for warning in s['blocked'][:5]:
            text += '\n⚠️ ' + discord.utils.escape_markdown(warning)[:200]
        if len(s['blocked']) > 5:
            text += f"\n{len(s['blocked'])-5} more blocked games; review them individually in Game Library."
        return text[:1950]

    async def refresh(self, interaction):
        if await self.interaction_check(interaction):
            await open_visible_setup(interaction)

    async def close(self, interaction):
        if await self.interaction_check(interaction):
            await interaction.response.edit_message(content='Closed. No additional changes made.', view=None)
        self.stop()
