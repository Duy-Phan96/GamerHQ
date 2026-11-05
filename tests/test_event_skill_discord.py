import asyncio
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.event_manager import EventManager, SkillEventEditor, call, can_manage, can_view, open_editor
from cogs.event_editor import (DateSelect, TimeSelect, PlayersSelect, RecurrenceSelect, WeekdayView,
    EventPreviewView, SettingsView, ParticipantsView, DetailsModal)
from cogs.event_editor import SettingModal
from cogs.lfg import WeekdayToggle, BuilderDateSelect, BuilderTimeSelect, BuilderPlayersSelect
from skill_runtime import ManagementField, ManagementFieldOption

FIELDS = (ManagementField(key="name", label="Name", type="string", config_path="name"),
    ManagementField(key="weekdays", label="Weekdays", type="multi_select", config_path="weekdays",
        options=tuple(ManagementFieldOption(str(day), name) for day, name in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))))
FIELDS += tuple(ManagementField(key=key, label=label, type=kind, config_path=key) for key, label, kind in (
    ("game", "Game", "string"), ("description", "Description", "long_text"), ("timezone", "Timezone", "timezone"),
    ("durationMinutes", "Duration in minutes", "integer"), ("autoEnroll", "Automatically enroll event members", "boolean"),
    ("voiceEnabled", "Temporary voice channel", "boolean"), ("voiceLeadMinutes", "Voice lead time (minutes)", "integer"),
    ("chatEnabled", "Persistent event chat", "boolean"), ("members", "Member IDs", "long_text"),
    ("invited", "Invited IDs", "long_text"), ("convertLegacySchedule", "Replace imported schedule", "boolean")))


class EventSkillDiscordTests(unittest.IsolatedAsyncioTestCase):
    def member(self, ident=10, admin=False):
        member = MagicMock(spec=discord.Member)
        member.id = ident
        member.guild_permissions = SimpleNamespace(administrator=admin, manage_guild=False)
        return member

    def test_private_visibility_and_owner_authorization(self):
        event = {"ownerId": "10", "members": ["20"], "invited": ["30"], "config": {"visibility": "private"}}
        self.assertTrue(can_manage(self.member(10), event))
        self.assertFalse(can_manage(self.member(20), event))
        self.assertTrue(can_view(self.member(30), event))
        self.assertFalse(can_view(self.member(40), event))
        self.assertTrue(can_view(self.member(40, True), event))

    async def test_schema_weekday_buttons_are_independent_and_invalidate_review(self):
        draft = {"name": "Raid", "kind": "recurring", "weekdays": ["0"]}
        editor = SkillEventEditor(10, FIELDS, draft)
        page = WeekdayView(editor)
        controls = [item for item in page.children if isinstance(item, WeekdayToggle)]
        self.assertEqual(len(controls), 7)
        editor.reviewed = "old"
        interaction = SimpleNamespace(user=self.member(), response=SimpleNamespace(edit_message=AsyncMock()))
        await controls[2].callback(interaction)
        self.assertEqual(draft["weekdays"], ["0"])
        self.assertEqual(page.selected, {0, 2})
        self.assertEqual(next(item for item in page.children if getattr(item, "day", None) == 2).style, discord.ButtonStyle.primary)
        await page.apply_days.callback(interaction)
        self.assertEqual(draft["weekdays"], [0, 2])
        self.assertIsNone(editor.reviewed)

    async def test_owner_editor_requires_current_preview_before_persistent_call(self):
        editor = SkillEventEditor(10, FIELDS, {"name": "Raid"})
        interaction = SimpleNamespace(user=self.member(), response=SimpleNamespace(send_message=AsyncMock()))
        with patch("cogs.event_manager.call", new_callable=AsyncMock) as api:
            await editor.save(interaction)
        api.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_creation_passes_stable_request_id_and_actor_to_public_skill_api(self):
        editor = SkillEventEditor(10, FIELDS, {"name": "Raid"})
        editor.reviewed = json.dumps(editor.draft, sort_keys=True)
        interaction = SimpleNamespace(user=self.member(), response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))
        with patch("cogs.event_manager.call", new_callable=AsyncMock, return_value={"event": {"id": "one"}}) as api:
            await editor.save(interaction)
        first = api.await_args_list[0].args
        self.assertEqual(first[1], "create")
        self.assertEqual(first[2]["ownerId"], "10")
        self.assertEqual(first[2]["requestId"], editor.request_id)
        self.assertEqual(api.await_args_list[1].args[1], "reconcile")

    def interaction(self, ident=10, guild=1):
        return SimpleNamespace(user=self.member(ident), guild=SimpleNamespace(id=guild),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock(), send_modal=AsyncMock()),
            edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    def builder(self, **changes):
        draft = dict(name="Raid", kind="once", startDate="2030-01-07", localTime="19:00", timezone="Europe/Berlin",
            weekdays=[], maxPlayers=5, visibility="private", voiceEnabled=False, voiceLeadMinutes=15,
            members="10", invited="", description="", game="", durationMinutes=120, autoEnroll=False, chatEnabled=True,
            convertLegacySchedule=False)
        draft.update(changes)
        return SkillEventEditor(10, FIELDS, draft, guild_id=1)

    async def test_creation_title_uses_per_guild_host_configuration(self):
        interaction = self.interaction(guild=12)
        schema = SimpleNamespace(sections=[SimpleNamespace(fields=[SimpleNamespace(collection=SimpleNamespace(item_fields=FIELDS))])])
        interaction.client = SimpleNamespace(skill_runtime=SimpleNamespace(management_ui_schema=lambda skill: schema))
        with patch("cogs.event_manager.call", AsyncMock(return_value={"defaultDraft": self.builder().draft})), \
                patch("cogs.event_manager.db.get_setting", side_effect=[None, "  Co-op Night  "]) as setting:
            await open_editor(interaction)
            self.assertEqual(interaction.followup.send.await_args.kwargs["view"].draft["name"], "GamerHQ Event")
            await open_editor(interaction)
            self.assertEqual(interaction.followup.send.await_args.kwargs["view"].draft["name"], "Co-op Night")
            setting.assert_called_with("event_manager_default_title:12")

    async def test_mutation_rechecks_owner_and_guild_before_dispatch(self):
        interaction = self.interaction(ident=20, guild=2)
        current = {"state": {"ownerId": "10"}}
        api = AsyncMock(return_value=current)
        interaction.client = SimpleNamespace(skill_runtime=SimpleNamespace(
            registry=SimpleNamespace(contains=lambda _: True), call_management=api))
        with self.assertRaises(PermissionError):
            await call(interaction, "delete", {"eventId": "guessed", "revision": 1})
        api.assert_awaited_once()
        self.assertEqual(api.await_args.kwargs["guild_id"], 2)
        interaction.user = self.member(20, admin=True)
        await call(interaction, "delete", {"eventId": "guessed", "revision": 1})
        self.assertEqual(api.await_args.kwargs["contract_id"], "event-manager.delete.v1")

    async def test_event_picker_uses_titles_and_distinguishes_owner_from_admin(self):
        interaction = self.interaction()
        interaction.channel = None
        first = {"id": "internal-a", "ownerId": "10", "status": "active", "members": ["10"],
                 "config": self.builder(name="Raid One").draft}
        other = {**first, "id": "internal-b", "ownerId": "20", "config": self.builder(name="Raid Two").draft}
        manager = EventManager(None)
        with patch("cogs.event_manager.call", AsyncMock(return_value={"events": [first, other]})):
            await manager.navigate(interaction, "edit")
            picker = interaction.followup.send.await_args.kwargs["view"]
            self.assertIn("My Events", picker.content())
            self.assertEqual([option.label for option in picker.children[0].options], ["Raid One"])
            interaction.user = self.member(admin=True)
            await manager.navigate(interaction, "manage")
            picker = interaction.followup.send.await_args.kwargs["view"]
            self.assertIn("All Events", picker.content())
            self.assertEqual([option.label for option in picker.children[0].options], ["Raid One", "Raid Two"])

    async def test_compact_component_and_copy_snapshot(self):
        editor = self.builder()
        snapshot = {"content": editor.content(), "rows": [[{
            "type": "select" if isinstance(item, discord.ui.Select) else "button",
            "label": getattr(item, "label", None), "placeholder": getattr(item, "placeholder", None),
            "choices": [o.label for o in item.options] if isinstance(item, RecurrenceSelect) else None,
        } for item in editor.children if item.row == row] for row in range(5)]}
        expected = json.loads((Path(__file__).parent / "fixtures" / "skill-event-compact-ui.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot, expected)
        self.assertIsInstance(editor.children[0], BuilderDateSelect)
        self.assertIsInstance(editor.children[1], BuilderTimeSelect)
        self.assertIsInstance(editor.children[2], BuilderPlayersSelect)
        for technical in ("2030405060708090", "False", "True", "convertLegacySchedule", "comma-separated", "weekdays:"):
            self.assertNotIn(technical, editor.content())
        for view in (editor, WeekdayView(editor), EventPreviewView(editor), SettingsView(editor), ParticipantsView(editor)):
            for item in view.to_components():
                self.assertLessEqual(len(item["components"]), 5)
            self.assertLessEqual(len(view.to_components()), 5)

    async def test_nonstandard_existing_schedule_and_capacity_are_not_lost(self):
        editor = self.builder(localTime="00:17", maxPlayers=150, timezone="America/New_York", members="123456789012345678")
        self.assertIn("12:17 AM", editor.content())
        self.assertIn(discord.utils.escape_markdown("America/New_York"), editor.content())
        self.assertNotIn("123456789012345678", editor.content())
        for child in editor.children[:3]:
            self.assertEqual(sum(option.default for option in child.options), 1)
            self.assertLessEqual(len(child.options), 25)
        self.assertEqual(next(o.value for o in editor.children[1].options if o.default), "12:17")
        self.assertEqual(editor.draft["localTime"], "00:17")
        self.assertEqual(editor.draft["maxPlayers"], 150)

    async def test_existing_controls_update_only_draft_and_require_preview_again(self):
        editor = self.builder()
        with patch("cogs.event_manager.call", new_callable=AsyncMock) as api:
            for cls, value, key, expected in ((DateSelect, "2030-01-09", "startDate", "2030-01-09"),
                    (TimeSelect, "12:00", "localTime", "12:00"), (PlayersSelect, "8", "maxPlayers", 8)):
                editor.reviewed = "old"
                select = next(item for item in editor.children if isinstance(item, cls))
                select._values = [value]
                await select.callback(self.interaction())
                self.assertEqual(editor.draft[key], expected)
                self.assertIsNone(editor.reviewed)
            await editor.ampm_button.callback(self.interaction())
            self.assertEqual(editor.draft["localTime"], "00:00")
            await editor.voice_lead.callback(self.interaction())
            self.assertTrue(editor.draft["voiceEnabled"])
            self.assertEqual(editor.draft["voiceLeadMinutes"], 5)
            await editor.visibility_button.callback(self.interaction())
            self.assertEqual(editor.draft["visibility"], "public")
        api.assert_not_awaited()

    async def test_weekday_back_empty_and_one_time_keep_review_safe(self):
        editor = self.builder(kind="recurring", weekdays=[0, 2])
        page = WeekdayView(editor)
        page.selected = {4}
        await page.back.callback(self.interaction())
        self.assertEqual(editor.draft["weekdays"], [0, 2])
        page = WeekdayView(editor)
        page.selected.clear()
        denied = self.interaction()
        await page.apply_days.callback(denied)
        denied.response.send_message.assert_awaited_once()
        select = next(c for c in editor.children if isinstance(c, RecurrenceSelect))
        self.assertEqual([option.value for option in select.options], ["once", "recurring"])
        select._values = ["once"]
        await select.callback(self.interaction())
        self.assertEqual(editor.draft["weekdays"], [])
        self.assertEqual(editor.draft["kind"], "once")
        select = next(c for c in editor.children if isinstance(c, RecurrenceSelect))
        select._values = ["recurring"]
        interaction = self.interaction()
        await select.callback(interaction)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["view"].selected, {0, 2})

    async def test_preview_opens_familiar_confirmation_and_edit_invalidates_it(self):
        editor = self.builder(kind="recurring", weekdays=[0, 2, 4])
        interaction = self.interaction()
        with patch("cogs.event_manager.call", new_callable=AsyncMock, return_value={"nextOccurrence": 1894042800}) as api:
            await editor.preview.callback(interaction)
        self.assertEqual(api.await_args.args[1], "validate")
        shown = interaction.edit_original_response.await_args.kwargs
        self.assertIn("# 📅 Event Preview", shown["content"])
        self.assertIn("Every Monday, Wednesday, Friday", shown["content"])
        self.assertIsInstance(shown["view"], EventPreviewView)
        self.assertEqual(shown["view"].confirm.label, "Create Event")
        self.assertIsNotNone(editor.reviewed)
        await shown["view"].edit.callback(self.interaction())
        self.assertIsNone(editor.reviewed)

    async def test_preview_failure_and_concurrent_draft_change_cannot_confirm(self):
        editor = self.builder()
        async def change(*args):
            editor.draft["name"] = "Changed while validation was pending"
            return {}
        for behavior in (ValueError("Invalid schedule"), change):
            with patch("cogs.event_manager.call", new_callable=AsyncMock, side_effect=behavior):
                await editor.preview.callback(self.interaction())
            self.assertIsNone(editor.reviewed)

    async def test_all_pages_and_callbacks_reject_wrong_actor_or_guild(self):
        editor = self.builder()
        original = dict(editor.draft)
        for interaction in (self.interaction(20), self.interaction(guild=2)):
            with patch("cogs.event_manager.call", new_callable=AsyncMock) as api:
                for item in editor.children:
                    if isinstance(item, discord.ui.Select):
                        item._values = [item.options[0].value]
                    await item.callback(interaction)
                for page in (WeekdayView(editor), SettingsView(editor), ParticipantsView(editor), EventPreviewView(editor)):
                    self.assertFalse(await page.interaction_check(interaction))
            api.assert_not_awaited()
        self.assertEqual(editor.draft, original)

    async def test_player_picker_preserves_existing_members_and_uses_names(self):
        editor = self.builder(members=",".join(str(i) for i in range(1, 45)), maxPlayers=50)
        page = ParticipantsView(editor)
        self.assertEqual([item.label for item in page.children], ["Apply", "Cancel", "Add", "Remove"])
        await page.add.callback(self.interaction())
        add, invite = [next(c for c in page.children if c.row == row) for row in range(2)]
        add._values = [SimpleNamespace(id=60, bot=False), SimpleNamespace(id=61, bot=True)]
        await add.callback(self.interaction())
        self.assertEqual(len(page.draft["members"].split(",")), 45)
        self.assertEqual(len(editor.draft["members"].split(",")), 44)
        self.assertNotIn("61", page.draft["members"].split(","))
        invite._values = [SimpleNamespace(id=62, bot=False)]
        await invite.callback(self.interaction())
        await page.apply.callback(self.interaction())
        self.assertEqual(editor.draft["invited"], "62")
        self.assertEqual(len(editor.draft["members"].split(",")), 45)
        page = ParticipantsView(editor)
        await page.remove.callback(self.interaction())
        remove = next(c for c in page.children if c.row == 0)
        remove._values = [SimpleNamespace(id=60, bot=False)]
        await remove.callback(self.interaction())
        await page.cancel.callback(self.interaction())
        self.assertIn("60", editor.draft["members"].split(","))
        self.assertIsNone(editor.reviewed)

    async def test_title_game_description_and_advanced_settings_remain_accessible(self):
        editor = self.builder(name="X" * 90)
        modal = DetailsModal(editor)
        self.assertEqual(modal.title_input.default, "X" * 90)
        modal.title_input._value, modal.game._value, modal.description._value = "Ranked Night", "Arena", "Team practice"
        await modal.on_submit(self.interaction())
        self.assertEqual([editor.draft[k] for k in ("name", "game", "description")], ["Ranked Night", "Arena", "Team practice"])
        page = SettingsView(editor)
        options = next(c for c in page.children if isinstance(c, discord.ui.Select)).options
        self.assertIn("Temporary voice channel", [option.label for option in options])
        self.assertNotIn("Replace imported schedule and end condition", [option.label for option in options])
        self.assertNotIn("Member IDs", [option.label for option in options])

    async def test_advanced_toggle_and_invalid_schedule_input_never_persist(self):
        editor = self.builder()
        page = SettingsView(editor)
        select = next(c for c in page.children if isinstance(c, discord.ui.Select))
        select._values = [next(option.value for option in select.options if option.label == "Temporary voice channel")]
        editor.reviewed = "old"
        with patch("cogs.event_manager.call", new_callable=AsyncMock) as api:
            await select.callback(self.interaction())
            self.assertFalse(editor.draft["voiceEnabled"])
            self.assertTrue(page.draft["voiceEnabled"])
            self.assertEqual(editor.reviewed, "old")
            field = ManagementField(key="localTime", label="Time", type="string", config_path="localTime")
            modal = SettingModal(page, field)
            modal.value._value = "tomorrow evening"
            interaction = self.interaction()
            await modal.on_submit(interaction)
            interaction.response.send_message.assert_awaited_once()
            self.assertEqual(editor.draft["localTime"], "19:00")
            await page.apply.callback(self.interaction())
            self.assertTrue(editor.draft["voiceEnabled"])
            self.assertIsNone(editor.reviewed)
        api.assert_not_awaited()

    async def test_duplicate_and_concurrent_confirmation_persists_and_reconciles_once(self):
        editor = self.builder()
        editor.reviewed = json.dumps(editor.draft, sort_keys=True)
        with patch("cogs.event_manager.call", new_callable=AsyncMock, return_value={"event": {"id": "one"}}) as api:
            await asyncio.gather(editor.save(self.interaction()), editor.save(self.interaction()))
            await editor.save(self.interaction())
        self.assertEqual([args.args[1] for args in api.await_args_list], ["create", "reconcile"])

    async def test_saved_event_is_not_recreated_after_reconcile_failure(self):
        editor = self.builder()
        editor.reviewed = json.dumps(editor.draft, sort_keys=True)
        interaction = self.interaction()
        with patch("cogs.event_manager.call", new_callable=AsyncMock, side_effect=[{"event": {"id": "one"}}, RuntimeError("transient")]) as api:
            await editor.save(interaction)
            await editor.save(self.interaction())
        self.assertTrue(editor.submitted)
        self.assertIn("synchronization is pending", interaction.edit_original_response.await_args.kwargs["content"])
        self.assertEqual(api.await_count, 2)

    async def test_edit_rechecks_authorization_and_uses_original_revision(self):
        editor = self.builder()
        editor.event_id, editor.revision = "one", 7
        editor.reviewed = json.dumps(editor.draft, sort_keys=True)
        state = {"ownerId": "10"}
        with patch("cogs.event_manager.call", new_callable=AsyncMock, side_effect=[{"state": state}, {"event": {"id": "one"}}, {}]) as api:
            await editor.save(self.interaction())
        self.assertEqual(api.await_args_list[1].args[1], "update")
        self.assertEqual(api.await_args_list[1].args[2]["revision"], 7)
        self.assertNotIn("requestId", api.await_args_list[1].args[2])
        editor.submitted = False
        with patch("cogs.event_manager.call", new_callable=AsyncMock, return_value={"state": {"ownerId": "20"}}) as api:
            await editor.save(self.interaction())
        self.assertEqual(api.await_count, 1)

    async def test_imported_finite_schedule_preview_uses_skill_state_and_requires_explicit_conversion(self):
        editor = self.builder(kind="recurring", weekdays=[0])
        editor.event_id, editor.revision, editor.legacy_schedule = "imported", 5, True
        interaction = self.interaction()
        with patch("cogs.event_manager.call", new_callable=AsyncMock, side_effect=[
                {"state": {"ownerId": "10"}}, {"nextOccurrence": 1900000000},
                {"state": {"nextOccurrence": None}}]) as api:
            await editor.preview.callback(interaction)
        self.assertEqual([call.args[1] for call in api.await_args_list], ["get", "validate", "get"])
        content = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("No future occurrence", content)
        self.assertIn("existing end condition retained", content)
        self.assertFalse(editor.payload()["convertLegacySchedule"])
        self.assertIn("imported schedule", WeekdayView(editor).content())
