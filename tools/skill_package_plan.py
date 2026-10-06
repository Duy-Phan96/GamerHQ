"""Print the reviewed external Skill deployment plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hosts.gamerhq.skill_deployment import (
    SkillDeploymentPlanError,
    deployment_plan,
    load_reviewed_skill_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path("requirements-skills.lock"),
        help="Reviewed external Skill requirements lock.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    try:
        packages = load_reviewed_skill_lock(args.lock)
    except SkillDeploymentPlanError as exc:
        print(f"ERROR: {exc}")
        return 1

    plan = deployment_plan(packages)
    if args.json:
        print(json.dumps(plan, sort_keys=True))
    else:
        print(
            f"Reviewed external Skill packages: {len(packages)} "
            "(image rebuild required; runtime install disabled)"
        )
        for package in packages:
            if package.source_kind == "github-commit":
                source = f"{package.source_repository}@{package.reviewed_commit}"
            else:
                source = f"{package.distribution}=={package.exact_version}"
            print(f"- {package.distribution} {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
