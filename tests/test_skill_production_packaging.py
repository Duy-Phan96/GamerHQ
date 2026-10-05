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
            "requirements-skills.lock",
            "pip install --no-cache-dir --no-deps --requirement requirements-skills.lock",
        ):
            self.assertIn(required, dockerfile)

    def test_docker_context_includes_runtime_but_not_external_skill_source(self):
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for required in ("!skill_runtime/", "!hosts/", "!skills/", "!requirements-skills.lock"):
            self.assertIn(required, dockerignore)
        self.assertNotIn("!packages/gamerhq-skill-recurring-posts/", dockerignore)

    def test_external_skill_lock_pins_immutable_github_commit(self):
        lock = (ROOT / "requirements-skills.lock").read_text(encoding="utf-8")
        self.assertIn(
            "gamerhq-skill-recurring-posts @ https://github.com/Duy-Phan96/"
            "gamerhq-skill-recurring-posts/archive/"
            "8317d854f0a39811804b9f28eff9b7061a5417e8.zip",
            lock,
        )
        self.assertNotIn("/main.zip", lock)

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
