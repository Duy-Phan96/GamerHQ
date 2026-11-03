"""Root-owned staging-only dispatcher behind a restricted SSH forced command."""
import json
import os
from pathlib import Path
import subprocess
import sys

WORKER = Path("/opt/gamerhq-staging/deployment-worker")
CONTROL = Path("/etc/gamerhq-staging")
PROFILE = CONTROL / "settings/profile.json"
ENVIRONMENT = CONTROL / "settings/worker.env"

# The installed root launcher uses Python isolated mode. Only this fixed,
# root-owned reviewed source is added; no SSH caller import paths are honored.
if __name__ == "__main__":
    sys.path.insert(0, str(WORKER))
from tools.staging_bridge_protocol import validate
from tools.staging_errors import StagingFailure, decode_evidence


def protected_file(path, *, private=False, uid=0):
    info = path.stat()
    if path.is_symlink() or info.st_uid != uid or info.st_mode & 0o022:
        raise PermissionError("Staging control files must be root-owned and protected.")
    if private and info.st_mode & 0o077:
        raise PermissionError("Staging worker credentials must be private.")


def buildx_state(path, uid):
    if path.parent.is_symlink() or path.is_symlink():
        raise PermissionError('Buildx state cannot be redirected.')
    parent = path.parent.stat()
    if parent.st_uid != uid or parent.st_mode & 0o077:
        raise PermissionError('Buildx state needs its private staging parent.')
    info = path.stat()
    if not path.is_dir() or info.st_uid != uid or info.st_mode & 0o077:
        raise PermissionError('Buildx state must remain private to staging.')
    return str(path)


def base_environment():
    uid = os.geteuid()
    if uid <= 0:
        raise PermissionError('Staging tools cannot run as root.')
    result = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/var/lib/gamerhq-staging-bridge", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
        "DOCKER_HOST": f"unix:///run/user/{uid}/docker.sock", "DOCKER_CONFIG": str(CONTROL / "docker-client"),
        "BUILDX_CONFIG": buildx_state(WORKER.parent / 'worker/buildx', uid),
        "GIT_CONFIG_GLOBAL": str(CONTROL / "gitconfig"), "GIT_OPTIONAL_LOCKS": "0",
        "XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}
    return result


def environment(path=ENVIRONMENT):
    protected_file(path, private=True, uid=os.geteuid())
    result = base_environment()
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
    if os.geteuid() == 0:
        raise PermissionError("Staging automation must never execute as root.")
    import fcntl
    # Preparation and update workflows share the same staging resources, including
    # when requests originate outside Actions. Never overlap those operations.
    lock = (WORKER.parent / "worker/operation.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (WORKER.parent / "worker/access-revoked").exists():
        raise PermissionError("Staging deployment access has been revoked.")
    protected_file(CONTROL / "policy.json")
    policy = json.loads((CONTROL / "policy.json").read_text())
    if policy.get("deploymentUid") != os.geteuid() or policy.get("schemaVersion") != 2:
        raise PermissionError("The root-owned staging identity policy does not match.")
    protected_file(PROFILE, uid=os.geteuid())
    protected_file(WORKER / "tools/staging_dispatch.py")
    if (WORKER / ".env").exists():
        raise PermissionError("The protected worker checkout must not contain a runtime environment file.")
    body = sys.stdin.buffer.read(32769)
    if len(body) > 32768:
        raise ValueError("Staging request exceeds its bound.")
    envelope = json.loads(body)
    if not isinstance(envelope, dict) or set(envelope) != {"request", "configuration"}:
        raise ValueError("Unsupported staging request envelope.")
    request = validate(envelope["request"])
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    from hosts.gamerhq.skill_staging import validate_profile
    validate_profile(profile)
    from tools.staging_configuration import validate_binding, configure, audit
    validate_binding(profile, policy)
    values = environment()
    os.environ.clear(); os.environ.update(values)
    from hosts.gamerhq.staging_rootless import verify_rootless, verify_identity
    if request["phase"] == "configure" and request["operation"] == "revoke":
        verify_identity(profile)  # revocation must work even when Docker is unhealthy
    else:
        verify_rootless(profile)
    if request["phase"] == "configure":
        detail = configure(request["operation"], envelope["configuration"], profile) or {}
        print(json.dumps({"result": "PASS", "operation": request["operation"], "productionChanged": False, **detail}))
        return
    if envelope["configuration"] is not None:
        raise ValueError("Unexpected configuration payload.")
    if request["operation"] == "build-base" and profile.get("workerEnabled") is True:
        raise PermissionError("The initial Host base cannot be replaced after live authorization.")
    current = None
    if request["phase"] == "update" and request["operation"] == "stage":
        from tools.skill_staging import host_request, release_for
        release = release_for(profile)
        current = host_request(profile, f"/api/v1/servers/{profile['stagingGuildId']}/skills/{release['skillId']}")["version"]
    command = command_for(request, profile, current_version=current)
    audit_id = audit(request["operation"], "STARTED")
    try:
        result = subprocess.run(command, cwd=WORKER, env=values, capture_output=True, text=True, timeout=2100)
        if len(result.stdout.encode()) > 65536:
            raise StagingFailure('WORKER_OUTPUT_LIMIT','worker-command',audit_id=audit_id)
        if result.returncode:
            evidence = decode_evidence(result.stderr)
            if evidence:
                raise StagingFailure(evidence['stagingFailure'],evidence['step'],
                    exit_code=evidence.get('exitCode'),audit_id=audit_id)
            raise StagingFailure('WORKER_COMMAND_FAILED','worker-command',exit_code=result.returncode,audit_id=audit_id)
    except Exception:
        audit(request["operation"], "FAILED", audit_id=audit_id)
        raise
    audit(request["operation"], "PASS", audit_id=audit_id)
    if request["operation"] == "build-base":
        from hosts.gamerhq.skill_deployment_docker import IMAGE
        evidence = json.loads((Path(profile["workRoot"]) / "base-candidate.json").read_text())
        if evidence.get("sourceCommit") != profile["hostSourceCommit"] or evidence.get("gatewayStarted") is not False or not IMAGE.fullmatch(evidence.get("image", "")):
            raise ValueError("Initial Host base provenance did not verify.")
        from tools.skill_staging import write_record
        write_record(PROFILE, {**profile, "baseImage": evidence["image"]})
    print(result.stdout, end="")


if __name__ == "__main__":
    try:
        main()
    except StagingFailure as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit("Staging bridge rejected or failed the operation. No private error or credential values were logged.") from None
