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

_GITHUB_REQUIREMENT = re.compile(
    r"^(?P<distribution>[A-Za-z0-9][A-Za-z0-9_.-]*)\\s*@\\s*"
    r"(?P<url>https://github\\.com/(?P<repository>[^/\\s]+/[^/\\s]+)/archive/"
    r"(?P<commit>[0-9a-f]{40})\\.zip)$"
)
_EXACT_REQUIREMENT = re.compile(
    r"^(?P<distribution>[A-Za-z0-9][A-Za-z0-9_.-]*)=="
    r"(?P<version>[^=<>!~*\\s]+)$"
)


class SkillDeploymentPlanError(ValueError):
    """Safe validation error for reviewed external Skill deployment metadata."""


@dataclass(frozen=True, slots=True)
class ReviewedSkillPackage:
    distribution: str
    source_kind: str
    source_url: str | None = None
    source_repository: str | None = None
    reviewed_commit: str | None = None
    exact_version: str | None = None

    @property
    def requirement(self) -> str:
        if self.source_kind == "github-commit" and self.source_url:
            return f"{self.distribution} @ {self.source_url}"
        if self.source_kind == "exact-version" and self.exact_version:
            return f"{self.distribution}=={self.exact_version}"
        raise SkillDeploymentPlanError(
            "Reviewed external Skill package has incomplete immutable source metadata."
        )

    def to_dict(self) -> dict[str, str]:
        result = {
            "distribution": self.distribution,
            "sourceKind": self.source_kind,
        }
        if self.source_url is not None:
            result["sourceUrl"] = self.source_url
        if self.source_repository is not None:
            result["sourceRepository"] = self.source_repository
        if self.reviewed_commit is not None:
            result["reviewedCommit"] = self.reviewed_commit
        if self.exact_version is not None:
            result["exactVersion"] = self.exact_version
        return result


def _normalized_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def parse_reviewed_skill_lock(text: str) -> tuple[ReviewedSkillPackage, ...]:
    """Parse immutable reviewed external Skill requirements.

    V1 supports full-commit GitHub archive pins and exact package-version pins.
    Moving branches/tags, short SHAs, version ranges and other source forms fail
    closed until an explicit reviewed contract supports them.
    """
    packages: list[ReviewedSkillPackage] = []
    seen: set[str] = set()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        github = _GITHUB_REQUIREMENT.fullmatch(line)
        exact = _EXACT_REQUIREMENT.fullmatch(line)
        if github is None and exact is None:
            raise SkillDeploymentPlanError(
                f"requirements-skills.lock:{line_number}: external Skill dependency "
                "is not an immutable reviewed pin."
            )

        match = github or exact
        assert match is not None
        distribution = match.group("distribution")
        key = _normalized_distribution(distribution)
        if key in seen:
            raise SkillDeploymentPlanError(
                f"requirements-skills.lock:{line_number}: duplicate external Skill distribution."
            )
        seen.add(key)

        if github is not None:
            packages.append(
                ReviewedSkillPackage(
                    distribution=distribution,
                    source_kind="github-commit",
                    source_url=github.group("url"),
                    source_repository=github.group("repository"),
                    reviewed_commit=github.group("commit"),
                )
            )
        else:
            packages.append(
                ReviewedSkillPackage(
                    distribution=distribution,
                    source_kind="exact-version",
                    exact_version=exact.group("version"),
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
