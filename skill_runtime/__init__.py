"""Portable Skill Runtime / SDK.

This package must remain free of GamerHQ-specific business logic so it can later
be extracted into an independent SDK/runtime package.
"""

from .contracts.capabilities import SkillCapability
from .contracts.context import DiscordChannelInfo, SkillContext, SkillRegistrationContext
from .contracts.errors import CapabilityUnavailableError, HostCapabilityError, HostPermissionDeniedError, InvalidHostOperationError, ResourceNotFoundError, TransientHostError
from .contracts.events import EventContract, EventDeliveryReport, EventEnvelope
from .contracts.lifecycle import Skill, SkillHealth
from .contracts.management import ManagementApiContract
from .contracts.management_ui import ManagementCollectionOperations, ManagementCollectionSchema, ManagementField, ManagementFieldOption, ManagementSection, ManagementUiSchema
from .contracts.manifest import SkillEvents, SkillManagementApis, SkillManifest, SkillPublicApis, validate_manifest
from .contracts.public_api import PublicApiContract
from .contracts.schedule import DailySchedule, IntervalSchedule, OnceSchedule, ScheduleSpec, WeeklySchedule, next_run_at
from .runtime import (
    EventBus,
    ScopedEventBus,
    ScopedSkillApi,
    SkillApiError,
    SkillApiRouter,
    SkillManagementError,
    SkillManagementRouter,
    SkillManager,
    SkillRegistry,
    SkillStateStorePort,
    ScheduledJob,
    SchedulerEngine,
    SchedulerRunReport,
    SchedulerStorePort,
    ScopedScheduler,
    ScopedSchedulerRegistration,
    ScopedSkillManagementRegistration,
    StaleSchedulerClaimError,
    ScopedSkillApiRegistration,
)

__all__ = [
    "CapabilityUnavailableError",
    "DiscordChannelInfo",
    "HostCapabilityError",
    "HostPermissionDeniedError",
    "InvalidHostOperationError",
    "ResourceNotFoundError",
    "TransientHostError",
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
    "ManagementApiContract",
    "ManagementCollectionOperations",
    "ManagementCollectionSchema",
    "ManagementField",
    "ManagementFieldOption",
    "ManagementSection",
    "ManagementUiSchema",
    "PublicApiContract",
    "ScopedEventBus",
    "ScopedSkillApi",
    "Skill",
    "SkillApiError",
    "SkillApiRouter",
    "SkillManagementError",
    "SkillManagementRouter",
    "SkillCapability",
    "SkillContext",
    "SkillRegistrationContext",
    "SkillEvents",
    "SkillHealth",
    "SkillManager",
    "SkillManagementApis",
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
    "ScopedSkillManagementRegistration",
    "StaleSchedulerClaimError",
    "ScopedSkillApiRegistration",
    "validate_manifest",
    "SkillConformanceError",
    "SkillConformanceReport",
    "validate_skill_factory",
    "validate_skill_implementation",
    "validate_skill_package_metadata",
    "validate_skill_package_matches_implementation",
    "SkillPackageMetadataReport",
    "SkillSourceAuditReport",
    "SkillSourceFinding",
    "audit_skill_source",
    "require_clean_skill_source",
]

from .devtools import SkillConformanceError, SkillConformanceReport, SkillPackageMetadataReport, SkillSourceAuditReport, SkillSourceFinding, audit_skill_source, require_clean_skill_source, validate_skill_factory, validate_skill_implementation, validate_skill_package_matches_implementation, validate_skill_package_metadata
