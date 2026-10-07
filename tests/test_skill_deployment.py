import json
import tempfile
import unittest
from pathlib import Path

from hosts.gamerhq.skill_deployment import (
    SkillDeploymentPlanError,
    deployment_plan,
    load_reviewed_skill_lock,
    parse_reviewed_skill_lock,
)


ROOT = Path(__file__).resolve().parents[1]


class ReviewedSkillDeploymentPlanTests(unittest.TestCase):
    def test_current_lock_parses_to_immutable_reviewed_plan(self):
        packages = load_reviewed_skill_lock(ROOT / "requirements-skills.lock")
        self.assertEqual(len(packages), 2)

        by_distribution = {package.distribution: package for package in packages}
        recurring = by_distribution["gamerhq-skill-recurring-posts"]
        progression = by_distribution["gamerhq-skill-xp-progression"]

        self.assertEqual(recurring.source_kind, "github-commit")
        self.assertEqual(
            recurring.source_repository,
            "Duy-Phan96/gamerhq-skill-recurring-posts",
        )
        self.assertEqual(
            recurring.reviewed_commit,
            "7656616a3259d70bc09ac10446cb0965d9ec6a6c",
        )
        self.assertEqual(progression.source_kind, "github-commit")
        self.assertEqual(
            progression.source_repository,
            "Duy-Phan96/gamerhq-skill-xp-progression",
        )
        self.assertEqual(
            progression.reviewed_commit,
            "565ee8379cdd22cb00c18db188eeaddd058e626d",
        )

        plan = deployment_plan(packages)
        self.assertEqual(plan["schemaVersion"], "1")
        self.assertTrue(plan["requiresImageRebuild"])
        self.assertFalse(plan["runtimeInstallAllowed"])
        reviewed = {item["distribution"]: item["reviewedCommit"] for item in plan["packages"]}
        self.assertEqual(reviewed["gamerhq-skill-recurring-posts"], recurring.reviewed_commit)
        self.assertEqual(reviewed["gamerhq-skill-xp-progression"], progression.reviewed_commit)
        json.dumps(plan)

    def test_comments_and_blank_lines_are_ignored(self):
        packages = parse_reviewed_skill_lock(
            """
# reviewed
gamerhq-skill-example @ https://github.com/example/example/archive/0123456789abcdef0123456789abcdef01234567.zip

"""
        )
        self.assertEqual(len(packages), 1)

    def test_exact_package_version_is_supported_as_immutable_source(self):
        packages = parse_reviewed_skill_lock(
            "gamerhq-skill-example==1.2.3\n"
        )
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertEqual(package.source_kind, "exact-version")
        self.assertEqual(package.exact_version, "1.2.3")
        self.assertEqual(package.requirement, "gamerhq-skill-example==1.2.3")
        self.assertEqual(
            deployment_plan(packages)["packages"][0]["exactVersion"],
            "1.2.3",
        )

    def test_version_range_fails_closed(self):
        with self.assertRaisesRegex(SkillDeploymentPlanError, "immutable reviewed pin"):
            parse_reviewed_skill_lock("gamerhq-skill-example>=1.2\n")

    def test_moving_branch_fails_closed(self):
        with self.assertRaisesRegex(SkillDeploymentPlanError, "non-immutable"):
            parse_reviewed_skill_lock(
                "gamerhq-skill-example @ https://github.com/example/example/archive/main.zip"
            )

    def test_short_commit_fails_closed(self):
        with self.assertRaisesRegex(SkillDeploymentPlanError, "non-immutable"):
            parse_reviewed_skill_lock(
                "gamerhq-skill-example @ https://github.com/example/example/archive/01234567.zip"
            )

    def test_duplicate_distribution_fails_closed(self):
        line = (
            "gamerhq-skill-example @ "
            "https://github.com/example/example/archive/"
            "0123456789abcdef0123456789abcdef01234567.zip"
        )
        with self.assertRaisesRegex(SkillDeploymentPlanError, "duplicate"):
            parse_reviewed_skill_lock(
                f"{line}\ngamerhq_skill_example==1.2.3\n"
            )

    def test_missing_lock_has_safe_error(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SkillDeploymentPlanError, "could not be read"):
                load_reviewed_skill_lock(Path(temp) / "missing.lock")


if __name__ == "__main__":
    unittest.main()
