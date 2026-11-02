"""Isolated Host staging policy and read-only verification; no Gateway startup."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import time
import re
import subprocess
from urllib.request import Request, HTTPRedirectHandler, build_opener

import discord
from .skill_deployment_docker import IMAGE
from .skill_artifacts import verify_wheel
from .skill_release_artifacts import fetch_release


def validate_profile(profile):
    if profile.get("environment") != "staging" or profile.get("service") != "gamerhq-staging" or profile.get("container") != "gamerhq-staging-bot":
        raise ValueError("A dedicated staging service/container is required.")
    root, config = Path(profile["root"]).resolve(), Path(profile["configurationRoot"]).resolve()
    if Path(profile["database"]).name != "gamerhq.db" or any(any(character in str(profile[key]) for character in (",", "\n", "\r")) for key in
            ("root", "configurationRoot", "database", "artifactRoot", "workRoot", "backupRoot", "verificationFile")):
        raise ValueError("Staging database/mount paths do not match the isolated container layout.")
    production = Path("/opt/gamerhq").resolve()
    for directory in (root, config):
        if directory.is_relative_to(production) or production.is_relative_to(directory):
            raise ValueError("Staging cannot overlap the production filesystem.")
    for key in ("database", "artifactRoot", "workRoot", "backupRoot", "verificationFile", "offlineVerificationFile"):
        if not Path(profile[key]).resolve().is_relative_to(root):
            raise ValueError("Mutable staging paths must stay inside their dedicated root.")
    for key in ("composeFile", "botEnvFile", "releaseFile", "sdkReleaseFile"):
        if not Path(profile[key]).resolve().is_relative_to(config):
            raise ValueError("Staging configuration paths must stay inside their protected root.")
    mounted = (Path(profile["database"]).resolve().parent, Path(profile["backupRoot"]).resolve())
    if any(Path(profile[key]).resolve().is_relative_to(directory) for key in ("artifactRoot", "workRoot", "verificationFile", "offlineVerificationFile") for directory in mounted):
        raise ValueError("Artifacts, worker and verification must not be mounted into the bot.")
    compose = json.loads(Path(profile["composeFile"]).read_text(encoding="utf-8"))
    if compose.get("name") != "gamerhq-staging" or set(compose.get("services", {})) != {"gamerhq-staging"}:
        raise ValueError("Staging must use its own Compose project and one dedicated service.")
    service = compose["services"]["gamerhq-staging"]
    if service.get("image") not in (profile["baseImage"], "${GAMERHQ_STAGING_IMAGE:?Set a reviewed immutable staging image}"):
        raise ValueError("Staging must select reviewed immutable image bytes.")
    if service.get("container_name") != profile["container"] or service.get("profiles") != ["live-acceptance"] or service.get("restart") != "no":
        raise ValueError("Live acceptance must be opt-in with no automatic restart.")
    if any(key in service for key in ("privileged", "network_mode", "pid", "ipc", "build", "entrypoint", "command")):
        raise ValueError("Staging cannot inherit privileged/shared runtime behavior.")
    expected = {str(mounted[0]): "/app/runtime/data", str(mounted[1]): "/app/backups"}
    actual = {}
    for value in service.get("volumes", []):
        source, target, mode = value.rsplit(":", 2)
        if mode != "Z":
            raise ValueError("Staging bind mounts require their own SELinux label.")
        actual[str(Path(source).resolve())] = target
    if actual != expected or len(service.get("volumes", [])) != 2:
        raise ValueError("Only dedicated staging data/backup mounts are permitted.")
    if service.get("ports") != ["127.0.0.1:18080:8080"] or service.get("networks") != ["staging"] or compose.get("networks") != {"staging": {"name": "gamerhq-staging-private", "driver": "bridge"}}:
        raise ValueError("Staging must use a dedicated network and loopback API port.")
    if service.get("env_file") != [{"path": profile["botEnvFile"], "required": False}]:
        raise ValueError("Only the dedicated staging credential file is permitted.")
    if any(Path(profile[key]).resolve().is_relative_to(directory) for key in ("composeFile", "botEnvFile", "releaseFile", "sdkReleaseFile") for directory in mounted):
        raise ValueError("Protected staging configuration cannot be mounted into the bot.")
    environment = service.get("environment", {})
    if environment.get("GAMERHQ_DB_PATH") != "/app/runtime/data/gamerhq.db" or any(key in environment for key in ("DISCORD_TOKEN", "GUILD_ID", "GAMERHQ_WEB_API_SECRET")):
        raise ValueError("Staging identity must come from the protected staging environment.")
    if service.get("read_only") is not True or service.get("cap_drop") != ["ALL"] or service.get("security_opt") != ["no-new-privileges:true"] or service.get("user") != "10001:10001":
        raise ValueError("Staging must retain the hardened Host container boundary.")
    if profile.get("healthUrl") != "http://127.0.0.1:18080":
        raise ValueError("Staging health must use its dedicated loopback port.")
    return compose


def fingerprint(profile):
    hasher = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode())
    for key in ("composeFile", "botEnvFile", "releaseFile", "sdkReleaseFile"):
        hasher.update(Path(profile[key]).read_bytes())
    return hasher.hexdigest()


def artifact_fingerprint(profile):
    fields = {key: profile[key] for key in ("baseImage", "sdkVersion", "sdkWheel", "sdkSha256", "hostSourceCommit", "approvedCapabilities")}
    hasher = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode())
    for key in ("releaseFile", "sdkReleaseFile"):
        hasher.update(Path(profile[key]).read_bytes())
    return hasher.hexdigest()


def verify_local_artifacts(profile):
    release = json.loads(Path(profile["releaseFile"]).read_text(encoding="utf-8"))
    sdk = json.loads(Path(profile["sdkReleaseFile"]).read_text(encoding="utf-8"))
    for descriptor in (release, sdk):
        if descriptor.get("sourceReviewed") is not True or descriptor.get("repository") not in profile["trustedRepositories"] or not re.fullmatch(r"[0-9a-f]{40}", descriptor.get("reviewedCommit", "")):
            raise ValueError("Offline candidates require reviewed source descriptors.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", descriptor.get("distribution", "")):
            raise ValueError("Invalid reviewed distribution.")
        name = descriptor["distribution"].replace("-", "_") + "-" + descriptor["version"] + "-py3-none-any.whl"
        target = Path(profile["artifactRoot"]) / name
        if target.is_symlink():
            raise ValueError("Local artifacts cannot be symbolic links.")
        verify_wheel(target, distribution=descriptor["distribution"], version=descriptor["version"], sha256=descriptor["sha256"])
    if sdk["distribution"] != "gamerhq-skill-sdk" or sdk["version"] != profile["sdkVersion"] or sdk["sha256"] != profile["sdkSha256"] or profile["sdkWheel"] != "gamerhq_skill_sdk-" + sdk["version"] + "-py3-none-any.whl":
        raise ValueError("SDK bytes do not match the protected profile.")
    from skill_runtime.contracts.capabilities import KNOWN_CAPABILITIES
    capabilities = release.get("capabilities")
    if release.get("runtimeApi") != "1" or not isinstance(capabilities, list) or not set(capabilities) <= KNOWN_CAPABILITIES or not set(capabilities) <= set(profile["approvedCapabilities"].get(release["skillId"], [])) or release.get("migrationMode") not in ("none", "backward-compatible"):
        raise ValueError("Offline release compatibility/capability/migration review did not pass.")


def verify_offline(profile):
    validate_profile(profile)
    verify_docker(profile)
    verify_local_artifacts(profile)
    return {"schemaVersion": 1, "kind": "offline-local-artifact", "verifiedAt": int(time.time()),
        "fingerprint": artifact_fingerprint(profile), "verified": True, "gatewayStarted": False,
        "liveIdentityVerified": False, "publishedReleaseVerified": False}


def require_offline_verification(profile, *, now=None):
    validate_profile(profile)
    verify_docker(profile)
    verify_local_artifacts(profile)
    proof = json.loads(Path(profile["offlineVerificationFile"]).read_text(encoding="utf-8"))
    age = (time.time() if now is None else now) - proof.get("verifiedAt", 0)
    if proof.get("kind") != "offline-local-artifact" or proof.get("schemaVersion") != 1 or proof.get("verified") is not True or proof.get("gatewayStarted") is not False or not 0 <= age <= 86400 or proof.get("fingerprint") != artifact_fingerprint(profile):
        raise PermissionError("Offline artifact verification is absent, expired or changed.")


def require_verification(profile, *, now=None):
    validate_profile(profile)
    if profile.get("workerEnabled") is not True:
        raise PermissionError("The staging worker is disabled in its protected profile.")
    verify_docker(profile)
    proof = json.loads(Path(profile["verificationFile"]).read_text(encoding="utf-8"))
    age = (time.time() if now is None else now) - proof.get("verifiedAt", 0)
    if proof.get("schemaVersion") != 1 or proof.get("kind") != "live" or not 0 <= age <= 3600 or proof.get("fingerprint") != fingerprint(profile) or proof.get("gatewayStarted") is not False or proof.get("verified") is not True:
        raise PermissionError("Staging trust/identity verification is absent, expired or changed.")
    release = json.loads(Path(profile["releaseFile"]).read_text(encoding="utf-8"))
    wheel = Path(profile["artifactRoot"]) / (release["distribution"].replace("-", "_") + "-" + release["version"] + "-py3-none-any.whl")
    verify_wheel(wheel, distribution=release["distribution"], version=release["version"], sha256=release["sha256"])
    if not IMAGE.fullmatch(profile["baseImage"]):
        raise ValueError("A reviewed immutable Host base image is required.")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Discord identity verification cannot follow redirects.")


def verify_daemon(profile):
    if profile.get("dockerMode") == "rootless":
        from .staging_rootless import verify_rootless
        verify_rootless(profile)
        return
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT", "default") != "default":
        raise ValueError("Staging must use the approved local VPS Docker context.")
    expected = profile.get("dockerDaemonId", "")
    if not expected or expected.startswith("REPLACE"):
        raise ValueError("Reviewed Docker daemon identity is required.")
    def run(command, **kwargs):
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=True, **kwargs).stdout.strip()
    if run(["docker", "context", "show"]) != "default" or run(["docker", "info", "--format", "{{.ID}}"] ) != expected:
        raise ValueError("Docker daemon identity differs from the approved staging VPS.")


def verify_docker(profile):
    verify_daemon(profile)
    if not IMAGE.fullmatch(profile["baseImage"]):
        raise ValueError("Reviewed immutable base identity is required.")
    def run(command, **kwargs):
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=True, **kwargs).stdout.strip()
    environment = {**os.environ, "GAMERHQ_STAGING_IMAGE": profile["baseImage"]}
    run(["docker", "compose", "--file", profile["composeFile"], "--profile", "live-acceptance", "config", "--quiet"], env=environment)
    database, backups = Path(profile["database"]), Path(profile["backupRoot"])
    if not database.is_file() or not backups.is_dir():
        raise ValueError("Initialize approved isolated data/backup paths before verification.")
    run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10001:10001", "--mount", f"type=bind,src={database.parent},dst=/staging-data",
        "--mount", f"type=bind,src={backups},dst=/staging-backups", "--entrypoint", "python", profile["baseImage"], "-c",
        "import os; assert all(os.access(path,os.R_OK|os.W_OK) for path in ('/staging-data','/staging-data/gamerhq.db','/staging-backups'))"])


def discord_read(path, token):
    request = Request("https://discord.com/api/v10" + path)
    request.add_unredirected_header("Authorization", "Bot " + token)
    with build_opener(NoRedirects()).open(request, timeout=15) as response:
        body = response.read(262145)
    if len(body) > 262144:
        raise ValueError("Discord verification response exceeds its bound.")
    return json.loads(body)


def category_permissions(guild_id, bot_id, member, roles, category):
    ids = {str(guild_id), *map(str, member.get("roles", []))}
    permissions = 0
    for role in roles:
        if str(role["id"]) in ids:
            permissions |= int(role["permissions"])
    if discord.Permissions(permissions).administrator:
        return discord.Permissions.all().value
    def apply(allow, deny):
        nonlocal permissions
        permissions = (permissions & ~deny) | allow
    overwrites = category.get("permission_overwrites", [])
    for row in overwrites:
        if str(row["id"]) == str(guild_id):
            apply(int(row["allow"]), int(row["deny"]))
    allow = deny = 0
    for row in overwrites:
        if row["type"] == 0 and str(row["id"]) in ids - {str(guild_id)}:
            allow |= int(row["allow"])
            deny |= int(row["deny"])
    apply(allow, deny)
    for row in overwrites:
        if row["type"] == 1 and str(row["id"]) == str(bot_id):
            apply(int(row["allow"]), int(row["deny"]))
    return permissions


def verify_remote(profile, *, read=discord_read, fetch=fetch_release):
    validate_profile(profile)
    if platform.system() != "Linux":
        raise ValueError("Verify on the approved isolated Linux staging Host.")
    verify_docker(profile)
    ids = {key: str(profile.get(key, "0")) for key in ("stagingBotId", "stagingGuildId", "productionBotId", "productionGuildId", "categoryId", "voiceCategoryId")}
    if any(not value.isascii() or not value.isdigit() or int(value) <= 0 for value in ids.values()) or ids["stagingBotId"] == ids["productionBotId"] or ids["stagingGuildId"] == ids["productionGuildId"]:
        raise ValueError("Explicit distinct production/staging identities and staging categories are required.")
    values = {}
    for line in Path(profile["botEnvFile"]).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError("Invalid or duplicate staging environment key.")
        values[key] = value.strip().strip("\"'")
    if values.get("GUILD_ID") != ids["stagingGuildId"] or not values.get("DISCORD_TOKEN") or len(values.get("GAMERHQ_WEB_API_SECRET", "")) < 32:
        raise ValueError("Dedicated staging credentials are incomplete.")
    if not hmac.compare_digest(values["GAMERHQ_WEB_API_SECRET"].encode(), os.environ.get(profile["healthSecretEnv"], "").encode()):
        raise ValueError("Protected staging health credential does not match the dedicated bot environment.")
    if profile.get("privateArtifactSource") and not os.environ.get(profile["releaseTokenEnv"]):
        raise ValueError("Approved private release access is not configured.")
    if os.name == "posix" and Path(profile["botEnvFile"]).stat().st_mode & 0o077:
        raise ValueError("Staging credential file must be private (mode 600).")
    if read("/users/@me", values["DISCORD_TOKEN"]).get("id") != ids["stagingBotId"]:
        raise ValueError("Staging token does not identify the approved staging bot.")
    guilds = read("/users/@me/guilds?limit=200", values["DISCORD_TOKEN"])
    if not isinstance(guilds, list) or {str(row["id"]) for row in guilds} != {ids["stagingGuildId"]}:
        raise ValueError("The dedicated staging bot must belong only to its approved staging guild.")
    guild_id = ids["stagingGuildId"]
    guild = read("/guilds/" + guild_id, values["DISCORD_TOKEN"])
    if str(guild.get("id")) != guild_id:
        raise ValueError("Staging guild identity did not verify.")
    member = read(f"/guilds/{guild_id}/members/{ids['stagingBotId']}", values["DISCORD_TOKEN"])
    roles = read(f"/guilds/{guild_id}/roles", values["DISCORD_TOKEN"])
    for kind, key in (("text", "categoryId"), ("voice", "voiceCategoryId")):
        category = read("/channels/" + ids[key], values["DISCORD_TOKEN"])
        if str(category.get("guild_id")) != guild_id or category.get("type") != 4:
            raise ValueError("A staging category does not belong to the staging guild.")
        required = discord.Permissions(view_channel=True, manage_channels=True, manage_roles=True,
            **({"send_messages": True, "read_message_history": True} if kind == "text" else {"connect": True})).value
        if category_permissions(guild_id, ids["stagingBotId"], member, roles, category) & required != required:
            raise ValueError("Staging bot lacks the required category permissions.")
    release = json.loads(Path(profile["releaseFile"]).read_text(encoding="utf-8"))
    fetch(release, artifact_root=profile["artifactRoot"], trusted_repositories=profile["trustedRepositories"],
        token=os.environ.get(profile["releaseTokenEnv"]))
    sdk = json.loads(Path(profile["sdkReleaseFile"]).read_text(encoding="utf-8"))
    if sdk.get("distribution") != "gamerhq-skill-sdk" or sdk.get("version") != profile["sdkVersion"] or sdk.get("sha256") != profile["sdkSha256"]:
        raise ValueError("SDK release differs from the reviewed staging profile.")
    fetch(sdk, artifact_root=profile["artifactRoot"], trusted_repositories=profile["trustedRepositories"],
        token=os.environ.get(profile["releaseTokenEnv"]))
    verify_local_artifacts(profile)
    return {"schemaVersion": 1, "kind": "live", "verifiedAt": int(time.time()), "fingerprint": fingerprint(profile), "verified": True, "gatewayStarted": False}
