"""Read-only staging watchdog; no repair, deployment or Discord connection."""
import json
import os
from pathlib import Path
import sys
import subprocess

if __name__ == "__main__":
    sys.path.insert(0, "/opt/gamerhq-staging/deployment-worker")


def health(profile):
    from hosts.gamerhq.staging_rootless import verify_rootless
    from tools.skill_staging import host_request
    from tools.staging_configuration import audit
    verify_rootless(profile)
    if profile.get("workerEnabled") is not True:
        audit("health", "READY_OFFLINE")
        return {"status": "READY_OFFLINE", "gatewayChecked": False}
    result = subprocess.run(["docker", "inspect", "--format", "{{.State.Health.Status}}", "gamerhq-staging-bot"],
        capture_output=True, text=True, timeout=30, check=True)
    if result.stdout.strip() != "healthy":
        raise RuntimeError("The staging Gateway heartbeat is not healthy.")
    skill = host_request(profile, f"/api/v1/servers/{profile['stagingGuildId']}/skills/event-manager")
    if not skill.get("version") or skill.get("missingCapabilities") or skill.get("state", {}).get("enabled") and skill.get("state", {}).get("healthy") is not True:
        raise RuntimeError("Staging Host/Skill health did not verify.")
    audit("health", "HOST_READY")
    return {"status": "HOST_READY", "gatewayChecked": True}


def main():
    from tools.staging_dispatch import CONTROL, PROFILE, environment, protected_file
    from tools.staging_configuration import validate_binding, audit
    if os.geteuid() == 0:
        raise PermissionError("The staging watchdog cannot run as root.")
    protected_file(CONTROL / "policy.json")
    policy = json.loads((CONTROL / "policy.json").read_text())
    profile = json.loads(PROFILE.read_text())
    validate_binding(profile, policy)
    from hosts.gamerhq.skill_staging import validate_profile
    validate_profile(profile)
    values = environment(); os.environ.clear(); os.environ.update(values)
    if (Path(profile["workRoot"]) / "access-revoked").exists():
        print(json.dumps({"status": "ACCESS_REVOKED"})); return
    try:
        print(json.dumps(health(profile)))
    except Exception:
        audit("health", "FAILED")
        raise SystemExit("Staging health failed; inspect the staging user journal. No repair or deployment was attempted.") from None


if __name__ == "__main__":
    main()
