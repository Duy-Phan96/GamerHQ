"""Portable Skill Runtime / SDK.

This package must remain free of GamerHQ-specific business logic so it can later
be extracted into an independent SDK/runtime package.
"""

from .contracts.capabilities import SkillCapability
from .contracts.context import SkillContext, SkillRegistrationContext
from .contracts.events import EventContract, EventDeliveryReport, EventEnvelope
from .contracts.lifecycle import Skill, SkillHealth
from .contracts.manifest import SkillEvents, SkillManifest, SkillPublicApis, validate_manifest
from .contracts.public_api import PublicApiContract
from .contracts.schedule import DailySchedule, IntervalSchedule, OnceSchedule, ScheduleSpec, WeeklySchedule, next_run_at
from .runtime import (
    EventBus,
    ScopedEventBus,
    ScopedSkillApi,
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
    ScopedSchedulerRegistration,
    ScopedSkillApiRegistration,
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
    "ScopedSkillApi",
    "Skill",
    "SkillApiError",
    "SkillApiRouter",
    "SkillCapability",
    "SkillContext",
    "SkillRegistrationContext",
    "SkillEvents",
    "SkillHealth",
    "SkillManager",
    "SkillManifest",
    "SkillPublicApis",
    "SkillRegistry",
    "SkillStateStorePort",
    "ScheduledJob",
    "SchedulerEngine",
    "SchedulerRunReport",
    "SchedulerStorePort",
    "ScopedScheduler",
    "ScopedSchedulerRegistration",
    "ScopedSkillApiRegistration",
    "validate_manifest",
]
