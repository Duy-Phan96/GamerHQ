from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping
import re

EVENT_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z][a-z0-9-]*)+\.v[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class EventContract:
    """Documented event a Skill emits or consumes."""

    id: str
    description: str = ""

    def __post_init__(self) -> None:
        if not EVENT_ID.fullmatch(self.id):
            raise ValueError(
                "Event IDs must be stable, lowercase and versioned, e.g. "
                "recurring-post.sent.v1."
            )


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Portable event envelope crossing Skill boundaries."""

    event_id: str
    producer_skill_id: str
    guild_id: int
    occurred_at: int
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        EventContract(self.event_id)
        if self.guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        if self.occurred_at < 0:
            raise ValueError("occurred_at must be non-negative.")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class EventDeliveryReport:
    """Non-throwing subscriber delivery summary returned to the producer."""

    delivered: int
    failed_consumers: tuple[str, ...] = ()

    @property
    def failed(self) -> int:
        return len(self.failed_consumers)
