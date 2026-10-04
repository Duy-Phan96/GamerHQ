"""Offline developer checks for GamerHQ Skill implementations.

These checks validate public SDK shape only. They do not execute lifecycle
methods, access Discord, inspect production state, or install packages.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from collections.abc import Callable

from .contracts.manifest import SkillManifest, validate_manifest


_REQUIRED_ASYNC_METHODS = (
    "register",
    "enable",
    "disable",
    "start",
    "stop",
    "health_check",
)


@dataclass(frozen=True, slots=True)
class SkillConformanceReport:
    skill_id: str
    version: str
    runtime_api_version: str
    lifecycle_methods: tuple[str, ...]


class SkillConformanceError(ValueError):
    """Safe SDK contract error for developer-facing validation."""


def validate_skill_implementation(skill: object) -> SkillConformanceReport:
    manifest = getattr(skill, "manifest", None)
    if not isinstance(manifest, SkillManifest):
        raise SkillConformanceError("Skill must expose a SkillManifest as manifest.")

    try:
        validate_manifest(manifest)
    except ValueError as exc:
        raise SkillConformanceError(str(exc)) from exc

    for name in _REQUIRED_ASYNC_METHODS:
        method = getattr(skill, name, None)
        if method is None or not callable(method):
            raise SkillConformanceError(f"Skill lifecycle method is missing: {name}.")
        if not inspect.iscoroutinefunction(method):
            raise SkillConformanceError(
                f"Skill lifecycle method must be async: {name}."
            )

    return SkillConformanceReport(
        skill_id=manifest.id,
        version=manifest.version,
        runtime_api_version=manifest.runtime_api_version,
        lifecycle_methods=_REQUIRED_ASYNC_METHODS,
    )


def validate_skill_factory(
    factory: Callable[[], object],
    *,
    expected_skill_id: str | None = None,
) -> SkillConformanceReport:
    if not callable(factory):
        raise SkillConformanceError("Skill factory must be callable.")
    try:
        skill = factory()
    except Exception as exc:
        raise SkillConformanceError("Skill factory failed.") from exc

    report = validate_skill_implementation(skill)
    if expected_skill_id is not None and report.skill_id != expected_skill_id:
        raise SkillConformanceError(
            "Skill factory result does not match the expected Skill ID."
        )
    return report
