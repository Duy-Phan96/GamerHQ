from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any

from ..contracts.capabilities import SkillCapability
from .registry import SkillRegistry

ApiHandler = Callable[[int, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
AvailabilityCheck = Callable[[int, str], Awaitable[bool]]


class SkillApiError(RuntimeError):
    """Safe public error raised at the Skill API boundary."""


class SkillApiRouter:
    """Routes versioned public Skill contracts without direct Skill imports."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        timeout_seconds: float = 30.0,
        availability: AvailabilityCheck | None = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self.registry = registry
        self.timeout_seconds = timeout_seconds
        self.availability = availability
        self._handlers: dict[tuple[str, str], ApiHandler] = {}

    def _exposed(self, skill_id: str, contract_id: str) -> bool:
        skill = self.registry.get(skill_id)
        return contract_id in {item.id for item in skill.manifest.public_apis.exposes}

    def _consumed(self, skill_id: str, contract_id: str) -> bool:
        skill = self.registry.get(skill_id)
        return contract_id in {item.id for item in skill.manifest.public_apis.consumes}

    def register_handler(self, *, skill_id: str, contract_id: str, handler: ApiHandler) -> None:
        if not self._exposed(skill_id, contract_id):
            raise PermissionError(
                f"Skill {skill_id} did not declare exposed public API: {contract_id}."
            )
        key = (skill_id, contract_id)
        if key in self._handlers:
            raise ValueError(f"Public Skill API handler already registered: {skill_id}/{contract_id}.")
        self._handlers[key] = handler

    def unregister_skill(self, *, skill_id: str) -> int:
        keys = [key for key in self._handlers if key[0] == skill_id]
        for key in keys:
            self._handlers.pop(key, None)
        return len(keys)

    async def call(
        self,
        *,
        guild_id: int,
        consumer_skill_id: str,
        skill_id: str,
        contract_id: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        consumer = self.registry.get(consumer_skill_id)
        self.registry.get(skill_id)
        if SkillCapability.SKILL_API_CALL.value not in consumer.manifest.permissions:
            raise PermissionError(
                f"Skill {consumer_skill_id} did not declare capability: "
                f"{SkillCapability.SKILL_API_CALL.value}."
            )
        if not self._consumed(consumer_skill_id, contract_id):
            raise PermissionError(
                f"Skill {consumer_skill_id} did not declare consumed public API: {contract_id}."
            )
        if not self._exposed(skill_id, contract_id):
            raise SkillApiError("Target Skill does not expose the requested contract.")
        if self.availability is not None:
            consumer_enabled, target_enabled = await asyncio.gather(
                self.availability(guild_id, consumer_skill_id),
                self.availability(guild_id, skill_id),
            )
            if not consumer_enabled:
                raise SkillApiError("Calling Skill is disabled for this guild.")
            if not target_enabled:
                raise SkillApiError("Target Skill is disabled for this guild.")
        handler = self._handlers.get((skill_id, contract_id))
        if handler is None:
            raise SkillApiError("Target Skill API is currently unavailable.")

        request = MappingProxyType(dict(payload))
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await handler(guild_id, request)
        except TimeoutError as exc:
            raise SkillApiError("Target Skill API timed out.") from exc
        except SkillApiError:
            raise
        except Exception as exc:
            # Never leak another Skill's private exception details across the API boundary.
            raise SkillApiError("Target Skill API failed.") from exc
        if not isinstance(response, Mapping):
            raise SkillApiError("Target Skill API returned an invalid response.")
        return MappingProxyType(dict(response))
