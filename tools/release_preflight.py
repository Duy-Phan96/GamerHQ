"""Read-only release metadata and external Skill pin preflight."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re

from hosts.gamerhq.skill_deployment import (
    SkillDeploymentPlanError,
    parse_reviewed_skill_lock,
)
from skill_runtime.contracts.manifest import SEMVER


ROOT = Path(__file__).resolve().parents[1]
VERSION_CHANNELS = frozenset({"develop", "candidate", "main"})
DEVELOPMENT_VERSION = re.compile(r"(?:^|[-.])dev(?:\.[0-9]+)?$")
RELEASE_CANDIDATE_VERSION = re.compile(r"-rc[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class ReleasePreflightResult:
    errors: tuple[str, ...]
    notices: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _first_changelog_section(text: str) -> str | None:
    parts = text.split("\n## ", 2)
    if len(parts) < 2:
        return None
    return parts[1].splitlines()[0].strip()


def validate_version_channel(version: str, expected_channel: str | None) -> tuple[str, ...]:
    """Validate the Host branch/release channel without creating a second version source."""
    if expected_channel is None:
        return ()
    if expected_channel not in VERSION_CHANNELS:
        raise ValueError("Unknown release version channel")

    development = bool(DEVELOPMENT_VERSION.search(version))
    candidate = bool(RELEASE_CANDIDATE_VERSION.search(version))

    if expected_channel == "develop" and not development:
        return ("develop VERSION must use the next planned semantic version with a -dev suffix",)
    if expected_channel == "candidate" and not candidate:
        return ("release-candidate VERSION must end with -rcN",)
    if expected_channel == "main" and development:
        return ("main VERSION must be an exact accepted version, not a -dev version",)
    return ()


def validate_skill_lock(text: str) -> tuple[str, ...]:
    try:
        parse_reviewed_skill_lock(text)
    except SkillDeploymentPlanError as exc:
        return (str(exc),)
    return ()


def check(
    *,
    version_path: Path | None = None,
    changelog_path: Path | None = None,
    skill_lock_path: Path | None = None,
    expected_channel: str | None = None,
) -> ReleasePreflightResult:
    version_path = version_path or ROOT / "VERSION"
    changelog_path = changelog_path or ROOT / "CHANGELOG.md"
    skill_lock_path = skill_lock_path or ROOT / "requirements-skills.lock"

    errors: list[str] = []
    notices: list[str] = []

    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        errors.append("VERSION could not be read")
    else:
        if not SEMVER.fullmatch(version):
            errors.append("VERSION does not match the supported semantic-version syntax")
        else:
            notices.append(f"Version metadata valid: {version}")
            channel_errors = validate_version_channel(version, expected_channel)
            errors.extend(channel_errors)
            if expected_channel and not channel_errors:
                notices.append(f"Version channel valid for {expected_channel}")

    try:
        changelog = changelog_path.read_text(encoding="utf-8")
    except OSError:
        errors.append("CHANGELOG.md could not be read")
    else:
        if _first_changelog_section(changelog) != "[Unreleased]":
            errors.append("CHANGELOG.md must start with the canonical [Unreleased] section")
        else:
            notices.append("Current changelog section is [Unreleased]")

    try:
        skill_lock = skill_lock_path.read_text(encoding="utf-8")
    except OSError:
        errors.append("requirements-skills.lock could not be read")
    else:
        lock_errors = validate_skill_lock(skill_lock)
        errors.extend(lock_errors)
        if not lock_errors:
            notices.append("External Skill lock passed reviewed deployment validation")

    return ReleasePreflightResult(tuple(errors), tuple(notices))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-channel", choices=sorted(VERSION_CHANNELS))
    args = parser.parse_args()

    result = check(expected_channel=args.expect_channel)
    for notice in result.notices:
        print(notice)
    for error in result.errors:
        print("ERROR: " + error)
    if result.errors:
        return 1
    print("Release preflight passed. No files were modified and no deployment was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
