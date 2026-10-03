import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from database import db
from hosts.gamerhq.runtime import GamerHQSkillRuntime
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.lifecycle import SkillHealth
from skill_runtime.contracts.manifest import SkillManifest


class FakeSkill:
    def __init__(self):
        self.manifest = SkillManifest(
            id="demo-skill",
            name="Demo Skill",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(
                SkillCapability.STORAGE_SKILL.value,
                SkillCapability.SCHEDULER_JOBS.value,
            ),
        )
        self.calls = []

    async def register(self): self.calls.append(("register", None))
    async def enable(self, ctx): self.calls.append(("enable", ctx.guild_id))
    async def disable(self, ctx): self.calls.append(("disable", ctx.guild_id))
    async def start(self, ctx): self.calls.append(("start", ctx.guild_id))
    async def stop(self, ctx): self.calls.append(("stop", ctx.guild_id))
    async def health_check(self, ctx): return SkillHealth("PASS", "ok")


class GamerHQSkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "runtime.db")
        self.db_patch.start()
        db.init_db()
        self.guild = SimpleNamespace(id=1, me=SimpleNamespace(id=99))
        self.bot = SimpleNamespace(
            guilds=[self.guild],
            get_guild=lambda guild_id: self.guild if guild_id == 1 else None,
        )
        self.skill = FakeSkill()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_setup_is_idempotent_and_starts_one_scheduler_task(self):
        runtime = GamerHQSkillRuntime(self.bot, skills=(self.skill,))
        await runtime.setup()
        first = runtime._scheduler_task
        await runtime.setup()
        self.assertIs(first, runtime._scheduler_task)
        self.assertTrue(runtime.scheduler_running)
        self.assertEqual(self.skill.calls, [("register", None)])
        await runtime.close()
        self.assertFalse(runtime.scheduler_running)

    async def test_context_is_scoped_and_uses_host_adapters(self):
        runtime = GamerHQSkillRuntime(self.bot, skills=(self.skill,))
        ctx = await runtime.context(1, "demo-skill")
        self.assertEqual(ctx.guild_id, 1)
        self.assertEqual(ctx.skill_id, "demo-skill")
        self.assertTrue(ctx.permissions.allows(SkillCapability.STORAGE_SKILL.value))
        await ctx.storage.set("demo", {"ok": True})
        self.assertEqual(await ctx.storage.get("demo"), {"ok": True})

    async def test_enable_restore_and_close_follow_skill_lifecycle(self):
        runtime = GamerHQSkillRuntime(self.bot, skills=(self.skill,))
        await runtime.setup()
        await runtime.manager.enable(guild_id=1, skill_id="demo-skill")
        restored = await runtime.restore_guild(1)
        self.assertEqual(restored, ("demo-skill",))
        self.assertTrue(runtime.manager.is_running(guild_id=1, skill_id="demo-skill"))
        await runtime.close()
        self.assertIn(("stop", 1), self.skill.calls)

    async def test_unknown_guild_fails_closed(self):
        runtime = GamerHQSkillRuntime(self.bot, skills=(self.skill,))
        with self.assertRaisesRegex(RuntimeError, "guild is unavailable"):
            await runtime.context(999, "demo-skill")

    async def test_scheduler_worker_stops_without_background_work(self):
        runtime = GamerHQSkillRuntime(self.bot, skills=(self.skill,))
        await runtime.setup()
        task = runtime._scheduler_task
        self.assertFalse(task.done())
        await runtime.close()
        self.assertTrue(task.done())


if __name__ == "__main__":
    unittest.main()
