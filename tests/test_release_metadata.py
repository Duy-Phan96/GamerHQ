import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_release_notes_read_current_release_section(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        current_section = changelog.split("\n## ", 2)[1]
        pattern = rf"^## \[{re.escape(get_version())}\](?: - [^\n]+)?\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, changelog, re.MULTILINE | re.DOTALL)
        if match:
            current_section = match.group(1)
        expected = [
            "• " + line[2:180]
            for line in current_section.splitlines()
            if line.startswith("- ")
        ][:3]

        notes = release_notes()
        self.assertEqual(notes.splitlines(), expected or ["• Maintenance and reliability improvements."])
        self.assertNotIn("Recurring Posts 1.1 integration", notes)

    def _fixture_notes(self, version, changelog):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
            with patch("release_info.ROOT", root), patch("release_info.get_version", return_value=version):
                return release_notes()

    def test_candidate_uses_exact_version_when_unreleased_is_empty(self):
        notes = self._fixture_notes(
            "1.0.0-beta.2-rc4",
            "# Changelog\n\n## [Unreleased]\n\n"
            "## [1.0.0-beta.2-rc3]\n- Old release\n\n"
            "## [1.0.0-beta.2-rc4] - 2026-11-26\n"
            "- First fix\n- Second fix\n- Third fix\n- Fourth fix\n"
            "## [Unreleased] - 2026-09-23\n- Historical work\n",
        )
        self.assertEqual(notes, "• First fix\n• Second fix\n• Third fix")

    def test_released_version_ignores_new_unreleased_work(self):
        notes = self._fixture_notes(
            "1.0.0",
            "# Changelog\n\n## [Unreleased]\n- Future work\n\n"
            "## [1.0.0]\n- Released fix\n",
        )
        self.assertEqual(notes, "• Released fix")

    def test_develop_and_legacy_candidates_use_canonical_unreleased(self):
        changelog = (
            "# Changelog\n\n## [Unreleased]\n- Current work\n\n"
            "## [Unreleased] - 2026-09-23\n- Historical work\n"
        )
        for version in ("1.0.0-beta.2-dev", "1.0.0-beta.2-rc3"):
            with self.subTest(version=version):
                self.assertEqual(self._fixture_notes(version, changelog), "• Current work")

    def test_empty_current_section_does_not_read_old_release_notes(self):
        notes = self._fixture_notes(
            "1.0.0-beta.2-dev",
            "# Changelog\n\n## [Unreleased]\n\n"
            "## [1.0.0-beta.1]\n- Old release\n",
        )
        self.assertEqual(notes, "• Maintenance and reliability improvements.")


if __name__ == "__main__":
    unittest.main()
