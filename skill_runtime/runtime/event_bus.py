from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..contracts.capabilities import SkillCapability
from ..contracts.events import EventDeliveryReport, EventEnvelope
from .registry import SkillRegistry

EventHandler = Callable[[EventEnvelope], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Subscription:
    guild_id: int
    consumer_skill_id: str
    event_id: str
    handler: EventHandler


class EventBus:
    """Typed in-process Event Bus for documented Skill contracts.

    The bus is intentionally host-neutral. A future host may replace transport
    without changing producer/consumer Skill contracts.
    """

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._subscriptions: dict[tuple[int, str], list[_Subscription]] = {}
        self._lock = asyncio.Lock()

    def _declared(self, skill_id: str, event_id: str, *, direction: str) -> bool:
        skill = self.registry.get(skill_id)
        contracts = skill.manifest.events.emits if direction == "emit" else skill.manifest.events.consumes
        return event_id in {contract.id for contract in contracts}

    def _require_capability(self, skill_id: str, capability: SkillCapability) -> None:
        skill = self.registry.get(skill_id)
        if capability.value not in skill.manifest.permissions:
            raise PermissionError(
                f"Skill {skill_id} did not declare capability: {capability.value}."
            )

    async def subscribe(
        self,
        *,
        guild_id: int,
        consumer_skill_id: str,
        event_id: str,
        handler: EventHandler,
    ) -> bool:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        self._require_capability(consumer_skill_id, SkillCapability.EVENTS_SUBSCRIBE)
        if not self._declared(consumer_skill_id, event_id, direction="consume"):
            raise PermissionError(
                f"Skill {consumer_skill_id} did not declare consumed event: {event_id}."
            )
        subscription = _Subscription(guild_id, consumer_skill_id, event_id, handler)
        key = (guild_id, event_id)
        async with self._lock:
            current = self._subscriptions.setdefault(key, [])
            if any(
                item.consumer_skill_id == consumer_skill_id and item.handler is handler
                for item in current
            ):
                return False
            current.append(subscription)
        return True

    async def unsubscribe(
        self,
        *,
        guild_id: int,
        consumer_skill_id: str,
        event_id: str,
        handler: EventHandler,
    ) -> bool:
        key = (guild_id, event_id)
        async with self._lock:
            current = self._subscriptions.get(key, [])
            remaining = [
                item for item in current
                if not (item.consumer_skill_id == consumer_skill_id and item.handler is handler)
            ]
            changed = len(remaining) != len(current)
            if remaining:
                self._subscriptions[key] = remaining
            else:
                self._subscriptions.pop(key, None)
        return changed

    async def unsubscribe_skill(self, *, guild_id: int, skill_id: str) -> int:
        """Remove every subscription owned by one guild/Skill lifecycle."""
        removed = 0
        async with self._lock:
            for key, current in tuple(self._subscriptions.items()):
                remaining = [item for item in current if item.consumer_skill_id != skill_id]
                removed += len(current) - len(remaining)
                if remaining:
                    self._subscriptions[key] = remaining
                else:
                    self._subscriptions.pop(key, None)
        return removed

    async def emit(self, event: EventEnvelope) -> EventDeliveryReport:
        self._require_capability(event.producer_skill_id, SkillCapability.EVENTS_EMIT)
        if not self._declared(event.producer_skill_id, event.event_id, direction="emit"):
            raise PermissionError(
                f"Skill {event.producer_skill_id} did not declare emitted event: {event.event_id}."
            )
        async with self._lock:
            subscriptions = tuple(self._subscriptions.get((event.guild_id, event.event_id), ()))

        if not subscriptions:
            return EventDeliveryReport(delivered=0, failed_consumers=())

        results = await asyncio.gather(
            *(item.handler(event) for item in subscriptions),
            return_exceptions=True,
        )
        failed = tuple(
            subscriptions[index].consumer_skill_id
            for index, result in enumerate(results)
            if isinstance(result, BaseException)
        )
        return EventDeliveryReport(
            delivered=len(subscriptions) - len(failed),
            failed_consumers=failed,
        )
