"""Installed-package discovery for external GamerHQ Skills.

Discovery never downloads, clones or installs code. It only loads explicitly
allowlisted Python entry points that are already present in the process
environment.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import Iterable

from ..contracts.manifest import SKILL_ID

ENTRY_POINT_GROUP = "gamerhq.skills"


class SkillPackageError(RuntimeError):
    """Safe package-discovery failure without leaking import internals."""


@dataclass(frozen=True, slots=True)
class InstalledSkillPackage:
    entry_point: str
    distribution: str | None
    skill: object


def _entry_points():
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return tuple(discovered.select(group=ENTRY_POINT_GROUP))
    return tuple(discovered.get(ENTRY_POINT_GROUP, ()))


def discover_installed_skills(enabled: Iterable[str]) -> tuple[InstalledSkillPackage, ...]:
    """Load explicitly allowlisted installed Skill entry points.

    Entry-point names are stable Skill IDs and must match the loaded manifest ID.
    Each entry point targets a zero-argument factory such as create_skill.
    """
    requested = tuple(str(value).strip() for value in enabled if str(value).strip())
    if len(set(requested)) != len(requested):
        raise SkillPackageError("External Skill allowlist contains duplicate IDs.")
    invalid = [value for value in requested if not SKILL_ID.fullmatch(value)]
    if invalid:
        raise SkillPackageError("External Skill allowlist contains an invalid Skill ID.")

    requested_set = set(requested)
    available = {}
    for entry_point in _entry_points():
        if entry_point.name not in requested_set:
            continue
        if entry_point.name in available:
            raise SkillPackageError(
                f"Multiple installed packages expose Skill entry point: {entry_point.name}."
            )
        available[entry_point.name] = entry_point

    loaded: list[InstalledSkillPackage] = []
    for skill_id in requested:
        entry_point = available.get(skill_id)
        if entry_point is None:
            raise SkillPackageError(
                f"Configured external Skill is not installed: {skill_id}."
            )
        try:
            factory = entry_point.load()
            if not callable(factory):
                raise TypeError("entry point is not callable")
            skill = factory()
            manifest = getattr(skill, "manifest", None)
            loaded_id = getattr(manifest, "id", None)
        except Exception as exc:
            raise SkillPackageError(
                f"External Skill package could not be loaded: {skill_id}."
            ) from exc
        if loaded_id != skill_id:
            raise SkillPackageError(
                f"External Skill entry point does not match its manifest ID: {skill_id}."
            )

        dist = getattr(entry_point, "dist", None)
        distribution = getattr(dist, "name", None) if dist is not None else None
        loaded.append(
            InstalledSkillPackage(
                entry_point=skill_id,
                distribution=str(distribution) if distribution else None,
                skill=skill,
            )
        )
    return tuple(loaded)
