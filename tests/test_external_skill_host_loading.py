import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hosts.gamerhq.skill_packages import BUNDLED_SKILL_IDS, configured_skill_ids, load_external_skill_packages
from skill_runtime.runtime.packages import InstalledSkillPackage, SkillPackageError


class FakeRuntime:
    def __init__(self):
        self.registered = []
        self.unavailable = []

    def register(self, skill, *, source_kind, source_distribution):
        self.registered.append(
            (skill.manifest.id, source_kind, source_distribution)
        )

    def record_external_unavailable(self, skill_id):
        self.unavailable.append(skill_id)


class ExternalSkillHostLoadingTests(unittest.TestCase):
    def test_recurring_posts_is_a_bundled_external_package(self):
        self.assertIn("recurring-posts", BUNDLED_SKILL_IDS)
        self.assertIn("awin-affiliate", BUNDLED_SKILL_IDS)
        self.assertEqual(
            configured_skill_ids(("recurring-posts", "other-skill")),
            ("recurring-posts", "progression", "awin-affiliate", "other-skill"),
        )

    def test_one_broken_package_does_not_block_healthy_package(self):
        runtime = FakeRuntime()
        healthy_skill = SimpleNamespace(
            manifest=SimpleNamespace(id="healthy-skill")
        )

        def discover(ids):
            skill_id = ids[0]
            if skill_id == "broken-skill":
                raise SkillPackageError("safe")
            return (
                InstalledSkillPackage(
                    entry_point="healthy-skill",
                    distribution="gamerhq-skill-healthy",
                    skill=healthy_skill,
                ),
            )

        with patch(
            "hosts.gamerhq.skill_packages.discover_installed_skills",
            side_effect=discover,
        ):
            report = load_external_skill_packages(
                runtime,
                ("broken-skill", "healthy-skill"),
            )

        self.assertEqual(report.loaded, ("healthy-skill",))
        self.assertEqual(report.unavailable, ("broken-skill",))
        self.assertEqual(runtime.unavailable, ["broken-skill"])
        self.assertEqual(
            runtime.registered,
            [("healthy-skill", "external", "gamerhq-skill-healthy")],
        )

    def test_registration_validation_failure_is_isolated(self):
        class RejectingRuntime(FakeRuntime):
            def register(self, skill, *, source_kind, source_distribution):
                raise ValueError("synthetic validation failure")

        runtime = RejectingRuntime()
        package = InstalledSkillPackage(
            entry_point="bad-skill",
            distribution="gamerhq-skill-bad",
            skill=SimpleNamespace(manifest=SimpleNamespace(id="bad-skill")),
        )
        with patch(
            "hosts.gamerhq.skill_packages.discover_installed_skills",
            return_value=(package,),
        ):
            report = load_external_skill_packages(runtime, ("bad-skill",))

        self.assertEqual(report.loaded, ())
        self.assertEqual(report.unavailable, ("bad-skill",))
        self.assertEqual(runtime.unavailable, ["bad-skill"])


if __name__ == "__main__":
    unittest.main()
