import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from database import db
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.lifecycle import SkillHealth
from skill_runtime.contracts.manifest import SkillManifest
from skill_runtime.contracts.schedule import OnceSchedule


class RegistrationFixtureSkill:
    manifest = SkillManifest(
        id="registration-fixture",
        name="Registration Fixture",
        version="1.0.0",
        runtime_api_version="1",
        description="fixture",
        author="test",
        permissions=(SkillCapability.SCHEDULER_JOBS.value,),
    )

    def __init__(self):
        self.calls = []

    async def register(self, ctx):
        self.calls.append(("register", ctx.skill_id))
        ctx.scheduler.register_handler("registration-fixture.execute.v1", self.execute)

    async def execute(self, ctx, job):
        self.calls.append(("execute", ctx.guild_id, ctx.skill_id, job.key))

    async def enable(self, ctx): pass
    async def disable(self, ctx): pass
    async def start(self, ctx): pass
    async def stop(self, ctx): pass
    async def health_check(self, ctx): return SkillHealth("PASS", "ok")


class SkillRuntimeRegistrationTests(unittest.IsolatedAsyncioTestCase):
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
        self.skill = RegistrationFixtureSkill()
        self.runtime.register(self.skill)

    async def asyncTearDown(self):
        await self.runtime.close()
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_registration_runs_once_and_handler_receives_guild_context(self):
        await self.runtime.register_all()
        await self.runtime.register_all()
        self.assertEqual(
            [call for call in self.skill.calls if call[0] == "register"],
            [("register", "registration-fixture")],
        )

        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="registration-fixture",
            enabled=True,
            version="1.0.0",
        )
        await self.runtime.scheduler.upsert_job(
            guild_id=self.guild.id,
            skill_id="registration-fixture",
            key="fixture",
            handler_id="registration-fixture.execute.v1",
            schedule=OnceSchedule(200),
            payload={},
            now=100,
        )
        report = await self.runtime.scheduler.run_due(now=200)
        self.assertEqual(report.executed, 1)
        self.assertIn(
            ("execute", self.guild.id, "registration-fixture", "fixture"),
            self.skill.calls,
        )

    async def test_unknown_persisted_skill_does_not_block_registered_restore(self):
        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="missing-skill",
            enabled=True,
            version="1.0.0",
        )
        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="registration-fixture",
            enabled=True,
            version="1.0.0",
        )
        started = await self.runtime.restore_guild(guild_id=self.guild.id)
        self.assertEqual(started, ("registration-fixture",))

    async def test_scheduler_background_lifecycle_is_idempotent(self):
        self.assertTrue(await self.runtime.start_scheduler(poll_seconds=1))
        self.assertFalse(await self.runtime.start_scheduler(poll_seconds=1))
        self.assertTrue(await self.runtime.stop_scheduler())
        self.assertFalse(await self.runtime.stop_scheduler())


if __name__ == "__main__":
    unittest.main()
