import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillAuthoringDocumentationTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_canonical_authoring_documents_exist_and_are_linked(self):
        index = self.read("docs/skills/README.md")
        for path, label in (
            ("docs/skills/developer-guide.md", "Skill Developer Guide"),
            ("docs/skills/authoring-contract.md", "Strict Authoring Contract"),
            ("docs/skills/review-checklist.md", "Skill Review Checklist"),
        ):
            self.assertTrue((ROOT / path).is_file())
            self.assertIn(label, index)

    def test_authoring_contract_contains_core_safety_invariants(self):
        contract = self.read("docs/skills/authoring-contract.md")
        for required in (
            "storage.skill",
            "scheduler.jobs",
            "health_check",
            "gamerhq.skills",
            "SkillRegistrationContext",
            "SkillContext",
            "Do not claim exactly-once",
            "raw database",
        ):
            self.assertIn(required, contract)

    def test_external_template_carries_repository_instructions_and_design_template(self):
        self.assertTrue((ROOT / "examples/external-skill-template/AGENTS.md").is_file())
        self.assertTrue((ROOT / "examples/external-skill-template/SKILL_DESIGN.md").is_file())
        agents = self.read("examples/external-skill-template/AGENTS.md")
        self.assertIn("Do not import Discord.py", agents)
        self.assertIn("minimum required capabilities", agents.lower())


if __name__ == "__main__":
    unittest.main()
