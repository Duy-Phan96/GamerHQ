import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.event_manager import SkillEventEditor, WeekdayButton, can_manage, can_view, open_editor
from skill_runtime import ManagementField, ManagementFieldOption

FIELDS = (ManagementField(key="name", label="Name", type="string", config_path="name"),
    ManagementField(key="weekdays", label="Weekdays", type="multi_select", config_path="weekdays",
        options=tuple(ManagementFieldOption(str(day), name) for day, name in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))))


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
        field = next(field for field in FIELDS if field.key == "weekdays")
        editor.rebuild(field)
        controls = [item for item in editor.children if isinstance(item, WeekdayButton)]
        self.assertEqual(len(controls), 7)
        editor.reviewed = "old"
        interaction = SimpleNamespace(user=self.member(), response=SimpleNamespace(edit_message=AsyncMock()))
        await controls[2].callback(interaction)
        self.assertEqual(draft["weekdays"], ["0", "2"])
        self.assertIsNone(editor.reviewed)

    async def test_owner_editor_requires_current_preview_before_persistent_call(self):
        editor = SkillEventEditor(10, FIELDS, {"name": "Raid"})
        interaction = SimpleNamespace(user=self.member(), response=SimpleNamespace(send_message=AsyncMock()))
        with patch("cogs.event_manager.call", new_callable=AsyncMock) as api:
            await editor.save.callback(interaction)
        api.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_creation_passes_stable_request_id_and_actor_to_public_skill_api(self):
        editor = SkillEventEditor(10, FIELDS, {"name": "Raid"})
        import json
        editor.reviewed = json.dumps(editor.draft, sort_keys=True)
        interaction = SimpleNamespace(user=self.member(), response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))
        with patch("cogs.event_manager.call", new_callable=AsyncMock, return_value={"event": {"id": "one"}}) as api:
            await editor.save.callback(interaction)
        first = api.await_args_list[0].args
        self.assertEqual(first[1], "create")
        self.assertEqual(first[2]["ownerId"], "10")
        self.assertEqual(first[2]["requestId"], editor.request_id)
        self.assertEqual(api.await_args_list[1].args[1], "reconcile")
