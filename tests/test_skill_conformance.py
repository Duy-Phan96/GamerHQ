import unittest

from skill_runtime import (
    SkillConformanceError,
    SkillHealth,
    SkillManifest,
    validate_skill_factory,
    validate_skill_implementation,
)


class GoodSkill:
    manifest = SkillManifest(
        id="good-skill",
        name="Good Skill",
        version="1.2.3",
        runtime_api_version="1",
        description="fixture",
        author="test",
    )

    async def register(self, ctx): pass
    async def enable(self, ctx): pass
    async def disable(self, ctx): pass
    async def start(self, ctx): pass
    async def stop(self, ctx): pass
    async def health_check(self, ctx): return SkillHealth("PASS", "ok")


class MissingStop(GoodSkill):
    stop = None


class SyncHealth(GoodSkill):
    def health_check(self, ctx):
        return SkillHealth("PASS", "wrong shape")


class SkillConformanceTests(unittest.TestCase):
    def test_valid_skill_returns_public_contract_summary(self):
        report = validate_skill_implementation(GoodSkill())
        self.assertEqual(report.skill_id, "good-skill")
        self.assertEqual(report.version, "1.2.3")
        self.assertEqual(report.runtime_api_version, "1")
        self.assertIn("register", report.lifecycle_methods)
        self.assertIn("health_check", report.lifecycle_methods)

    def test_missing_lifecycle_method_is_rejected(self):
        with self.assertRaisesRegex(SkillConformanceError, "missing: stop"):
            validate_skill_implementation(MissingStop())

    def test_sync_lifecycle_method_is_rejected(self):
        with self.assertRaisesRegex(SkillConformanceError, "must be async: health_check"):
            validate_skill_implementation(SyncHealth())

    def test_factory_expected_id_must_match(self):
        with self.assertRaisesRegex(SkillConformanceError, "expected Skill ID"):
            validate_skill_factory(GoodSkill, expected_skill_id="different-skill")

    def test_factory_exception_does_not_leak_private_detail(self):
        marker = "private-factory-detail"
        def broken():
            raise RuntimeError(marker)
        with self.assertRaises(SkillConformanceError) as caught:
            validate_skill_factory(broken)
        self.assertNotIn(marker, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
