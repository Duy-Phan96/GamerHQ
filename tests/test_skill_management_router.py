import unittest
from types import SimpleNamespace

from skill_runtime.contracts.management import ManagementApiContract
from skill_runtime.contracts.manifest import (
    SkillManagementApis,
    SkillManifest,
)
from skill_runtime.runtime.management_router import (
    SkillManagementError,
    SkillManagementRouter,
)
from skill_runtime.runtime.registry import SkillRegistry


class FakeSkill:
    def __init__(self, skill_id="managed-skill", contracts=("managed-skill.list.v1",)):
        self.manifest = SkillManifest(
            id=skill_id,
            name="Managed Skill",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            management_apis=SkillManagementApis(
                exposes=tuple(ManagementApiContract(value) for value in contracts)
            ),
        )


class SkillManagementRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = SkillRegistry()
        self.registry.register(FakeSkill())
        self.enabled = True

        async def availability(*, guild_id, skill_id):
            return self.enabled

        self.router = SkillManagementRouter(
            self.registry,
            availability=availability,
        )

    async def test_declared_handler_routes_host_request(self):
        async def handler(guild_id, payload):
            return {"guild": guild_id, "value": payload["value"]}

        self.router.register_handler(
            skill_id="managed-skill",
            contract_id="managed-skill.list.v1",
            handler=handler,
        )
        response = await self.router.call(
            guild_id=123,
            skill_id="managed-skill",
            contract_id="managed-skill.list.v1",
            payload={"value": "ok"},
        )
        self.assertEqual(dict(response), {"guild": 123, "value": "ok"})

    def test_undeclared_management_handler_is_rejected(self):
        async def handler(guild_id, payload):
            return {}

        with self.assertRaisesRegex(PermissionError, "did not declare"):
            self.router.register_handler(
                skill_id="managed-skill",
                contract_id="managed-skill.delete.v1",
                handler=handler,
            )

    async def test_disabled_skill_cannot_receive_management_calls(self):
        async def handler(guild_id, payload):
            return {}

        self.router.register_handler(
            skill_id="managed-skill",
            contract_id="managed-skill.list.v1",
            handler=handler,
        )
        self.enabled = False
        with self.assertRaisesRegex(SkillManagementError, "disabled"):
            await self.router.call(
                guild_id=123,
                skill_id="managed-skill",
                contract_id="managed-skill.list.v1",
                payload={},
            )

    async def test_private_handler_error_is_not_exposed(self):
        marker = "private-management-detail"

        async def handler(guild_id, payload):
            raise RuntimeError(marker)

        self.router.register_handler(
            skill_id="managed-skill",
            contract_id="managed-skill.list.v1",
            handler=handler,
        )
        with self.assertRaises(SkillManagementError) as caught:
            await self.router.call(
                guild_id=123,
                skill_id="managed-skill",
                contract_id="managed-skill.list.v1",
                payload={},
            )
        self.assertNotIn(marker, str(caught.exception))

    async def test_invalid_skill_response_is_rejected(self):
        async def handler(guild_id, payload):
            return ["not", "a", "mapping"]

        self.router.register_handler(
            skill_id="managed-skill",
            contract_id="managed-skill.list.v1",
            handler=handler,
        )
        with self.assertRaisesRegex(SkillManagementError, "invalid response"):
            await self.router.call(
                guild_id=123,
                skill_id="managed-skill",
                contract_id="managed-skill.list.v1",
                payload={},
            )


if __name__ == "__main__":
    unittest.main()
