from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..contracts.context import SkillContext
from .api_router import SkillApiRouter
from .management_router import SkillManagementRouter
from .scheduler import ScheduledJob, SchedulerEngine

ContextFactory = Callable[[int, str], Awaitable[SkillContext]]
SchedulerSkillHandler = Callable[[SkillContext, ScheduledJob], Awaitable[None]]
PublicApiSkillHandler = Callable[
    [SkillContext, Mapping[str, Any]],
    Awaitable[Mapping[str, Any]],
]
ManagementSkillHandler = Callable[
    [SkillContext, Mapping[str, Any]],
    Awaitable[Mapping[str, Any]],
]


class ScopedSchedulerRegistration:
    """Process-level scheduler bindings scoped to one Skill identity."""

    def __init__(
        self,
        engine: SchedulerEngine,
        *,
        skill_id: str,
        context_factory: ContextFactory,
    ):
        self.engine = engine
        self.skill_id = skill_id
        self.context_factory = context_factory

    def register_handler(
        self,
        handler_id: str,
        handler: SchedulerSkillHandler,
    ) -> None:
        async def invoke(job: ScheduledJob) -> None:
            ctx = await self.context_factory(job.guild_id, self.skill_id)
            await handler(ctx, job)

        self.engine.register_handler(
            skill_id=self.skill_id,
            handler_id=handler_id,
            handler=invoke,
        )


class ScopedSkillApiRegistration:
    """Process-level Public Skill API bindings scoped to one provider Skill."""

    def __init__(
        self,
        router: SkillApiRouter,
        *,
        skill_id: str,
        context_factory: ContextFactory,
    ):
        self.router = router
        self.skill_id = skill_id
        self.context_factory = context_factory

    def expose(
        self,
        contract_id: str,
        handler: PublicApiSkillHandler,
    ) -> None:
        async def invoke(
            guild_id: int,
            payload: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            ctx = await self.context_factory(guild_id, self.skill_id)
            return await handler(ctx, payload)

        self.router.register_handler(
            skill_id=self.skill_id,
            contract_id=contract_id,
            handler=invoke,
        )



class ScopedSkillManagementRegistration:
    """Process-level management bindings scoped to one Skill identity."""

    def __init__(
        self,
        router: SkillManagementRouter,
        *,
        skill_id: str,
        context_factory: ContextFactory,
    ):
        self.router = router
        self.skill_id = skill_id
        self.context_factory = context_factory

    def expose(
        self,
        contract_id: str,
        handler: ManagementSkillHandler,
    ) -> None:
        async def invoke(
            guild_id: int,
            payload: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            ctx = await self.context_factory(guild_id, self.skill_id)
            return await handler(ctx, payload)

        self.router.register_handler(
            skill_id=self.skill_id,
            contract_id=contract_id,
            handler=invoke,
        )
