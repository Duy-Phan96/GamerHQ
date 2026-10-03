from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .context import SkillContext

SchedulerHandler = Callable[[SkillContext, Mapping[str, Any]], Awaitable[None]]
PublicApiHandler = Callable[
    [SkillContext, Mapping[str, Any]],
    Awaitable[Mapping[str, Any]],
]


class SchedulerRegistrationPort(Protocol):
    def register_handler(self, handler_id: str, handler: SchedulerHandler) -> None: ...


class SkillApiRegistrationPort(Protocol):
    def expose(self, contract_id: str, handler: PublicApiHandler) -> None: ...


@dataclass(frozen=True, slots=True)
class SkillRegistrationContext:
    """Process-level registration surface for one Skill.

    It exposes no guild, Discord client, database or host internals. Registered
    handlers receive a normal guild-scoped SkillContext only when invoked.
    """

    skill_id: str
    scheduler: SchedulerRegistrationPort
    skills: SkillApiRegistrationPort

    def __post_init__(self) -> None:
        if not self.skill_id:
            raise ValueError("skill_id is required.")
