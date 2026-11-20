"""Read-only staging watchdog; no repair, deployment or Discord connection."""
import json
import os
from pathlib import Path
import socket
import sys
import subprocess
from urllib.error import HTTPError, URLError

if __name__ == "__main__":
    sys.path.insert(0, "/opt/gamerhq-staging/deployment-worker")


HEALTH_FAILURE_REASONS = frozenset({
    'HEALTH_DAEMON_REJECTED', 'HEALTH_CONTAINER_LOOKUP_FAILED', 'HEALTH_CONTAINER_ABSENT',
    'HEALTH_CONTAINER_INSPECTION_REJECTED', 'HEALTH_GATEWAY_HEARTBEAT_REJECTED',
    'HEALTH_CREDENTIAL_MISSING', 'HEALTH_HTTP_REJECTED', 'HEALTH_CONNECTION_REFUSED',
    'HEALTH_TIMEOUT', 'HEALTH_TRANSPORT_REJECTED', 'HEALTH_RESPONSE_REJECTED',
    'HEALTH_SKILL_VERSION_MISSING', 'HEALTH_SKILL_CAPABILITY_MISSING',
    'HEALTH_ENABLED_SKILL_UNHEALTHY', 'HEALTH_AUDIT_REJECTED', 'HEALTH_NOT_READY',
    'HEALTH_UNCLASSIFIED_FAILURE',
})


class HostHealthFailure(RuntimeError):
    """Stable, non-secret diagnostic for a failed Staging health verification."""

    def __init__(self, reason, *, status=None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def verified_skill_health(profile, request):
    """Apply the existing Host readiness conditions and distinguish failure causes."""
    if not os.environ.get(profile.get('healthSecretEnv', '')):
        raise HostHealthFailure('HEALTH_CREDENTIAL_MISSING')
    try:
        skill = request(profile, f"/api/v1/servers/{profile['stagingGuildId']}/skills/event-manager")
    except HTTPError as error:
        raise HostHealthFailure('HEALTH_HTTP_REJECTED', status=error.code) from None
    except (URLError, OSError) as error:
        cause = error.reason if isinstance(error, URLError) else error
        reason = ('HEALTH_CONNECTION_REFUSED' if isinstance(cause, ConnectionRefusedError)
                  else 'HEALTH_TIMEOUT' if isinstance(cause, (TimeoutError, socket.timeout))
                  else 'HEALTH_TRANSPORT_REJECTED')
        raise HostHealthFailure(reason) from None
    except (ValueError, TypeError, KeyError):
        raise HostHealthFailure('HEALTH_RESPONSE_REJECTED') from None
    state = skill.get('state', {}) if isinstance(skill, dict) else None
    if not isinstance(state, dict):
        raise HostHealthFailure('HEALTH_RESPONSE_REJECTED')
    if not skill.get('version'):
        raise HostHealthFailure('HEALTH_SKILL_VERSION_MISSING')
    if skill.get('missingCapabilities'):
        raise HostHealthFailure('HEALTH_SKILL_CAPABILITY_MISSING')
    if state.get('enabled') and state.get('healthy') is not True:
        raise HostHealthFailure('HEALTH_ENABLED_SKILL_UNHEALTHY')
    return skill


def health(profile):
    from hosts.gamerhq.staging_rootless import verify_rootless
    from tools.skill_staging import host_request
    from tools.staging_configuration import audit
    try:
        verify_rootless(profile)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise HostHealthFailure('HEALTH_DAEMON_REJECTED') from None
    if profile.get("workerEnabled") is not True:
        record_health(audit, 'READY_OFFLINE')
        return {"status": "READY_OFFLINE", "gatewayChecked": False}
    try:
        result = subprocess.run(["docker", "inspect", "--format", "{{.State.Health.Status}}", "gamerhq-staging-bot"],
            capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        raise HostHealthFailure('HEALTH_GATEWAY_HEARTBEAT_REJECTED') from None
    if result.stdout.strip() != "healthy":
        raise HostHealthFailure('HEALTH_GATEWAY_HEARTBEAT_REJECTED')
    verified_skill_health(profile, host_request)
    record_health(audit, 'HOST_READY')
    return {"status": "HOST_READY", "gatewayChecked": True}


def record_health(audit, result):
    try:
        audit('health', result)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise HostHealthFailure('HEALTH_AUDIT_REJECTED') from None


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
