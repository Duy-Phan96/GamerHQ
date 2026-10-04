import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillProductionPackagingTests(unittest.TestCase):
    def test_dockerfile_copies_complete_skill_runtime_stack(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for required in (
            "skill_runtime/ ./skill_runtime/",
            "hosts/ ./hosts/",
            "skills/ ./skills/",
        ):
            self.assertIn(required, dockerfile)

    def test_ci_keeps_packaging_checks_running_after_independent_audit_failure(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "if: always() && matrix.python == '3.12'",
            workflow,
        )
        self.assertIn(
            "- name: Validate Compose without credentials\n        if: always()",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
