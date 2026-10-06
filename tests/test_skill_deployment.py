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
        self.assertEqual(len(packages), 1)

        package = packages[0]
        self.assertEqual(package.distribution, "gamerhq-skill-recurring-posts")
        self.assertEqual(
            package.source_repository,
            "Duy-Phan96/gamerhq-skill-recurring-posts",
        )
        self.assertEqual(
            package.reviewed_commit,
            "7656616a3259d70bc09ac10446cb0965d9ec6a6c",
        )

        plan = deployment_plan(packages)
        self.assertEqual(plan["schemaVersion"], "1")
        self.assertTrue(plan["requiresImageRebuild"])
        self.assertFalse(plan["runtimeInstallAllowed"])
        self.assertEqual(
            plan["packages"][0]["reviewedCommit"],
            package.reviewed_commit,
        )
        json.dumps(plan)

    def test_comments_and_blank_lines_are_ignored(self):
        packages = parse_reviewed_skill_lock(
            """
# reviewed
gamerhq-skill-example @ https://github.com/example/example/archive/0123456789abcdef0123456789abcdef01234567.zip

"""
        )
        self.assertEqual(len(packages), 1)

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
            parse_reviewed_skill_lock(f"{line}\n{line}\n")

    def test_missing_lock_has_safe_error(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SkillDeploymentPlanError, "could not be read"):
                load_reviewed_skill_lock(Path(temp) / "missing.lock")


if __name__ == "__main__":
    unittest.main()
