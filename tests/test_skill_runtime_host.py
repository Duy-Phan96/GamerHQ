import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hosts.gamerhq.skill_discord import DenyAllDiscordPolicy
from hosts.gamerhq.skill_runtime_host import GamerHQSkillRuntimeHost
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.manifest import SkillManifest
from skill_runtime.runtime.registry import SkillRegistry


class FakeSkill:
    def __init__(self):
        self.manifest=SkillManifest(
            id="fixture-skill",
            name="Fixture Skill",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(
                SkillCapability.STORAGE_SKILL.value,
                SkillCapability.SCHEDULER_JOBS.value,
            ),
        )
        self.registered=0

    async def register(self): self.registered+=1
    async def enable(self,ctx): pass
    async def disable(self,ctx): pass
    async def start(self,ctx): pass
    async def stop(self,ctx): pass
    async def health_check(self,ctx): return SimpleNamespace(state="PASS",detail="")


class SkillRuntimeHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guild=SimpleNamespace(id=123,me=SimpleNamespace(id=900),get_channel=lambda cid:None)
        self.bot=SimpleNamespace(get_guild=lambda gid:self.guild if gid==123 else None)
        self.registry=SkillRegistry()
        self.skill=FakeSkill()
        self.registry.register(self.skill)

    async def test_context_composes_portable_runtime_from_gamerhq_adapters(self):
        host=GamerHQSkillRuntimeHost(self.bot,self.registry)
        ctx=await host.context(123,"fixture-skill")
        self.assertEqual(ctx.guild_id,123)
        self.assertEqual(ctx.skill_id,"fixture-skill")
        self.assertTrue(ctx.permissions.allows(SkillCapability.STORAGE_SKILL.value))
        self.assertIsInstance(ctx.discord.policy,DenyAllDiscordPolicy)
        self.assertEqual(ctx.storage.skill_id,"fixture-skill")
        self.assertEqual(ctx.scheduler.skill_id,"fixture-skill")
        self.assertEqual(ctx.events.skill_id,"fixture-skill")
        self.assertEqual(ctx.skills.skill_id,"fixture-skill")

    async def test_unknown_guild_or_skill_fails_closed(self):
        host=GamerHQSkillRuntimeHost(self.bot,self.registry)
        with self.assertRaisesRegex(ValueError,"Guild is not available"):
            await host.context(999,"fixture-skill")
        with self.assertRaisesRegex(KeyError,"not registered"):
            await host.context(123,"missing-skill")

    async def test_register_is_idempotent_through_skill_manager(self):
        host=GamerHQSkillRuntimeHost(self.bot,self.registry)
        await host.register()
        await host.register()
        self.assertEqual(self.skill.registered,1)

    async def test_stop_and_disable_cleanup_guild_event_subscriptions(self):
        host=GamerHQSkillRuntimeHost(self.bot,self.registry)
        host.event_bus.unsubscribe_skill=AsyncMock(return_value=0)
        host.manager.stop=AsyncMock(return_value=False)
        host.manager.disable=AsyncMock(return_value=False)

        await host.stop(guild_id=123,skill_id="fixture-skill")
        await host.disable(guild_id=123,skill_id="fixture-skill")

        self.assertEqual(host.event_bus.unsubscribe_skill.await_count,2)
        for call in host.event_bus.unsubscribe_skill.await_args_list:
            self.assertEqual(call.kwargs,{"guild_id":123,"skill_id":"fixture-skill"})

    async def test_restore_reports_unknown_and_failed_skills_without_blocking_known(self):
        host=GamerHQSkillRuntimeHost(self.bot,self.registry)
        host.state.enabled_skill_ids=AsyncMock(return_value=("fixture-skill","missing-skill"))
        host.manager.start=AsyncMock(side_effect=RuntimeError("synthetic start failure"))

        report=await host.restore_guild(123)

        self.assertEqual(report.started,())
        self.assertEqual(report.unavailable,("missing-skill",))
        self.assertEqual(report.failed,("fixture-skill",))
        host.manager.start.assert_awaited_once_with(guild_id=123,skill_id="fixture-skill")

    async def test_policy_factory_is_bound_per_guild_and_skill(self):
        calls=[]
        class Allow:
            def allows(self,**kwargs): return True
        def factory(guild_id,skill_id):
            calls.append((guild_id,skill_id))
            return Allow()
        host=GamerHQSkillRuntimeHost(self.bot,self.registry,policy_factory=factory)
        ctx=await host.context(123,"fixture-skill")
        self.assertEqual(calls,[(123,"fixture-skill")])
        self.assertTrue(ctx.discord.policy.allows(
            guild_id=123,skill_id="fixture-skill",channel_id=1,operation="messages.send"
        ))


if __name__=="__main__":
    unittest.main()
