import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from database import db
from hosts.gamerhq.runtime import GamerHQSkillRuntime
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.lifecycle import SkillHealth
from skill_runtime.contracts.manifest import SkillManifest, SkillPublicApis
from skill_runtime.contracts.public_api import PublicApiContract
from skill_runtime.contracts.schedule import OnceSchedule


class ProviderSkill:
    def __init__(self):
        self.manifest = SkillManifest(
            id="provider",
            name="Provider",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(
                SkillCapability.SCHEDULER_JOBS.value,
                SkillCapability.STORAGE_SKILL.value,
                SkillCapability.SKILL_API_CALL.value,
            ),
            public_apis=SkillPublicApis(
                exposes=(PublicApiContract("provider.status.v1"),),
            ),
        )
        self.calls = []

    async def register(self, ctx):
        self.calls.append(("register", ctx.skill_id))
        ctx.scheduler.register_handler("provider.tick.v1", self.tick)
        ctx.skills.expose("provider.status.v1", self.status)

    async def tick(self, ctx, job):
        self.calls.append(("tick", ctx.guild_id, ctx.skill_id, job.key))

    async def status(self, ctx, payload):
        self.calls.append(("api", ctx.guild_id, ctx.skill_id, dict(payload)))
        return {"provider": ctx.skill_id, "guild": ctx.guild_id}

    async def enable(self, ctx): self.calls.append(("enable", ctx.guild_id))
    async def disable(self, ctx): self.calls.append(("disable", ctx.guild_id))
    async def start(self, ctx): self.calls.append(("start", ctx.guild_id))
    async def stop(self, ctx): self.calls.append(("stop", ctx.guild_id))
    async def health_check(self, ctx): return SkillHealth("PASS", "ok")


class ConsumerSkill:
    def __init__(self):
        self.manifest = SkillManifest(
            id="consumer",
            name="Consumer",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(SkillCapability.SKILL_API_CALL.value,),
            public_apis=SkillPublicApis(
                consumes=(PublicApiContract("provider.status.v1"),),
            ),
        )

    async def register(self, ctx): pass
    async def enable(self, ctx): pass
    async def disable(self, ctx): pass
    async def start(self, ctx): pass
    async def stop(self, ctx): pass
    async def health_check(self, ctx): return SkillHealth("PASS", "ok")


class UnsupportedSkill(ConsumerSkill):
    def __init__(self):
        self.manifest = SkillManifest(
            id="external-http",
            name="External HTTP",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(SkillCapability.HTTP_EXTERNAL.value,),
        )


class GamerHQSkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "runtime.db")
        self.db_patch.start()
        db.init_db()
        self.guild = SimpleNamespace(id=123)
        self.bot = SimpleNamespace(
            guilds=[self.guild],
            get_guild=lambda guild_id: self.guild if guild_id == self.guild.id else None,
        )
        self.runtime = GamerHQSkillRuntime(self.bot)
        self.provider = ProviderSkill()
        self.consumer = ConsumerSkill()

    async def asyncTearDown(self):
        # No background scheduler is started in these tests, but close remains
        # safe/idempotent for future bootstrap changes.
        await self.runtime.close()
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_registration_context_binds_scheduler_and_public_api_once(self):
        self.runtime.add_skill(self.provider)
        self.runtime.add_skill(self.consumer)
        await self.runtime.initialize()
        await self.runtime.initialize()
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "register"],
            [("register", "provider")],
        )

        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="provider",
            enabled=True,
            version="1.0.0",
        )
        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="consumer",
            enabled=True,
            version="1.0.0",
        )

        await self.runtime.scheduler.upsert_job(
            guild_id=self.guild.id,
            skill_id="provider",
            key="job-1",
            handler_id="provider.tick.v1",
            schedule=OnceSchedule(200),
            payload={},
            now=100,
        )
        report = await self.runtime.scheduler.run_due(now=200)
        self.assertEqual(report.executed, 1)
        self.assertIn(("tick", self.guild.id, "provider", "job-1"), self.provider.calls)

        consumer_ctx = await self.runtime.context(self.guild.id, "consumer")
        response = await consumer_ctx.skills.call(
            skill_id="provider",
            contract_id="provider.status.v1",
            payload={"request": "status"},
        )
        self.assertEqual(dict(response), {"provider": "provider", "guild": self.guild.id})
        self.assertIn(
            ("api", self.guild.id, "provider", {"request": "status"}),
            self.provider.calls,
        )

    def test_host_rejects_skill_requiring_unimplemented_capability(self):
        with self.assertRaisesRegex(ValueError, "http.external"):
            self.runtime.add_skill(UnsupportedSkill())

    async def test_enable_skill_initializes_enables_and_starts_idempotently(self):
        self.runtime.add_skill(self.provider)
        self.assertTrue(await self.runtime.enable_skill(
            guild_id=self.guild.id,
            skill_id="provider",
        ))
        self.assertFalse(await self.runtime.enable_skill(
            guild_id=self.guild.id,
            skill_id="provider",
        ))
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "enable"],
            [("enable", self.guild.id)],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "start"],
            [("start", self.guild.id)],
        )

    async def test_disable_stops_skill_and_persists_disabled_state(self):
        self.runtime.add_skill(self.provider)
        await self.runtime.enable_skill(guild_id=self.guild.id, skill_id="provider")
        self.assertTrue(await self.runtime.disable_skill(
            guild_id=self.guild.id,
            skill_id="provider",
        ))
        self.assertFalse(await self.runtime.state.is_enabled(
            guild_id=self.guild.id,
            skill_id="provider",
        ))
        self.assertIn(("stop", self.guild.id), self.provider.calls)
        self.assertIn(("disable", self.guild.id), self.provider.calls)

    async def test_restore_reports_unknown_without_blocking_registered_skill(self):
        self.runtime.add_skill(self.provider)
        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="missing-skill",
            enabled=True,
            version="1.0.0",
        )
        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="provider",
            enabled=True,
            version="1.0.0",
        )
        report = await self.runtime.restore_guild(guild_id=self.guild.id)
        self.assertEqual(report.started, ("provider",))
        self.assertEqual(report.unknown, ("missing-skill",))
        self.assertEqual(report.failed, ())

    async def test_context_fails_closed_for_uncached_guild(self):
        self.runtime.add_skill(self.provider)
        with self.assertRaisesRegex(RuntimeError, "guild is unavailable"):
            await self.runtime.context(999, "provider")

    async def test_scheduler_background_start_stop_is_idempotent(self):
        self.runtime.add_skill(self.provider)
        self.assertTrue(await self.runtime.start_scheduler(poll_seconds=1))
        self.assertFalse(await self.runtime.start_scheduler(poll_seconds=1))
        self.assertTrue(await self.runtime.stop_scheduler())
        self.assertFalse(await self.runtime.stop_scheduler())


if __name__ == "__main__":
    unittest.main()
