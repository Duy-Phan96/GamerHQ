"""Read-only release readiness report composed from existing release checks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from hosts.gamerhq.skill_deployment import (
    SkillDeploymentPlanError,
    deployment_plan,
    load_reviewed_skill_lock,
)
from tools.release_preflight import ROOT, check


SCHEMA_VERSION = "1"


def build_release_readiness(
    *,
    version_path: Path | None = None,
    changelog_path: Path | None = None,
    skill_lock_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded machine-readable release readiness report.

    This composes existing authoritative validators. It does not create releases,
    tags, packages or deployments and does not perform live acceptance.
    """
    version_path = version_path or ROOT / "VERSION"
    changelog_path = changelog_path or ROOT / "CHANGELOG.md"
    skill_lock_path = skill_lock_path or ROOT / "requirements-skills.lock"

    preflight = check(
        version_path=version_path,
        changelog_path=changelog_path,
        skill_lock_path=skill_lock_path,
    )

    version: str | None = None
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass

    external_skills: dict[str, Any] | None = None
    skill_error: str | None = None
    try:
        packages = load_reviewed_skill_lock(skill_lock_path)
    except SkillDeploymentPlanError as exc:
        skill_error = str(exc)
    else:
        external_skills = deployment_plan(packages)

    errors = list(preflight.errors)
    if skill_error is not None and skill_error not in errors:
        errors.append(skill_error)

    ready = not errors and external_skills is not None

    return {
        "schemaVersion": SCHEMA_VERSION,
        "readyForManualAcceptance": ready,
        "version": version,
        "preflight": {
            "ok": preflight.ok,
            "errors": list(preflight.errors),
            "notices": list(preflight.notices),
        },
        "externalSkills": external_skills,
        "errors": errors,
        "manualAcceptanceRequired": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    report = build_release_readiness()
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        version = report["version"] or "unavailable"
        print(f"Release readiness for {version}")
        print(
            "Automated release metadata: "
            + ("PASS" if report["preflight"]["ok"] else "FAIL")
        )
        external_skills = report["externalSkills"]
        if external_skills is None:
            print("Reviewed external Skill deployment plan: FAIL")
        else:
            print(
                "Reviewed external Skill packages: "
                f"{len(external_skills['packages'])}"
            )
            print(
                "Image rebuild required: "
                + ("yes" if external_skills["requiresImageRebuild"] else "no")
            )

        if report["readyForManualAcceptance"]:
            print("Ready for manual release acceptance: yes")
            print("Manual Discord/VPS acceptance is still required.")
            return 0

        print("Ready for manual release acceptance: no")
        for error in report["errors"]:
            print("ERROR: " + error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
