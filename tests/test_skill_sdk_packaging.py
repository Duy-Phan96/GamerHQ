import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillSdkPackagingTests(unittest.TestCase):
    def test_pyproject_packages_only_portable_skill_runtime(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]
        self.assertEqual(project["name"], "gamerhq-skill-sdk")
        self.assertEqual(project["version"], "0.2.0")
        self.assertEqual(project["requires-python"], ">=3.12")

        finder = data["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(finder["include"], ["skill_runtime", "skill_runtime.*"])
        excluded = set(finder["exclude"])
        self.assertTrue({"hosts*", "skills*", "database*", "cogs*", "services*"}.issubset(excluded))

    def test_external_template_uses_stable_entry_point_group(self):
        data = tomllib.loads(
            (ROOT / "examples" / "external-skill-template" / "pyproject.toml").read_text(encoding="utf-8")
        )
        entry_points = data["project"]["entry-points"]["gamerhq.skills"]
        self.assertEqual(entry_points["example-skill"], "gamerhq_skill_example:create_skill")


if __name__ == "__main__":
    unittest.main()
