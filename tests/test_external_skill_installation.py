import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "external-skill-template"
RECURRING = ROOT / "packages" / "gamerhq-skill-recurring-posts"


class ExternalSkillInstallationTests(unittest.TestCase):
    def test_sdk_and_external_skill_install_and_discover_in_isolated_target(self):
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
            for source in (ROOT, TEMPLATE, RECURRING):
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
from skill_runtime import validate_skill_implementation
from skill_runtime.runtime.packages import discover_installed_skills

loaded = discover_installed_skills(("example-skill", "recurring-posts"))
assert [item.entry_point for item in loaded] == ["example-skill", "recurring-posts"]
reports = [validate_skill_implementation(item.skill) for item in loaded]
assert [report.skill_id for report in reports] == ["example-skill", "recurring-posts"]
print(",".join(report.skill_id for report in reports))
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(target)
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
            self.assertEqual(result.stdout.strip(), "example-skill,recurring-posts")


if __name__ == "__main__":
    unittest.main()
