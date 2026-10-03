"""GamerHQ composition root for the portable Skill Runtime.

This module is host-specific by design. Portable Skill code must never import it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.context import SkillContext, SkillRegistrationContext
from skill_runtime.runtime import (
    EventBus,
    ScopedEventBus,
    ScopedScheduler,
    ScopedSkillApi,
    SkillApiRouter,
    SkillManager,
    SkillRegistry,
    SchedulerEngine,
)
from skill_runtime.runtime.registration import (
    ScopedSchedulerRegistration,
    ScopedSkillApiRegistration,
)

from .skill_discord import GamerHQDiscordPort
from .skill_host import (
    CapabilityPermissions,
    GamerHQSkillAudit,
    GamerHQSkillStateStore,
    GamerHQSkillStorage,
)
from .skill_scheduler import GamerHQSchedulerStore


HOST_CAPABILITIES = frozenset({
    SkillCapability.DISCORD_MESSAGES_SEND.value,
    SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
    SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
    SkillCapability.DISCORD_EMBEDS_SEND.value,
    SkillCapability.DISCORD_MENTIONS_EVERYONE.value,
    SkillCapability.SCHEDULER_JOBS.value,
    SkillCapability.STORAGE_SKILL.value,
    SkillCapability.EVENTS_EMIT.value,
    SkillCapability.EVENTS_SUBSCRIBE.value,
    SkillCapability.SKILL_API_CALL.value,
    SkillCapability.AUDIT_WRITE.value,
})


@dataclass(frozen=True, slots=True)
class RestoreReport:
    started: tuple[str, ...]
    unknown: tuple[str, ...]
    failed: tuple[str, ...]


class GamerHQSkillRuntime:
    """Composes portable Runtime services with GamerHQ host adapters."""

    def __init__(self, bot):
        self.bot = bot
        self.registry = SkillRegistry()
        self.state = GamerHQSkillStateStore()
        self.events = EventBus(self.registry, availability=self.state.is_enabled)
        self.apis = SkillApiRouter(self.registry, availability=self.state.is_enabled)
        self.scheduler_store = GamerHQSchedulerStore()
        self.scheduler = SchedulerEngine(
            self.registry,
            self.scheduler_store,
            availability=self.state.is_enabled,
        )
        self.manager = SkillManager(
            self.registry,
            self.state,
            self.context,
            self.registration_context,
        )
        self.log = logging.getLogger("gamerhq.skills")
        self._scheduler_stop = asyncio.Event()
        self._scheduler_task: asyncio.Task | None = None

    def add_skill(self, skill) -> None:
        missing = sorted(set(skill.manifest.permissions) - HOST_CAPABILITIES)
        if missing:
            raise ValueError(
                "GamerHQ host does not provide required Skill capabilities: "
                + ", ".join(missing)
            )
        self.registry.register(skill)

    async def context(self, guild_id: int, skill_id: str) -> SkillContext:
        skill = self.registry.get(skill_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("Skill guild is unavailable in the host cache.")
        permissions = CapabilityPermissions(
            skill.manifest.permissions,
            available=HOST_CAPABILITIES,
        )
        return SkillContext(
            guild_id=guild_id,
            skill_id=skill_id,
            discord=GamerHQDiscordPort(
                guild=guild,
                skill_id=skill_id,
                permissions=permissions,
            ),
            events=ScopedEventBus(
                self.events,
                guild_id=guild_id,
                skill_id=skill_id,
            ),
            scheduler=ScopedScheduler(
                self.scheduler,
                guild_id=guild_id,
                skill_id=skill_id,
            ),
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
            skills=ScopedSkillApi(
                self.apis,
                guild_id=guild_id,
                skill_id=skill_id,
            ),
            logger=logging.getLogger(f"gamerhq.skill.{skill_id}"),
        )

    async def registration_context(self, skill_id: str) -> SkillRegistrationContext:
        self.registry.get(skill_id)
        return SkillRegistrationContext(
            skill_id=skill_id,
            scheduler=ScopedSchedulerRegistration(
                self.scheduler,
                skill_id=skill_id,
                context_factory=self.context,
            ),
            skills=ScopedSkillApiRegistration(
                self.apis,
                skill_id=skill_id,
                context_factory=self.context,
            ),
            logger=logging.getLogger(f"gamerhq.skill.{skill_id}"),
        )

    async def initialize(self) -> None:
        await self.manager.register_all()

    async def enable_skill(self, *, guild_id: int, skill_id: str) -> bool:
        await self.initialize()
        changed = await self.manager.enable(guild_id=guild_id, skill_id=skill_id)
        await self.manager.start(guild_id=guild_id, skill_id=skill_id)
        return changed

    async def disable_skill(self, *, guild_id: int, skill_id: str) -> bool:
        changed = await self.manager.disable(guild_id=guild_id, skill_id=skill_id)
        await self.events.unsubscribe_skill(guild_id=guild_id, skill_id=skill_id)
        return changed

    async def restore_guild(self, *, guild_id: int) -> RestoreReport:
        """Start persisted enabled Skills without letting one stale entry block others."""
        await self.initialize()
        started: list[str] = []
        unknown: list[str] = []
        failed: list[str] = []
        for skill_id in await self.state.enabled_skill_ids(guild_id=guild_id):
            if not self.registry.contains(skill_id):
                unknown.append(skill_id)
                continue
            try:
                if await self.manager.start(guild_id=guild_id, skill_id=skill_id):
                    started.append(skill_id)
            except Exception:
                failed.append(skill_id)
                self.log.exception(
                    "Skill restore failed guild=%s skill=%s",
                    guild_id,
                    skill_id,
                )
        return RestoreReport(
            started=tuple(started),
            unknown=tuple(unknown),
            failed=tuple(failed),
        )

    async def stop_guild(self, *, guild_id: int) -> tuple[str, ...]:
        stopped = await self.manager.stop_guild(guild_id=guild_id)
        for skill_id in self.registry.ids():
            await self.events.unsubscribe_skill(guild_id=guild_id, skill_id=skill_id)
        return stopped

    async def start_scheduler(self, *, poll_seconds: float = 15.0) -> bool:
        await self.initialize()
        if self._scheduler_task and not self._scheduler_task.done():
            return False
        self._scheduler_stop = asyncio.Event()
        self._scheduler_task = asyncio.create_task(
            self.scheduler.serve(
                self._scheduler_stop,
                poll_seconds=poll_seconds,
            ),
            name="gamerhq-skill-scheduler",
        )
        return True

    async def stop_scheduler(self) -> bool:
        task = self._scheduler_task
        if task is None:
            return False
        self._scheduler_stop.set()
        try:
            await task
        finally:
            self._scheduler_task = None
        return True

    async def close(self) -> None:
        await self.stop_scheduler()
        for guild in tuple(getattr(self.bot, "guilds", ())):
            try:
                await self.stop_guild(guild_id=guild.id)
            except Exception:
                self.log.exception("Skill shutdown failed guild=%s", guild.id)
