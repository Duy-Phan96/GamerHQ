import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import os
from zipfile import ZipFile

import discord
import pytest
from hosts.gamerhq.skill_staging import validate_profile, verify_remote, require_verification, fingerprint, category_permissions, verify_docker
from tools.skill_staging import initialize, bootstrap

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def staging(tmp_path):
    profile = json.loads((ROOT / "deploy/staging/profile.example.json").read_text())
    root, config = tmp_path / "staging", tmp_path / "configuration"
    root.mkdir(); config.mkdir()
    for key in ("root", "database", "artifactRoot", "workRoot", "backupRoot", "verificationFile"):
        profile[key] = profile[key].replace("/opt/gamerhq-staging", root.as_posix())
    for key in ("configurationRoot", "composeFile", "botEnvFile", "releaseFile", "sdkReleaseFile"):
        profile[key] = profile[key].replace("/etc/gamerhq/staging", config.as_posix())
    profile.update(stagingBotId="1", stagingGuildId="2", productionBotId="3", productionGuildId="4", categoryId="5", voiceCategoryId="6",
        baseImage="sha256:" + "a" * 64)
    compose = json.loads((ROOT / "deploy/staging/compose.json").read_text())
    service = compose["services"]["gamerhq-staging"]
    service["volumes"] = [f"{root.as_posix()}/data:/app/runtime/data:Z", f"{root.as_posix()}/backups:/app/backups:Z"]
    service["env_file"][0]["path"] = profile["botEnvFile"]
    Path(profile["composeFile"]).write_text(json.dumps(compose))
    Path(profile["botEnvFile"]).write_text("DISCORD_TOKEN=fake-staging-token\nGUILD_ID=2\nGAMERHQ_WEB_API_SECRET=" + "s" * 40)
    Path(profile["botEnvFile"]).chmod(0o600)
    release = json.loads((ROOT / "deploy/staging/event-manager.release.example.json").read_text())
    artifact = Path(profile["artifactRoot"]); artifact.mkdir()
    wheel = artifact / "gamerhq_skill_event_manager-0.1.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("gamerhq_skill_event_manager-0.1.0.dist-info/METADATA", "Name: gamerhq-skill-event-manager\nVersion: 0.1.0\n")
    release.update(sourceReviewed=True, reviewedCommit="a" * 40, sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    Path(profile["releaseFile"]).write_text(json.dumps(release))
    sdk = {"distribution": "gamerhq-skill-sdk", "version": "0.3.0", "sha256": "c" * 64}
    profile["sdkSha256"] = sdk["sha256"]
    Path(profile["sdkReleaseFile"]).write_text(json.dumps(sdk))
    with patch("hosts.gamerhq.skill_staging.verify_docker"):
        yield profile, compose, release, wheel


def test_templates_remain_disabled_and_have_no_usable_credentials():
    profile = json.loads((ROOT / "deploy/staging/profile.example.json").read_text())
    assert profile["workerEnabled"] is False
    assert json.loads((ROOT / "deploy/staging/event-manager.release.example.json").read_text())["sourceReviewed"] is False
    assert "DISCORD_TOKEN=\n" in (ROOT / "deploy/staging/bot.env.example").read_text()


@pytest.mark.parametrize("mutation", ["production-root", "production-mount", "network", "restart", "socket", "gateway-default", "credentials", "verification-mount"])
def test_profile_rejects_production_overlap_and_shared_privileged_resources(staging, mutation):
    profile, compose, _, _ = staging
    service = compose["services"]["gamerhq-staging"]
    if mutation == "production-root": profile["root"] = "/opt/gamerhq"
    if mutation == "production-mount": service["volumes"][0] = "/opt/gamerhq/data:/app/runtime/data:Z"
    if mutation == "network": compose["networks"]["staging"]["name"] = "production-network"
    if mutation == "restart": service["restart"] = "unless-stopped"
    if mutation == "socket": service["volumes"].append("/var/run/docker.sock:/var/run/docker.sock:Z")
    if mutation == "gateway-default": service.pop("profiles")
    if mutation == "credentials": service["env_file"][0]["path"] = "/opt/gamerhq/.env"
    if mutation == "verification-mount": profile["verificationFile"] = str(Path(profile["database"]).parent / "verification.json")
    Path(profile["composeFile"]).write_text(json.dumps(compose))
    with pytest.raises(ValueError): validate_profile(profile)


def proof_for(profile):
    permissions = discord.Permissions(view_channel=True, manage_channels=True, manage_roles=True, send_messages=True, read_message_history=True, connect=True).value
    def read(path, token):
        assert token == "fake-staging-token"
        if path == "/users/@me": return {"id": "1"}
        if path == "/users/@me/guilds?limit=200": return [{"id": "2"}]
        if path == "/guilds/2": return {"id": "2"}
        if path.endswith("/members/1"): return {"roles": []}
        if path.endswith("/roles"): return [{"id": "2", "permissions": str(permissions)}]
        if path.startswith("/channels/"): return {"guild_id": "2", "type": 4, "permission_overwrites": []}
        raise AssertionError(path)
    with patch("hosts.gamerhq.skill_staging.platform.system", return_value="Linux"), patch.dict(os.environ,
            {profile["healthSecretEnv"]: "s" * 40, profile["releaseTokenEnv"]: "fake-read-only-release-access"}):
        return verify_remote(profile, read=read, fetch=Mock())


def test_read_only_verification_never_enables_the_worker_and_proof_is_bound_and_expires(staging):
    profile, _, _, wheel = staging
    proof = proof_for(profile)
    assert proof["verified"] and proof["gatewayStarted"] is False
    assert profile["workerEnabled"] is False
    Path(profile["verificationFile"]).write_text(json.dumps(proof))
    with pytest.raises(PermissionError, match="disabled"): require_verification(profile)
    profile["workerEnabled"] = True
    proof = proof_for(profile)
    Path(profile["verificationFile"]).write_text(json.dumps(proof))
    require_verification(profile, now=proof["verifiedAt"])
    with pytest.raises(PermissionError): require_verification(profile, now=proof["verifiedAt"] + 3601)
    Path(profile["botEnvFile"]).write_text(Path(profile["botEnvFile"]).read_text() + "\n# changed identity configuration")
    with pytest.raises(PermissionError): require_verification(profile)


def test_production_identity_and_category_denies_cannot_be_attested(staging):
    profile, _, _, _ = staging
    profile["stagingBotId"] = profile["productionBotId"]
    with pytest.raises(ValueError, match="distinct"): proof_for(profile)
    denied = discord.Permissions(view_channel=True).value
    assert category_permissions("2", "1", {"roles": []}, [{"id": "2", "permissions": str(denied)}],
        {"permission_overwrites": [{"type": 0, "id": "2", "allow": "0", "deny": str(denied)}]}) == 0


def test_staging_initialization_is_exclusive_and_never_overwrites_a_dataset(staging):
    profile, _, _, _ = staging
    with pytest.raises(PermissionError): initialize(profile, "yes")
    initialize(profile, "INITIALIZE ISOLATED STAGING")
    path = Path(profile["database"])
    before = path.read_bytes()
    with pytest.raises(FileExistsError): initialize(profile, "INITIALIZE ISOLATED STAGING")
    assert path.read_bytes() == before


def test_bootstrap_requires_live_authorization_and_does_not_replace_an_existing_container(staging):
    profile, _, _, _ = staging
    with patch("tools.skill_staging.require_verification"), patch("tools.skill_staging.subprocess.run", return_value=SimpleNamespace(stdout="existing")) as run:
        with pytest.raises(PermissionError): bootstrap(profile, "yes")
        run.assert_not_called()
        with pytest.raises(ValueError, match="already exists"): bootstrap(profile, "BOOTSTRAP ISOLATED STAGING")
        command = run.call_args.args[0]
        assert command[command.index("--filter") + 1] == "name=^/gamerhq-staging-bot$"


def test_verifier_refuses_an_unapproved_docker_context_or_daemon():
    profile = {"dockerDaemonId": "approved-staging-daemon", "baseImage": "sha256:" + "a" * 64}
    with patch.dict(os.environ, {"DOCKER_HOST": "fake-remote-daemon"}):
        with pytest.raises(ValueError, match="local"): verify_docker(profile)
    with patch.dict(os.environ, {"DOCKER_HOST": "", "DOCKER_CONTEXT": "default"}), patch(
            "hosts.gamerhq.skill_staging.subprocess.run", return_value=SimpleNamespace(stdout="unapproved-daemon")):
        with pytest.raises(ValueError, match="identity"): verify_docker(profile)


def test_docker_readiness_checks_staging_uid_access_without_gateway_or_credentials(staging):
    profile, _, _, _ = staging
    initialize(profile, "INITIALIZE ISOLATED STAGING")
    Path(profile["backupRoot"]).mkdir()
    profile["dockerDaemonId"] = "approved-staging-daemon"
    values = [SimpleNamespace(stdout=value) for value in ("default", "approved-staging-daemon", "", "")]
    with patch.dict(os.environ, {"DOCKER_HOST": "", "DOCKER_CONTEXT": "default"}), patch(
            "hosts.gamerhq.skill_staging.subprocess.run", side_effect=values) as run:
        verify_docker(profile)
    command = run.call_args.args[0]
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "10001:10001"
    assert "--env-file" not in command and "bot.py" not in command
