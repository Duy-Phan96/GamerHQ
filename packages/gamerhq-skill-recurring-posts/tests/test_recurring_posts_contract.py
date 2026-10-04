import unittest
from pathlib import Path

from gamerhq_skill_recurring_posts import RecurringPostsSkill, create_skill
from skill_runtime import (
    require_clean_skill_source,
    validate_skill_factory,
    validate_skill_package_matches_implementation,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "gamerhq_skill_recurring_posts"


class RecurringPostsPackageContractTests(unittest.TestCase):
    def test_source_stays_portable(self):
        report = require_clean_skill_source((PACKAGE,))
        self.assertTrue(report.passed)

    def test_factory_matches_stable_skill_identity(self):
        report = validate_skill_factory(
            create_skill,
            expected_skill_id="recurring-posts",
        )
        self.assertEqual(report.skill_id, "recurring-posts")
        self.assertEqual(report.version, "1.0.0")

    def test_static_package_metadata_matches_manifest(self):
        report = validate_skill_package_matches_implementation(
            ROOT / "pyproject.toml",
            RecurringPostsSkill(),
        )
        self.assertEqual(report.skill_id, "recurring-posts")
        self.assertIn("scheduler.jobs", report.capabilities)


if __name__ == "__main__":
    unittest.main()
