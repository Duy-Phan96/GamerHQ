"""Architecture guards for the portable Skill Runtime / future SDK."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skill_runtime"

# Portable SDK/runtime code may use the Python standard library and its own
# package, but not the GamerHQ host, Discord library, DB, cogs or services.
FORBIDDEN_IMPORT_ROOTS = {
    "bot",
    "cogs",
    "config",
    "database",
    "discord",
    "hosts",
    "services",
}


class SkillRuntimeBoundaryTests(unittest.TestCase):
    def runtime_python_files(self):
        return sorted(RUNTIME.rglob("*.py"))

    def test_runtime_contains_no_host_specific_imports(self):
        violations = []
        for path in self.runtime_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    root = module.split(".", 1)[0]
                    if root in FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{path.relative_to(ROOT)} imports {module}")
        self.assertEqual(violations, [], "\n".join(violations))

    def test_runtime_does_not_embed_gamerhq_business_identifiers(self):
        # Keep this deliberately narrow: architectural words/docs may mention
        # GamerHQ, but runtime Python contracts must not know concrete GamerHQ
        # managed resources or commands.
        forbidden_literals = (
            "managed_channel:",
            "managed_category:",
            "/server manage",
            "/server setup",
            "support-tickets",
            "looking-for-group",
        )
        violations = []
        for path in self.runtime_python_files():
            text = path.read_text(encoding="utf-8")
            for value in forbidden_literals:
                if value in text:
                    violations.append(f"{path.relative_to(ROOT)} contains {value!r}")
        self.assertEqual(violations, [], "\n".join(violations))

    def test_server_management_does_not_import_private_skill_implementations(self):
        path = ROOT / "cogs" / "server_management.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if module == "skills" or module.startswith("skills."):
                    violations.append(module)
        self.assertEqual(
            violations,
            [],
            "Server management must use public Skill management contracts, not private Skill imports.",
        )

    def test_recurring_posts_has_no_embedded_implementation(self):
        self.assertFalse(
            (ROOT / "skills" / "recurring_posts.py").exists(),
            "Recurring Posts must not return as a built-in Skill.",
        )
        self.assertFalse(
            (ROOT / "packages" / "gamerhq-skill-recurring-posts").exists(),
            "Recurring Posts implementation must live only in its standalone repository.",
        )
        lock = (ROOT / "requirements-skills.lock").read_text(encoding="utf-8")
        self.assertIn(
            "gamerhq-skill-recurring-posts @ https://github.com/Duy-Phan96/"
            "gamerhq-skill-recurring-posts/archive/"
            "d1b123db3425168d167ec589ea3135168ee11e8e.zip",
            lock,
        )

    def test_gamerhq_adapter_is_outside_portable_runtime(self):
        adapter = ROOT / "hosts" / "gamerhq" / "skill_host.py"
        self.assertTrue(adapter.is_file())
        self.assertFalse(str(adapter.relative_to(ROOT)).startswith("skill_runtime/"))


if __name__ == "__main__":
    unittest.main()
