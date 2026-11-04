"""Protected-workflow staging configuration, never production administration."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

CAPABILITIES = {"storage.skill", "storage.atomic", "scheduler.jobs", "discord.resources.manage"}


def audit(operation, state, *, audit_id=None):
    # No payload, credential, subprocess output or exception message is logged.
    import syslog
    audit_id = audit_id or uuid.uuid4().hex
    if not re.fullmatch('[0-9a-f]{32}',audit_id):
        raise ValueError('Invalid audit identifier.')
    syslog.openlog("gamerhq-staging", syslog.LOG_PID, syslog.LOG_AUTHPRIV)
    syslog.syslog(syslog.LOG_NOTICE, json.dumps({"operation": operation, "state": state, "id": audit_id, "uid": os.geteuid()}))
    return audit_id


def validate_binding(profile, policy):
    for key in ("root", "configurationRoot", "deploymentUid", "deploymentHome", "dockerHost", "hostSourceCommit"):
        if profile.get(key) != policy.get(key):
            raise PermissionError("The staging profile cannot change its bootstrap identity or isolation roots.")
    if profile.get("dockerMode") != "rootless":
        raise PermissionError("Only isolated rootless staging is supported.")


def atomic_json(path, value):
    from tools.skill_staging import write_record
    write_record(path, value)


def atomic_private_text(path, body):
    if path.is_symlink():
        raise ValueError("Private staging settings cannot be symbolic links.")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False) as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
        target = Path(stream.name)
    target.chmod(0o600); target.replace(path)


def descriptor(value, *, sdk=False):
    from hosts.gamerhq.skill_artifacts import version_parts
    allowed = {"repository", "distribution", "version", "tag", "reviewedCommit", "sha256", "sourceReviewed", "allowPrerelease"}
    if not sdk:
        allowed |= {"skillId", "runtimeApi", "capabilities", "migrationMode"}
    if not isinstance(value, dict) or set(value) - allowed or value.get("sourceReviewed") is not True:
        raise ValueError("An explicitly reviewed, bounded release descriptor is required.")
    version_parts(value["version"])
    if not re.fullmatch(r"[0-9a-f]{40}", value.get("reviewedCommit", "")) or not re.fullmatch(r"[0-9a-f]{64}", value.get("sha256", "")):
        raise ValueError("Exact reviewed commit and SHA-256 are required.")
    repository = "Duy-Phan96/GamerHQ" if sdk else "Duy-Phan96/gamerhq-skill-event-manager"
    distribution = "gamerhq-skill-sdk" if sdk else "gamerhq-skill-event-manager"
    if value.get("repository") != repository or value.get("distribution") != distribution:
        raise ValueError("The bootstrap publisher/distribution policy cannot be expanded remotely.")
    if sdk and not value["version"].startswith("0.3."):
        raise ValueError("The installed Host requires SDK 0.3.x.")
    expected_tag = ("skill-sdk-v" if sdk else "v") + value["version"]
    if value.get("tag", expected_tag) != expected_tag or type(value.get("allowPrerelease", False)) is not bool:
        raise ValueError("Release tag or prerelease policy is invalid.")
    if not sdk and (value.get("skillId") != "event-manager" or value.get("runtimeApi") != "1" or
            not isinstance(value.get("capabilities"), list) or not set(value["capabilities"]) <= CAPABILITIES or
            value.get("migrationMode") not in ("none", "backward-compatible")):
        raise ValueError("Release compatibility, capability or migration policy is invalid.")
    return dict(value)


def configure(operation, payload, profile):
    from tools.skill_staging import write_record, backend_for
    from tools.staging_errors import StagingFailure, VerificationFailure
    audit_id = uuid.uuid4().hex
    audit(operation, "STARTED", audit_id=audit_id)
    try:
        if operation == "health":
            if payload is not None: raise ValueError("Health accepts no configuration.")
            from tools.staging_health import health
            result = health(profile)
            audit(operation, "PASS")
            return result
        elif operation == "revoke":
            if payload is not None: raise ValueError("Revocation accepts no configuration.")
            write_record(Path(profile["workRoot"]) / "access-revoked", {"revoked": True})
            # Revocation stays effective even if the dedicated container stop fails.
            try:
                result = subprocess.run(["docker", "ps", "--all", "--filter", "name=^/gamerhq-staging-bot$", "--format", "{{.ID}}"],
                    capture_output=True, text=True, timeout=30, check=True)
                if result.stdout.strip():
                    subprocess.run(["docker", "stop", "--time", "30", "gamerhq-staging-bot"], capture_output=True, text=True, timeout=60, check=True)
            except Exception:
                audit(operation, "DOCKER_STOP_UNAVAILABLE")
            subprocess.run(["systemctl", "--user", "stop", "docker.service"], capture_output=True, text=True, timeout=90, check=True)
        elif operation == "authorize-live":
            if payload is not None: raise ValueError("Live authorization accepts no configuration.")
            from hosts.gamerhq.skill_staging import verify_remote
            candidate = {**profile, "workerEnabled": True}
            proof = verify_remote(candidate)
            from tools.staging_dispatch import PROFILE
            write_record(profile["verificationFile"], proof)
            # Never enable the worker if proof persistence fails. A proof for the
            # candidate profile cannot authorize the old disabled profile.
            atomic_json(PROFILE, candidate)
        elif operation == "release":
            if not isinstance(payload, dict) or set(payload) != {"skillRelease"}:
                raise ValueError("Only one reviewed Skill descriptor may change.")
            atomic_json(profile["releaseFile"], descriptor(payload["skillRelease"]))
        elif operation == "credentials":
            if not isinstance(payload, dict) or set(payload) != {"releaseToken"} or not isinstance(payload["releaseToken"], str) or not 20 <= len(payload["releaseToken"]) <= 2048 or any(c.isspace() for c in payload["releaseToken"]):
                raise ValueError("Supply only the renewed read-only staging artifact credential.")
            from tools.staging_dispatch import ENVIRONMENT
            atomic_private_text(ENVIRONMENT, "GAMERHQ_SKILL_RELEASE_TOKEN=" + payload["releaseToken"] +
                "\nGAMERHQ_STAGING_WEB_API_SECRET=" + os.environ["GAMERHQ_STAGING_WEB_API_SECRET"] + "\n")
        elif operation == "initial":
            initial(payload, profile)
        else:
            raise ValueError("Unsupported staging configuration operation.")
    except StagingFailure as error:
        audit(operation, "FAILED", audit_id=audit_id)
        raise VerificationFailure(error.evidence['stagingFailure'], error.evidence['step'],
            exit_code=error.evidence.get('exitCode'), audit_id=audit_id) from None
    except Exception:
        audit(operation, "FAILED", audit_id=audit_id)
        raise VerificationFailure('CONFIGURATION_REJECTED', 'configure', audit_id=audit_id) from None
    audit(operation, "PASS", audit_id=audit_id)


def initial(payload, profile):
    ids = {"stagingBotId", "stagingGuildId", "productionBotId", "productionGuildId", "categoryId", "voiceCategoryId"}
    allowed = ids | {"discordToken", "releaseToken", "sdkRelease", "skillRelease"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError("Use the documented complete one-time staging configuration packet.")
    if any(not isinstance(payload[key], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", payload[key]) for key in ids):
        raise ValueError("Dedicated staging and production exclusion IDs are required.")
    if payload["stagingBotId"] == payload["productionBotId"] or payload["stagingGuildId"] == payload["productionGuildId"]:
        raise ValueError("Staging identity and guild must differ from production.")
    for key in ("discordToken", "releaseToken"):
        if not isinstance(payload[key], str) or not 20 <= len(payload[key]) <= 2048 or any(c.isspace() for c in payload[key]):
            raise ValueError("New staging-only credentials are required.")
    sdk, skill = descriptor(payload["sdkRelease"], sdk=True), descriptor(payload["skillRelease"])
    result = subprocess.run(["docker", "ps", "--all", "--filter", "name=^/gamerhq-staging-bot$", "--format", "{{.ID}}"],
        capture_output=True, text=True, timeout=30, check=True)
    if result.stdout.strip() or Path(profile["database"]).exists():
        raise PermissionError("Initial configuration cannot replace an existing staging dataset/container.")
    profile = {**profile, **{key: payload[key] for key in ids}, "workerEnabled": False,
        "sdkVersion": sdk["version"], "sdkSha256": sdk["sha256"], "sdkWheel": "gamerhq_skill_sdk-" + sdk["version"] + "-py3-none-any.whl"}
    from tools.staging_dispatch import PROFILE, ENVIRONMENT
    secret = os.environ["GAMERHQ_STAGING_WEB_API_SECRET"]
    # Atomic private replacements; never print or put secrets into SSH command arguments.
    for path, body in ((Path(profile["botEnvFile"]), "DISCORD_TOKEN=" + payload["discordToken"] + "\nGUILD_ID=" + payload["stagingGuildId"] + "\nGAMERHQ_WEB_API_SECRET=" + secret + "\n"),
            (ENVIRONMENT, "GAMERHQ_SKILL_RELEASE_TOKEN=" + payload["releaseToken"] + "\nGAMERHQ_STAGING_WEB_API_SECRET=" + secret + "\n")):
        atomic_private_text(path, body)
    atomic_json(profile["sdkReleaseFile"], sdk)
    atomic_json(profile["releaseFile"], skill)
    atomic_json(PROFILE, profile)
