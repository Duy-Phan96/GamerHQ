import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hosts.gamerhq.web_platform import (
    GamerHQWebPlatformService,
    WebPlatformAuthorizationError,
)
from skill_runtime.contracts.management_ui import (
    ManagementField,
    ManagementSection,
    ManagementUiSchema,
)


class WebPlatformServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guild = SimpleNamespace(id=123, name="GamerHQ", member_count=42)
        self.bot = SimpleNamespace(
            guilds=[self.guild],
            get_guild=lambda guild_id: self.guild if int(guild_id) == 123 else None,
        )
        self.status = SimpleNamespace(
            skill_id="progression",
            name="Progression & Achievements",
            version="0.1.0",
            description="Progression",
            source_kind="built-in",
            source_distribution=None,
            enabled=True,
            health="PASS",
            health_detail="ok",
            required_capabilities=("storage.skill",),
            missing_capabilities=(),
            management_available=True,
            management_schema_available=True,
        )
        self.schema = ManagementUiSchema(
            version="1",
            read_contract="progression.get-config.v1",
            write_contract="progression.update-config.v1",
            sections=(
                ManagementSection(
                    id="general",
                    title="General",
                    fields=(
                        ManagementField(
                            key="enabled",
                            label="Enabled",
                            type="boolean",
                            config_path="settings.enabled",
                        ),
                    ),
                ),
            ),
        )
        self.runtime = SimpleNamespace(
            statuses=AsyncMock(return_value=(self.status,)),
            status=AsyncMock(return_value=self.status),
            management_ui_schema=lambda skill_id: self.schema if skill_id == "progression" else None,
            call_management=AsyncMock(return_value={"config": {"revision": 2}}),
            enable_skill=AsyncMock(return_value=True),
            disable_skill=AsyncMock(return_value=True),
        )
        self.service = GamerHQWebPlatformService(bot=self.bot, skill_runtime=self.runtime)

    async def test_lists_only_authorized_servers(self):
        servers = await self.service.list_servers(authorized_guild_ids=(123,))
        self.assertEqual(servers, [{
            "id": "123",
            "name": "GamerHQ",
            "memberCount": 42,
            "manageable": True,
        }])
        self.assertEqual(
            await self.service.list_servers(authorized_guild_ids=(999,)),
            [],
        )

    async def test_rejects_unauthorized_guild_before_runtime_call(self):
        with self.assertRaises(WebPlatformAuthorizationError):
            await self.service.list_skills(guild_id=123, authorized_guild_ids=(999,))
        self.runtime.statuses.assert_not_awaited()

    async def test_skill_projection_is_json_safe_and_server_scoped(self):
        values = await self.service.list_skills(
            guild_id=123,
            authorized_guild_ids=(123,),
        )
        self.assertEqual(values[0]["id"], "progression")
        self.assertTrue(values[0]["state"]["enabled"])
        self.assertTrue(values[0]["managementSchemaAvailable"])
        self.assertEqual(values[0]["capabilities"], ["storage.skill"])

    async def test_management_schema_uses_public_camel_case_shape(self):
        schema = await self.service.get_management_schema(
            guild_id=123,
            skill_id="progression",
            authorized_guild_ids=(123,),
        )
        self.assertEqual(schema["readContract"], "progression.get-config.v1")
        self.assertEqual(schema["writeContract"], "progression.update-config.v1")
        field = schema["sections"][0]["fields"][0]
        self.assertEqual(field["configPath"], "settings.enabled")
        self.assertEqual(field["type"], "boolean")

    async def test_management_call_stays_on_versioned_runtime_router(self):
        response = await self.service.call_management(
            guild_id=123,
            skill_id="progression",
            contract_id="progression.get-config.v1",
            payload={},
            authorized_guild_ids=(123,),
        )
        self.assertEqual(response["config"]["revision"], 2)
        self.runtime.call_management.assert_awaited_once_with(
            guild_id=123,
            skill_id="progression",
            contract_id="progression.get-config.v1",
            payload={},
        )

    async def test_enable_disable_reuses_authoritative_runtime_state(self):
        await self.service.set_skill_enabled(
            guild_id=123,
            skill_id="progression",
            enabled=False,
            authorized_guild_ids=(123,),
        )
        self.runtime.disable_skill.assert_awaited_once_with(
            guild_id=123,
            skill_id="progression",
        )


if __name__ == "__main__":
    unittest.main()
