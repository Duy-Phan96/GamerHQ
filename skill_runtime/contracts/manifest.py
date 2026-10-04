from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
import re

from .capabilities import KNOWN_CAPABILITIES
from .events import EventContract
from .management import ManagementApiContract
from .public_api import PublicApiContract

SKILL_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$")
SUPPORTED_RUNTIME_API_VERSIONS = frozenset({"1"})


@dataclass(frozen=True, slots=True)
class SkillEvents:
    emits: tuple[EventContract, ...] = ()
    consumes: tuple[EventContract, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillPublicApis:
    exposes: tuple[PublicApiContract, ...] = ()
    consumes: tuple[PublicApiContract, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillManagementApis:
    exposes: tuple[ManagementApiContract, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillManifest:
    id: str
    name: str
    version: str
    runtime_api_version: str
    description: str
    author: str
    permissions: tuple[str, ...] = ()
    events: SkillEvents = field(default_factory=SkillEvents)
    public_apis: SkillPublicApis = field(default_factory=SkillPublicApis)
    management_apis: SkillManagementApis = field(default_factory=SkillManagementApis)

    def __post_init__(self) -> None:
        validate_manifest(self)


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def validate_manifest(manifest: SkillManifest, *, supported_api_versions: frozenset[str] = SUPPORTED_RUNTIME_API_VERSIONS) -> None:
    if not SKILL_ID.fullmatch(manifest.id):
        raise ValueError("Skill id must be a stable lowercase kebab-case machine ID.")
    if not manifest.name.strip():
        raise ValueError("Skill name is required.")
    if not SEMVER.fullmatch(manifest.version):
        raise ValueError("Skill version must use semantic versioning, e.g. 1.0.0.")
    if manifest.runtime_api_version not in supported_api_versions:
        raise ValueError(f"Unsupported Skill Runtime API version: {manifest.runtime_api_version}.")
    unknown = sorted(set(manifest.permissions) - KNOWN_CAPABILITIES)
    if unknown:
        raise ValueError("Unknown Skill capability: " + ", ".join(unknown))
    duplicate_caps = _duplicates(manifest.permissions)
    if duplicate_caps:
        raise ValueError("Duplicate Skill capabilities are not allowed: " + ", ".join(sorted(duplicate_caps)))

    emitted = tuple(contract.id for contract in manifest.events.emits)
    consumed = tuple(contract.id for contract in manifest.events.consumes)
    duplicate_events = _duplicates((*emitted, *consumed))
    if duplicate_events:
        raise ValueError("Duplicate event contract declarations are not allowed: " + ", ".join(sorted(duplicate_events)))

    exposed = tuple(contract.id for contract in manifest.public_apis.exposes)
    api_consumed = tuple(contract.id for contract in manifest.public_apis.consumes)
    duplicate_apis = _duplicates((*exposed, *api_consumed))
    if duplicate_apis:
        raise ValueError("Duplicate public Skill API declarations are not allowed: " + ", ".join(sorted(duplicate_apis)))

    management = tuple(contract.id for contract in manifest.management_apis.exposes)
    duplicate_management = _duplicates(management)
    if duplicate_management:
        raise ValueError("Duplicate management API declarations are not allowed: " + ", ".join(sorted(duplicate_management)))
