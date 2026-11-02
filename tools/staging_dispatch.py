"""Root-owned staging-only dispatcher behind a restricted SSH forced command."""
import json
import os
from pathlib import Path
import subprocess
import sys

WORKER = Path("/opt/gamerhq-staging/deployment-worker")
PROFILE = Path("/etc/gamerhq/staging/profile.json")
ENVIRONMENT = Path("/etc/gamerhq/staging/worker.env")

# The installed root launcher uses Python isolated mode. Only this fixed,
# root-owned reviewed source is added; no SSH caller import paths are honored.
if __name__ == "__main__":
    sys.path.insert(0, str(WORKER))
from tools.staging_bridge_protocol import validate


def protected_file(path, *, private=False):
    info = path.stat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("Staging control files must be root-owned and protected.")
    if private and info.st_mode & 0o077:
        raise PermissionError("Staging worker credentials must be private.")


def environment(path=ENVIRONMENT):
    protected_file(path, private=True)
    result = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    permitted = {"GAMERHQ_SKILL_RELEASE_TOKEN", "GAMERHQ_STAGING_WEB_API_SECRET"}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key not in permitted or key in result:
            raise ValueError("Unsupported staging worker environment key.")
        result[key] = value.strip().strip("\"'")
    return result


def command_for(request, profile, *, current_version=None):
    value = validate(request)
    if profile.get("environment") != "staging":
        raise PermissionError("Production is unavailable through this bridge.")
    python = str(WORKER / ".venv/bin/python")
    if value["phase"] == "prepare":
        return [python, "-m", "tools.skill_staging", "--profile", str(PROFILE), value["operation"], "--approval", value["approval"]]
    args = [python, "-m", "tools.skill_deployment_worker", "--profile", str(PROFILE)]
    if value["operation"] == "apply":
        return args + ["apply", "--id", value["deploymentId"], "--approval", value["approval"]]
    release = json.loads(Path(profile["releaseFile"]).read_text(encoding="utf-8"))
    if value["operation"] == "history":
        return args + ["history", "--skill-id", release["skillId"]]
    from hosts.gamerhq.skill_artifacts import version_parts
    version_parts(current_version)
    wheel = release["distribution"].replace("-", "_") + "-" + release["version"] + "-py3-none-any.whl"
    return args + ["stage", "--release", Path(wheel).with_suffix(".release.json").name, "--wheel", wheel,
        "--current-version", current_version, "--manual-review"]


def main():
    if os.geteuid() != 0:
        raise PermissionError("The staging dispatcher requires its fixed privileged launcher.")
    protected_file(PROFILE)
    protected_file(WORKER / "tools/staging_dispatch.py")
    if (WORKER / ".env").exists():
        raise PermissionError("The protected worker checkout must not contain a runtime environment file.")
    body = sys.stdin.buffer.read(4097)
    if len(body) > 4096:
        raise ValueError("Staging request exceeds its bound.")
    request = validate(json.loads(body))
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    from hosts.gamerhq.skill_staging import validate_profile
    validate_profile(profile)
    values = environment()
    os.environ.clear(); os.environ.update(values)
    current = None
    if request["phase"] == "update" and request["operation"] == "stage":
        from tools.skill_staging import host_request, release_for
        release = release_for(profile)
        current = host_request(profile, f"/api/v1/servers/{profile['stagingGuildId']}/skills/{release['skillId']}")["version"]
    command = command_for(request, profile, current_version=current)
    result = subprocess.run(command, cwd=WORKER, env=values, capture_output=True, text=True, timeout=2100)
    if result.returncode or len(result.stdout.encode()) > 65536:
        raise RuntimeError("The staging operation failed; production was not targeted.")
    print(result.stdout, end="")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Staging bridge rejected or failed the operation. No private error or credential values were logged.") from None
