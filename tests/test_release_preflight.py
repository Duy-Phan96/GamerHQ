import tempfile
import unittest
from pathlib import Path

from tools.release_preflight import check, validate_skill_lock


class ReleasePreflightTests(unittest.TestCase):
    def test_skill_lock_accepts_full_git_commit_archives(self):
        errors = validate_skill_lock(
            "gamerhq-skill-example @ "
            "https://github.com/example/gamerhq-skill-example/archive/"
            "0123456789abcdef0123456789abcdef01234567.zip\n"
        )
        self.assertEqual(errors, ())

    def test_skill_lock_accepts_exact_package_versions(self):
        self.assertEqual(
            validate_skill_lock("gamerhq-skill-example==1.2.3\n"),
            (),
        )

    def test_skill_lock_rejects_moving_or_unpinned_dependencies(self):
        cases = (
            "gamerhq-skill-example @ https://github.com/example/repo/archive/main.zip\n",
            "gamerhq-skill-example>=1.2\n",
            "gamerhq-skill-example\n",
        )
        for body in cases:
            with self.subTest(body=body):
                self.assertTrue(validate_skill_lock(body))

    def test_check_validates_release_metadata_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "VERSION"
            changelog = root / "CHANGELOG.md"
            lock = root / "requirements-skills.lock"

            version.write_text("1.2.3\n", encoding="utf-8")
            changelog.write_text(
                "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Test\n",
                encoding="utf-8",
            )
            lock.write_text(
                "gamerhq-skill-example @ "
                "https://github.com/example/gamerhq-skill-example/archive/"
                "0123456789abcdef0123456789abcdef01234567.zip\n",
                encoding="utf-8",
            )
            before = {
                path: path.read_bytes()
                for path in (version, changelog, lock)
            }

            result = check(
                version_path=version,
                changelog_path=changelog,
                skill_lock_path=lock,
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.errors, ())
            for path, contents in before.items():
                self.assertEqual(path.read_bytes(), contents)

    def test_check_reports_invalid_version_changelog_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "VERSION"
            changelog = root / "CHANGELOG.md"
            lock = root / "requirements-skills.lock"

            version.write_text("not-a-version\n", encoding="utf-8")
            changelog.write_text("# Changelog\n\n## [1.0.0]\n", encoding="utf-8")
            lock.write_text(
                "gamerhq-skill-example @ https://github.com/example/repo/archive/main.zip\n",
                encoding="utf-8",
            )

            result = check(
                version_path=version,
                changelog_path=changelog,
                skill_lock_path=lock,
            )

            self.assertFalse(result.ok)
            self.assertEqual(len(result.errors), 3)


if __name__ == "__main__":
    unittest.main()
