"""GamerHQ host orchestration for the portable Skill Runtime."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from skill_runtime import (
    EventBus,
    ScopedEventBus,
    ScopedScheduler,
    ScopedSkillApi,
    SkillApiRouter,
    SkillContext,
    SkillManager,
    SkillRegistry,
)
from skill_runtime.runtime.scheduler import SchedulerEngine

from .discord_host import GamerHQDiscordAdapter
from .scheduler_host import GamerHQSkillJobStore
from .skill_host import (
    CapabilityPermissions,
    GamerHQSkillAudit,
    GamerHQSkillStateStore,
    GamerHQSkillStorage,
)


class GamerHQSkillRuntime:
    """One process-level Runtime host shared by every enabled Skill."""

    def __init__(self, bot, *, skills: Iterable = ()):
        self.bot = bot
        self.registry = SkillRegistry()
        for skill in skills:
            self.registry.register(skill)

        self.state = GamerHQSkillStateStore()
        self.events = EventBus(self.registry, availability=self.state.is_enabled)
        self.apis = SkillApiRouter(self.registry, availability=self.state.is_enabled)
        self.job_store = GamerHQSkillJobStore()
        self.scheduler = SchedulerEngine(
            self.registry,
            self.job_store,
            availability=self.state.is_enabled,
        )
        self.manager = SkillManager(self.registry, self.state, self.context)
        self._stop = asyncio.Event()
        self._scheduler_task = None
        self._setup_complete = False
        self.log = logging.getLogger("gamerhq.skill-runtime")

    @property
    def scheduler_running(self) -> bool:
        return bool(self._scheduler_task and not self._scheduler_task.done())

    async def setup(self) -> None:
        if self._setup_complete:
            return
        await self.manager.register_all()
        self._stop.clear()
        self._scheduler_task = asyncio.create_task(
            self.scheduler.serve(self._stop),
            name="gamerhq-skill-scheduler",
        )
        self._setup_complete = True

    async def context(self, guild_id: int, skill_id: str) -> SkillContext:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("Skill guild is unavailable.")
        skill = self.registry.get(skill_id)
        permissions = CapabilityPermissions(skill.manifest.permissions)
        return SkillContext(
            guild_id=guild_id,
            skill_id=skill_id,
            discord=GamerHQDiscordAdapter(guild=guild, permissions=permissions),
            events=ScopedEventBus(self.events, guild_id=guild_id, skill_id=skill_id),
            scheduler=ScopedScheduler(self.scheduler, guild_id=guild_id, skill_id=skill_id),
            storage=GamerHQSkillStorage(
                guild_id=guild_id,
                skill_id=skill_id,
                permissions=permissions,
            ),
            audit=GamerHQSkillAudit(
                guild=guild,
                skill_id=skill_id,
                permissions=permissions,
            ),
            permissions=permissions,
            skills=ScopedSkillApi(self.apis, guild_id=guild_id, skill_id=skill_id),
            logger=logging.getLogger(f"gamerhq.skill.{skill_id}"),
        )

    async def restore_guild(self, guild_id: int) -> tuple[str, ...]:
        if not self._setup_complete:
            raise RuntimeError("Skill Runtime is not set up.")
        return await self.manager.restore_guild(guild_id=guild_id)

    async def close(self) -> None:
        if not self._setup_complete:
            return
        self._stop.set()
        if self._scheduler_task:
            try:
                await self._scheduler_task
            finally:
                self._scheduler_task = None
        for guild in tuple(getattr(self.bot, "guilds", ())):
            await self.manager.stop_guild(guild_id=guild.id)
        self._setup_complete = False
