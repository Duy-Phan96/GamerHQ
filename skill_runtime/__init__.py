"""Portable Skill Runtime contracts.

This package must remain free of GamerHQ-specific business logic so it can later
be extracted into an independent SDK/runtime package.
"""

from .contracts.capabilities import SkillCapability
from .contracts.context import SkillContext
from .contracts.events import EventContract, EventEnvelope
from .contracts.lifecycle import Skill
from .contracts.manifest import SkillManifest, validate_manifest
from .contracts.public_api import PublicApiContract

__all__ = [
    "EventContract",
    "EventEnvelope",
    "PublicApiContract",
    "Skill",
    "SkillCapability",
    "SkillContext",
    "SkillManifest",
    "validate_manifest",
]
