"""Concrete isolated stage / approved Compose image-switch backend.

This Host-side worker is never a Skill capability. Stage has no credentials,
production mounts, network, Docker socket or Gateway connection.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from .skill_artifacts import verify_wheel


IMAGE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[0-9a-f]{64}$")


class DockerSkillDeployment:
    def __init__(self, *, base_image, sdk_wheel, sdk_version, sdk_sha256, compose_file, service, container, work_root, health_timeout=300, health_probe=None, run=None):
        if not IMAGE.fullmatch(base_image) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", service):
            raise ValueError("A reviewed immutable base image and a fixed Compose service are required.")
        self.base_image, self.sdk_wheel = base_image, Path(sdk_wheel).resolve()
        self.sdk_version, self.sdk_sha256 = sdk_version, sdk_sha256
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", self.sdk_wheel.name):
            raise ValueError("Invalid SDK wheel filename.")
        self.compose_file, self.service, self.container = Path(compose_file).resolve(), service, container
        self.work_root, self.health_timeout = Path(work_root).resolve(), health_timeout
        self.run = run or self._run
        self.health_probe = health_probe

    def _run(self, args):
        # Never log raw subprocess stderr: it can contain deployment details.
        result = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode:
            from tools.staging_errors import docker_failure
            raise docker_failure(subprocess.CalledProcessError(result.returncode,args,stderr=result.stderr)) from None
        return result.stdout

    def stage(self, *, release, wheel, deployment_id):
        self.work_root.mkdir(parents=True, exist_ok=True)
        # BuildKit interprets a bare sha256 image ID in FROM as a registry name.
        # Resolve approved local bytes, then use an owned tag checked on both
        # sides of the build. The final candidate remains addressed by image ID.
        base_id = self.run(["docker", "image", "inspect", "--format", "{{.Id}}", self.base_image]).strip()
        if not IMAGE.fullmatch(base_id):
            raise ValueError("Approved base image must already be available locally.")
        alias = "gamerhq-skill-stage-base:" + base_id.rsplit(":", 1)[-1]
        self.run(["docker", "tag", base_id, alias])
        if self.run(["docker", "image", "inspect", "--format", "{{.Id}}", alias]).strip() != base_id:
            raise ValueError("Reviewed base image alias changed.")
        with tempfile.TemporaryDirectory(dir=self.work_root, prefix="skill-stage-") as directory:
            root = Path(directory)
            shutil.copyfile(wheel, root / "skill.whl")
            # Preserve valid wheel filenames; pip validates distribution metadata.
            skill_name = release["distribution"].replace("-", "_") + "-" + release["version"] + "-py3-none-any.whl"
            (root / "skill.whl").rename(root / skill_name)
            shutil.copyfile(self.sdk_wheel, root / self.sdk_wheel.name)
            verify_wheel(root / skill_name, distribution=release["distribution"], version=release["version"], sha256=release["sha256"])
            verify_wheel(root / self.sdk_wheel.name, distribution="gamerhq-skill-sdk", version=self.sdk_version, sha256=self.sdk_sha256)
            (root / "Dockerfile").write_text(
                f"FROM {alias}\nUSER root\nCOPY --chown=10001:10001 {skill_name} /opt/skill-release/{skill_name}\n"
                f"COPY --chown=10001:10001 {self.sdk_wheel.name} /opt/skill-release/{self.sdk_wheel.name}\n"
                f"RUN python -m pip install --no-index --no-deps --force-reinstall /opt/skill-release/{self.sdk_wheel.name} /opt/skill-release/{skill_name} && python -m pip check\n"
                "USER 10001:10001\n", encoding="utf-8")
            self.run(["docker", "build", "--network", "none", "--iidfile", str(root / "image-id"), str(root)])
            if self.run(["docker", "image", "inspect", "--format", "{{.Id}}", alias]).strip() != base_id:
                raise ValueError("Reviewed base image alias changed during staging.")
            image = (root / "image-id").read_text().strip()
            if not IMAGE.fullmatch(image):
                raise ValueError("Docker did not produce an immutable image identity.")
            args = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--user", "10001:10001", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
                "--entrypoint", "python", image, "-m", "tools.skill_image_probe", "--skill-id", release["skillId"],
                "--distribution", release["distribution"], "--version", release["version"], "--sha256", release["sha256"],
                "--wheel", "/opt/skill-release/" + skill_name]
            result = json.loads(self.run(args))
            return {**result, "image": image, "isolated": True}

    def current_image(self):
        image = self.run(["docker", "inspect", "--format", "{{.Image}}", self.container]).strip()
        if not IMAGE.fullmatch(image):
            raise ValueError("Current Host image identity is unavailable.")
        return image

    def stop(self):
        self.run(["docker", "compose", "--file", str(self.compose_file), "stop", self.service])

    def activate(self, image):
        if not IMAGE.fullmatch(image):
            raise ValueError("Only immutable reviewed images may be applied.")
        self.work_root.mkdir(parents=True, exist_ok=True)
        override = self.work_root / "approved-image.json"
        override.write_text(json.dumps({"services": {self.service: {"image": image}}}), encoding="utf-8")
        self.run(["docker", "compose", "--file", str(self.compose_file), "--file", str(override),
            "up", "--detach", "--no-build", "--pull", "never", self.service])

    def healthy(self, *, expected_version):
        deadline = time.monotonic() + self.health_timeout
        while time.monotonic() < deadline:
            value = self.run(["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}", self.container]).strip()
            if value == "healthy":
                return self.health_probe is not None and self.health_probe(expected_version) is True
            if value in ("unhealthy", "missing"):
                return False
            time.sleep(2)
        return False
