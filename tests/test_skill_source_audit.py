import tempfile
import unittest
from pathlib import Path

from skill_runtime import (
    SkillConformanceError,
    audit_skill_source,
    require_clean_skill_source,
)


class SkillSourceAuditTests(unittest.TestCase):
    def source(self, text):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        path = root / "skill.py"
        path.write_text(text, encoding="utf-8")
        self.addCleanup(temp.cleanup)
        return root

    def test_clean_sdk_only_source_passes(self):
        root = self.source(
            "from skill_runtime import SkillManifest\n"
            "class Example:\n"
            "    pass\n"
        )
        report = audit_skill_source((root,))
        self.assertTrue(report.passed)
        self.assertEqual(report.files_checked, 1)

    def test_host_and_discord_imports_are_rejected(self):
        root = self.source(
            "import discord\n"
            "from database import db\n"
            "from services.foo import bar\n"
        )
        report = audit_skill_source((root,))
        self.assertFalse(report.passed)
        details = [finding.detail for finding in report.findings]
        self.assertIn("Portable Skills must not import discord.", details)
        self.assertIn("Portable Skills must not import database.", details)
        self.assertIn("Portable Skills must not import services.", details)

    def test_environment_and_shell_escape_calls_are_rejected(self):
        root = self.source(
            "import os\n"
            "TOKEN = os.getenv('TOKEN')\n"
            "VALUE = os.environ['VALUE']\n"
            "os.system('echo bad')\n"
        )
        report = audit_skill_source((root,))
        rules = [finding.rule for finding in report.findings]
        self.assertGreaterEqual(rules.count("host.escape"), 3)

    def test_subprocess_and_dotenv_are_rejected(self):
        root = self.source(
            "import subprocess\n"
            "from dotenv import load_dotenv\n"
        )
        report = audit_skill_source((root,))
        details = [finding.detail for finding in report.findings]
        self.assertIn("Portable Skills must not import subprocess.", details)
        self.assertIn("Portable Skills must not import dotenv.", details)

    def test_invalid_python_source_fails_closed(self):
        root = self.source("def broken(:\n")
        report = audit_skill_source((root,))
        self.assertEqual(report.findings[0].rule, "source.invalid")

    def test_require_clean_raises_safe_conformance_error(self):
        root = self.source("import discord\n")
        with self.assertRaisesRegex(SkillConformanceError, "source audit failed"):
            require_clean_skill_source((root,))


if __name__ == "__main__":
    unittest.main()
