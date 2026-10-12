from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

import json

from .events import EventDeliveryReport, EventEnvelope


@dataclass(frozen=True, slots=True)
class DiscordMemberInfo:
    id: int
    display_name: str
    role_ids: tuple[int, ...]
    joined_at: str | None
    is_bot: bool


@dataclass(frozen=True, slots=True)
class DiscordChannelInfo:
    id: int
    name: str
    kind: str


class DiscordPort(Protocol):
    async def get_member(self, *, member_id: int) -> DiscordMemberInfo: ...
    async def get_channel(self, *, channel_id: int) -> DiscordChannelInfo: ...
    async def grant_role(self, *, member_id: int, role_id: int) -> bool: ...
    async def remove_role(self, *, member_id: int, role_id: int) -> bool: ...
    async def send_message(self, *, channel_id: int, content: str | None = None, embed: Mapping[str, Any] | None = None,
                           allowed_mentions: Mapping[str, Any] | None = None,
                           link_buttons: tuple[Mapping[str, str], ...] | None = None) -> int: ...
    async def edit_own_message(self, *, channel_id: int, message_id: int, content: str | None = None,
                               embed: Mapping[str, Any] | None = None) -> None: ...
    async def delete_own_message(self, *, channel_id: int, message_id: int) -> None: ...


class EventBusPort(Protocol):
    async def emit(self, event: EventEnvelope) -> EventDeliveryReport: ...
    async def subscribe(self, event_id: str, handler: Callable[[EventEnvelope], Awaitable[None]]) -> None: ...


@dataclass(frozen=True, slots=True)
class SchedulerJobStatus:
    key: str
    handler_id: str
    next_run_at: int
    last_run_at: int | None
    failure_count: int
    revision: int


class SchedulerPort(Protocol):
    async def upsert_job(
        self,
        *,
        key: str,
        handler_id: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None: ...
    async def remove_job(self, *, key: str) -> None: ...
    async def get_job(self, *, key: str) -> SchedulerJobStatus | None: ...


class SkillStoragePort(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ExternalHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: str

    def json(self) -> Any:
        return json.loads(self.body)


class ExternalHttpPort(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, str | int | float | bool] | None = None,
        json_body: Any | None = None,
        timeout_seconds: float = 15.0,
    ) -> ExternalHttpResponse: ...


class SkillSecretStorePort(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> None: ...
    async def delete(self, key: str) -> None: ...


class AuditPort(Protocol):
    async def write(self, *, action: str, target: str | None = None, metadata: Mapping[str, Any] | None = None) -> None: ...


class PermissionPort(Protocol):
    def allows(self, capability: str) -> bool: ...
    def require(self, capability: str) -> None: ...


class SkillApiPort(Protocol):
    async def call(self, *, skill_id: str, contract_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class SchedulerRegistrationPort(Protocol):
    def register_handler(
        self,
        handler_id: str,
        handler: Callable[["SkillContext", Any], Awaitable[None]],
    ) -> None: ...


class SkillManagementRegistrationPort(Protocol):
    def expose(
        self,
        contract_id: str,
        handler: Callable[["SkillContext", Mapping[str, Any]], Awaitable[Mapping[str, Any]]],
    ) -> None: ...


class SkillApiRegistrationPort(Protocol):
    def expose(
        self,
        contract_id: str,
        handler: Callable[["SkillContext", Mapping[str, Any]], Awaitable[Mapping[str, Any]]],
    ) -> None: ...


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
    secrets: SkillSecretStorePort
    http: ExternalHttpPort
    audit: AuditPort
    permissions: PermissionPort
    skills: SkillApiPort
    logger: SkillLogger

    def __post_init__(self) -> None:
        if self.guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        if not self.skill_id:
            raise ValueError("skill_id is required.")


@dataclass(frozen=True, slots=True)
class SkillRegistrationContext:
    """Process-level binding surface used only during Skill registration."""

    skill_id: str
    scheduler: SchedulerRegistrationPort
    management: SkillManagementRegistrationPort
    skills: SkillApiRegistrationPort
    logger: SkillLogger

    def __post_init__(self) -> None:
        if not self.skill_id:
            raise ValueError("skill_id is required.")
