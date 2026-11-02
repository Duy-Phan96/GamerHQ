import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.staging_bridge_protocol import validate, encode, decode
from tools.staging_dispatch import command_for, environment
from tools.staging_ssh_client import invoke
from tools.prepare_staging_access import public_key, prepare


def request(operation="inspect", **values):
    return {"version": 1, "phase": "prepare", "operation": operation, **values}


@pytest.mark.parametrize("value", [request(phase="production"), request(profile="/opt/gamerhq"),
    request("bootstrap"), request("inspect; bash"), request("initialize", approval="yes"),
    request(version=True), request(phase="update", operation="apply", deploymentId="a" * 32, approval="yes")])
def test_bridge_rejects_production_paths_shells_and_unapproved_effects(value):
    with pytest.raises(ValueError): validate(value)


def test_protocol_bounds_payload_and_round_trips_explicit_approvals():
    value = request("bootstrap", approval="BOOTSTRAP ISOLATED STAGING")
    assert decode(encode(value)) == validate(value)
    for command in (encode(value) + " && id", "bash -c", "gamerhq-staging-v1 " + base64.b64encode(b"x" * 4097).decode()):
        with pytest.raises(ValueError): decode(command)


def test_client_pins_host_and_key_without_remote_shell_or_forwarding():
    def run(args, **kwargs):
        assert "-T" in args and "StrictHostKeyChecking=yes" in args
        assert "ForwardAgent=no" in args and "ClearAllForwardings=yes" in args
        assert args[args.index("-F") + 1] == "/dev/null"
        assert args[-2] == "gamerhq-staging-bridge@stage.example.test"
        assert decode(args[-1])["operation"] == "inspect"
        assert Path(args[args.index("-i") + 1]).read_text() == "private-fixture\n"
        return SimpleNamespace(returncode=0, stdout='{"result":"PASS"}', stderr="private fixture")
    assert "PASS" in invoke(request(), host="stage.example.test", port=2222,
        private_key="private-fixture", known_hosts="pinned-fixture", run=run)
    with pytest.raises(ValueError):
        invoke(request(), host="-oProxyCommand=bad", port=22, private_key="x", known_hosts="y", run=run)


def test_root_dispatcher_uses_only_fixed_staging_paths_and_protected_release(tmp_path):
    release = tmp_path / "reviewed.json"
    release.write_text(json.dumps({"skillId": "event-manager", "distribution": "gamerhq-skill-event-manager", "version": "0.2.0"}))
    profile = {"environment": "staging", "releaseFile": str(release)}
    command = command_for(request(phase="update", operation="stage"), profile, current_version="0.1.0")
    assert Path(command[command.index("--profile") + 1]).as_posix() == "/etc/gamerhq-staging/settings/profile.json"
    assert command[command.index("--wheel") + 1] == "gamerhq_skill_event_manager-0.2.0-py3-none-any.whl"
    assert command[command.index("--current-version") + 1] == "0.1.0"
    with pytest.raises(PermissionError): command_for(request(), {"environment": "production"})


def test_worker_environment_cannot_import_discord_or_process_configuration(tmp_path):
    path = tmp_path / "worker.env"
    path.write_text("DISCORD_TOKEN=fixture\n")
    path.chmod(0o600)
    with patch("tools.staging_dispatch.protected_file"), patch("os.geteuid", return_value=1001, create=True):
        with pytest.raises(ValueError): environment(path)
        path.write_text("GAMERHQ_STAGING_WEB_API_SECRET=fixture\n")
        values = environment(path)
        assert values["GAMERHQ_STAGING_WEB_API_SECRET"] == "fixture" and "DISCORD_TOKEN" not in values


def test_owner_setup_requires_dedicated_public_key_and_explicit_apply_approval():
    key = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"a" * 32).decode()
    assert public_key(key + " dedicated") == key
    with pytest.raises(ValueError): public_key('command="bash" ' + key)
    with patch("tools.prepare_staging_access.preflight", return_value={"workerEnabled": False, "gatewayStarted": False}), patch(
            "tools.prepare_staging_access.prerequisites", return_value="fixture-python"):
        plan = prepare("a" * 40, key)
        assert plan["workerEnabled"] is False and plan["gatewayStarted"] is False
        with pytest.raises(PermissionError): prepare("a" * 40, key, apply=True)
