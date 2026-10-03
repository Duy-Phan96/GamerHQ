import asyncio
import unittest

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.manifest import SkillManifest, SkillPublicApis
from skill_runtime.contracts.public_api import PublicApiContract
from skill_runtime.runtime.api_router import SkillApiError, SkillApiRouter
from skill_runtime.runtime.registry import SkillRegistry
from skill_runtime.runtime.scoped import ScopedSkillApi


class FakeSkill:
    def __init__(self, skill_id, *, exposes=(), consumes=(), permissions=()):
        self.manifest = SkillManifest(
            id=skill_id,
            name=skill_id.title(),
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=tuple(permissions),
            public_apis=SkillPublicApis(
                exposes=tuple(PublicApiContract(value) for value in exposes),
                consumes=tuple(PublicApiContract(value) for value in consumes),
            ),
        )


class SkillApiRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = SkillRegistry()
        self.registry.register(FakeSkill(
            "events",
            exposes=("events.get-event.v1",),
        ))
        self.registry.register(FakeSkill(
            "consumer",
            consumes=("events.get-event.v1",),
            permissions=(SkillCapability.SKILL_API_CALL.value,),
        ))
        self.router = SkillApiRouter(self.registry, timeout_seconds=0.05)

    async def test_declared_request_response_contract_routes_without_direct_import(self):
        requests = []
        async def handler(guild_id, payload):
            requests.append((guild_id, dict(payload)))
            return {"title": "Demo"}

        self.router.register_handler(
            skill_id="events",
            contract_id="events.get-event.v1",
            handler=handler,
        )
        response = await self.router.call(
            guild_id=7,
            consumer_skill_id="consumer",
            skill_id="events",
            contract_id="events.get-event.v1",
            payload={"eventId": "123"},
        )
        self.assertEqual(response["title"], "Demo")
        self.assertEqual(requests, [(7, {"eventId": "123"})])
        with self.assertRaises(TypeError):
            response["title"] = "changed"

    async def test_consumer_must_declare_capability_and_contract(self):
        self.registry.register(FakeSkill("no-cap", consumes=("events.get-event.v1",)))
        with self.assertRaisesRegex(PermissionError, "skills.api.call"):
            await self.router.call(
                guild_id=1, consumer_skill_id="no-cap", skill_id="events",
                contract_id="events.get-event.v1", payload={}
            )

        self.registry.register(FakeSkill(
            "undeclared",
            permissions=(SkillCapability.SKILL_API_CALL.value,),
        ))
        with self.assertRaisesRegex(PermissionError, "did not declare consumed public API"):
            await self.router.call(
                guild_id=1, consumer_skill_id="undeclared", skill_id="events",
                contract_id="events.get-event.v1", payload={}
            )

    def test_provider_must_declare_exposed_contract(self):
        with self.assertRaisesRegex(PermissionError, "did not declare exposed public API"):
            self.router.register_handler(
                skill_id="events",
                contract_id="events.private.v1",
                handler=lambda guild_id, payload: None,
            )

    async def test_private_provider_exception_is_not_leaked_across_boundary(self):
        async def handler(guild_id, payload):
            raise ValueError("private provider detail must stay internal")
        self.router.register_handler(
            skill_id="events",
            contract_id="events.get-event.v1",
            handler=handler,
        )
        with self.assertRaisesRegex(SkillApiError, "^Target Skill API failed\.$") as caught:
            await self.router.call(
                guild_id=1, consumer_skill_id="consumer", skill_id="events",
                contract_id="events.get-event.v1", payload={}
            )
        self.assertNotIn("private provider detail", str(caught.exception))

    async def test_timeout_and_missing_handler_fail_with_public_errors(self):
        with self.assertRaisesRegex(SkillApiError, "currently unavailable"):
            await self.router.call(
                guild_id=1, consumer_skill_id="consumer", skill_id="events",
                contract_id="events.get-event.v1", payload={}
            )

        async def slow(guild_id, payload):
            await asyncio.sleep(1)
            return {}
        self.router.register_handler(
            skill_id="events",
            contract_id="events.get-event.v1",
            handler=slow,
        )
        with self.assertRaisesRegex(SkillApiError, "timed out"):
            await self.router.call(
                guild_id=1, consumer_skill_id="consumer", skill_id="events",
                contract_id="events.get-event.v1", payload={}
            )

    async def test_scoped_api_cannot_spoof_consumer_identity(self):
        async def handler(guild_id, payload):
            return {"guild": guild_id}
        self.router.register_handler(
            skill_id="events",
            contract_id="events.get-event.v1",
            handler=handler,
        )
        scoped = ScopedSkillApi(self.router, guild_id=99, skill_id="consumer")
        response = await scoped.call(
            skill_id="events",
            contract_id="events.get-event.v1",
            payload={},
        )
        self.assertEqual(response["guild"], 99)


if __name__ == "__main__":
    unittest.main()
