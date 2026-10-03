"""Portable Skill Runtime / SDK.

This package must remain free of GamerHQ-specific business logic so it can later
be extracted into an independent SDK/runtime package.
"""

from .contracts.capabilities import SkillCapability
from .contracts.context import SkillContext
from .contracts.events import EventContract, EventDeliveryReport, EventEnvelope
from .contracts.lifecycle import Skill, SkillHealth
from .contracts.manifest import SkillEvents, SkillManifest, SkillPublicApis, validate_manifest
from .contracts.public_api import PublicApiContract
from .runtime import (
    EventBus,
    ScopedEventBus,
    ScopedSkillApi,
    SkillApiError,
    SkillApiRouter,
    SkillManager,
    SkillRegistry,
    SkillStateStorePort,
)

__all__ = [
    "EventBus",
    "EventContract",
    "EventDeliveryReport",
    "EventEnvelope",
    "PublicApiContract",
    "ScopedEventBus",
    "ScopedSkillApi",
    "Skill",
    "SkillApiError",
    "SkillApiRouter",
    "SkillCapability",
    "SkillContext",
    "SkillEvents",
    "SkillHealth",
    "SkillManager",
    "SkillManifest",
    "SkillPublicApis",
    "SkillRegistry",
    "SkillStateStorePort",
    "validate_manifest",
]
