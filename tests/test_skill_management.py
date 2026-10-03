import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.skills import ConfirmSkillStateView, SkillsView, dashboard_text
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.events import EventContract
from skill_runtime.contracts.manifest import SkillEvents, SkillManifest
from skill_runtime.runtime.registry import SkillRegistry


class FakeSkill:
    def __init__(self):
        self.manifest=SkillManifest(
            id="fixture-skill",
            name="Fixture Skill",
            version="1.2.3",
            runtime_api_version="1",
            description="A documented fixture Skill.",
            author="test",
            permissions=(
                SkillCapability.DISCORD_MESSAGES_SEND.value,
                SkillCapability.STORAGE_SKILL.value,
            ),
            events=SkillEvents(
                emits=(EventContract("fixture.changed.v1"),),
            ),
        )


class SkillManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner=SimpleNamespace(
            id=42,
            guild_permissions=discord.Permissions(administrator=True),
        )
        self.admin=SimpleNamespace(
            id=43,
            guild_permissions=discord.Permissions(administrator=True),
        )
        members={42:self.owner,43:self.admin}
        self.guild=SimpleNamespace(
            id=1,
            owner_id=42,
            get_member=members.get,
        )
        self.registry=SkillRegistry()
        self.registry.register(FakeSkill())
        self.state=SimpleNamespace(
            is_enabled=AsyncMock(return_value=False),
            enabled_skill_ids=AsyncMock(return_value=()),
        )
        self.manager=SimpleNamespace(
            is_running=lambda **kwargs:False,
            health=AsyncMock(return_value=SimpleNamespace(state="DISABLED",detail="")),
        )
        self.runtime=SimpleNamespace(
            registry=self.registry,
            state=self.state,
            manager=self.manager,
            enable=AsyncMock(return_value=True),
            start=AsyncMock(return_value=True),
            disable=AsyncMock(return_value=True),
        )

    def interaction(self,user=None):
        user=user or self.owner
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
            is_done=lambda:False,
        )
        return SimpleNamespace(
            guild=self.guild,
            guild_id=self.guild.id,
            user=user,
            client=SimpleNamespace(skill_runtime=self.runtime),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_dashboard_lists_registered_skill_and_state(self):
        text=await dashboard_text(self.guild,self.runtime)
        self.assertIn("# 🧩 Skills",text)
        self.assertIn("Fixture Skill",text)
        self.assertIn("Disabled",text)

    async def test_skill_detail_shows_manifest_contracts(self):
        view=SkillsView(self.guild,self.owner.id,self.runtime,selected_skill_id="fixture-skill")
        await view.refresh_state()
        text=view.text()
        self.assertIn("Version **1.2.3**",text)
        self.assertIn("discord.messages.send",text)
        self.assertIn("storage.skill",text)
        self.assertIn("fixture.changed.v1",text)
        self.assertIn("Public APIs exposed",text)

    async def test_admin_can_view_but_enable_button_is_disabled(self):
        view=SkillsView(self.guild,self.admin.id,self.runtime,selected_skill_id="fixture-skill")
        await view.refresh_state()
        view.rebuild()
        buttons=[item for item in view.children if isinstance(item,discord.ui.Button)]
        change=next(item for item in buttons if item.label=="Enable Skill")
        self.assertTrue(change.disabled)

    async def test_owner_confirmation_enables_and_starts_skill(self):
        view=ConfirmSkillStateView(
            self.guild,self.owner.id,self.runtime,"fixture-skill",enable=True
        )
        interaction=self.interaction()
        with patch("services.server_log_service.emit",new_callable=AsyncMock,return_value=True) as emit:
            await view.confirm.callback(interaction)
        self.runtime.enable.assert_awaited_once_with(guild_id=1,skill_id="fixture-skill")
        self.runtime.start.assert_awaited_once_with(guild_id=1,skill_id="fixture-skill")
        self.runtime.disable.assert_not_awaited()
        emit.assert_awaited_once()
        interaction.edit_original_response.assert_awaited()

    async def test_owner_confirmation_disables_without_deleting_skill_data(self):
        self.state.is_enabled.return_value=True
        view=ConfirmSkillStateView(
            self.guild,self.owner.id,self.runtime,"fixture-skill",enable=False
        )
        interaction=self.interaction()
        with patch("services.server_log_service.emit",new_callable=AsyncMock,return_value=True):
            await view.confirm.callback(interaction)
        self.runtime.disable.assert_awaited_once_with(guild_id=1,skill_id="fixture-skill")
        self.runtime.enable.assert_not_awaited()
        self.runtime.start.assert_not_awaited()

    async def test_admin_cannot_reuse_owner_confirmation(self):
        view=ConfirmSkillStateView(
            self.guild,self.admin.id,self.runtime,"fixture-skill",enable=True
        )
        interaction=self.interaction(self.admin)
        await view.confirm.callback(interaction)
        self.runtime.enable.assert_not_awaited()
        self.runtime.start.assert_not_awaited()
        interaction.response.send_message.assert_awaited()
        self.assertIn("Only the server owner",interaction.response.send_message.await_args.args[0])

    async def test_cancel_changes_nothing(self):
        view=ConfirmSkillStateView(
            self.guild,self.owner.id,self.runtime,"fixture-skill",enable=True
        )
        interaction=self.interaction()
        await view.cancel.callback(interaction)
        self.runtime.enable.assert_not_awaited()
        self.runtime.disable.assert_not_awaited()
        self.runtime.start.assert_not_awaited()


if __name__=="__main__":
    unittest.main()
