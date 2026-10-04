import unittest
from types import SimpleNamespace
from unittest.mock import patch

from skill_runtime.contracts.manifest import SkillManifest
from skill_runtime.runtime.packages import (
    ENTRY_POINT_GROUP,
    SkillPackageError,
    discover_installed_skills,
)


class FakeSkill:
    def __init__(self, skill_id):
        self.manifest = SkillManifest(
            id=skill_id,
            name=skill_id.title(),
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
        )


class FakeEntryPoint:
    def __init__(self, name, factory, dist="fixture-dist"):
        self.name = name
        self._factory = factory
        self.dist = SimpleNamespace(name=dist)

    def load(self):
        return self._factory


class FakeEntryPoints(tuple):
    def select(self, *, group):
        return self if group == ENTRY_POINT_GROUP else ()


class ExternalSkillPackageTests(unittest.TestCase):
    def discover(self, enabled, entry_points):
        with patch(
            "skill_runtime.runtime.packages.metadata.entry_points",
            return_value=FakeEntryPoints(entry_points),
        ):
            return discover_installed_skills(enabled)

    def test_only_explicit_allowlisted_skill_is_loaded(self):
        alpha = FakeEntryPoint("alpha-skill", lambda: FakeSkill("alpha-skill"))
        beta = FakeEntryPoint("beta-skill", lambda: FakeSkill("beta-skill"))
        loaded = self.discover(("beta-skill",), (alpha, beta))
        self.assertEqual([item.entry_point for item in loaded], ["beta-skill"])
        self.assertEqual(loaded[0].distribution, "fixture-dist")

    def test_missing_installed_package_fails_closed(self):
        with self.assertRaisesRegex(SkillPackageError, "not installed"):
            self.discover(("missing-skill",), ())

    def test_entry_point_name_must_match_manifest_id(self):
        wrong = FakeEntryPoint("expected-skill", lambda: FakeSkill("different-skill"))
        with self.assertRaisesRegex(SkillPackageError, "manifest ID"):
            self.discover(("expected-skill",), (wrong,))

    def test_duplicate_installed_entry_points_fail_closed(self):
        one = FakeEntryPoint("same-skill", lambda: FakeSkill("same-skill"), "one")
        two = FakeEntryPoint("same-skill", lambda: FakeSkill("same-skill"), "two")
        with self.assertRaisesRegex(SkillPackageError, "Multiple installed packages"):
            self.discover(("same-skill",), (one, two))

    def test_broken_factory_returns_safe_error_without_private_exception(self):
        marker = "private-import-detail"
        def broken():
            raise RuntimeError(marker)
        entry = FakeEntryPoint("broken-skill", broken)
        with self.assertRaises(SkillPackageError) as caught:
            self.discover(("broken-skill",), (entry,))
        self.assertNotIn(marker, str(caught.exception))

    def test_invalid_or_duplicate_allowlist_is_rejected_before_loading(self):
        with self.assertRaisesRegex(SkillPackageError, "invalid"):
            self.discover(("Bad Skill",), ())
        with self.assertRaisesRegex(SkillPackageError, "duplicate"):
            self.discover(("alpha-skill", "alpha-skill"), ())


if __name__ == "__main__":
    unittest.main()
