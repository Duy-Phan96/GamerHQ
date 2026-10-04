from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .context import SkillContext, SkillRegistrationContext
from .manifest import SkillManifest


@dataclass(frozen=True, slots=True)
class SkillHealth:
    state: str
    detail: str = ""


class Skill(Protocol):
    """Portable lifecycle contract implemented by first- and third-party Skills."""

    manifest: SkillManifest

    async def register(self, ctx: SkillRegistrationContext) -> None: ...
    async def enable(self, ctx: SkillContext) -> None: ...
    async def disable(self, ctx: SkillContext) -> None: ...
    async def start(self, ctx: SkillContext) -> None: ...
    async def stop(self, ctx: SkillContext) -> None: ...
    async def health_check(self, ctx: SkillContext) -> SkillHealth: ...
