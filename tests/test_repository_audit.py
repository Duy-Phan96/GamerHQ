import unittest
from unittest.mock import patch

from tools import repository_audit


class RepositoryAuditTests(unittest.TestCase):
    def test_history_commits_are_scoped_to_checked_out_head(self):
        with patch.object(
            repository_audit,
            "git",
            return_value=b"abc123\ndef456\n",
        ) as git:
            self.assertEqual(
                repository_audit.history_commits(),
                ["abc123", "def456"],
            )
        git.assert_called_once_with("rev-list", "HEAD")

    def test_literal_secret_assignment_still_fails_heuristic(self):
        name = "API_" + "SECRET"
        value = "real-looking-" + "secret-value-123456"
        findings = repository_audit.secret_findings(
            f'{name} = "{value}"\n'
        )
        self.assertIn("potential credential assignment", findings)

    def test_public_capability_identifier_is_not_a_literal_credential(self):
        findings = repository_audit.secret_findings(
            'SKILL_SECURE_STORAGE = "secrets.skill"\n'
        )
        self.assertNotIn("potential credential assignment", findings)


if __name__ == "__main__":
    unittest.main()
