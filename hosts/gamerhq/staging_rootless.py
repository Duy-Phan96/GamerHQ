"""Staging-only rootless daemon and namespace checks. Never use production Docker."""
import json
import os
from pathlib import Path
import subprocess

PRODUCTION = ("/opt/gamerhq", "/etc/gamerhq", "/run/docker.sock", "/var/lib/docker", "/root")


def verify_identity(profile):
    uid = profile.get("deploymentUid")
    if type(uid) is not int or uid <= 0 or os.geteuid() != uid:
        raise PermissionError("Staging must run as its dedicated unprivileged identity.")
    socket = f"unix:///run/user/{uid}/docker.sock"
    if profile.get("dockerHost") != socket or os.environ.get("DOCKER_HOST") != socket or os.environ.get("DOCKER_CONTEXT"):
        raise PermissionError("Only the dedicated local rootless socket is allowed.")
    if any(os.access(path, os.R_OK) for path in PRODUCTION):
        raise PermissionError("Production paths must be hidden in the staging worker namespace.")


def verify_rootless(profile, *, run=subprocess.run):
    verify_identity(profile)
    result = run(["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=30, check=True)
    info = json.loads(result.stdout)
    home = Path(profile["deploymentHome"]).resolve()
    if not any("rootless" in item for item in info.get("SecurityOptions", [])) or not Path(info["DockerRootDir"]).resolve().is_relative_to(home):
        raise PermissionError("Staging Docker is not its independent rootless daemon.")
    if info.get("CgroupDriver") != "systemd" or str(info.get("CgroupVersion")) != "2":
        raise PermissionError("Rootless staging needs enforced cgroup v2 resource limits.")
    expected = profile.get("dockerDaemonId")
    if not expected or info.get("ID") != expected:
        raise PermissionError("The approved rootless daemon identity changed.")
    return info
