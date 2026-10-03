import unittest
from types import SimpleNamespace

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.manifest import SkillManifest, SkillPublicApis
from skill_runtime.contracts.public_api import PublicApiContract
from skill_runtime.runtime.api_router import SkillApiRouter
from skill_runtime.runtime.registry import SkillRegistry
from skill_runtime.runtime.scheduler import ScheduledJob, SchedulerEngine
from skill_runtime.runtime.scoped import ScopedSchedulerRegistration, ScopedSkillApiRegistration


class FakeSkill:
    def __init__(self, skill_id, *, exposes=()):
        self.manifest = SkillManifest(
            id=skill_id,
            name=skill_id,
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(
                SkillCapability.SCHEDULER_JOBS.value,
            ),
            public_apis=SkillPublicApis(
                exposes=tuple(PublicApiContract(value) for value in exposes),
            ),
        )


class NullStore:
    async def upsert_job(self, job): pass
    async def remove_job(self, **kwargs): return False
    async def claim_due(self, **kwargs): return ()
    async def finish_success(self, *args, **kwargs): pass
    async def finish_failure(self, *args, **kwargs): pass
    async def defer_job(self, *args, **kwargs): pass


class RegistrationContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_registration_injects_guild_scoped_context(self):
        registry = SkillRegistry()
        registry.register(FakeSkill("demo"))
        engine = SchedulerEngine(registry, NullStore())
        contexts = []

        async def context_factory(guild_id, skill_id):
            ctx = SimpleNamespace(guild_id=guild_id, skill_id=skill_id)
            contexts.append(ctx)
            return ctx

        registration = ScopedSchedulerRegistration(
            engine,
            skill_id="demo",
            context_factory=context_factory,
        )
        seen = []

        async def handler(ctx, payload):
            seen.append((ctx.guild_id, ctx.skill_id, dict(payload)))

        registration.register_handler("demo.execute.v1", handler)
        wrapped = engine._handlers[("demo", "demo.execute.v1")]
        await wrapped(ScheduledJob(
            guild_id=7,
            skill_id="demo",
            key="x",
            handler_id="demo.execute.v1",
            schedule=__import__("skill_runtime").IntervalSchedule(60),
            payload={"id": "1"},
            next_run_at=100,
        ))
        self.assertEqual(seen, [(7, "demo", {"id": "1"})])
        self.assertEqual(len(contexts), 1)

    async def test_public_api_registration_injects_provider_context(self):
        registry = SkillRegistry()
        registry.register(FakeSkill("provider", exposes=("provider.lookup.v1",)))
        router = SkillApiRouter(registry)

        async def context_factory(guild_id, skill_id):
            return SimpleNamespace(guild_id=guild_id, skill_id=skill_id)

        registration = ScopedSkillApiRegistration(
            router,
            skill_id="provider",
            context_factory=context_factory,
        )

        async def handler(ctx, payload):
            return {"guild": ctx.guild_id, "provider": ctx.skill_id, "id": payload["id"]}

        registration.expose("provider.lookup.v1", handler)
        response = await router._handlers[("provider", "provider.lookup.v1")](9, {"id": "abc"})
        self.assertEqual(dict(response), {"guild": 9, "provider": "provider", "id": "abc"})


if __name__ == "__main__":
    unittest.main()
