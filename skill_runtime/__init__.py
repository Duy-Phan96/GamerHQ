"""Portable Skill Runtime / SDK.

This package must remain free of GamerHQ-specific business logic so it can later
be extracted into an independent SDK/runtime package.
"""

from .contracts.capabilities import SkillCapability
from .contracts.context import SkillContext
from .contracts.discord import (
    SkillDiscordError,
    SkillDiscordNotFound,
    SkillDiscordOperationFailed,
    SkillDiscordPermissionDenied,
)
from .contracts.events import EventContract, EventDeliveryReport, EventEnvelope
from .contracts.lifecycle import Skill, SkillHealth
from .contracts.manifest import SkillEvents, SkillManifest, SkillPublicApis, validate_manifest
from .contracts.public_api import PublicApiContract
from .contracts.registration import SkillRegistrationContext
from .contracts.schedule import DailySchedule, IntervalSchedule, OnceSchedule, ScheduleSpec, WeeklySchedule, next_run_at
from .runtime import (
    EventBus,
    ScopedEventBus,
    ScopedSchedulerRegistration,
    ScopedSkillApi,
    ScopedSkillApiRegistration,
    SkillApiError,
    SkillApiRouter,
    SkillManager,
    SkillRegistry,
    SkillStateStorePort,
    ScheduledJob,
    SchedulerEngine,
    SchedulerRunReport,
    SchedulerStorePort,
    ScopedScheduler,
)

__all__ = [
    "DailySchedule",
    "IntervalSchedule",
    "OnceSchedule",
    "ScheduleSpec",
    "WeeklySchedule",
    "next_run_at",
    "EventBus",
    "EventContract",
    "EventDeliveryReport",
    "EventEnvelope",
    "PublicApiContract",
    "ScopedEventBus",
    "ScopedSchedulerRegistration",
    "ScopedSkillApi",
    "ScopedSkillApiRegistration",
    "Skill",
    "SkillDiscordError",
    "SkillDiscordNotFound",
    "SkillDiscordOperationFailed",
    "SkillDiscordPermissionDenied",
    "SkillApiError",
    "SkillApiRouter",
    "SkillCapability",
    "SkillContext",
    "SkillEvents",
    "SkillHealth",
    "SkillManager",
    "SkillManifest",
    "SkillRegistrationContext",
    "SkillPublicApis",
    "SkillRegistry",
    "SkillStateStorePort",
    "ScheduledJob",
    "SchedulerEngine",
    "SchedulerRunReport",
    "SchedulerStorePort",
    "ScopedScheduler",
    "validate_manifest",
]
