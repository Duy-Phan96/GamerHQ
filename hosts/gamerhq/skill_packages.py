"""GamerHQ external Skill package loading with per-package failure isolation."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from collections.abc import Iterable

from skill_runtime.runtime.packages import SkillPackageError, discover_installed_skills

BUNDLED_SKILL_IDS = ("recurring-posts", "progression")


def configured_skill_ids(external_ids: Iterable[str]) -> tuple[str, ...]:
    """Return bundled + configured external Skill IDs once, in stable order."""
    return tuple(dict.fromkeys((*BUNDLED_SKILL_IDS, *tuple(external_ids))))


@dataclass(frozen=True, slots=True)
class ExternalSkillLoadReport:
    loaded: tuple[str, ...]
    unavailable: tuple[str, ...]


def load_external_skill_packages(runtime, skill_ids: Iterable[str]) -> ExternalSkillLoadReport:
    """Load configured external Skills independently.

    One broken or missing package must not prevent built-in Skills or another
    healthy external package from starting.
    """
    log = logging.getLogger("gamerhq.skills.packages")
    loaded: list[str] = []
    unavailable: list[str] = []

    for skill_id in skill_ids:
        try:
            package = discover_installed_skills((skill_id,))[0]
            runtime.register(
                package.skill,
                source_kind="external",
                source_distribution=package.distribution or package.entry_point,
            )
        except (SkillPackageError, ValueError, TypeError):
            runtime.record_external_unavailable(skill_id)
            unavailable.append(skill_id)
            log.warning("Configured external Skill unavailable: %s", skill_id)
            continue
        loaded.append(skill_id)

    return ExternalSkillLoadReport(
        loaded=tuple(loaded),
        unavailable=tuple(unavailable),
    )
