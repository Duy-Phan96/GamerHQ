import subprocess
import sys


def test_artifact_probe_does_not_import_live_host_configuration_or_discord():
    result = subprocess.run([sys.executable, "-c",
        "import sys; import tools.skill_image_probe; "
        "assert not {'config','database.db','discord','dotenv'} & set(sys.modules)"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
