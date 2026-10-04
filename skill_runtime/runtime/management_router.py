from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any

from .registry import SkillRegistry

ManagementHandler = Callable[[int, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
AvailabilityCheck = Callable[[int, str], Awaitable[bool]]


class SkillManagementError(RuntimeError):
    """Safe host-facing error raised at the management contract boundary."""


class SkillManagementRouter:
    """Routes trusted host management calls without importing Skill internals."""

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
        self._handlers: dict[tuple[str, str], ManagementHandler] = {}

    def _exposed(self, skill_id: str, contract_id: str) -> bool:
        skill = self.registry.get(skill_id)
        return contract_id in {
            item.id for item in skill.manifest.management_apis.exposes
        }

    def register_handler(
        self,
        *,
        skill_id: str,
        contract_id: str,
        handler: ManagementHandler,
    ) -> None:
        if not self._exposed(skill_id, contract_id):
            raise PermissionError(
                f"Skill {skill_id} did not declare management API: {contract_id}."
            )
        key = (skill_id, contract_id)
        if key in self._handlers:
            raise ValueError(
                f"Skill management handler already registered: "
                f"{skill_id}/{contract_id}."
            )
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
        skill_id: str,
        contract_id: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        self.registry.get(skill_id)
        if not self._exposed(skill_id, contract_id):
            raise SkillManagementError(
                "Target Skill does not expose the requested management contract."
            )
        if self.availability is not None:
            enabled = await self.availability(
                guild_id=guild_id,
                skill_id=skill_id,
            )
            if not enabled:
                raise SkillManagementError(
                    "Target Skill is disabled for this guild."
                )
        handler = self._handlers.get((skill_id, contract_id))
        if handler is None:
            raise SkillManagementError(
                "Target Skill management API is currently unavailable."
            )

        request = MappingProxyType(dict(payload))
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await handler(guild_id, request)
        except TimeoutError as exc:
            raise SkillManagementError(
                "Target Skill management API timed out."
            ) from exc
        except SkillManagementError:
            raise
        except (ValueError, KeyError) as exc:
            raise SkillManagementError(
                "Target Skill rejected the management request."
            ) from exc
        except Exception as exc:
            raise SkillManagementError(
                "Target Skill management API failed."
            ) from exc

        if not isinstance(response, Mapping):
            raise SkillManagementError(
                "Target Skill management API returned an invalid response."
            )
        return MappingProxyType(dict(response))
