import unittest
from pathlib import Path

from release_info import get_version, release_notes
from skill_runtime.contracts.manifest import SEMVER


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_uses_semantic_version_syntax(self):
        version = get_version()
        self.assertRegex(version, SEMVER)

    def test_changelog_starts_with_canonical_unreleased_section(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        first_section = changelog.split("\n## ", 2)[1].splitlines()[0]
        self.assertEqual(first_section, "[Unreleased]")

    def test_release_notes_read_current_unreleased_bullets(self):
        notes = release_notes()
        self.assertIn("Software Development Lifecycle", notes)
        self.assertNotIn("Recurring Posts 1.1 integration", notes)


if __name__ == "__main__":
    unittest.main()
