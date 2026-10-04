import tempfile
import unittest
from pathlib import Path

from skill_runtime import SkillConformanceError, validate_skill_package_metadata


class SkillPackageMetadataTests(unittest.TestCase):
    def write(self, body):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "pyproject.toml").write_text(body, encoding="utf-8")
        self.addCleanup(temp.cleanup)
        return root

    def valid(self):
        return """
[project]
name = "gamerhq-skill-example"
version = "0.1.0"

[project.entry-points."gamerhq.skills"]
example-skill = "gamerhq_skill_example:create_skill"

[tool.gamerhq]
skill-id = "example-skill"
runtime-api = "1"
sdk = ">=0.1,<0.2"
"""

    def test_valid_metadata_reports_package_contract(self):
        report = validate_skill_package_metadata(self.write(self.valid()))
        self.assertEqual(report.package_name, "gamerhq-skill-example")
        self.assertEqual(report.skill_id, "example-skill")
        self.assertEqual(report.entry_point, "gamerhq_skill_example:create_skill")

    def test_entry_point_must_match_skill_id(self):
        body = self.valid().replace(
            'example-skill = "gamerhq_skill_example:create_skill"',
            'different-skill = "gamerhq_skill_example:create_skill"',
        )
        with self.assertRaisesRegex(SkillConformanceError, "must match"):
            validate_skill_package_metadata(self.write(body))

    def test_exactly_one_skill_per_distribution(self):
        body = self.valid().replace(
            'example-skill = "gamerhq_skill_example:create_skill"',
            'example-skill = "gamerhq_skill_example:create_skill"\nother-skill = "other:create_skill"',
        )
        with self.assertRaisesRegex(SkillConformanceError, "exactly one"):
            validate_skill_package_metadata(self.write(body))

    def test_missing_sdk_compatibility_fails_closed(self):
        body = self.valid().replace('sdk = ">=0.1,<0.2"\n', "")
        with self.assertRaisesRegex(SkillConformanceError, "missing or invalid"):
            validate_skill_package_metadata(self.write(body))


if __name__ == "__main__":
    unittest.main()
