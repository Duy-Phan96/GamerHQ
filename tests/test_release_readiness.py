import json
import tempfile
import unittest
from pathlib import Path

from tools.release_readiness import build_release_readiness


class ReleaseReadinessTests(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        return (
            root / "VERSION",
            root / "CHANGELOG.md",
            root / "requirements-skills.lock",
        )

    def _valid_changelog(self, path: Path) -> None:
        path.write_text(
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Test\n",
            encoding="utf-8",
        )

    def _valid_lock(self, path: Path) -> None:
        path.write_text(
            "gamerhq-skill-example @ "
            "https://github.com/example/gamerhq-skill-example/archive/"
            "0123456789abcdef0123456789abcdef01234567.zip\n",
            encoding="utf-8",
        )

    def test_valid_candidate_is_ready_for_manual_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version, changelog, lock = self._paths(root)
            version.write_text("1.2.3-rc1\n", encoding="utf-8")
            self._valid_changelog(changelog)
            self._valid_lock(lock)

            report = build_release_readiness(
                version_path=version,
                changelog_path=changelog,
                skill_lock_path=lock,
            )

            self.assertEqual(report["schemaVersion"], "1")
            self.assertTrue(report["readyForManualAcceptance"])
            self.assertTrue(report["preflight"]["ok"])
            self.assertTrue(report["manualAcceptanceRequired"])
            self.assertEqual(report["version"], "1.2.3-rc1")
            self.assertEqual(report["versionChannel"], "candidate")
            self.assertEqual(
                report["externalSkills"]["packages"][0]["sourceKind"],
                "github-commit",
            )
            json.dumps(report)

    def test_exact_main_version_can_be_checked_after_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version, changelog, lock = self._paths(root)
            version.write_text("1.2.3\n", encoding="utf-8")
            self._valid_changelog(changelog)
            self._valid_lock(lock)

            report = build_release_readiness(
                version_path=version,
                changelog_path=changelog,
                skill_lock_path=lock,
                expected_channel="main",
            )

            self.assertTrue(report["readyForManualAcceptance"])
            self.assertEqual(report["versionChannel"], "main")

    def test_develop_version_is_not_a_release_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version, changelog, lock = self._paths(root)
            version.write_text("1.2.4-dev\n", encoding="utf-8")
            self._valid_changelog(changelog)
            self._valid_lock(lock)

            report = build_release_readiness(
                version_path=version,
                changelog_path=changelog,
                skill_lock_path=lock,
            )

            self.assertFalse(report["readyForManualAcceptance"])
            self.assertFalse(report["preflight"]["ok"])
            self.assertTrue(any("-rcN" in error for error in report["errors"]))

    def test_readiness_rejects_develop_channel_argument(self):
        with self.assertRaisesRegex(ValueError, "candidate or main"):
            build_release_readiness(expected_channel="develop")

    def test_invalid_skill_lock_fails_closed_without_echoing_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version, changelog, lock = self._paths(root)
            version.write_text("1.2.3-rc1\n", encoding="utf-8")
            self._valid_changelog(changelog)
            rejected = (
                "gamerhq-skill-example @ "
                "https://github.com/example/private-repo/archive/main.zip"
            )
            lock.write_text(rejected + "\n", encoding="utf-8")

            report = build_release_readiness(
                version_path=version,
                changelog_path=changelog,
                skill_lock_path=lock,
            )

            self.assertFalse(report["readyForManualAcceptance"])
            self.assertFalse(report["preflight"]["ok"])
            self.assertIsNone(report["externalSkills"])
            rendered = json.dumps(report)
            self.assertNotIn("private-repo", rendered)
            self.assertNotIn("main.zip", rendered)

    def test_invalid_release_metadata_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version, changelog, lock = self._paths(root)
            version.write_text("invalid\n", encoding="utf-8")
            changelog.write_text("# Changelog\n\n## [1.0.0]\n", encoding="utf-8")
            lock.write_text("gamerhq-skill-example==1.2.3\n", encoding="utf-8")

            report = build_release_readiness(
                version_path=version,
                changelog_path=changelog,
                skill_lock_path=lock,
            )

            self.assertFalse(report["readyForManualAcceptance"])
            self.assertFalse(report["preflight"]["ok"])
            self.assertIsNotNone(report["externalSkills"])
            self.assertTrue(report["manualAcceptanceRequired"])


if __name__ == "__main__":
    unittest.main()
