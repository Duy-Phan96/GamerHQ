"""Reviewed external Skill deployment plan.

The reviewed requirements-skills.lock file remains the only deployment source
of truth. This module parses it into a bounded machine-readable plan for CI,
release tooling and future deployment orchestration.

It never downloads, clones, installs or executes package code.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

_REQUIREMENT = re.compile(
    r"^(?P<distribution>[A-Za-z0-9][A-Za-z0-9_.-]*)\s*@\s*"
    r"(?P<url>https://github\.com/(?P<repository>[^/\s]+/[^/\s]+)/archive/"
    r"(?P<commit>[0-9a-f]{40})\.zip)$"
)


class SkillDeploymentPlanError(ValueError):
    """Safe validation error for reviewed external Skill deployment metadata."""


@dataclass(frozen=True, slots=True)
class ReviewedSkillPackage:
    distribution: str
    source_url: str
    source_repository: str
    reviewed_commit: str

    @property
    def requirement(self) -> str:
        return f"{self.distribution} @ {self.source_url}"

    def to_dict(self) -> dict[str, str]:
        return {
            "distribution": self.distribution,
            "sourceUrl": self.source_url,
            "sourceRepository": self.source_repository,
            "reviewedCommit": self.reviewed_commit,
        }


def parse_reviewed_skill_lock(text: str) -> tuple[ReviewedSkillPackage, ...]:
    """Parse immutable reviewed external Skill requirements.

    V1 intentionally accepts only GitHub archive URLs pinned to a full lowercase
    40-character commit SHA. Moving branches, tags, short SHAs and other source
    forms fail closed until an explicit reviewed contract supports them.
    """
    packages: list[ReviewedSkillPackage] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        match = _REQUIREMENT.fullmatch(line)
        if match is None:
            raise SkillDeploymentPlanError(
                "External Skill lock contains an unsupported or non-immutable requirement."
            )

        distribution = match.group("distribution")
        key = distribution.lower().replace("_", "-")
        if key in seen:
            raise SkillDeploymentPlanError(
                "External Skill lock contains a duplicate distribution."
            )
        seen.add(key)

        packages.append(
            ReviewedSkillPackage(
                distribution=distribution,
                source_url=match.group("url"),
                source_repository=match.group("repository"),
                reviewed_commit=match.group("commit"),
            )
        )

    return tuple(packages)


def load_reviewed_skill_lock(path: Path) -> tuple[ReviewedSkillPackage, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillDeploymentPlanError(
            "External Skill deployment lock could not be read."
        ) from exc
    return parse_reviewed_skill_lock(text)


def deployment_plan(packages: tuple[ReviewedSkillPackage, ...]) -> dict[str, Any]:
    """Return a stable JSON-safe deployment plan.

    Changing this plan changes executable host dependencies and therefore
    requires a reviewed image rebuild/redeploy. It is never a runtime install
    instruction.
    """
    return {
        "schemaVersion": "1",
        "requiresImageRebuild": True,
        "runtimeInstallAllowed": False,
        "packages": [package.to_dict() for package in packages],
    }
