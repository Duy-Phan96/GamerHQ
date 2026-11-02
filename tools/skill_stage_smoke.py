"""CI-only isolated Docker stage using a reviewed wheel or the public example.

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
    parser.add_argument("--candidate-wheel", type=Path)
    parser.add_argument("--candidate-release", type=Path)
    parser.add_argument("--evidence-file", type=Path)
    args = parser.parse_args()
    if bool(args.candidate_wheel) != bool(args.candidate_release):
        parser.error("Candidate wheel and reviewed descriptor must be supplied together.")
    with tempfile.TemporaryDirectory(prefix="gamerhq-isolated-stage-") as directory:
        root = Path(directory)
        if args.candidate_wheel:
            wheel = args.candidate_wheel.resolve(strict=True)
            release = json.loads(args.candidate_release.read_text())
            from hosts.gamerhq.skill_artifacts import verify_wheel
            verify_wheel(wheel, distribution=release["distribution"], version=release["version"], sha256=release["sha256"])
        else:
            subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
                "--wheel-dir", str(root), str(Path(__file__).resolve().parents[1] / "examples/external-skill-template")], check=True)
            wheel = root / "gamerhq_skill_example-0.1.0-py3-none-any.whl"
            release = {"skillId": "example-skill", "distribution": "gamerhq-skill-example", "version": "0.1.0",
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "capabilities": []}
        backend = DockerSkillDeployment(base_image=args.base_image, sdk_wheel=args.sdk_wheel,
            sdk_version="0.3.0", sdk_sha256=hashlib.sha256(args.sdk_wheel.read_bytes()).hexdigest(),
            compose_file=root / "unused-compose.json", service="unused", container="unused", work_root=root / "stage")
        def fixture_run(command):
            # Only this public fixture opts into diagnostics. The production
            # worker continues to suppress potentially private Docker stderr.
            value = subprocess.run(command, capture_output=True, text=True, timeout=600)
            if value.returncode:
                raise RuntimeError("Public fixture Docker failure:\n" + value.stderr[-10000:])
            return value.stdout
        if not args.candidate_wheel:
            backend.run = fixture_run
        result = backend.stage(release=release, wheel=wheel, deployment_id="isolated-ci")
        try:
            assert result["healthy"] is True and result["isolated"] is True
            assert result["runtimeApi"] == "1" and set(result["capabilities"]) == set(release["capabilities"])
            assert result["gatewayStarted"] is False and result["artifactSha256"] == release["sha256"]
            evidence = {"isolatedDockerStage": "PASS", "gatewayStarted": False, "image": result["image"],
                "skillId": release["skillId"], "version": release["version"], "artifactSha256": release["sha256"],
                "capabilities": result["capabilities"], "runtimeApi": result["runtimeApi"]}
            if args.evidence_file:
                with args.evidence_file.open("x", encoding="utf-8") as output:
                    json.dump(evidence, output, indent=2)
            print(json.dumps(evidence))
        finally:
            # This image was created by this test on an ephemeral CI runner.
            subprocess.run(["docker", "image", "rm", result["image"]], check=True, capture_output=True)


if __name__ == "__main__":
    main()
