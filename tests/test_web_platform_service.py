import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hosts.gamerhq.web_platform import (
    GamerHQWebPlatformService,
    WebPlatformAuthorizationError,
)
from skill_runtime.contracts.management_ui import (
    ManagementCollectionOperations,
    ManagementCollectionSchema,
    ManagementDocumentBinding,
    ManagementField,
    ManagementSection,
    ManagementUiSchema,
)


class WebPlatformServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        category = SimpleNamespace(id=50, name="Community")
        channel_general = SimpleNamespace(
            id=10,
            name="general",
            type="text",
            position=2,
            category=category,
        )
        channel_news = SimpleNamespace(
            id=11,
            name="announcements",
            type="news",
            position=1,
            category=category,
        )
        default_role = SimpleNamespace(
            id=123,
            name="@everyone",
            position=0,
            managed=False,
        )
        member_role = SimpleNamespace(
            id=200,
            name="Member",
            position=2,
            managed=False,
        )
        bot_role = SimpleNamespace(
            id=201,
            name="Bot",
            position=3,
            managed=True,
        )
        self.guild = SimpleNamespace(
            id=123,
            name="GamerHQ",
            member_count=42,
            channels=[channel_general, channel_news],
            roles=[default_role, member_role, bot_role],
            default_role=default_role,
        )
        self.bot = SimpleNamespace(
            guilds=[self.guild],
            get_guild=lambda guild_id: self.guild if int(guild_id) == 123 else None,
        )
        self.status = SimpleNamespace(
            skill_id="progression",
            name="Progression & Achievements",
            version="0.1.0",
            description="Progression",
            installed=False,
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
            document=ManagementDocumentBinding(
                read_path="config",
                write_path="config",
                revision_path="revision",
                expected_revision_key="expectedRevision",
            ),
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
                        ManagementField(
                            key="posts",
                            label="Posts",
                            type="collection",
                            config_path="posts",
                            collection=ManagementCollectionSchema(
                                operations=ManagementCollectionOperations(
                                    list_contract="progression.get-config.v1",
                                    create_contract="progression.update-config.v1",
                                    describe_contract="progression.get-config.v1",
                                ),
                                item_fields=(
                                    ManagementField(
                                        key="name",
                                        label="Name",
                                        type="string",
                                        config_path="name",
                                    ),
                                ),
                                item_id_payload_key="postId",
                                item_read_path="item",
                                max_items=20,
                            ),
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
            install_skill=AsyncMock(return_value=True),
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

    async def test_lists_authorized_discord_resources(self):
        channels = await self.service.list_discord_channels(
            guild_id=123,
            authorized_guild_ids=(123,),
        )
        self.assertEqual(
            [channel["id"] for channel in channels],
            ["11", "10"],
        )
        self.assertEqual(channels[0]["categoryName"], "Community")
        self.assertEqual(channels[0]["kind"], "news")

        roles = await self.service.list_discord_roles(
            guild_id=123,
            authorized_guild_ids=(123,),
        )
        self.assertEqual([role["id"] for role in roles], ["201", "200", "123"])
        self.assertTrue(roles[0]["managed"])
        self.assertTrue(roles[-1]["isDefault"])

    async def test_resource_lookup_rejects_unauthorized_guild(self):
        with self.assertRaises(WebPlatformAuthorizationError):
            await self.service.list_discord_channels(
                guild_id=123,
                authorized_guild_ids=(999,),
            )
        with self.assertRaises(WebPlatformAuthorizationError):
            await self.service.list_discord_roles(
                guild_id=123,
                authorized_guild_ids=(999,),
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
        self.assertFalse(values[0]["state"]["installed"])
        self.assertTrue(values[0]["state"]["enabled"])
        self.assertFalse(values[0]["state"]["configured"])
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
        self.assertEqual(schema["document"]["readPath"], "config")
        self.assertEqual(schema["document"]["writePath"], "config")
        self.assertEqual(schema["document"]["revisionPath"], "revision")
        self.assertEqual(schema["document"]["expectedRevisionKey"], "expectedRevision")
        field = schema["sections"][0]["fields"][0]
        self.assertEqual(field["configPath"], "settings.enabled")
        self.assertEqual(field["type"], "boolean")
        collection = schema["sections"][0]["fields"][1]["collection"]
        self.assertEqual(collection["operations"]["listContract"], "progression.get-config.v1")
        self.assertEqual(collection["operations"]["createContract"], "progression.update-config.v1")
        self.assertEqual(collection["operations"]["describeContract"], "progression.get-config.v1")
        self.assertEqual(collection["itemFields"][0]["configPath"], "name")
        self.assertEqual(collection["itemIdPayloadKey"], "postId")
        self.assertEqual(collection["itemReadPath"], "item")
        self.assertEqual(collection["maxItems"], 20)

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

    async def test_install_skill_reuses_authoritative_runtime_state(self):
        result = await self.service.install_skill(
            guild_id=123,
            skill_id="progression",
            authorized_guild_ids=(123,),
        )
        self.runtime.install_skill.assert_awaited_once_with(
            guild_id=123,
            skill_id="progression",
        )
        self.assertEqual(result["id"], "progression")

    async def test_install_skill_rejects_unauthorized_guild(self):
        with self.assertRaises(WebPlatformAuthorizationError):
            await self.service.install_skill(
                guild_id=123,
                skill_id="progression",
                authorized_guild_ids=(999,),
            )
        self.runtime.install_skill.assert_not_awaited()

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
