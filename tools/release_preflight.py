"""Read-only release metadata and external Skill pin preflight."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from release_info import get_version
from skill_runtime.contracts.manifest import SEMVER


ROOT = Path(__file__).resolve().parents[1]
GITHUB_ARCHIVE = re.compile(
    r"^https://github\.com/[^/]+/[^/]+/archive/([0-9a-f]{40})\.zip$"
)
EXACT_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.-]+==[^=<>!~*\s]+$")


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


def validate_skill_lock(text: str) -> tuple[str, ...]:
    errors: list[str] = []
    entries = 0

    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries += 1

        if " @ " in line:
            package, url = line.split(" @ ", 1)
            if not package.strip():
                errors.append(f"requirements-skills.lock:{line_number}: missing package name")
                continue
            if not GITHUB_ARCHIVE.fullmatch(url.strip()):
                errors.append(
                    f"requirements-skills.lock:{line_number}: external Skill URL must pin a full 40-character Git commit archive"
                )
            continue

        if EXACT_REQUIREMENT.fullmatch(line):
            continue

        errors.append(
            f"requirements-skills.lock:{line_number}: external Skill dependency is not an immutable exact pin"
        )

    if entries == 0:
        return ()
    return tuple(errors)


def check(
    *,
    version_path: Path | None = None,
    changelog_path: Path | None = None,
    skill_lock_path: Path | None = None,
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
            notices.append("External Skill lock uses immutable exact pins")

    return ReleasePreflightResult(tuple(errors), tuple(notices))


def main() -> int:
    result = check()
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
