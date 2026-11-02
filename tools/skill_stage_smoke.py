"""CI-only isolated Docker stage using the public example Skill, never live data.

Accepts an already-built local Host image ID and SDK wheel. This tool has no apply
operation, deployment profile, credentials, production mounts or Gateway startup.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from hosts.gamerhq.skill_deployment_docker import DockerSkillDeployment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--sdk-wheel", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="gamerhq-isolated-stage-") as directory:
        root = Path(directory)
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
            "--wheel-dir", str(root), str(Path(__file__).resolve().parents[1] / "examples/external-skill-template")], check=True)
        wheel = root / "gamerhq_skill_example-0.1.0-py3-none-any.whl"
        release = {"skillId": "example-skill", "distribution": "gamerhq-skill-example", "version": "0.1.0",
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        backend = DockerSkillDeployment(base_image=args.base_image, sdk_wheel=args.sdk_wheel,
            sdk_version="0.3.0", sdk_sha256=hashlib.sha256(args.sdk_wheel.read_bytes()).hexdigest(),
            compose_file=root / "unused-compose.json", service="unused", container="unused", work_root=root / "stage")
        result = backend.stage(release=release, wheel=wheel, deployment_id="isolated-ci")
        try:
            assert result["healthy"] is True and result["isolated"] is True
            assert result["runtimeApi"] == "1" and result["capabilities"] == []
            assert result["gatewayStarted"] is False and result["artifactSha256"] == release["sha256"]
            print(json.dumps({"isolatedDockerStage": "PASS", "gatewayStarted": False, "image": result["image"]}))
        finally:
            # This image was created by this test on an ephemeral CI runner.
            subprocess.run(["docker", "image", "rm", result["image"]], check=True, capture_output=True)


if __name__ == "__main__":
    main()
