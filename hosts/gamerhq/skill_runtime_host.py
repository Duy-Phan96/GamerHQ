"""Composition root connecting the portable Skill Runtime to GamerHQ."""
from __future__ import annotations

import logging
from collections.abc import Callable

from skill_runtime.contracts.context import SkillContext
from skill_runtime.runtime import EventBus, ScopedEventBus, ScopedScheduler, ScopedSkillApi, SkillApiRouter, SkillManager, SkillRegistry
from skill_runtime.runtime.scheduler import SchedulerEngine

from .skill_discord import DenyAllDiscordPolicy, DiscordResourcePolicy, GamerHQDiscordPort
from .skill_host import CapabilityPermissions, GamerHQSkillAudit, GamerHQSkillStateStore, GamerHQSkillStorage
from .skill_scheduler import GamerHQSchedulerStore


PolicyFactory = Callable[[int, str], DiscordResourcePolicy]


class GamerHQSkillRuntimeHost:
    """Own the concrete adapters while keeping Skills portable.

    Constructing this host does not enable Skills, mutate Discord or start a
    scheduler task. Lifecycle/startup integration remains explicit.
    """

    def __init__(self, bot, registry: SkillRegistry, *, policy_factory: PolicyFactory | None = None):
        self.bot = bot
        self.registry = registry
        self.state = GamerHQSkillStateStore()
        self.event_bus = EventBus(registry, availability=self.state.is_enabled)
        self.api_router = SkillApiRouter(registry, availability=self.state.is_enabled)
        self.scheduler_store = GamerHQSchedulerStore()
        self.scheduler = SchedulerEngine(registry, self.scheduler_store, availability=self.state.is_enabled)
        self.policy_factory = policy_factory or (lambda guild_id, skill_id: DenyAllDiscordPolicy())
        self.manager = SkillManager(registry, self.state, self.context)

    async def context(self, guild_id: int, skill_id: str) -> SkillContext:
        skill = self.registry.get(skill_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError("Guild is not available to the GamerHQ host.")
        permissions = CapabilityPermissions(skill.manifest.permissions)
        return SkillContext(
            guild_id=guild_id,
            skill_id=skill_id,
            discord=GamerHQDiscordPort(
                guild=guild,
                skill_id=skill_id,
                permissions=permissions,
                policy=self.policy_factory(guild_id, skill_id),
            ),
            events=ScopedEventBus(self.event_bus, guild_id=guild_id, skill_id=skill_id),
            scheduler=ScopedScheduler(self.scheduler, guild_id=guild_id, skill_id=skill_id),
            storage=GamerHQSkillStorage(guild_id=guild_id, skill_id=skill_id, permissions=permissions),
            audit=GamerHQSkillAudit(guild=guild, skill_id=skill_id, permissions=permissions),
            permissions=permissions,
            skills=ScopedSkillApi(self.api_router, guild_id=guild_id, skill_id=skill_id),
            logger=logging.getLogger(f"gamerhq.skill.{skill_id}"),
        )

    async def register(self) -> None:
        await self.manager.register_all()

    async def enable(self, *, guild_id: int, skill_id: str) -> bool:
        return await self.manager.enable(guild_id=guild_id, skill_id=skill_id)

    async def start(self, *, guild_id: int, skill_id: str) -> bool:
        return await self.manager.start(guild_id=guild_id, skill_id=skill_id)

    async def stop(self, *, guild_id: int, skill_id: str) -> bool:
        stopped = await self.manager.stop(guild_id=guild_id, skill_id=skill_id)
        await self.event_bus.unsubscribe_skill(guild_id=guild_id, skill_id=skill_id)
        return stopped

    async def disable(self, *, guild_id: int, skill_id: str) -> bool:
        changed = await self.manager.disable(guild_id=guild_id, skill_id=skill_id)
        await self.event_bus.unsubscribe_skill(guild_id=guild_id, skill_id=skill_id)
        return changed

    async def restore_guild(self, guild_id: int) -> tuple[str, ...]:
        return await self.manager.restore_guild(guild_id=guild_id)

    async def stop_guild(self, guild_id: int) -> tuple[str, ...]:
        stopped = await self.manager.stop_guild(guild_id=guild_id)
        for skill_id in stopped:
            await self.event_bus.unsubscribe_skill(guild_id=guild_id, skill_id=skill_id)
        return stopped
