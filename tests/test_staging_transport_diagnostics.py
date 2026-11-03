import json
import subprocess
from types import SimpleNamespace

import pytest

from tools.staging_ssh_client import StagingAccessError, invoke


REQUEST = {"version": 1, "phase": "prepare", "operation": "build-base", "approval": "BUILD ISOLATED STAGING BASE"}


@pytest.mark.parametrize("status,stderr,expected", [
    (255, "Host key verification failed.", "SSH_HOST_KEY_REJECTED"),
    (255, "Permission denied (publickey).", "SSH_AUTHENTICATION_REJECTED"),
    (255, "connect: Connection refused", "SSH_CONNECTION_FAILED"),
    (255, "Connection closed", "SSH_SESSION_FAILED"),
    (1, "Staging bridge rejected or failed the operation.", "REMOTE_WORKER_REJECTED"),
    (1, "Unsupported or unavailable isolated staging request.", "REMOTE_ENTRY_REJECTED"),
    (1, "Isolated staging operation failed.", "REMOTE_PREPARATION_FAILED"),
    (4, "Unknown private exception details", "REMOTE_COMMAND_FAILED"),
])
def test_reports_failure_layer_without_remote_output(status, stderr, expected):
    def run(args, **kwargs):
        return SimpleNamespace(returncode=status, stdout="PRIVATE_RESPONSE", stderr=stderr + " PRIVATE_CREDENTIAL")
    with pytest.raises(StagingAccessError) as error:
        invoke(REQUEST, host="127.0.0.1", port=22, private_key="PRIVATE_KEY", known_hosts="fixture", run=run)
    assert error.value.evidence["stagingError"] == expected
    assert error.value.evidence["sshExitCode"] == status
    assert "PRIVATE" not in str(error.value)


def test_records_public_key_authentication_and_preserves_strict_transport():
    request = {"version": 1, "phase": "configure", "operation": "initial", "approval": "CONFIGURE ISOLATED STAGING"}
    def run(args, **kwargs):
        assert all(value in args for value in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "IdentityAgent=none",
            "ForwardAgent=no", "ClearAllForwardings=yes", "PasswordAuthentication=no", "KbdInteractiveAuthentication=no"))
        assert "PRIVATE_PAYLOAD" not in " ".join(args)
        assert json.loads(kwargs["input"])["discordToken"] == "PRIVATE_PAYLOAD"
        return SimpleNamespace(returncode=1, stdout="", stderr='Authenticated to fixture using "publickey".\nStaging bridge rejected or failed the operation.')
    with pytest.raises(StagingAccessError) as error:
        invoke(request, host="127.0.0.1", port=22, private_key="fixture", known_hosts="fixture",
            payload={"discordToken": "PRIVATE_PAYLOAD"}, run=run)
    assert error.value.evidence["authenticated"] is True
    assert "PRIVATE_PAYLOAD" not in str(error.value)


def test_timeout_and_output_limit_are_redacted():
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 2100, output="PRIVATE", stderr="PRIVATE")
    with pytest.raises(StagingAccessError, match="SSH_OPERATION_TIMEOUT") as error:
        invoke(REQUEST, host="127.0.0.1", port=22, private_key="fixture", known_hosts="fixture", run=timeout)
    assert "PRIVATE" not in str(error.value)
    with pytest.raises(StagingAccessError, match="REMOTE_OUTPUT_LIMIT"):
        invoke(REQUEST, host="127.0.0.1", port=22, private_key="fixture", known_hosts="fixture",
            run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="x" * 65537, stderr=""))
