import copy
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from hosts.gamerhq.staging_rootless import verify_rootless
from tools.prepare_staging_access import package_plan
from tools.staging_bridge_protocol import encode, decode
from tools.staging_configuration import descriptor, validate_binding
from tools.staging_ssh_client import invoke


@pytest.fixture
def rootless(tmp_path):
    home = tmp_path / "deployment-home"
    profile = {"deploymentUid": 1001, "deploymentHome": str(home), "dockerHost": "unix:///run/user/1001/docker.sock", "dockerDaemonId": "staging-only"}
    info = {"SecurityOptions": ["name=rootless"], "DockerRootDir": str(home / ".local/share/docker"), "CgroupDriver": "systemd", "CgroupVersion": "2", "ID": "staging-only"}
    with patch("os.geteuid", return_value=1001, create=True), patch("os.access", return_value=False), patch.dict(os.environ, {"DOCKER_HOST": profile["dockerHost"], "DOCKER_CONTEXT": ""}):
        yield profile, info


def test_rootless_worker_cannot_fall_back_to_production_socket_or_root(rootless):
    profile, info = rootless
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(info)))
    assert verify_rootless(profile, run=run)["ID"] == "staging-only"
    with patch.dict(os.environ, {"DOCKER_HOST": "unix:///var/run/docker.sock"}):
        with pytest.raises(PermissionError): verify_rootless(profile, run=run)
    with patch("os.geteuid", return_value=0, create=True):
        with pytest.raises(PermissionError): verify_rootless(profile, run=run)
    with patch("os.access", return_value=True):
        with pytest.raises(PermissionError): verify_rootless(profile, run=run)


@pytest.mark.parametrize("field,value", [("SecurityOptions", []), ("DockerRootDir", "/var/lib/docker"),
    ("ID", "production"), ("CgroupDriver", "none"), ("CgroupVersion", "1")])
def test_rootless_engine_identity_storage_and_enforced_limits_are_required(rootless, field, value):
    profile, info = rootless
    info[field] = value
    with pytest.raises(PermissionError): verify_rootless(profile, run=Mock(return_value=SimpleNamespace(stdout=json.dumps(info))))


def test_prerequisites_never_upgrade_remove_or_replace_existing_daemon():
    package_plan("Inst uidmap (1.0 Ubuntu)\nInst acl (2.0 Ubuntu)")
    for plan in ("Inst docker-ce (1.0 Docker)", "Inst libc6 [1.0] (2.0 Ubuntu)", "Remv docker.io [1.0]", "Inst openssh-server (1.0 Ubuntu)"):
        with pytest.raises(PermissionError): package_plan(plan)


@pytest.fixture
def release():
    return {"repository": "Duy-Phan96/gamerhq-skill-event-manager", "distribution": "gamerhq-skill-event-manager", "version": "0.1.0",
        "reviewedCommit": "a" * 40, "sha256": "b" * 64, "sourceReviewed": True, "skillId": "event-manager", "runtimeApi": "1",
        "capabilities": ["storage.skill", "storage.atomic"], "migrationMode": "backward-compatible"}


@pytest.mark.parametrize("change", [{"repository": "other/private"}, {"migrationMode": "destructive"}, {"sourceReviewed": False},
    {"capabilities": ["docker.root"]}, {"version": "../production"}, {"sha256": "bad"}, {"profile": "/opt/gamerhq"}])
def test_remote_candidate_configuration_cannot_expand_paths_publishers_or_capabilities(release, change):
    with pytest.raises((ValueError, TypeError)): descriptor({**release, **change})


def test_candidate_configuration_reuses_versioned_release_trust_contract(release):
    assert descriptor(release)["version"] == "0.1.0"
    sdk = {key: release[key] for key in ("version", "reviewedCommit", "sha256", "sourceReviewed")}
    sdk.update(repository="Duy-Phan96/GamerHQ", distribution="gamerhq-skill-sdk", version="0.3.0")
    assert descriptor(sdk, sdk=True)["version"] == "0.3.0"
    with pytest.raises(ValueError): descriptor({**sdk, "version": "0.4.0"}, sdk=True)


def test_secrets_travel_on_stdin_never_ssh_command_or_argv():
    value = {"version": 1, "phase": "configure", "operation": "initial", "approval": "CONFIGURE ISOLATED STAGING"}
    seen = []
    def run(args, **kwargs):
        seen.append(args)
        assert "fixture-private-value" not in " ".join(args)
        assert json.loads(kwargs["input"])["discordToken"] == "fixture-private-value"
        assert decode(args[-1])["phase"] == "configure"
        return SimpleNamespace(returncode=0, stdout='{"result":"PASS"}')
    invoke(value, host="stage.example.test", port=22, private_key="fixture", known_hosts="fixture", payload={"discordToken": "fixture-private-value"}, run=run)
    assert len(seen) == 1


def test_immutable_policy_binds_config_to_bootstrapped_identity_and_roots():
    policy = {key: "fixed" for key in ("root", "configurationRoot", "deploymentUid", "deploymentHome", "dockerHost", "hostSourceCommit")}
    profile = {**policy, "dockerMode": "rootless"}
    validate_binding(profile, policy)
    for key in policy:
        with pytest.raises(PermissionError): validate_binding({**profile, key: "production"}, policy)


def test_revocation_is_durable_even_when_container_stop_fails(tmp_path):
    from tools.staging_configuration import configure
    marker = tmp_path / "access-revoked"
    profile = {"workRoot": str(tmp_path)}
    with patch("tools.staging_configuration.audit"), patch("tools.staging_configuration.subprocess.run", side_effect=RuntimeError("fixture failure")):
        with pytest.raises(RuntimeError): configure("revoke", None, profile)
    assert json.loads(marker.read_text())["revoked"] is True


def test_remote_dispatcher_rejects_root_before_processing_any_request():
    from tools.staging_dispatch import main
    with patch("os.geteuid", return_value=0, create=True):
        with pytest.raises(PermissionError, match="never execute as root"): main()


def test_self_contained_operator_command_verifies_bytes_before_execution():
    from tools.bootstrap_command import render
    key = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"a" * 32).decode()
    body = b"print('synthetic installer')\n"
    command = render("a" * 40, body, key)
    assert "REVIEWED_" not in command and "APPROVED-TEMP" not in command
    code = command.split("\n", 1)[1].rsplit("GAMERHQ_VERIFIED_BOOTSTRAP", 1)[0]
    with patch("os.geteuid", return_value=0, create=True), patch("urllib.request.build_opener") as opener, patch("subprocess.run") as run:
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b"tampered bytes"
        with pytest.raises(SystemExit, match="verification failed"): exec(code, {})
        run.assert_not_called()
        response.read.return_value = body
        def executed(args, **kwargs):
            assert Path(args[1]).read_bytes() == body
            assert args[args.index("--commit") + 1] == "a" * 40
            assert args[args.index("--ssh-public-key") + 1] == key
        run.side_effect = executed
        exec(code, {})
        run.assert_called_once()


def test_watchdog_is_read_only_and_requires_gateway_and_enabled_skill_health():
    from tools.staging_health import health
    with patch("hosts.gamerhq.staging_rootless.verify_rootless"), patch("tools.staging_configuration.audit"), patch(
            "tools.skill_staging.host_request") as host, patch("tools.staging_health.subprocess.run") as docker:
        assert health({"workerEnabled": False})["status"] == "READY_OFFLINE"
        host.assert_not_called(); docker.assert_not_called()
        docker.return_value = SimpleNamespace(stdout="healthy")
        host.return_value = {"version": "0.1.0", "state": {"enabled": True, "healthy": False}}
        with pytest.raises(RuntimeError): health({"workerEnabled": True, "stagingGuildId": "2"})
        host.return_value["state"]["healthy"] = True
        assert health({"workerEnabled": True, "stagingGuildId": "2"})["status"] == "HOST_READY"
        assert all(call.args[0][1] == "inspect" for call in docker.call_args_list)


def test_atomic_staging_records_recover_without_reusing_stale_temporary_files(tmp_path):
    from tools.skill_staging import write_record
    path = tmp_path / "proof.json"
    stale = path.with_suffix(".new"); stale.write_text("interrupted old attempt")
    write_record(path, {"verified": True})
    assert json.loads(path.read_text()) == {"verified": True}
    assert stale.read_text() == "interrupted old attempt"


def test_vendored_rootless_launcher_is_exactly_the_recorded_upstream_bytes():
    import hashlib
    root = Path(__file__).resolve().parents[1] / "deploy/staging/rootless"
    source = json.loads((root / "UPSTREAM.json").read_text())
    assert source["commit"] == "f8215cc266744ef195a50a70d427c345da2acdbb"
    assert hashlib.sha256((root / "dockerd-rootless.sh").read_bytes()).hexdigest() == source["sha256"]["dockerd-rootless.sh"]


def test_artifact_access_can_rotate_without_rewriting_bot_credentials_or_dataset(tmp_path):
    from tools.staging_configuration import configure
    target = tmp_path / "worker.env"
    target.write_text("old fixture")
    with patch("tools.staging_configuration.audit"), patch("tools.staging_dispatch.ENVIRONMENT", target), patch.dict(
            os.environ, {"GAMERHQ_STAGING_WEB_API_SECRET": "synthetic-staging-api-secret"}):
        configure("credentials", {"releaseToken": "synthetic-renewed-read-only-access"}, {"workerEnabled": True})
        assert "synthetic-renewed-read-only-access" in target.read_text()
        assert "synthetic-staging-api-secret" in target.read_text()
        assert "DISCORD_TOKEN" not in target.read_text()
        before = target.read_bytes()
        with pytest.raises(ValueError): configure("credentials", {"releaseToken": "new", "profile": "/opt/gamerhq"}, {})
        assert target.read_bytes() == before
