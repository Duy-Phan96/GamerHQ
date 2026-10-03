import asyncio
import unittest
from types import SimpleNamespace

from skill_runtime.contracts.manifest import SkillManifest
from skill_runtime.contracts.lifecycle import SkillHealth
from skill_runtime.runtime.manager import SkillManager
from skill_runtime.runtime.registry import SkillRegistry


class FakeState:
    def __init__(self):
        self.rows = {}
        self.writes = []

    async def is_enabled(self, *, guild_id, skill_id):
        return self.rows.get((guild_id, skill_id), {}).get("enabled", False)

    async def set_enabled(self, *, guild_id, skill_id, enabled, version):
        self.rows[(guild_id, skill_id)] = {"enabled": enabled, "version": version}
        self.writes.append((guild_id, skill_id, enabled, version))

    async def enabled_skill_ids(self, *, guild_id):
        return tuple(sorted(skill_id for (gid, skill_id), row in self.rows.items() if gid == guild_id and row["enabled"]))


class FakeSkill:
    def __init__(self, skill_id="alpha", *, runtime_api_version="1"):
        self.manifest = SkillManifest(
            id=skill_id,
            name=skill_id.title(),
            version="1.0.0",
            runtime_api_version=runtime_api_version,
            description="fixture",
            author="test",
        )
        self.calls = []
        self.fail_enable = False

    async def register(self, ctx): self.calls.append(("register", ctx.skill_id))
    async def enable(self, ctx):
        self.calls.append(("enable", ctx.guild_id))
        if self.fail_enable: raise RuntimeError("synthetic")
    async def disable(self, ctx): self.calls.append(("disable", ctx.guild_id))
    async def start(self, ctx): self.calls.append(("start", ctx.guild_id))
    async def stop(self, ctx): self.calls.append(("stop", ctx.guild_id))
    async def health_check(self, ctx): return SkillHealth("PASS", f"guild={ctx.guild_id}")


async def context_factory(guild_id, skill_id):
    return SimpleNamespace(guild_id=guild_id, skill_id=skill_id)


async def registration_context_factory(skill_id):
    return SimpleNamespace(skill_id=skill_id)


class RegistryTests(unittest.TestCase):
    def test_registration_is_sorted_and_duplicate_id_rejected(self):
        registry = SkillRegistry()
        registry.register(FakeSkill("beta"))
        registry.register(FakeSkill("alpha"))
        self.assertEqual(registry.ids(), ("alpha", "beta"))
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(FakeSkill("alpha"))

    def test_registry_revalidates_supported_runtime_api(self):
        skill = FakeSkill("future")
        registry = SkillRegistry(supported_api_versions=frozenset({"2"}))
        with self.assertRaisesRegex(ValueError, "Unsupported Skill Runtime API"):
            registry.register(skill)


class SkillManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.skill = FakeSkill()
        self.registry = SkillRegistry()
        self.registry.register(self.skill)
        self.state = FakeState()
        self.manager = SkillManager(
            self.registry,
            self.state,
            context_factory,
            registration_context_factory,
        )

    async def test_registration_runs_once(self):
        await self.manager.register_all()
        await self.manager.register_all()
        self.assertEqual(self.skill.calls, [("register", "alpha")])

    async def test_enable_is_per_guild_and_idempotent(self):
        self.assertTrue(await self.manager.enable(guild_id=1, skill_id="alpha"))
        self.assertFalse(await self.manager.enable(guild_id=1, skill_id="alpha"))
        self.assertTrue(await self.manager.enable(guild_id=2, skill_id="alpha"))
        self.assertEqual([c for c in self.skill.calls if c[0] == "enable"], [("enable", 1), ("enable", 2)])

    async def test_failed_enable_is_not_persisted_enabled(self):
        self.skill.fail_enable = True
        with self.assertRaises(RuntimeError):
            await self.manager.enable(guild_id=1, skill_id="alpha")
        self.assertFalse(await self.state.is_enabled(guild_id=1, skill_id="alpha"))
        self.assertEqual(self.state.writes, [])

    async def test_disabled_skill_does_not_start(self):
        self.assertFalse(await self.manager.start(guild_id=1, skill_id="alpha"))
        self.assertFalse(self.manager.is_running(guild_id=1, skill_id="alpha"))
        self.assertNotIn(("start", 1), self.skill.calls)

    async def test_enabled_skill_starts_only_once(self):
        await self.manager.enable(guild_id=1, skill_id="alpha")
        self.assertTrue(await self.manager.start(guild_id=1, skill_id="alpha"))
        self.assertFalse(await self.manager.start(guild_id=1, skill_id="alpha"))
        self.assertEqual([c for c in self.skill.calls if c[0] == "start"], [("start", 1)])

    async def test_disable_stops_running_skill_before_disabling(self):
        await self.manager.enable(guild_id=1, skill_id="alpha")
        await self.manager.start(guild_id=1, skill_id="alpha")
        self.skill.calls.clear()
        self.assertTrue(await self.manager.disable(guild_id=1, skill_id="alpha"))
        self.assertEqual(self.skill.calls, [("stop", 1), ("disable", 1)])
        self.assertFalse(await self.state.is_enabled(guild_id=1, skill_id="alpha"))

    async def test_restore_guild_starts_persisted_enabled_skills_after_restart(self):
        beta = FakeSkill("beta")
        self.registry.register(beta)
        await self.state.set_enabled(guild_id=7, skill_id="alpha", enabled=True, version="1.0.0")
        await self.state.set_enabled(guild_id=7, skill_id="beta", enabled=True, version="1.0.0")
        restored = await self.manager.restore_guild(guild_id=7)
        self.assertEqual(restored, ("alpha", "beta"))
        self.assertTrue(self.manager.is_running(guild_id=7, skill_id="alpha"))
        self.assertTrue(self.manager.is_running(guild_id=7, skill_id="beta"))
        self.assertEqual(await self.manager.restore_guild(guild_id=7), ())

    async def test_unknown_persisted_skill_fails_closed(self):
        await self.state.set_enabled(guild_id=7, skill_id="missing", enabled=True, version="1.0.0")
        with self.assertRaisesRegex(KeyError, "not registered"):
            await self.manager.restore_guild(guild_id=7)

    async def test_health_reports_disabled_without_calling_skill(self):
        health = await self.manager.health(guild_id=1, skill_id="alpha")
        self.assertEqual(health.state, "DISABLED")
        await self.manager.enable(guild_id=1, skill_id="alpha")
        health = await self.manager.health(guild_id=1, skill_id="alpha")
        self.assertEqual(health.state, "PASS")

    async def test_concurrent_enable_is_coalesced_by_guild_skill_lock(self):
        results = await asyncio.gather(*(
            self.manager.enable(guild_id=1, skill_id="alpha") for _ in range(5)
        ))
        self.assertEqual(sum(results), 1)
        self.assertEqual([c for c in self.skill.calls if c[0] == "enable"], [("enable", 1)])


if __name__ == "__main__":
    unittest.main()
