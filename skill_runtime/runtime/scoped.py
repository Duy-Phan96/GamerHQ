from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..contracts.events import EventDeliveryReport, EventEnvelope
from .api_router import SkillApiRouter
from .event_bus import EventBus


class ScopedEventBus:
    """SkillContext-facing Event Bus bound to one guild and one Skill identity."""

    def __init__(self, bus: EventBus, *, guild_id: int, skill_id: str):
        self.bus = bus
        self.guild_id = guild_id
        self.skill_id = skill_id

    async def emit(self, event: EventEnvelope) -> EventDeliveryReport:
        if event.guild_id != self.guild_id or event.producer_skill_id != self.skill_id:
            raise PermissionError("Event identity does not match the scoped SkillContext.")
        return await self.bus.emit(event)

    async def subscribe(
        self,
        event_id: str,
        handler: Callable[[EventEnvelope], Awaitable[None]],
    ) -> None:
        await self.bus.subscribe(
            guild_id=self.guild_id,
            consumer_skill_id=self.skill_id,
            event_id=event_id,
            handler=handler,
        )


class ScopedSkillApi:
    """SkillContext-facing API caller bound to the consuming Skill identity."""

    def __init__(self, router: SkillApiRouter, *, guild_id: int, skill_id: str):
        self.router = router
        self.guild_id = guild_id
        self.skill_id = skill_id

    async def call(
        self,
        *,
        skill_id: str,
        contract_id: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return await self.router.call(
            guild_id=self.guild_id,
            consumer_skill_id=self.skill_id,
            skill_id=skill_id,
            contract_id=contract_id,
            payload=payload,
        )
