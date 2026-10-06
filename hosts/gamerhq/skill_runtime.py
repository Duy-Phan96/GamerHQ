"""Composition root for the portable Skill Runtime inside GamerHQ."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.context import SkillContext, SkillRegistrationContext
from skill_runtime.contracts.errors import CapabilityUnavailableError, ResourceNotFoundError
from skill_runtime.runtime.api_router import SkillApiRouter
from skill_runtime.runtime.event_bus import EventBus
from skill_runtime.runtime.management_router import SkillManagementRouter
from skill_runtime.runtime.manager import SkillManager
from skill_runtime.runtime.registry import SkillRegistry
from skill_runtime.runtime.scheduler import SchedulerEngine, ScopedScheduler
from skill_runtime.runtime.scoped import ScopedEventBus, ScopedSkillApi
from skill_runtime.runtime.registration import ScopedSchedulerRegistration, ScopedSkillApiRegistration, ScopedSkillManagementRegistration

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
    installed: bool
    enabled: bool
    running: bool
    health: str
    health_detail: str
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    source_kind: str = "built-in"
    source_distribution: str | None = None
    management_available: bool = False
    management_schema_available: bool = False


class GamerHQSkillRuntime:
    """Owns one process-local Runtime and GamerHQ's host adapters."""

    def __init__(self, bot):
        self.bot = bot
        self.registry = SkillRegistry()
        self.state = GamerHQSkillStateStore()
        self.scheduler_store = GamerHQSchedulerStore()
        self.events = EventBus(self.registry, availability=self.state.is_enabled)
        self.apis = SkillApiRouter(self.registry, availability=self.state.is_enabled)
        self.management = SkillManagementRouter(
            self.registry,
            availability=self.state.is_enabled,
        )
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
        self._sources: dict[str, tuple[str, str | None]] = {}
        self._unavailable_external: dict[str, str] = {}

    def register(
        self,
        skill,
        *,
        source_kind: str = "built-in",
        source_distribution: str | None = None,
    ) -> None:
        if source_kind not in {"built-in", "external"}:
            raise ValueError("Skill source kind must be built-in or external.")
        self.registry.register(skill)
        self._sources[skill.manifest.id] = (source_kind, source_distribution)
        self.clear_external_unavailable(skill.manifest.id)

    def source(self, skill_id: str) -> tuple[str, str | None]:
        self.registry.get(skill_id)
        return self._sources.get(skill_id, ("built-in", None))

    def record_external_unavailable(self, skill_id: str) -> None:
        if self.registry.contains(skill_id):
            return
        self._unavailable_external[skill_id] = "package_unavailable"

    def clear_external_unavailable(self, skill_id: str) -> None:
        self._unavailable_external.pop(skill_id, None)

    def _unavailable_status(self, skill_id: str) -> GuildSkillStatus:
        return GuildSkillStatus(
            skill_id=skill_id,
            name=skill_id.replace("-", " ").title(),
            version="unknown",
            description="Configured external Skill package is unavailable.",
            installed=False,
            enabled=False,
            running=False,
            health="UNAVAILABLE",
            health_detail="The configured external Skill package could not be loaded.",
            required_capabilities=(),
            missing_capabilities=(),
            source_kind="external",
            source_distribution=None,
            management_available=False,
            management_schema_available=False,
        )

    async def register_all(self) -> None:
        await self.manager.register_all()

    async def registration_context(self, skill_id: str) -> SkillRegistrationContext:
        self.registry.get(skill_id)
        return SkillRegistrationContext(
            skill_id=skill_id,
            scheduler=ScopedSchedulerRegistration(
                self.scheduler,
                skill_id=skill_id,
                context_factory=self.context,
            ),
            management=ScopedSkillManagementRegistration(
                self.management,
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

    def management_ui_schema(self, skill_id: str):
        """Return only the portable declarative UI contract for one registered Skill."""
        skill = self.registry.get(skill_id)
        return skill.manifest.management_ui

    async def call_management(
        self,
        *,
        guild_id: int,
        skill_id: str,
        contract_id: str,
        payload,
    ):
        return await self.management.call(
            guild_id=guild_id,
            skill_id=skill_id,
            contract_id=contract_id,
            payload=payload,
        )

    async def install_skill(self, *, guild_id: int, skill_id: str) -> bool:
        skill = self.registry.get(skill_id)
        return await self.state.install(
            guild_id=guild_id,
            skill_id=skill_id,
            version=skill.manifest.version,
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
        changed = await self.manager.disable(guild_id=guild_id, skill_id=skill_id)
        await self.events.unsubscribe_skill(guild_id=guild_id, skill_id=skill_id)
        return changed

    async def restore_guild(self, *, guild_id: int) -> tuple[str, ...]:
        started: list[str] = []
        for skill_id in await self.state.enabled_skill_ids(guild_id=guild_id):
            if not self.registry.contains(skill_id):
                self.log.warning(
                    "Persisted Skill is not registered guild=%s skill=%s",
                    guild_id,
                    skill_id,
                )
                continue
            try:
                if await self.manager.start(guild_id=guild_id, skill_id=skill_id):
                    started.append(skill_id)
            except Exception:
                self.log.exception(
                    "Skill restore failed guild=%s skill=%s",
                    guild_id,
                    skill_id,
                )
        return tuple(started)

    async def start_scheduler(self, *, poll_seconds: float = 15.0) -> bool:
        await self.register_all()
        if self._scheduler_task and not self._scheduler_task.done():
            return False
        self._scheduler_stop = asyncio.Event()
        self._scheduler_task = asyncio.create_task(
            self.scheduler.serve(self._scheduler_stop, poll_seconds=poll_seconds),
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
                await self.manager.stop_guild(guild_id=guild.id)
            except Exception:
                self.log.exception("Skill shutdown failed guild=%s", guild.id)

    async def status(self, *, guild_id: int, skill_id: str) -> GuildSkillStatus:
        if not self.registry.contains(skill_id) and skill_id in self._unavailable_external:
            return self._unavailable_status(skill_id)
        skill = self.registry.get(skill_id)
        permissions = self.permissions(skill_id)
        missing = permissions.missing_declared()
        installed = await self.state.is_installed(guild_id=guild_id, skill_id=skill_id)
        enabled = await self.state.is_enabled(guild_id=guild_id, skill_id=skill_id)
        running = self.manager.is_running(guild_id=guild_id, skill_id=skill_id)

        if not installed:
            health = "NOT_INSTALLED"
            detail = "Skill package is available in the host but has not been added to this guild."
        elif missing:
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

        source_kind, source_distribution = self.source(skill_id)
        return GuildSkillStatus(
            skill_id=skill.manifest.id,
            name=skill.manifest.name,
            version=skill.manifest.version,
            description=skill.manifest.description,
            installed=installed,
            enabled=enabled,
            running=running,
            health=health,
            health_detail=detail,
            required_capabilities=tuple(skill.manifest.permissions),
            missing_capabilities=missing,
            source_kind=source_kind,
            source_distribution=source_distribution,
            management_available=bool(skill.manifest.management_apis.exposes),
            management_schema_available=skill.manifest.management_ui is not None,
        )

    async def statuses(self, *, guild_id: int) -> tuple[GuildSkillStatus, ...]:
        result = []
        for skill in self.registry.all():
            result.append(await self.status(guild_id=guild_id, skill_id=skill.manifest.id))
        for skill_id in sorted(self._unavailable_external):
            if not self.registry.contains(skill_id):
                result.append(self._unavailable_status(skill_id))
        return tuple(sorted(result, key=lambda item: item.skill_id))
