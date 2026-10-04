import tempfile
import unittest
from pathlib import Path

from skill_runtime import (
    SkillCapability,
    SkillConformanceError,
    SkillHealth,
    SkillManifest,
    validate_skill_package_matches_implementation,
    validate_skill_package_metadata,
)


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
capabilities = ["storage.skill"]
"""

    def fixture_skill(self):
        class FixtureSkill:
            manifest = SkillManifest(
                id="example-skill",
                name="Example",
                version="0.1.0",
                runtime_api_version="1",
                description="fixture",
                author="test",
                permissions=(SkillCapability.STORAGE_SKILL.value,),
            )

            async def register(self, ctx): pass
            async def enable(self, ctx): pass
            async def disable(self, ctx): pass
            async def start(self, ctx): pass
            async def stop(self, ctx): pass
            async def health_check(self, ctx): return SkillHealth("PASS", "ok")

        return FixtureSkill()

    def test_valid_metadata_reports_package_contract(self):
        report = validate_skill_package_metadata(self.write(self.valid()))
        self.assertEqual(report.package_name, "gamerhq-skill-example")
        self.assertEqual(report.skill_id, "example-skill")
        self.assertEqual(report.entry_point, "gamerhq_skill_example:create_skill")
        self.assertEqual(report.capabilities, ("storage.skill",))

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

    def test_unknown_capability_fails_closed(self):
        body = self.valid().replace("storage.skill", "not.a.capability")
        with self.assertRaisesRegex(SkillConformanceError, "unknown capability"):
            validate_skill_package_metadata(self.write(body))

    def test_duplicate_capabilities_fail_closed(self):
        body = self.valid().replace(
            'capabilities = ["storage.skill"]',
            'capabilities = ["storage.skill", "storage.skill"]',
        )
        with self.assertRaisesRegex(SkillConformanceError, "duplicates"):
            validate_skill_package_metadata(self.write(body))

    def test_static_capabilities_must_match_executable_manifest(self):
        report = validate_skill_package_matches_implementation(
            self.write(self.valid()),
            self.fixture_skill(),
        )
        self.assertEqual(report.capabilities, ("storage.skill",))

        mismatch = self.valid().replace("storage.skill", "scheduler.jobs")
        with self.assertRaisesRegex(SkillConformanceError, "capabilities do not match"):
            validate_skill_package_matches_implementation(
                self.write(mismatch),
                self.fixture_skill(),
            )

    def test_missing_sdk_compatibility_fails_closed(self):
        body = self.valid().replace('sdk = ">=0.1,<0.2"\n', "")
        with self.assertRaisesRegex(SkillConformanceError, "missing or invalid"):
            validate_skill_package_metadata(self.write(body))


if __name__ == "__main__":
    unittest.main()
