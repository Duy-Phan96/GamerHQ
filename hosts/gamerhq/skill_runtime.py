"""Composition root for the portable Skill Runtime inside GamerHQ."""
from __future__ import annotations

from dataclasses import dataclass
import logging

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.context import SkillContext
from skill_runtime.contracts.errors import CapabilityUnavailableError, ResourceNotFoundError
from skill_runtime.runtime.api_router import SkillApiRouter
from skill_runtime.runtime.event_bus import EventBus
from skill_runtime.runtime.manager import SkillManager
from skill_runtime.runtime.registry import SkillRegistry
from skill_runtime.runtime.scheduler import SchedulerEngine, ScopedScheduler
from skill_runtime.runtime.scoped import ScopedEventBus, ScopedSkillApi

from .skill_discord import DISCORD_HOST_CAPABILITIES, GamerHQDiscordAdapter
from .skill_host import (
    CapabilityPermissions,
    GamerHQSkillAudit,
    GamerHQSkillStateStore,
    GamerHQSkillStorage,
)
from .skill_scheduler import GamerHQSchedulerStore


HOST_CAPABILITIES = frozenset({
    *DISCORD_HOST_CAPABILITIES,
    SkillCapability.SCHEDULER_JOBS.value,
    SkillCapability.STORAGE_SKILL.value,
    SkillCapability.EVENTS_EMIT.value,
    SkillCapability.EVENTS_SUBSCRIBE.value,
    SkillCapability.SKILL_API_CALL.value,
    SkillCapability.AUDIT_WRITE.value,
})


@dataclass(frozen=True, slots=True)
class GuildSkillStatus:
    skill_id: str
    name: str
    version: str
    description: str
    enabled: bool
    running: bool
    health: str
    health_detail: str
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]


class GamerHQSkillRuntime:
    """Owns one process-local Runtime and GamerHQ's host adapters."""

    def __init__(self, bot):
        self.bot = bot
        self.registry = SkillRegistry()
        self.state = GamerHQSkillStateStore()
        self.scheduler_store = GamerHQSchedulerStore()
        self.events = EventBus(self.registry, availability=self.state.is_enabled)
        self.apis = SkillApiRouter(self.registry, availability=self.state.is_enabled)
        self.scheduler = SchedulerEngine(
            self.registry,
            self.scheduler_store,
            availability=self.state.is_enabled,
        )
        self.manager = SkillManager(self.registry, self.state, self.context)

    def register(self, skill) -> None:
        self.registry.register(skill)

    async def register_all(self) -> None:
        await self.manager.register_all()

    def _guild(self, guild_id: int):
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            raise ResourceNotFoundError("Guild is not available to the GamerHQ host.")
        return guild

    def permissions(self, skill_id: str) -> CapabilityPermissions:
        skill = self.registry.get(skill_id)
        return CapabilityPermissions(skill.manifest.permissions, available=HOST_CAPABILITIES)

    async def context(self, guild_id: int, skill_id: str) -> SkillContext:
        guild = self._guild(guild_id)
        permissions = self.permissions(skill_id)
        permissions.require_all_declared()
        return SkillContext(
            guild_id=guild.id,
            skill_id=skill_id,
            discord=GamerHQDiscordAdapter(
                guild=guild,
                skill_id=skill_id,
                permissions=permissions,
            ),
            events=ScopedEventBus(
                self.events,
                guild_id=guild.id,
                skill_id=skill_id,
            ),
            scheduler=ScopedScheduler(
                self.scheduler,
                guild_id=guild.id,
                skill_id=skill_id,
            ),
            storage=GamerHQSkillStorage(
                guild_id=guild.id,
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
                guild_id=guild.id,
                skill_id=skill_id,
            ),
            logger=logging.getLogger(f"gamerhq.skill.{skill_id}"),
        )

    async def enable_skill(self, *, guild_id: int, skill_id: str) -> bool:
        # Context creation validates every required capability before lifecycle
        # code or persistent enabled state is touched.
        await self.context(guild_id, skill_id)
        changed = await self.manager.enable(guild_id=guild_id, skill_id=skill_id)
        try:
            await self.manager.start(guild_id=guild_id, skill_id=skill_id)
        except Exception:
            if changed:
                await self.manager.disable(guild_id=guild_id, skill_id=skill_id)
            raise
        return changed

    async def disable_skill(self, *, guild_id: int, skill_id: str) -> bool:
        return await self.manager.disable(guild_id=guild_id, skill_id=skill_id)

    async def restore_guild(self, *, guild_id: int) -> tuple[str, ...]:
        return await self.manager.restore_guild(guild_id=guild_id)

    async def status(self, *, guild_id: int, skill_id: str) -> GuildSkillStatus:
        skill = self.registry.get(skill_id)
        permissions = self.permissions(skill_id)
        missing = permissions.missing_declared()
        enabled = await self.state.is_enabled(guild_id=guild_id, skill_id=skill_id)
        running = self.manager.is_running(guild_id=guild_id, skill_id=skill_id)

        if missing:
            health = "UNAVAILABLE"
            detail = "Required host capabilities are unavailable."
        elif not enabled:
            health = "DISABLED"
            detail = "Skill is disabled for this guild."
        else:
            try:
                report = await self.manager.health(guild_id=guild_id, skill_id=skill_id)
            except CapabilityUnavailableError:
                health = "UNAVAILABLE"
                detail = "Required host capabilities are unavailable."
            except Exception:
                health = "ERROR"
                detail = "Skill health check failed."
            else:
                health = report.state
                detail = report.detail

        return GuildSkillStatus(
            skill_id=skill.manifest.id,
            name=skill.manifest.name,
            version=skill.manifest.version,
            description=skill.manifest.description,
            enabled=enabled,
            running=running,
            health=health,
            health_detail=detail,
            required_capabilities=tuple(skill.manifest.permissions),
            missing_capabilities=missing,
        )

    async def statuses(self, *, guild_id: int) -> tuple[GuildSkillStatus, ...]:
        result = []
        for skill in self.registry.all():
            result.append(await self.status(guild_id=guild_id, skill_id=skill.manifest.id))
        return tuple(result)
