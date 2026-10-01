"""Offline regressions: temporary SQLite only, no bot login or Discord requests."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_runtime = tempfile.TemporaryDirectory()
os.environ["GAMERHQ_DB_PATH"] = str(Path(_runtime.name) / "test.db")

import discord
from cogs import games as games_cog, lfg
from database import db
from services import game_service


GAME = {"id": 1, "name": "Test Game", "emoji": "🎮", "role_id": 10,
        "display_group": games_cog.DISPLAY_GROUP_ORDER[0], "selectable": 1,
        "area_enabled": 0, "area_has_lfg": 1}


def interaction():
    member = MagicMock(spec=discord.Member)
    member.id = 123
    member.guild = MagicMock()
    return SimpleNamespace(user=member, guild=member.guild, channel_id=456,
                           response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(),
                                                    edit_message=AsyncMock(), send_modal=AsyncMock()),
                           edit_original_response=AsyncMock())


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def open_entry(self, entry):
        request = interaction()
        if entry == "button":
            hub = lfg.LFGHubView()
            self.assertTrue(hub.is_persistent())
            self.assertEqual(hub.create_event.custom_id, "gamerhq:lfg:create")
            await hub.create_event.callback(request)
        else:
            cog = object.__new__(lfg.LFG)
            await lfg.LFG.lfg_create.callback(cog, request)
        return request

    async def test_both_entries_open_valid_serializable_builder_without_game(self):
        for entry in ("button", "slash"):
            with self.subTest(entry=entry):
                request = await self.open_entry(entry)
                request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
                result = request.edit_original_response.call_args.kwargs
                self.assertIn("Create GamerHQ Event", result["content"])
                self.assertNotIn("Game:", result["content"])
                self.assertNotIn("Duration", result["content"])
                builder = result["view"]
                self.assertIsInstance(builder, lfg.EventBuilderView)
                rows = builder.to_components()
                # Date, time and participants each use one row; controls use the last row.
                self.assertEqual(len(rows), 4)
                self.assertEqual([len(row["components"]) for row in rows], [1, 1, 1, 5])
                self.assertEqual(
                    [component["label"] for component in rows[-1]["components"]],
                    ["Title", "PM", "15m", "Public", "Preview"],
                )
                for row in rows:
                    self.assertLessEqual(len(row["components"]), 5)
                    for component in row["components"]:
                        self.assertLessEqual(len(component.get("options", [])), 25)
                builder.stop()

    async def test_event_creation_does_not_require_games_or_channel_role(self):
        for entry in ("button", "slash"):
            request = await self.open_entry(entry)
            self.assertIsInstance(request.edit_original_response.call_args.kwargs["view"], lfg.EventBuilderView)
            self.assertNotIn("Select a game", request.edit_original_response.call_args.kwargs["content"])

    async def test_builder_modal_preview_edit_and_cancel(self):
        request = interaction()
        builder = lfg.EventBuilderView(host=request.user)
        await builder.preview.callback(request)
        self.assertIn("Choose a date and time", request.response.send_message.call_args.args[0])
        await builder.edit_title.callback(request)
        self.assertIsInstance(request.response.send_modal.call_args.args[0], lfg.EventTitleModal)
        builder.date = (datetime.now(lfg.SERVER_TZ) + timedelta(days=1)).date().isoformat()
        builder.time_12h = "12:30"
        builder._sync_24h_time()
        self.assertEqual(builder.hour, 12)
        await builder.ampm_button.callback(request)
        self.assertEqual(builder.hour, 0)
        await builder.visibility_button.callback(request)
        self.assertEqual(builder.visibility, "private")
        await builder.preview.callback(request)
        draft = request.response.edit_message.call_args.kwargs["view"]
        self.assertIsInstance(draft, lfg.EventDraftView)
        draft.to_components()
        await draft.edit_event.callback(request)
        self.assertIs(request.response.edit_message.call_args.kwargs["view"], builder)
        draft = lfg.EventDraftView(builder)
        await draft.cancel.callback(request)
        self.assertIsNone(request.response.edit_message.call_args.kwargs["view"])
        builder.stop()


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_rename_keeps_emoji_when_omitted_and_accepts_new_emoji(self):
        selected = {**GAME, "category_id": None}
        for supplied, expected in ((None, "🎮"), ("🚀", "🚀")):
            with self.subTest(emoji=supplied):
                request = interaction()
                request.guild.roles = []
                request.guild.categories = []
                request.guild.get_role.return_value = None
                request.guild.get_channel.return_value = None
                with patch.object(games_cog, "resolve_library_game", return_value=selected), patch.object(
                    db, "get_game_by_name", return_value=None
                ):
                    await games_cog.Games.rename.callback(
                        SimpleNamespace(), request, "Test Game", "Renamed Game", supplied
                    )
                preview = request.response.send_message.call_args.kwargs["view"]
                self.assertIsInstance(preview, games_cog.ConfirmRenameView)
                self.assertEqual(preview.new_emoji, expected)
                self.assertIn(f"**New:** {expected} Renamed Game", request.response.send_message.call_args.kwargs["embed"].description)
                preview.stop()

    async def test_confirm_rename_updates_emoji_across_resources(self):
        selected = {**GAME, "category_id": 20}
        request = interaction()
        role = SimpleNamespace(id=10, name="🎮 Test Game", edit=AsyncMock())
        category = SimpleNamespace(id=20, name="🎮 TEST GAME", edit=AsyncMock())
        request.guild.get_role.return_value = role
        request.guild.get_channel.return_value = category
        request.guild.roles = [role]
        request.guild.categories = [category]
        view = games_cog.ConfirmRenameView(SimpleNamespace(bot=None), selected, "Renamed", "🚀", request.user.id)
        updated = {**selected, "name": "Renamed", "emoji": "🚀"}
        with patch.object(db, "get_game_by_name", return_value=None), patch.object(
            games_cog, "_update_seed_game_name", return_value=None
        ) as seed, patch.object(games_cog, "_rename_game_database") as rename_db, patch.object(
            games_cog, "refresh_choose_games_message", new_callable=AsyncMock
        ) as refresh:
            await view.confirm.callback(request)
        seed.assert_called_once_with("Test Game", "Renamed", "🚀")
        rename_db.assert_called_once_with(1, "Test Game", "Renamed", "🚀")
        role.edit.assert_awaited_once_with(name="🚀 Renamed", reason="GamerHQ confirmed game rename")
        category.edit.assert_awaited_once_with(name="🚀 RENAMED", reason="GamerHQ confirmed game rename")
        refresh.assert_awaited_once()
        view.stop()

    async def test_failed_rename_rolls_back_old_emoji(self):
        selected = {**GAME, "category_id": None}
        request = interaction()
        role = SimpleNamespace(id=10, name="🎮 Test Game", edit=AsyncMock(side_effect=RuntimeError("failure")))
        request.guild.get_role.return_value = role
        request.guild.get_channel.return_value = None
        request.guild.roles = [role]
        request.guild.categories = []
        view = games_cog.ConfirmRenameView(SimpleNamespace(bot=None), selected, "Renamed", "🚀", request.user.id)
        with patch.object(db, "get_game_by_name", return_value=None), patch.object(
            games_cog, "_update_seed_game_name", return_value=(Path("seed"), "Test Game", "🎮")
        ), patch.object(games_cog, "_rename_game_database"), patch.object(
            games_cog, "_rollback_game_database_name"
        ) as rollback_db, patch.object(games_cog, "_restore_seed_name") as restore_seed:
            await view.confirm.callback(request)
        rollback_db.assert_called_once_with(1, "Renamed", "Test Game", "🎮")
        restore_seed.assert_called_once_with((Path("seed"), "Test Game", "🎮"), "Renamed")
        self.assertIn("Rename failed safely", request.edit_original_response.call_args.kwargs["content"])
        view.stop()

    async def test_create_acknowledges_then_reviews_visibility_without_creating_area(self):
        request = interaction()
        request.guild.roles = []
        request.guild.create_role = AsyncMock(return_value=SimpleNamespace(id=10))

        def lookup(name):
            request.response.defer.assert_awaited_once()
            return None

        with patch.object(db, "get_game_by_name", side_effect=lookup), patch.object(
            db, "upsert_custom_game", return_value=GAME
        ), patch.object(db, "set_game_role"), patch.object(db, "set_game_selectable") as selectable, patch.object(
            db, "get_game_by_id", return_value=GAME
        ), patch.object(games_cog, "refresh_choose_games_message", new_callable=AsyncMock) as refresh, patch(
            "cogs.game_channels.open_visibility", new_callable=AsyncMock
        ) as review:
            await games_cog.Games.create_game.callback(SimpleNamespace(bot=None), request,
                                                      "Test Game", GAME["display_group"])
            selectable.assert_called_once_with(GAME["id"], False)
            review.assert_awaited_once_with(request, GAME["id"], True)
            refresh.assert_not_awaited()
        request.guild.create_role.assert_awaited_once()
        request.guild.create_category.assert_not_called()
        request.guild.create_text_channel.assert_not_called()

    async def test_add_area_acknowledges_before_lookup(self):
        request = interaction()

        def lookup(name):
            request.response.defer.assert_awaited_once()
            return None

        with patch.object(games_cog, "resolve_library_game", side_effect=lookup):
            await games_cog.Games.add_area.callback(SimpleNamespace(), request, "missing")
        self.assertIn("not found", request.edit_original_response.call_args.kwargs["content"])

    async def test_area_confirmation_skips_selector_refresh(self):
        request = interaction()
        view = games_cog.ConfirmGameAddView(SimpleNamespace(bot=None), GAME, {}, request.user.id)

        def scan(*args):
            request.response.defer.assert_awaited_once()
            return {"conflicts": []}

        with patch.object(games_cog, "inspect_game_structure", side_effect=scan), patch.object(
            games_cog, "create_game_structure_confirmed", new_callable=AsyncMock, return_value=GAME
        ), patch.object(db, "get_game_by_id", return_value=GAME), patch.object(
            games_cog, "refresh_choose_games_message", new_callable=AsyncMock
        ) as refresh:
            await view.confirm.callback(request)
            refresh.assert_not_awaited()
        self.assertIn("area is ready", request.edit_original_response.call_args.kwargs["content"])
        view.stop()


def message(message_id, content, view):
    result = SimpleNamespace(id=message_id, content=content, author=SimpleNamespace(id=99),
                             embeds=[], pinned=True, edit=AsyncMock(), delete=AsyncMock())
    result.components = [SimpleNamespace(to_dict=lambda data=data: data) for data in view.to_components()]
    return result


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_rejects_missing_views_before_discord_access(self):
        bot = SimpleNamespace(get_channel=MagicMock())
        with patch.object(game_service, "CHOOSE_GAMES_CHANNEL_ID", 1):
            with self.assertRaises(game_service.GameStructureError):
                await game_service.refresh_choose_games_message(bot)
        bot.get_channel.assert_not_called()

    async def test_refresh_reuses_history_and_edits_only_changed_section(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                intro = message(1, game_service.build_choose_games_message(), games_cog.ChooseGamesButtons())
                title = "## " + GAME["display_group"]
                section = message(2, title, games_cog.GameCategoryView([GAME]))
                channel = MagicMock(spec=discord.TextChannel)
                channel.guild.me.id = 99
                channel.fetch_message = AsyncMock()
                channel.send = AsyncMock()

                async def history(**kwargs):
                    for item in (intro, section):
                        yield item

                channel.history = history
                settings = {"choose_games_message_id": "1",
                            "choose_games_section_message_ids": '{"' + game_service._choose_games_section_key(title) + '": 2}'}
                games = [GAME, {**GAME, "id": 2, "name": "Another Game"}] if changed else [GAME]
                with patch.object(game_service, "CHOOSE_GAMES_CHANNEL_ID", 1), patch.object(
                    db, "get_setting", side_effect=settings.get
                ), patch.object(db, "set_setting"), patch.object(
                    game_service, "build_choose_games_sections", return_value=[(title, games)]
                ), patch.object(game_service, "cleanup_pin_system_messages", new_callable=AsyncMock):
                    await game_service.refresh_choose_games_message(
                        SimpleNamespace(get_channel=lambda _: channel), view=games_cog.GameCategoryView,
                        intro_view=games_cog.ChooseGamesButtons)
                channel.fetch_message.assert_not_awaited()
                channel.send.assert_not_awaited()
                intro.edit.assert_not_awaited()
                self.assertEqual(section.edit.await_count, int(changed))
                section.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
