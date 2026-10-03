from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..contracts.context import SkillContext
from ..contracts.lifecycle import SkillHealth
from .registry import SkillRegistry


class SkillStateStorePort(Protocol):
    """Persistent host-owned enablement state.

    GamerHQ may implement this with SQLite first. Another host may use another
    database without changing Skill or SkillManager code.
    """

    async def is_enabled(self, *, guild_id: int, skill_id: str) -> bool: ...
    async def set_enabled(self, *, guild_id: int, skill_id: str, enabled: bool, version: str) -> None: ...
    async def enabled_skill_ids(self, *, guild_id: int) -> tuple[str, ...]: ...


ContextFactory = Callable[[int, str], Awaitable[SkillContext]]


class SkillManager:
    """Coordinates validated Skills while keeping guild state in a host port."""

    def __init__(self, registry: SkillRegistry, state: SkillStateStorePort, context_factory: ContextFactory):
        self.registry = registry
        self.state = state
        self.context_factory = context_factory
        self._registered = False
        self._running: set[tuple[int, str]] = set()
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    def _lock(self, guild_id: int, skill_id: str) -> asyncio.Lock:
        return self._locks.setdefault((guild_id, skill_id), asyncio.Lock())

    async def register_all(self) -> None:
        """Run process-level registration exactly once per manager instance."""
        if self._registered:
            return
        for skill in self.registry.all():
            await skill.register()
        self._registered = True

    async def enabled(self, *, guild_id: int, skill_id: str) -> bool:
        self.registry.get(skill_id)
        return await self.state.is_enabled(guild_id=guild_id, skill_id=skill_id)

    async def enable(self, *, guild_id: int, skill_id: str) -> bool:
        """Enable idempotently. Returns True only when state changed."""
        skill = self.registry.get(skill_id)
        async with self._lock(guild_id, skill_id):
            if await self.state.is_enabled(guild_id=guild_id, skill_id=skill_id):
                return False
            ctx = await self.context_factory(guild_id, skill_id)
            await skill.enable(ctx)
            await self.state.set_enabled(
                guild_id=guild_id,
                skill_id=skill_id,
                enabled=True,
                version=skill.manifest.version,
            )
            return True

    async def disable(self, *, guild_id: int, skill_id: str) -> bool:
        """Stop + disable idempotently before persisting disabled state."""
        skill = self.registry.get(skill_id)
        async with self._lock(guild_id, skill_id):
            if not await self.state.is_enabled(guild_id=guild_id, skill_id=skill_id):
                self._running.discard((guild_id, skill_id))
                return False
            ctx = await self.context_factory(guild_id, skill_id)
            if (guild_id, skill_id) in self._running:
                await skill.stop(ctx)
                self._running.discard((guild_id, skill_id))
            await skill.disable(ctx)
            await self.state.set_enabled(
                guild_id=guild_id,
                skill_id=skill_id,
                enabled=False,
                version=skill.manifest.version,
            )
            return True

    async def start(self, *, guild_id: int, skill_id: str) -> bool:
        """Start only enabled Skills and never start the same guild/Skill twice."""
        skill = self.registry.get(skill_id)
        async with self._lock(guild_id, skill_id):
            key = (guild_id, skill_id)
            if key in self._running:
                return False
            if not await self.state.is_enabled(guild_id=guild_id, skill_id=skill_id):
                return False
            ctx = await self.context_factory(guild_id, skill_id)
            await skill.start(ctx)
            self._running.add(key)
            return True

    async def stop(self, *, guild_id: int, skill_id: str) -> bool:
        skill = self.registry.get(skill_id)
        async with self._lock(guild_id, skill_id):
            key = (guild_id, skill_id)
            if key not in self._running:
                return False
            ctx = await self.context_factory(guild_id, skill_id)
            await skill.stop(ctx)
            self._running.remove(key)
            return True

    async def restore_guild(self, *, guild_id: int) -> tuple[str, ...]:
        """Restart entry point: start every persisted enabled Skill once."""
        started: list[str] = []
        for skill_id in await self.state.enabled_skill_ids(guild_id=guild_id):
            self.registry.get(skill_id)
            if await self.start(guild_id=guild_id, skill_id=skill_id):
                started.append(skill_id)
        return tuple(started)

    async def stop_guild(self, *, guild_id: int) -> tuple[str, ...]:
        stopped: list[str] = []
        for current_guild, skill_id in tuple(sorted(self._running)):
            if current_guild == guild_id and await self.stop(guild_id=guild_id, skill_id=skill_id):
                stopped.append(skill_id)
        return tuple(stopped)

    async def health(self, *, guild_id: int, skill_id: str) -> SkillHealth:
        skill = self.registry.get(skill_id)
        if not await self.state.is_enabled(guild_id=guild_id, skill_id=skill_id):
            return SkillHealth("DISABLED", "Skill is disabled for this guild.")
        ctx = await self.context_factory(guild_id, skill_id)
        return await skill.health_check(ctx)

    def is_running(self, *, guild_id: int, skill_id: str) -> bool:
        return (guild_id, skill_id) in self._running
