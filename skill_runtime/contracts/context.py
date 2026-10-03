from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from .events import EventEnvelope


class DiscordPort(Protocol):
    async def send_message(self, *, channel_id: int, content: str | None = None, embed: Mapping[str, Any] | None = None,
                           allowed_mentions: Mapping[str, Any] | None = None) -> int: ...
    async def edit_own_message(self, *, channel_id: int, message_id: int, content: str | None = None,
                               embed: Mapping[str, Any] | None = None) -> None: ...
    async def delete_own_message(self, *, channel_id: int, message_id: int) -> None: ...


class EventBusPort(Protocol):
    async def emit(self, event: EventEnvelope) -> None: ...
    async def subscribe(self, event_id: str, handler: Callable[[EventEnvelope], Awaitable[None]]) -> None: ...


class SchedulerPort(Protocol):
    async def upsert_job(self, *, key: str, schedule: Mapping[str, Any], payload: Mapping[str, Any]) -> None: ...
    async def remove_job(self, *, key: str) -> None: ...


class SkillStoragePort(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...


class AuditPort(Protocol):
    async def write(self, *, action: str, target: str | None = None, metadata: Mapping[str, Any] | None = None) -> None: ...


class PermissionPort(Protocol):
    def allows(self, capability: str) -> bool: ...
    def require(self, capability: str) -> None: ...


class SkillApiPort(Protocol):
    async def call(self, *, skill_id: str, contract_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class SkillLogger(Protocol):
    def info(self, message: str, *args: object) -> None: ...
    def warning(self, message: str, *args: object) -> None: ...
    def error(self, message: str, *args: object) -> None: ...


@dataclass(frozen=True, slots=True)
class SkillContext:
    """Only supported boundary through which a portable Skill reaches its host."""

    guild_id: int
    skill_id: str
    discord: DiscordPort
    events: EventBusPort
    scheduler: SchedulerPort
    storage: SkillStoragePort
    audit: AuditPort
    permissions: PermissionPort
    skills: SkillApiPort
    logger: SkillLogger

    def __post_init__(self) -> None:
        if self.guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        if not self.skill_id:
            raise ValueError("skill_id is required.")
