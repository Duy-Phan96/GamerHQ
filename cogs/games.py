import asyncio
import json
import sqlite3
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import DISPLAY_GROUP_ORDER, GAME_SUGGESTIONS_CHANNEL_ID, CHOOSE_GAMES_CHANNEL_ID
from database import db
from services.game_service import (
    GameStructureError,
    create_game_structure_confirmed,
    format_plan,
    inspect_game_structure,
    refresh_choose_games_message,
    rebuild_choose_games_message,
    build_choose_games_sections,
    remove_game_structure,
    search_games,
)


class ToggleGameView(discord.ui.View):
    def __init__(self, game, role, member):
        super().__init__(timeout=120)
        self.game = game
        self.role = role
        self.member_id = member.id

        if role in member.roles:
            self.toggle.label = "Remove Game"
            self.toggle.style = discord.ButtonStyle.danger
            self.toggle.emoji = "➖"

    @discord.ui.button(label="Add Game", emoji="➕", style=discord.ButtonStyle.success)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "This selector belongs to another user.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            if self.role in interaction.user.roles:
                await interaction.user.remove_roles(self.role, reason="GamerHQ game select")
                result_text = f"✅ Removed **{self.game['name']}**."
            else:
                await interaction.user.add_roles(self.role, reason="GamerHQ game select")
                result_text = f"✅ Added **{self.game['name']}**."
        except discord.Forbidden:
            result_text = "❌ I could not update that role. Please ask staff to check the bot role position/permissions."
        except discord.HTTPException as exc:
            result_text = f"❌ Discord could not update the role: `{exc}`"

        await interaction.edit_original_response(
            content=result_text,
            embed=None,
            view=None,
        )
        self.stop()


class ConfirmGameAddView(discord.ui.View):
    def __init__(self, cog, game, plan, admin_id, library_snapshot=None):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.plan = plan
        self.admin_id = admin_id
        self.library_snapshot = library_snapshot or {
            "id": game["id"],
            "name": game["name"],
            "selectable": game.get("selectable"),
            "role_id": game.get("role_id"),
            "display_group": game.get("display_group"),
        }

    @discord.ui.button(
        label="Confirm & Apply",
        emoji="✅",
        style=discord.ButtonStyle.success,
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this preview can confirm it.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Re-scan immediately before execution so stale previews cannot create surprises.
        fresh_plan = inspect_game_structure(interaction.guild, self.game)

        if fresh_plan["conflicts"]:
            await interaction.edit_original_response(
                content=(
                    "❌ The server changed since the preview. Conflicts now exist.\n\n"
                    + format_plan(fresh_plan)
                    + "\n\nNothing was changed."
                ),
                embed=None,
                view=None,
            )
            return

        try:
            game = await create_game_structure_confirmed(
                interaction.guild,
                self.game,
                fresh_plan,
            )
            game = db.get_game_by_id(game["id"])

            # Area operations must preserve Game Library identity and visibility.
            if game is None:
                raise GameStructureError(
                    "VERIFY DATABASE",
                    RuntimeError("The Game Library entry disappeared during area creation."),
                )
            if game["id"] != self.library_snapshot["id"] or game["name"] != self.library_snapshot["name"]:
                raise GameStructureError(
                    "VERIFY DATABASE",
                    RuntimeError("The Game Library identity changed during area creation."),
                )
            if game.get("selectable") != self.library_snapshot.get("selectable"):
                raise GameStructureError(
                    "VERIFY DATABASE",
                    RuntimeError("Game visibility changed during area creation."),
                )
        except GameStructureError as exc:
            original = exc.original
            await interaction.edit_original_response(
                content=(
                    f"❌ **Nothing should remain from this failed operation.**\n"
                    f"The bot rolled back every resource it created during this attempt.\n\n"
                    f"**Step:** `{exc.stage}`\n"
                    f"**Error:** `{type(original).__name__}: {original}`"
                ),
                embed=None,
                view=None,
            )
            return

        # Areas do not affect selector membership or its stable game-ID buttons.
        overview = "Choose Your Games did not need a refresh."

        await interaction.edit_original_response(
            content=(
                f"✅ The dedicated **{game['name']}** area is ready.\n"
                f"Role/category/channels were created or explicitly reused as shown in the preview.\n"
                f"Choose Your Games visibility was left unchanged.\n\n"
                f"{overview}"
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this preview can cancel it.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content="Cancelled. Nothing was changed.",
            embed=None,
            view=None,
        )


class RemoveConfirmView(discord.ui.View):
    def __init__(self, cog, game, admin_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.game = game
        self.admin_id = admin_id

    @discord.ui.button(label="Remove Area", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this confirmation can confirm it.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await remove_game_structure(interaction.guild, self.game)

        try:
            await refresh_choose_games_message(self.cog.bot, view=GameCategoryView, intro_view=lambda: ChooseGamesButtons(self.cog))
            overview = "\n✅ `choose-your-games` updated."
        except GameStructureError as exc:
            overview = (
                "\n⚠️ Game was removed, but `choose-your-games` could not be updated: "
                f"`{type(exc.original).__name__}: {exc.original}`"
            )

        await interaction.edit_original_response(
            content=f"✅ The dedicated **{self.game['name']}** area was removed. The game role/library entry was kept.{overview}",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Cancelled. Nothing was changed.",
            embed=None,
            view=None,
        )


class DeleteGameConfirmView(discord.ui.View):
    def __init__(self, cog, game, admin_id):
        super().__init__(timeout=90)
        self.cog = cog
        self.game = game
        self.admin_id = admin_id

    @discord.ui.button(label="Delete Game Permanently", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this confirmation can confirm it.",
                ephemeral=True,
            )
            return

        current = db.get_game_by_id(self.game["id"])
        if current is None:
            await interaction.response.edit_message(
                content="ℹ️ This game has already been deleted.",
                embed=None,
                view=None,
            )
            return

        deps = db.get_game_delete_dependencies(current["id"])
        if any(deps.values()):
            await interaction.response.edit_message(
                content=(
                    "❌ Permanent deletion was blocked because this game is still referenced by data:\n"
                    f"• LFG events: **{deps['lfg_events']}**\n"
                    f"• Streamer applications: **{deps['streamer_applications']}**\n"
                    f"• Streamer profiles: **{deps['streamer_profiles']}**\n\n"
                    "Reassign or clean up those records first. Nothing was deleted."
                ),
                embed=None,
                view=None,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            if current.get("area_enabled") or current.get("category_id"):
                await remove_game_structure(interaction.guild, current)
                current = db.get_game_by_id(current["id"]) or current
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="❌ I could not remove the dedicated area. Check Manage Channels permission. The library entry was kept.",
                embed=None,
                view=None,
            )
            return

        role = interaction.guild.get_role(int(current["role_id"])) if current.get("role_id") else None
        if role is not None:
            try:
                await role.delete(reason=f"GamerHQ permanent game delete: {current['name']}")
            except discord.Forbidden:
                await interaction.edit_original_response(
                    content="❌ I could not delete the game role. Check Manage Roles and the bot role position. The library entry was kept.",
                    embed=None,
                    view=None,
                )
                return

        try:
            db.delete_game_permanently(current["id"])
        except Exception as exc:
            await interaction.edit_original_response(
                content=(
                    "⚠️ Discord resources were removed, but the library record could not be deleted.\n"
                    f"`{type(exc).__name__}: {exc}`"
                ),
                embed=None,
                view=None,
            )
            return

        try:
            await refresh_choose_games_message(
                self.cog.bot,
                view=GameCategoryView,
                intro_view=lambda: ChooseGamesButtons(self.cog),
            )
            overview = "\n✅ `choose-your-games` updated."
        except GameStructureError as exc:
            overview = (
                "\n⚠️ The game was deleted, but `choose-your-games` could not refresh: "
                f"`{type(exc.original).__name__}: {exc.original}`"
            )

        await interaction.edit_original_response(
            content=(
                f"✅ **{self.game['name']}** was permanently deleted from GamerHQ.\n"
                "Its dedicated area (if present), game role and Game Library entry were removed."
                f"{overview}"
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this confirmation can cancel it.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Cancelled. Nothing was changed.",
            embed=None,
            view=None,
        )



def _setup_scan(guild):
    games = db.get_area_games()
    results, conflicts, existing = [], [], 0

    for game in games:
        plan = inspect_game_structure(guild, game)
        results.append((game, plan))
        conflicts += [f"{game['name']}: {c}" for c in plan["conflicts"]]

        if (
            plan["role"]
            or plan["category"]
            or any(x["existing"] for x in plan["channels"].values())
        ):
            existing += 1

    return results, conflicts, existing


def _setup_counts(results):
    roles = categories = texts = voices = 0

    for _, plan in results:
        roles += int(plan["role"] is None)
        categories += int(plan["category"] is None)
        texts += sum(
            int(plan["channels"][k]["existing"] is None)
            for k in ("chat", "lfg") if k in plan["channels"]
        )
        voices += int(plan["channels"]["create_voice"]["existing"] is None)

    return roles, categories, texts, voices


class ConfirmInitialSetupView(discord.ui.View):
    def __init__(self, cog, admin_id):
        super().__init__(timeout=180)
        self.cog = cog
        self.admin_id = admin_id
        self.running = False

    @discord.ui.button(
        label="Start Setup",
        emoji="✅",
        style=discord.ButtonStyle.success,
    )
    async def confirm(self, interaction, button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this setup can confirm it.",
                ephemeral=True,
            )
            return

        if self.running:
            await interaction.response.send_message(
                "Setup is already running.",
                ephemeral=True,
            )
            return

        self.running = True

        # Fresh full scan immediately before any mutation.
        results, conflicts, _ = _setup_scan(interaction.guild)

        if conflicts:
            preview = "\n".join("• " + x for x in conflicts[:15])
            more = ""
            if len(conflicts) > 15:
                more = f"\n…and {len(conflicts) - 15} more conflict(s)."

            await interaction.response.edit_message(
                content=(
                    "❌ **Setup aborted before creating anything.**\n"
                    "Conflicts were found during the final safety scan:\n\n"
                    f"{preview}{more}\n\n"
                    "Resolve these duplicates first, then run `/game-admin setup` again."
                ),
                embed=None,
                view=None,
            )
            return

        if not results:
            await interaction.response.edit_message(
                content="✅ All catalog games are already active. Nothing was changed.",
                embed=None,
                view=None,
            )
            return

        r, c, t, v = _setup_counts(results)
        projected_roles = len(interaction.guild.roles) + r
        projected_channels = len(interaction.guild.channels) + c + t + v

        if projected_roles > 250:
            await interaction.response.edit_message(
                content=(
                    "❌ **Setup aborted before creating anything.**\n"
                    f"Projected roles: `{projected_roles}/250`."
                ),
                embed=None,
                view=None,
            )
            return

        if projected_channels > 500:
            await interaction.response.edit_message(
                content=(
                    "❌ **Setup aborted before creating anything.**\n"
                    f"Projected channels/categories: `{projected_channels}/500`."
                ),
                embed=None,
                view=None,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        completed = []

        for game, _ in results:
            # Fresh per-game scan before every creation.
            plan = inspect_game_structure(interaction.guild, game)

            if plan["conflicts"]:
                await interaction.edit_original_response(
                    content=(
                        f"⚠️ Setup stopped safely before **{game['name']}** because "
                        f"a conflict appeared.\nCompleted games: **{len(completed)}**.\n\n"
                        "Nothing for the conflicting game was created."
                    )
                )
                return

            try:
                completed.append(
                    await create_game_structure_confirmed(
                        interaction.guild,
                        game,
                        plan,
                    )
                )
            except GameStructureError as exc:
                await interaction.edit_original_response(
                    content=(
                        f"⚠️ Setup stopped safely at **{game['name']}**.\n"
                        f"Completed: **{len(completed)}**\n"
                        f"Step: `{exc.stage}`\n"
                        f"Error: `{type(exc.original).__name__}: {exc.original}`\n\n"
                        "The failed game's partial resources were rolled back. "
                        "Games completed before the failure were kept. "
                        "Run `/game-admin setup` again after fixing the issue."
                    )
                )
                return

            await asyncio.sleep(0.75)

        try:
            await refresh_choose_games_message(self.cog.bot, view=GameCategoryView, intro_view=lambda: ChooseGamesButtons(self.cog))
            overview = "✅ `choose-your-games` updated."
        except GameStructureError as exc:
            overview = (
                "⚠️ Games were created, but `choose-your-games` could not be updated: "
                f"`{type(exc.original).__name__}: {exc.original}`"
            )

        await interaction.edit_original_response(
            content=(
                "🎉 **Area reconciliation completed.**\n"
                f"Game areas reconciled: **{len(completed)}**\n"
                f"{overview}"
            )
        )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(self, interaction, button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this setup can cancel it.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content="Area reconciliation cancelled. Nothing was changed.",
            embed=None,
            view=None,
        )



class ConfirmOverviewView(discord.ui.View):
    def __init__(self, cog, admin_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.admin_id = admin_id

    @discord.ui.button(label="Rebuild Game Overview", emoji="🔄", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this preview can confirm it.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await rebuild_choose_games_message(
                self.cog.bot,
                view=GameCategoryView,
                intro_view=lambda: ChooseGamesButtons(self.cog),
            )
        except GameStructureError as exc:
            await interaction.edit_original_response(
                content=(
                    "❌ **The GamerHQ overview could not be rebuilt.**\n"
                    f"`{type(exc.original).__name__}: {exc.original}`\n\n"
                    "Only GamerHQ Bot-owned Choose Your Games messages are ever targeted by this rebuild."
                ),
                embed=None,
                view=None,
            )
            return

        await interaction.edit_original_response(
            content=(
                "✅ **GamerHQ Choose Your Games rebuilt.**\n\n"
                f"Old GamerHQ managed messages removed: **{result['deleted']}**\n"
                f"Fresh managed messages created: **{result['created']}**\n\n"
                "The overview and category buttons were regenerated from the current Game Library/Beta visibility state."
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this preview can cancel it.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Cancelled. Nothing was changed.",
            embed=None,
            view=None,
        )



class GameRoleConfirmView(discord.ui.View):
    def __init__(self, game: dict, member: discord.Member, *, remove: bool):
        super().__init__(timeout=90)
        self.game = game
        self.member_id = member.id
        self.remove = remove

        action = discord.ui.Button(
            label="Remove Game" if remove else "Add Game",
            style=discord.ButtonStyle.danger if remove else discord.ButtonStyle.success,
            custom_id=f"gamerhq:game_confirm:{game['id']}:{'remove' if remove else 'add'}",
        )
        action.callback = self.apply
        self.add_item(action)

        keep = discord.ui.Button(
            label="Keep" if remove else "Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gamerhq:game_confirm:{game['id']}:cancel",
        )
        keep.callback = self.cancel
        self.add_item(keep)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This confirmation belongs to another user.", ephemeral=True)
            return False
        return True

    async def apply(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return

        # A role update is a Discord API request and may occasionally take longer
        # than the component interaction acknowledgement window. Acknowledge the
        # click first, then perform the role change.
        await interaction.response.defer()

        fresh = db.get_game_by_id(int(self.game["id"]))
        role_id = fresh.get("role_id") if fresh else None
        role = interaction.guild.get_role(int(role_id)) if role_id else None
        if role is None:
            await interaction.edit_original_response(
                content="❌ The game role is missing. Please contact staff.",
                embed=None,
                view=None,
            )
            self.stop()
            return

        try:
            if self.remove:
                await interaction.user.remove_roles(role, reason="GamerHQ game button")
                result_text = f"✅ **{fresh['name']}** removed from your games."
            else:
                await interaction.user.add_roles(role, reason="GamerHQ game button")
                result_text = f"✅ **{fresh['name']}** added to your games."
        except discord.Forbidden:
            result_text = "❌ I could not update that role. Please ask staff to check the bot role position/permissions."
        except discord.HTTPException as exc:
            result_text = f"❌ Discord could not update the role: `{exc}`"

        await interaction.edit_original_response(
            content=result_text,
            embed=None,
            view=None,
        )
        self.stop()

    async def cancel(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(
            content=(f"Kept **{self.game['name']}**." if self.remove else "Cancelled. Nothing was changed."),
            view=None,
        )
        self.stop()


class GameNameButton(discord.ui.Button):
    def __init__(self, game: dict, row: int):
        self.game_id = int(game["id"])
        super().__init__(
            label=game["name"][:80],
            emoji=game.get("emoji") or None,
            style=discord.ButtonStyle.secondary,
            custom_id=f"gamerhq:game:{self.game_id}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        # IMPORTANT: never defer/edit the component message itself here.
        # A separate ephemeral response keeps the pinned category message intact.
        game = db.get_game_by_id(self.game_id)
        if not game or not game.get("selectable"):
            await interaction.response.send_message(
                "This game is not currently available in the Beta selection.",
                ephemeral=True,
            )
            return

        role_id = game.get("role_id")
        role = interaction.guild.get_role(int(role_id)) if role_id else None
        if role is None:
            await interaction.response.send_message(
                "❌ The game role is missing. Please contact staff.",
                ephemeral=True,
            )
            return

        remove = role in interaction.user.roles
        await interaction.response.send_message(
            f"**{'Remove' if remove else 'Add'} {game['name']}?**",
            view=GameRoleConfirmView(
                game,
                interaction.user,
                remove=remove,
            ),
            ephemeral=True,
        )



class GameCategoryView(discord.ui.View):
    """Persistent public game buttons. One view is capped at Discord's 25-button limit."""
    def __init__(self, games: list[dict]):
        super().__init__(timeout=None)
        for index, game in enumerate(games[:25]):
            self.add_item(GameNameButton(game, row=index // 5))


class CategoryGameSelect(discord.ui.Select):
    def __init__(self, session, group):
        self.session = session
        self.group = group
        games = session.games_by_group.get(group, [])
        options = []
        for game in games[:25]:
            options.append(discord.SelectOption(
                label=game["name"][:100],
                value=str(game["id"]),
                emoji=game.get("emoji") or None,
                default=game["id"] in session.pending_ids,
            ))
        super().__init__(
            placeholder=f"Choose games from {group}"[:150],
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.member_id:
            await interaction.response.send_message("This game selector belongs to another user.", ephemeral=True)
            return
        group_ids = {g["id"] for g in self.session.games_by_group.get(self.group, [])}
        self.session.pending_ids.difference_update(group_ids)
        self.session.pending_ids.update(int(value) for value in self.values)
        self.session.rebuild()
        await interaction.response.edit_message(content=self.session.status_text(), view=self.session)


class CategoryButton(discord.ui.Button):
    def __init__(self, session, group, row):
        self.session = session
        self.group = group
        super().__init__(
            label=group[:80],
            style=discord.ButtonStyle.primary if group == session.current_group else discord.ButtonStyle.secondary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.member_id:
            await interaction.response.send_message("This game selector belongs to another user.", ephemeral=True)
            return
        self.session.current_group = self.group
        self.session.rebuild()
        await interaction.response.edit_message(content=self.session.status_text(), view=self.session)


class GameSelectionSession(discord.ui.View):
    def __init__(self, member, games):
        super().__init__(timeout=300)
        self.member_id = member.id
        self.guild = member.guild
        self.games = games
        self.games_by_group = {group: [] for group in DISPLAY_GROUP_ORDER}
        for game in games:
            self.games_by_group.setdefault(game["display_group"], []).append(game)
        self.original_ids = set()
        for game in games:
            role = self.guild.get_role(int(game["role_id"])) if game.get("role_id") else None
            if role and role in member.roles:
                self.original_ids.add(game["id"])
        self.pending_ids = set(self.original_ids)
        self.current_group = next((g for g in DISPLAY_GROUP_ORDER if self.games_by_group.get(g)), None)
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        visible_groups = [g for g in DISPLAY_GROUP_ORDER if self.games_by_group.get(g)]
        for index, group in enumerate(visible_groups):
            self.add_item(CategoryButton(self, group, row=index // 5))
        if self.current_group:
            self.add_item(CategoryGameSelect(self, self.current_group))
        confirm = discord.ui.Button(label="Confirm Selection", emoji="✅", style=discord.ButtonStyle.success, row=4)
        confirm.callback = self.confirm_selection
        self.add_item(confirm)
        cancel = discord.ui.Button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary, row=4)
        cancel.callback = self.cancel_selection
        self.add_item(cancel)

    def status_text(self):
        return (
            "🎮 **Select Games**\n"
            "Choose a category, then select all games you want from it. You can switch categories freely; "
            "your choices stay saved until you confirm.\n\n"
            f"**Current category:** {self.current_group or 'None'}\n"
            f"**Selected overall:** {len(self.pending_ids)}\n"
            f"**Pending changes:** +{len(self.pending_ids-self.original_ids)} / -{len(self.original_ids-self.pending_ids)}\n\n"
            "Nothing changes until you press **Confirm Selection**."
        )

    async def confirm_selection(self, interaction: discord.Interaction):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This game selector belongs to another user.", ephemeral=True)
            return
        await interaction.response.edit_message(content="⏳ **Saving your game selection…**", view=None)
        by_id = {g["id"]: g for g in self.games}
        add_roles, remove_roles, added, removed = [], [], [], []
        for gid in self.pending_ids-self.original_ids:
            game=by_id.get(gid); role=self.guild.get_role(int(game["role_id"])) if game and game.get("role_id") else None
            if role: add_roles.append(role); added.append(game["name"])
        for gid in self.original_ids-self.pending_ids:
            game=by_id.get(gid); role=self.guild.get_role(int(game["role_id"])) if game and game.get("role_id") else None
            if role: remove_roles.append(role); removed.append(game["name"])
        try:
            if add_roles: await interaction.user.add_roles(*add_roles, reason="GamerHQ confirmed multi-game selection")
            if remove_roles: await interaction.user.remove_roles(*remove_roles, reason="GamerHQ confirmed multi-game selection")
        except discord.Forbidden:
            await interaction.edit_original_response(content="❌ I could not update your game roles. Please ask staff to check the bot role position/permissions.", view=None); self.stop(); return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ Discord could not save the selection: `{exc}`", view=None); self.stop(); return
        lines=["✅ **Your game selection has been saved.**"]
        if added: lines.append("\n**Added:** "+", ".join(sorted(added)))
        if removed: lines.append("\n**Removed:** "+", ".join(sorted(removed)))
        if not added and not removed: lines.append("\nNo roles needed to be changed.")
        await interaction.edit_original_response(content="".join(lines), view=None); self.stop()

    async def cancel_selection(self, interaction: discord.Interaction):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This game selector belongs to another user.", ephemeral=True); return
        await interaction.response.edit_message(content="✖️ Game selection cancelled. Nothing was changed.", view=None); self.stop()


class ChooseGamesButtons(discord.ui.View):
    def __init__(self, cog=None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Select Games", emoji="🎮", style=discord.ButtonStyle.primary, custom_id="gamerhq:select_games_categories")
    async def select_games(self, interaction: discord.Interaction, button: discord.ui.Button):
        games = [g for g in db.get_selectable_games() if g.get("selectable") and g.get("role_id")]
        if not games:
            await interaction.response.send_message("No games are currently available for selection.", ephemeral=True); return
        session = GameSelectionSession(interaction.user, games)
        await interaction.response.send_message(session.status_text(), view=session, ephemeral=True)

    @discord.ui.button(label="Suggest Game", emoji="💡", style=discord.ButtonStyle.secondary, custom_id="gamerhq:suggest_game")
    async def suggest_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SuggestGameModal(self.cog))


class SuggestGameModal(discord.ui.Modal, title="💡 Suggest a Game"):
    game_name = discord.ui.TextInput(
        label="Game name",
        placeholder="e.g. Delta Force, Pokémon TCG Pocket...",
        required=True,
        max_length=100,
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        name = str(self.game_name).strip()
        existing = db.get_game_by_name(name)
        if existing and existing.get("selectable"):
            await interaction.response.send_message(
                f"✅ **{existing['name']}** is already available in Choose Your Games.",
                ephemeral=True,
            )
            return
        channel = self.cog.bot.get_channel(GAME_SUGGESTIONS_CHANNEL_ID) if GAME_SUGGESTIONS_CHANNEL_ID else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ The suggestions channel is not configured yet.", ephemeral=True)
            return
        embed = discord.Embed(title="💡 Game Suggestion", description=f"**{name}**")
        embed.add_field(name="Suggested by", value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"User ID: {interaction.user.id}")
        await channel.send(embed=embed)
        await interaction.response.send_message(f"✅ **{name}** was suggested to the GamerHQ staff.", ephemeral=True)


def _find_database_path():
    candidates = []

    for attr in ("DB_PATH", "DATABASE_PATH", "db_path", "database_path"):
        value = getattr(db, attr, None)
        if value:
            candidates.append(Path(value))

    candidates.extend(
        [
            Path("gamerhq.db"),
            Path("database/gamerhq.db"),
            Path("data/gamerhq.db"),
        ]
    )

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def _update_seed_game_name(old_name: str, new_name: str, new_emoji: str):
    """
    Keep games_seed.json consistent so a future seed does not recreate
    the old catalog name after a successful rename.
    """
    seed_path = Path("data/games_seed.json")
    if not seed_path.exists():
        return None

    data = json.loads(seed_path.read_text(encoding="utf-8"))
    games = data.get("games", [])

    match = None
    for game in games:
        if game.get("name", "").lower() == old_name.lower():
            match = game
            break

    if match is None:
        return None

    # Refuse to create a duplicate seed entry.
    if any(
        game is not match
        and game.get("name", "").lower() == new_name.lower()
        for game in games
    ):
        raise RuntimeError(
            f"`{new_name}` already exists in data/games_seed.json."
        )

    old_seed_name = match["name"]
    old_seed_emoji = match.get("emoji", "🎮")
    match["name"] = new_name
    match["emoji"] = new_emoji
    seed_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return (seed_path, old_seed_name, old_seed_emoji)


def _restore_seed_name(seed_change, current_name: str):
    if not seed_change:
        return

    seed_path, old_name, old_emoji = seed_change
    data = json.loads(seed_path.read_text(encoding="utf-8"))

    for game in data.get("games", []):
        if game.get("name", "").lower() == current_name.lower():
            game["name"] = old_name
            game["emoji"] = old_emoji
            break

    seed_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _rename_game_database(game_id: int, old_name: str, new_name: str, new_emoji: str):
    """
    Prefer an existing database service method if the project provides one.
    Otherwise use the actual sqlite database file safely.
    """
    db_path = _find_database_path()
    if db_path is None:
        raise RuntimeError(
            "Could not locate the GamerHQ sqlite database file."
        )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM games WHERE lower(name) = lower(?) AND id != ?",
            (new_name, game_id),
        ).fetchone()

        if row:
            raise RuntimeError(
                f"`{new_name}` already exists in the database."
            )

        cur = conn.execute(
            "UPDATE games SET name = ?, emoji = ? WHERE id = ? AND lower(name) = lower(?)",
            (new_name, new_emoji, game_id, old_name),
        )

        if cur.rowcount != 1:
            raise RuntimeError(
                "The database game row could not be updated safely."
            )

        conn.commit()
        return ("sqlite", str(db_path))
    finally:
        conn.close()


def _rollback_game_database_name(game_id: int, current_name: str, old_name: str, old_emoji: str):
    db_path = _find_database_path()
    if db_path is None:
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE games SET name = ?, emoji = ? WHERE id = ? AND lower(name) = lower(?)",
            (old_name, old_emoji, game_id, current_name),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class ConfirmRenameView(discord.ui.View):
    def __init__(self, cog, game, new_name, new_emoji, admin_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.new_name = new_name.strip()
        self.new_emoji = new_emoji
        self.admin_id = admin_id

    @discord.ui.button(
        label="Confirm Rename",
        emoji="✅",
        style=discord.ButtonStyle.success,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this preview can confirm it.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content="⏳ **Renaming game safely…**",
            embed=None,
            view=None,
        )

        # Fresh database conflict check.
        existing = db.get_game_by_name(self.new_name)
        if existing and existing["id"] != self.game["id"]:
            await interaction.edit_original_response(
                content=(
                    f"❌ **{self.new_name}** already exists in the GamerHQ catalog.\n"
                    "Nothing was changed."
                ),
                view=None,
            )
            return

        role = (
            interaction.guild.get_role(self.game["role_id"])
            if self.game["role_id"]
            else None
        )
        category = (
            interaction.guild.get_channel(self.game["category_id"])
            if self.game["category_id"]
            else None
        )

        expected_new_role_name = f"{self.new_emoji} {self.new_name}"
        expected_new_category_name = (
            f"{self.new_emoji} {self.new_name.upper()}"
        )

        role_conflict = next(
            (
                r
                for r in interaction.guild.roles
                if r.id != getattr(role, "id", None)
                and r.name.lower() == expected_new_role_name.lower()
            ),
            None,
        )
        category_conflict = next(
            (
                c
                for c in interaction.guild.categories
                if c.id != getattr(category, "id", None)
                and c.name.lower() == expected_new_category_name.lower()
            ),
            None,
        )

        if role_conflict or category_conflict:
            await interaction.edit_original_response(
                content=(
                    "❌ A Discord role/category for the new name already exists.\n"
                    "Nothing was changed."
                ),
                view=None,
            )
            return

        old_role_name = role.name if role else None
        old_category_name = category.name if category else None
        seed_change = None
        db_changed = False

        try:
            # Keep the seed catalog consistent first.
            seed_change = _update_seed_game_name(
                self.game["name"],
                self.new_name,
                self.new_emoji,
            )

            _rename_game_database(
                self.game["id"],
                self.game["name"],
                self.new_name,
                self.new_emoji,
            )
            db_changed = True

            if role:
                await role.edit(
                    name=expected_new_role_name,
                    reason="GamerHQ confirmed game rename",
                )

            if category:
                await category.edit(
                    name=expected_new_category_name,
                    reason="GamerHQ confirmed game rename",
                )

            await refresh_choose_games_message(
                self.cog.bot,
                view=GameCategoryView,
                intro_view=lambda: ChooseGamesButtons(self.cog),
            )

        except Exception as exc:
            # Best-effort rollback of every component changed by this operation.
            if role and old_role_name:
                try:
                    await role.edit(
                        name=old_role_name,
                        reason="GamerHQ rename rollback",
                    )
                except Exception:
                    pass

            if category and old_category_name:
                try:
                    await category.edit(
                        name=old_category_name,
                        reason="GamerHQ rename rollback",
                    )
                except Exception:
                    pass

            if db_changed:
                _rollback_game_database_name(
                    self.game["id"],
                    self.new_name,
                    self.game["name"],
                    self.game["emoji"],
                )

            try:
                _restore_seed_name(seed_change, self.new_name)
            except Exception:
                pass

            await interaction.edit_original_response(
                content=(
                    f"❌ **Rename failed safely.**\n"
                    f"`{type(exc).__name__}: {exc}`\n\n"
                    "Rollback was attempted for the database, seed catalog, "
                    "role and category."
                ),
                view=None,
            )
            return

        await interaction.edit_original_response(
            content=(
                f"✅ **{self.game['name']}** was renamed to "
                f"**{self.new_emoji} {self.new_name}**.\n\n"
                "Updated:\n"
                "• Game database/catalog\n"
                "• Game emoji\n"
                "• Game role\n"
                "• Game category\n"
                "• Choose Your Games overview\n\n"
                "`chat`, `looking-for-group` and `create-voice` stayed unchanged."
            ),
            view=None,
        )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the admin who opened this preview can cancel it.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content="✖️ Rename cancelled. Nothing was changed.",
            embed=None,
            view=None,
        )


def resolve_library_game(value: str):
    """Resolve an admin-supplied game against the DB, exact name first."""
    value = (value or "").strip()
    if not value:
        return None

    exact = db.get_game_by_name(value)
    if exact is not None:
        return exact

    matches = search_games(value, selectable_only=False, limit=1)
    return matches[0] if matches else None


async def resolve_or_adopt_game_role(guild: discord.Guild, game: dict, *, create_if_missing: bool = False):
    """Resolve the linked role, adopt one exact orphan role, or create it."""
    role_id = game.get("role_id")
    linked = guild.get_role(int(role_id)) if role_id else None
    if linked is not None:
        return linked, "linked", None

    expected_name = f"{game.get('emoji') or '🎮'} {game['name']}"
    matches = [r for r in guild.roles if r.name.casefold() == expected_name.casefold()]

    if len(matches) > 1:
        return None, "conflict", (
            f"Multiple Discord roles named **{expected_name}** exist. "
            "Remove or rename the duplicates first."
        )

    if len(matches) == 1:
        role = matches[0]
        db.set_game_role(game["id"], role.id)
        return role, "adopted", None

    if not create_if_missing:
        return None, "missing", None

    try:
        role = await guild.create_role(
            name=expected_name,
            reason=f"GamerHQ create/relink game role: {game['name']}",
        )
    except discord.Forbidden:
        return None, "missing", (
            "I could not create the missing game role. Check Manage Roles and the bot role position."
        )
    except discord.HTTPException as exc:
        return None, "missing", f"Discord could not create the game role: `{exc}`"

    db.set_game_role(game["id"], role.id)
    return role, "created", None


class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_view(ChooseGamesButtons(self))
        for _, games in build_choose_games_sections():
            self.bot.add_view(GameCategoryView(games))

    game = app_commands.Group(
        name="game",
        description="Choose and suggest GamerHQ games",
    )

    game_admin = app_commands.Group(
        name="game-admin",
        description="GamerHQ game administration",
        default_permissions=discord.Permissions(administrator=True),
    )

    @game.command(name="select", description="Add or remove one of your game roles.")
    @app_commands.describe(game="Start typing a game name")
    async def select(self, interaction: discord.Interaction, game: str):
        selected = db.get_game_by_name(game)
        if selected is None or not selected.get("selectable"):
            matches = search_games(game, selectable_only=True, limit=1)
            selected = matches[0] if matches else None
        if selected is None:
            await interaction.response.send_message(
                "🔎 That game is not available yet. Use `/game suggest` if you'd like us to add it.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(selected["role_id"]) if selected["role_id"] else None

        if role is None:
            await interaction.response.send_message(
                "❌ The game role is missing. Please contact staff.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"{selected['emoji']} {selected['name']}",
            description="Add or remove this game from your GamerHQ profile.",
        )
        embed.add_field(name="Category", value=selected["display_group"])

        await interaction.response.send_message(
            embed=embed,
            view=ToggleGameView(selected, role, interaction.user),
            ephemeral=True,
        )

    @select.autocomplete("game")
    async def select_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(
                name=f"{g['emoji']} {g['name']} • {g['display_group']}",
                value=g["name"],
            )
            for g in search_games(current, selectable_only=True, limit=25)
        ]

    @game.command(name="suggest", description="Suggest a game that GamerHQ should add.")
    @app_commands.describe(name="Game you want to suggest")
    async def suggest(self, interaction: discord.Interaction, name: str):
        existing = db.get_game_by_name(name)
        if existing and existing.get("selectable"):
            await interaction.response.send_message(
                f"✅ **{existing['name']}** is already available. Use `/game select`.",
                ephemeral=True,
            )
            return

        channel = (
            self.bot.get_channel(GAME_SUGGESTIONS_CHANNEL_ID)
            if GAME_SUGGESTIONS_CHANNEL_ID
            else None
        )

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ The suggestions channel has not been configured yet.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="💡 Game Suggestion")
        embed.add_field(name="Game", value=name, inline=False)
        embed.add_field(name="Suggested by", value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        await channel.send(embed=embed)
        await interaction.response.send_message(
            f"✅ **{name}** was suggested to the GamerHQ staff.",
            ephemeral=True,
        )

    @game_admin.command(
        name="database",
        description="Admin: show the runtime GamerHQ database path and Game Library counts.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def database_info(self, interaction: discord.Interaction):
        from config import DB_PATH

        all_games = db.get_all_games(active_only=False)
        selectable = [g for g in all_games if g.get("selectable")]
        areas = [g for g in all_games if g.get("area_enabled")]
        custom = [
            g for g in all_games
            if not json.loads(g.get("aliases_json") or "[]") and not json.loads(g.get("tags_json") or "[]")
        ]

        await interaction.response.send_message(
            "\n".join(
                [
                    "🗄️ **GamerHQ Runtime Database**",
                    f"Path: `{DB_PATH.resolve()}`",
                    f"Exists: **{'Yes' if DB_PATH.exists() else 'No'}**",
                    f"Game Library entries: **{len(all_games)}**",
                    f"Selectable games: **{len(selectable)}**",
                    f"DB-enabled areas: **{len(areas)}**",
                    f"Likely custom-created games: **{len(custom)}**",
                    "",
                    "**This is the database the bot is reading right now.**",
                ]
            ),
            ephemeral=True,
        )

    @game_admin.command(
        name="recover-existing",
        description="Admin: reconnect an orphaned Discord game role/area to the Game Library.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        name="Exact game name to restore into the Game Library",
        group="GamerHQ game category",
        emoji="Emoji used by the existing game role/category",
        visible="Show the recovered game in Choose Your Games",
        with_lfg="Whether the existing area is multiplayer/co-op with LFG",
    )
    async def recover_existing(
        self,
        interaction: discord.Interaction,
        name: str,
        group: str,
        emoji: str = "🎮",
        visible: bool = True,
        with_lfg: bool = True,
    ):
        name = (name or "").strip()
        emoji = (emoji or "🎮").strip() or "🎮"

        if not name:
            await interaction.response.send_message("❌ Game name cannot be empty.", ephemeral=True)
            return
        if group not in DISPLAY_GROUP_ORDER:
            await interaction.response.send_message("❌ Invalid GamerHQ game category.", ephemeral=True)
            return
        if db.get_game_by_name(name):
            await interaction.response.send_message(
                f"ℹ️ **{name}** already exists in the Game Library. Use `/game-admin status` instead.",
                ephemeral=True,
            )
            return

        expected_role = f"{emoji} {name}"
        expected_category = f"{emoji} {name.upper()}"

        role_matches = [r for r in interaction.guild.roles if r.name.casefold() == expected_role.casefold()]
        category_matches = [c for c in interaction.guild.categories if c.name.casefold() == expected_category.casefold()]

        if len(role_matches) != 1:
            await interaction.response.send_message(
                f"❌ Recovery requires exactly one Discord role named **{expected_role}**. Found: **{len(role_matches)}**.",
                ephemeral=True,
            )
            return
        if len(category_matches) > 1:
            await interaction.response.send_message(
                f"❌ Multiple Discord categories named **{expected_category}** exist. Resolve duplicates first.",
                ephemeral=True,
            )
            return

        role = role_matches[0]
        category = category_matches[0] if category_matches else None
        chat = lfg = create_voice = None

        if category is not None:
            for channel in category.channels:
                if channel.name == "💬・chat" and isinstance(channel, discord.TextChannel):
                    chat = channel
                elif channel.name == "🎯・looking-for-group" and isinstance(channel, discord.TextChannel):
                    lfg = channel
                elif channel.name == "➕・create-voice" and isinstance(channel, discord.VoiceChannel):
                    create_voice = channel

            missing = []
            if chat is None: missing.append("chat")
            if create_voice is None: missing.append("create-voice")
            if with_lfg and lfg is None: missing.append("looking-for-group")
            if missing:
                await interaction.response.send_message(
                    "❌ Existing area is incomplete. Missing: "
                    + ", ".join(f"`{item}`" for item in missing)
                    + ". Nothing was changed.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True, thinking=True)

        game_row = None
        try:
            game_row = db.upsert_custom_game(name, emoji, group)
            db.set_game_role(game_row["id"], role.id)
            db.set_game_selectable(game_row["id"], visible)

            if category is not None:
                db.set_game_structure(
                    game_row["id"],
                    role_id=role.id,
                    category_id=category.id,
                    chat_id=chat.id,
                    memes_id=None,
                    lfg_id=lfg.id if lfg else None,
                    create_voice_id=create_voice.id,
                    has_lfg=with_lfg,
                )
            else:
                db.set_game_area_options(game_row["id"], enabled=False, has_lfg=with_lfg)

            await refresh_choose_games_message(
                self.bot,
                view=GameCategoryView,
                intro_view=lambda: ChooseGamesButtons(self),
            )
        except Exception as exc:
            if game_row is not None:
                try:
                    db.delete_game_permanently(game_row["id"])
                except Exception:
                    pass
            await interaction.edit_original_response(
                content=(
                    f"❌ Recovery failed safely: `{type(exc).__name__}: {exc}`\n"
                    "Existing Discord resources were not changed."
                )
            )
            return

        await interaction.edit_original_response(
            content=(
                f"✅ **{name}** recovered into the Game Library.\n"
                f"🔗 Existing role linked: {role.mention}\n"
                f"📁 Existing area: **{'re-linked' if category else 'none found'}**\n"
                f"👁️ Visible in Choose Your Games: **{'Yes' if visible else 'No'}**"
            )
        )

    @recover_existing.autocomplete("group")
    async def recover_existing_group_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=group, value=group)
            for group in DISPLAY_GROUP_ORDER
            if current.casefold() in group.casefold()
        ][:25]


    @game_admin.command(
        name="status",
        description="Admin: inspect one game's DB state and linked Discord resources.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(game="Game Library entry to inspect")
    async def game_status(self, interaction: discord.Interaction, game: str):
        matches = search_games(game, selectable_only=False, limit=1)
        if not matches:
            raw_name = (game or "").strip()
            role_matches = [
                role for role in interaction.guild.roles
                if raw_name.casefold() in role.name.casefold()
            ]
            category_matches = [
                category for category in interaction.guild.categories
                if raw_name.casefold() in category.name.casefold()
            ]
            role_lines = "\n".join(f"• {r.name} (`{r.id}`)" for r in role_matches[:5]) or "• None"
            category_lines = "\n".join(f"• {c.name} (`{c.id}`)" for c in category_matches[:5]) or "• None"

            await interaction.response.send_message(
                (
                    f"❌ **{raw_name}** is not in the current Game Library.\n\n"
                    "Discord resources with a similar name still exist:\n"
                    f"**Roles**\n{role_lines}\n"
                    f"**Categories**\n{category_lines}\n\n"
                    "This is an orphaned Discord state. Use `/game-admin recover-existing` "
                    "to reconnect it to the database."
                ),
                ephemeral=True,
            )
            return
        selected = matches[0]
        guild = interaction.guild
        role = guild.get_role(int(selected["role_id"])) if selected.get("role_id") else None
        category = guild.get_channel(int(selected["category_id"])) if selected.get("category_id") else None
        chat = guild.get_channel(int(selected["chat_channel_id"])) if selected.get("chat_channel_id") else None
        lfg_id = selected.get("lfg_channel_id") or selected.get("clips_channel_id")
        lfg = guild.get_channel(int(lfg_id)) if lfg_id else None
        voice = guild.get_channel(int(selected["create_voice_channel_id"])) if selected.get("create_voice_channel_id") else None

        lines = [
            f"🎮 **{selected['emoji']} {selected['name']}**",
            f"Category: **{selected['display_group']}**",
            "",
            "**DB desired state**",
            f"Selectable: **{'Yes' if selected.get('selectable') else 'No'}**",
            f"Dedicated area enabled: **{'Yes' if selected.get('area_enabled') else 'No'}**",
            f"LFG in area: **{'Yes' if selected.get('area_has_lfg') else 'No'}**",
            "",
            "**Discord links**",
            f"Role: **{'OK' if role else 'Missing/None'}**",
            f"Category: **{'OK' if category else 'Missing/None'}**",
            f"chat: **{'OK' if chat else 'Missing/None'}**",
            f"looking-for-group: **{'OK' if lfg else ('Not required' if not selected.get('area_has_lfg') else 'Missing/None')}**",
            f"create-voice: **{'OK' if voice else 'Missing/None'}**",
            "",
            "_Read-only check: nothing was created, deleted, hidden, or repaired._",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @game_status.autocomplete("game")
    async def game_status_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{g['emoji']} {g['name']} • {g['display_group']}", value=g["name"])
            for g in search_games(current, selectable_only=False, limit=25)
        ]

    @game_admin.command(name="set-visible", description="Admin: show/hide a library game in Choose Your Games without changing its area.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(game="Library game name", visible="Show or hide this game in the Beta selector")
    async def set_visible(self, interaction: discord.Interaction, game: str, visible: bool):
        # Refreshing all managed/pinned category messages can take longer than
        # Discord's initial interaction window, so acknowledge immediately.
        await interaction.response.defer(ephemeral=True, thinking=True)

        selected = resolve_library_game(game)
        if selected is None:
            await interaction.edit_original_response(content="❌ Game not found in the library.")
            return
        role_status = "not-needed"
        if visible:
            role, role_status, role_error = await resolve_or_adopt_game_role(
                interaction.guild,
                selected,
                create_if_missing=True,
            )
            if role is None:
                await interaction.edit_original_response(
                    content=f"❌ {role_error or 'The game role could not be resolved.'}"
                )
                return

        db.set_game_selectable(selected["id"], visible)
        try:
            await refresh_choose_games_message(self.bot, view=GameCategoryView, intro_view=lambda: ChooseGamesButtons(self))
        except GameStructureError as exc:
            await interaction.edit_original_response(
                content=f"⚠️ The game state was saved, but Choose Your Games could not refresh: `{exc.original}`"
            )
            return
        role_note = ""
        if visible and role_status == "adopted":
            role_note = "\n🔗 Existing matching Discord role re-linked to the Game Library entry."
        elif visible and role_status == "created":
            role_note = "\n➕ Missing Discord game role recreated and linked."

        await interaction.edit_original_response(
            content=(
                f"✅ **{selected['name']}** is now {'visible' if visible else 'hidden'} in Choose Your Games. "
                "Its visibility is stored in the database and preserved across overview rebuilds/restarts."
                f"{role_note}"
            )
        )

    @set_visible.autocomplete("game")
    async def set_visible_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{g['emoji']} {g['name']} • {g['display_group']}", value=g['name'])
            for g in search_games(current, selectable_only=False, limit=25)
        ]

    @game_admin.command(
        name="create",
        description="Admin: create a new Game Library entry and its game role.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        name="New game name",
        group="Game category used in Choose Your Games",
        emoji="Emoji used for the game role and buttons",
        visible="Show the new game in Choose Your Games immediately",
    )
    async def create_game(
        self,
        interaction: discord.Interaction,
        name: str,
        group: str,
        emoji: str = "🎮",
        visible: bool = True,
    ):
        name = name.strip()
        emoji = (emoji or "🎮").strip() or "🎮"

        if not name:
            await interaction.response.send_message("❌ Game name cannot be empty.", ephemeral=True)
            return
        if group not in DISPLAY_GROUP_ORDER:
            await interaction.response.send_message("❌ Invalid display group.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if db.get_game_by_name(name):
            await interaction.edit_original_response(
                content=f"⚠️ **{name}** already exists in the Game Library. Nothing was changed.",
            )
            return

        role_name = f"{emoji} {name}"
        matching_roles = [
            role for role in interaction.guild.roles
            if role.name.casefold() == role_name.casefold()
        ]
        if len(matching_roles) > 1:
            await interaction.edit_original_response(
                content=f"❌ Multiple Discord roles named **{role_name}** exist. "
                "Resolve the duplicate-role conflict first.",
            )
            return

        game = None
        role = matching_roles[0] if matching_roles else None
        reused_existing_role = role is not None
        try:
            game = db.upsert_custom_game(name, emoji, group)
            if role is None:
                role = await interaction.guild.create_role(
                    name=role_name,
                    reason=f"GamerHQ create Game Library entry: {name}",
                )
            db.set_game_role(game["id"], role.id)
            db.set_game_selectable(game["id"], visible)
            game = db.get_game_by_id(game["id"])
        except Exception as exc:
            if role is not None and not reused_existing_role:
                try:
                    await role.delete(reason="Rollback failed GamerHQ game create")
                except Exception:
                    pass
            if game is not None:
                try:
                    db.delete_game_permanently(game["id"])
                except Exception:
                    pass
            await interaction.edit_original_response(
                content=f"❌ Game creation failed and was rolled back.\n`{type(exc).__name__}: {exc}`"
            )
            return

        if visible:
            try:
                await refresh_choose_games_message(
                    self.bot,
                    view=GameCategoryView,
                    intro_view=lambda: ChooseGamesButtons(self),
                )
                overview = "\n✅ `choose-your-games` updated."
            except GameStructureError as exc:
                overview = (
                    "\n⚠️ The game was created, but `choose-your-games` could not refresh: "
                    f"`{type(exc.original).__name__}: {exc.original}`"
                )
        else:
            overview = "\nℹ️ The game is hidden from `choose-your-games` until you enable it with `/game-admin set-visible`."

        role_result = (
            "its existing Discord game role was linked"
            if reused_existing_role
            else "its game role was created"
        )
        await interaction.edit_original_response(
            content=(
                f"✅ **{game['name']}** was added to the Game Library and {role_result}."
                f"{overview}\n"
                "No dedicated Discord area was created. Use `/game-admin add-area` when needed."
            )
        )

    @create_game.autocomplete("group")
    async def create_group_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=g, value=g)
            for g in DISPLAY_GROUP_ORDER
            if current.lower() in g.lower()
        ][:25]

    @game_admin.command(
        name="add-area",
        description="Admin: create or restore a dedicated Discord area for a library game.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        game="Existing Game Library entry",
        with_lfg="True for multiplayer/co-op; False for a solo area",
    )
    async def add_area(
        self,
        interaction: discord.Interaction,
        game: str,
        with_lfg: bool = True,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        selected = resolve_library_game(game)
        if selected is None:
            await interaction.edit_original_response(
                content="❌ Game not found in the Game Library. Use `/game-admin create` first.",
            )
            return

        if selected.get("area_enabled"):
            await interaction.edit_original_response(
                content=f"⚠️ **{selected['name']}** already has a dedicated game area. Nothing was changed.",
            )
            return

        # Snapshot library identity/state. Area creation is not allowed to change
        # any of these fields.
        library_snapshot = {
            "id": selected["id"],
            "name": selected["name"],
            "selectable": selected.get("selectable"),
            "role_id": selected.get("role_id"),
            "display_group": selected.get("display_group"),
        }

        db.set_game_area_options(selected["id"], has_lfg=with_lfg)
        selected = db.get_game_by_id(selected["id"])
        plan = inspect_game_structure(interaction.guild, selected)

        if plan["conflicts"]:
            await interaction.edit_original_response(
                content=(
                    f"❌ **Cannot safely create the {selected['name']} area** because conflicts exist.\n\n"
                    + format_plan(plan)
                    + "\n\nResolve the duplicates first. Nothing was changed on Discord."
                ),
            )
            return

        existing_anything = (
            plan["role"] is not None
            or plan["category"] is not None
            or any(item["existing"] is not None for item in plan["channels"].values())
        )
        warning = (
            "\n\n⚠️ **Existing Discord resources were found.** "
            "If you confirm, they will be explicitly reused; only missing resources will be created."
            if existing_anything
            else "\n\nNothing has been created yet."
        )

        area_kind = "Multiplayer / Co-op" if with_lfg else "Solo"
        embed = discord.Embed(
            title=f"🛡️ Confirm Area Setup: {selected['name']}",
            description=(
                f"**Area type:** {area_kind}\n\n"
                "Review exactly what the bot will do:\n\n"
                + format_plan(plan)
                + warning
                + "\n\nThe game's Choose Your Games visibility will not be changed."
            ),
        )
        await interaction.edit_original_response(
            embed=embed,
            view=ConfirmGameAddView(self, selected, plan, interaction.user.id, library_snapshot=library_snapshot),
        )

    @add_area.autocomplete("game")
    async def add_area_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(
                name=f"{g['emoji']} {g['name']} • {g['display_group']}",
                value=g["name"],
            )
            for g in search_games(current, selectable_only=False, limit=100)
            if not g.get("area_enabled")
        ][:25]

    @game_admin.command(name="remove-area", description="Admin: remove only a game's dedicated Discord area.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(game="Game to remove")
    async def remove_area(self, interaction: discord.Interaction, game: str):
        selected = resolve_library_game(game)
        if selected is None:
            await interaction.response.send_message(
                "Game not found. Nothing was changed.",
                ephemeral=True,
            )
            return

        if not selected.get("area_enabled") and not selected.get("category_id"):
            await interaction.response.send_message(
                f"ℹ️ **{selected['name']}** has no dedicated area to remove.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"⚠️ Remove dedicated area: {selected['name']}?",
            description=(
                "This will remove the **dedicated Discord area** currently linked to this game:\n"
                "• Game category\n"
                "• chat\n"
                "• looking-for-group (if enabled)\n"
                "• Create Voice\n\n"
                "**The game role, Game Library entry and Choose Your Games visibility are kept.**\n"
                "Use `/game-admin set-visible` separately if you also want to hide the game.\n\n"
                "**Nothing will be deleted until you press Remove Area.**"
            ),
        )

        await interaction.response.send_message(
            embed=embed,
            view=RemoveConfirmView(self, selected, interaction.user.id),
            ephemeral=True,
        )

    @remove_area.autocomplete("game")
    async def remove_area_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{g['emoji']} {g['name']}", value=g["name"])
            for g in search_games(current, selectable_only=False, limit=100)
            if g.get("area_enabled")
        ][:25]

    @game_admin.command(
        name="delete",
        description="Admin: permanently delete a game, its role and dedicated area.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(game="Game to permanently delete from GamerHQ")
    async def delete_game(self, interaction: discord.Interaction, game: str):
        selected = resolve_library_game(game)
        if selected is None:
            await interaction.response.send_message(
                "❌ Game not found in the Game Library. Nothing was changed.",
                ephemeral=True,
            )
            return

        deps = db.get_game_delete_dependencies(selected["id"])
        dependency_text = ""
        if any(deps.values()):
            dependency_text = (
                "\n\n❌ **Deletion is currently blocked by references:**\n"
                f"• LFG events: **{deps['lfg_events']}**\n"
                f"• Streamer applications: **{deps['streamer_applications']}**\n"
                f"• Streamer profiles: **{deps['streamer_profiles']}**\n"
                "Reassign or clean these records first."
            )

        area_text = "Yes" if selected.get("area_enabled") or selected.get("category_id") else "No"
        role = interaction.guild.get_role(int(selected["role_id"])) if selected.get("role_id") else None
        role_text = role.mention if role else "No linked role found"

        embed = discord.Embed(
            title=f"🚨 Permanently delete {selected['name']}?",
            description=(
                "**This is the destructive Game Library action.**\n\n"
                "• Remove from `choose-your-games`: **Yes**\n"
                "• Delete Game Library entry: **Yes**\n"
                f"• Delete game role: {role_text}\n"
                f"• Delete dedicated area: **{area_text}**\n"
                "• Prevent built-in seed data from restoring it after restart: **Yes**\n\n"
                "Users will lose this game role. This is different from `/game-admin remove-area`."
                + dependency_text
            ),
        )

        view = DeleteGameConfirmView(self, selected, interaction.user.id)
        if any(deps.values()):
            for item in view.children:
                if isinstance(item, discord.ui.Button) and item.style == discord.ButtonStyle.danger:
                    item.disabled = True

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @delete_game.autocomplete("game")
    async def delete_game_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(
                name=f"{g['emoji']} {g['name']} • {g['display_group']}",
                value=g["name"],
            )
            for g in search_games(current, selectable_only=False, limit=25)
        ]


    @game_admin.command(
        name="rename",
        description="Admin: safely rename an existing GamerHQ game.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        game="Existing game to rename",
        new_name="New full game name",
        emoji="Optional new emoji; leave empty to keep the current emoji",
    )
    async def rename(
        self,
        interaction: discord.Interaction,
        game: str,
        new_name: str,
        emoji: str | None = None,
    ):
        selected = resolve_library_game(game)
        if selected is None:
            await interaction.response.send_message(
                "❌ Game not found. Nothing was changed.",
                ephemeral=True,
            )
            return

        new_name = new_name.strip()
        new_emoji = (emoji or "").strip() or selected["emoji"]

        if not new_name:
            await interaction.response.send_message(
                "❌ New name cannot be empty. Nothing was changed.",
                ephemeral=True,
            )
            return

        if (new_name.lower() == selected["name"].lower()
                and new_emoji == selected["emoji"]):
            await interaction.response.send_message(
                "❌ The name and emoji are unchanged.",
                ephemeral=True,
            )
            return

        existing = db.get_game_by_name(new_name)
        if existing and existing["id"] != selected["id"]:
            await interaction.response.send_message(
                f"❌ **{new_name}** already exists. Nothing was changed.",
                ephemeral=True,
            )
            return

        role = (
            interaction.guild.get_role(selected["role_id"])
            if selected["role_id"]
            else None
        )
        category = (
            interaction.guild.get_channel(selected["category_id"])
            if selected["category_id"]
            else None
        )

        new_role_name = f"{new_emoji} {new_name}"
        new_category_name = f"{new_emoji} {new_name.upper()}"

        role_conflict = next(
            (
                r
                for r in interaction.guild.roles
                if r.id != getattr(role, "id", None)
                and r.name.lower() == new_role_name.lower()
            ),
            None,
        )
        category_conflict = next(
            (
                c
                for c in interaction.guild.categories
                if c.id != getattr(category, "id", None)
                and c.name.lower() == new_category_name.lower()
            ),
            None,
        )

        if role_conflict or category_conflict:
            await interaction.response.send_message(
                "❌ A Discord role/category using the new name already exists.\n"
                "Nothing was changed.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✏️ Confirm Game Rename",
            description=(
                f"**Current:** {selected['emoji']} {selected['name']}\n"
                f"**New:** {new_emoji} {new_name}\n\n"
                "After confirmation the bot will rename:\n"
                "• GamerHQ database/catalog entry\n"
                "• linked game role\n"
                "• linked game category\n"
                "• Choose Your Games overview\n\n"
                "The `chat`, `looking-for-group` and `create-voice` "
                "channel names stay unchanged.\n\n"
                "**Nothing has been changed yet.**"
            ),
        )

        await interaction.response.send_message(
            embed=embed,
            view=ConfirmRenameView(
                self,
                selected,
                new_name,
                new_emoji,
                interaction.user.id,
            ),
            ephemeral=True,
        )

    @rename.autocomplete("game")
    async def rename_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ):
        return [
            app_commands.Choice(
                name=f"{game['emoji']} {game['name']}",
                value=game["name"],
            )
            for game in search_games(
                current,
                selectable_only=False,
                limit=25,
            )
        ]


    @game_admin.command(
        name="setup",
        description="Admin: inspect/reconcile Discord areas enabled in the Game Library.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_games(self, interaction: discord.Interaction):
        results, conflicts, existing = _setup_scan(interaction.guild)

        if conflicts:
            preview = "\n".join("• " + x for x in conflicts[:15])
            more = ""
            if len(conflicts) > 15:
                more = f"\n…and {len(conflicts) - 15} more conflict(s)."

            await interaction.response.send_message(
                content=(
                    "❌ **Area reconciliation cannot start yet.**\n"
                    "The read-only scan found conflicts:\n\n"
                    f"{preview}{more}\n\n"
                    "Nothing was created."
                ),
                ephemeral=True,
            )
            return

        if not results:
            await interaction.response.send_message(
                "✅ All catalog games are already active.",
                ephemeral=True,
            )
            return

        r, c, t, v = _setup_counts(results)
        projected_roles = len(interaction.guild.roles) + r
        projected_channels = len(interaction.guild.channels) + c + t + v

        if projected_roles > 250 or projected_channels > 500:
            await interaction.response.send_message(
                content=(
                    "❌ **Setup cannot safely fit.**\n"
                    f"Roles: **{projected_roles}/250**\n"
                    f"Channels/categories: **{projected_channels}/500**\n\n"
                    "Nothing was created."
                ),
                ephemeral=True,
            )
            return

        grouped = {group: 0 for group in DISPLAY_GROUP_ORDER}
        for game, _ in results:
            grouped[game["display_group"]] = (
                grouped.get(game["display_group"], 0) + 1
            )

        groups = "\n".join(
            f"**{group}:** {count}"
            for group, count in grouped.items()
            if count
        )

        embed = discord.Embed(
            title="🛡️ GamerHQ Initial Game Setup",
            description=(
                f"**{len(results)} inactive catalog games** are ready.\n\n"
                f"{groups}\n\n"
                "**Nothing has been created yet.**"
            ),
        )

        embed.add_field(
            name="Planned changes",
            value=(
                f"➕ Roles: **{r}**\n"
                f"➕ Categories: **{c}**\n"
                f"➕ Text channels: **{t}**\n"
                f"➕ Create-Voice channels: **{v}**\n"
                f"♻️ Games with matching existing resources: **{existing}**"
            ),
            inline=False,
        )

        embed.add_field(
            name="Projected totals",
            value=(
                f"Roles: **{projected_roles}/250**\n"
                f"Channels/categories: **{projected_channels}/500**"
            ),
            inline=False,
        )

        embed.add_field(
            name="Safety",
            value=(
                "• Read-only scan first\n"
                "• Explicit confirmation required\n"
                "• Fresh scan before execution\n"
                "• Duplicate conflicts abort\n"
                "• Failed game's partial resources are rolled back"
            ),
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            view=ConfirmInitialSetupView(self, interaction.user.id),
            ephemeral=True,
        )


    @game_admin.command(
        name="overview",
        description="Admin: create or refresh the GamerHQ Choose Your Games overview.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def overview(self, interaction: discord.Interaction):
        active_games = db.get_selectable_games()

        if not active_games:
            await interaction.response.send_message(
                "❌ No Beta games are selectable yet. Nothing was changed.",
                ephemeral=True,
            )
            return

        channel = self.bot.get_channel(CHOOSE_GAMES_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ The configured choose-your-games channel could not be found. Nothing was changed.",
                ephemeral=True,
            )
            return

        grouped = {group: [] for group in DISPLAY_GROUP_ORDER}
        for game in active_games:
            grouped.setdefault(game["display_group"], []).append(game["name"])

        lines = []
        for group in DISPLAY_GROUP_ORDER:
            names = grouped.get(group, [])
            if names:
                lines.append(f"**{group}:** {len(names)} games")

        embed = discord.Embed(
            title="🔄 Rebuild GamerHQ Game Overview?",
            description=(
                f"Channel: {channel.mention}\n"
                f"Visible Beta games: **{len(active_games)}**\n\n"
                + "\n".join(lines)
                + "\n\n**Nothing changes until you confirm.**"
            ),
        )
        embed.add_field(
            name="What will be rebuilt?",
            value=(
                "• Existing **GamerHQ Bot-owned** Choose Your Games overview/category messages are removed.\n"
                "• The intro and all category messages are generated fresh from the current database.\n• Only games with `selectable = true` are rendered; hidden games stay hidden.\n"
                "• Fresh messages are pinned and their new message IDs are stored.\n"
                "• `Select Games`, `/game select`, `Suggest Game` and `/game suggest` remain available.\n"
                "• User messages and messages from other bots are never deleted."
            ),
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            view=ConfirmOverviewView(self, interaction.user.id),
            ephemeral=True,
        )



async def setup(bot):
    await bot.add_cog(Games(bot))
