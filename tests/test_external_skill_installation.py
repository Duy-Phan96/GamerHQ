import importlib.metadata
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skill_runtime import validate_skill_implementation
from skill_runtime.runtime.packages import discover_installed_skills


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "external-skill-template"


class ExternalSkillInstallationTests(unittest.TestCase):
    def test_example_skill_installs_and_discovers_in_isolated_target(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            target = temp_path / "site"
            target.mkdir()

            base = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-build-isolation",
                "--target",
                str(target),
            ]
            for source in (ROOT, TEMPLATE):
                result = subprocess.run(
                    [*base, str(source)],
                    cwd=temp_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=(
                        f"Local package install failed for {source.name}.\n"
                        f"stdout:\n{result.stdout[-4000:]}\n"
                        f"stderr:\n{result.stderr[-4000:]}"
                    ),
                )

            code = """
import os
from pathlib import Path
from skill_runtime import validate_skill_implementation
from skill_runtime.runtime.packages import discover_installed_skills

target = Path(os.environ["GAMERHQ_TEST_TARGET"]).resolve()
loaded = discover_installed_skills(("example-skill",))
assert len(loaded) == 1
report = validate_skill_implementation(loaded[0].skill)
assert report.skill_id == "example-skill"
print(report.skill_id)
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(target)
            env["GAMERHQ_TEST_TARGET"] = str(target)
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertEqual(result.stdout.strip(), "example-skill")

    def test_pinned_recurring_posts_distribution_is_installed_and_discoverable(self):
        distribution = importlib.metadata.distribution("gamerhq-skill-recurring-posts")
        self.assertEqual(distribution.version, "1.2.2")

        loaded = discover_installed_skills(("recurring-posts",))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].distribution, "gamerhq-skill-recurring-posts")

        report = validate_skill_implementation(loaded[0].skill)
        self.assertEqual(report.skill_id, "recurring-posts")

        module_path = Path(sys.modules[loaded[0].skill.__class__.__module__.split(".")[0]].__file__).resolve()
        self.assertNotIn(str(ROOT / "packages"), str(module_path))


if __name__ == "__main__":
    unittest.main()
