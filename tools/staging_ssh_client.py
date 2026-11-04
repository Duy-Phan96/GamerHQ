"""GitHub-hosted client for a forced-command staging key; never a remote shell."""
import argparse
import json
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .staging_bridge_protocol import encode
from .staging_errors import decode_evidence


class StagingAccessError(RuntimeError):
    """Public categorical evidence only; never retain remote output or payloads."""
    def __init__(self, code, request, *, exit_code=None, authenticated=False, worker_evidence=None):
        self.evidence = {"stagingError": code, "phase": request["phase"], "operation": request["operation"],
            "sshExitCode": exit_code, "authenticated": authenticated}
        if worker_evidence:
            checked = decode_evidence(json.dumps(worker_evidence))
            if checked:
                self.evidence.update(workerFailure=checked['stagingFailure'],workerStep=checked['step'])
                if 'exitCode' in checked:self.evidence['workerExitCode']=checked['exitCode']
                if 'auditId' in checked:self.evidence['workerAuditId']=checked['auditId']
        super().__init__(json.dumps(self.evidence, separators=(",", ":")))


def failure_code(returncode, stderr):
    if decode_evidence(stderr):
        return 'REMOTE_WORKER_REJECTED'
    if returncode == 255:
        if "Host key verification failed" in stderr or "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
            return "SSH_HOST_KEY_REJECTED"
        if "Permission denied" in stderr:
            return "SSH_AUTHENTICATION_REJECTED"
        if any(text in stderr for text in ("Connection refused", "Connection timed out", "Could not resolve hostname", "No route to host")):
            return "SSH_CONNECTION_FAILED"
        return "SSH_SESSION_FAILED"
    if "Staging bridge rejected or failed the operation" in stderr:
        return "REMOTE_WORKER_REJECTED"
    if "Unsupported or unavailable isolated staging request" in stderr:
        return "REMOTE_ENTRY_REJECTED"
    if "Isolated staging operation failed" in stderr:
        return "REMOTE_PREPARATION_FAILED"
    return "REMOTE_COMMAND_FAILED"


def invoke(request, *, host, port, private_key, known_hosts, payload=None, run=subprocess.run, encoder=encode, failure_factory=None):
    command = encoder(request)
    try:
        ipaddress.ip_address(host or "")
        valid_host = True
    except ValueError:
        valid_host = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host or ""))
    if not valid_host or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Configure the approved staging SSH endpoint.")
    if not private_key or not known_hosts or "\x00" in private_key + known_hosts:
        raise ValueError("Dedicated staging key and independently pinned host key are required.")
    with tempfile.TemporaryDirectory(prefix="staging-ssh-") as directory:
        key, hosts = Path(directory) / "key", Path(directory) / "known_hosts"
        key.write_text(private_key.rstrip() + "\n", encoding="utf-8")
        hosts.write_text(known_hosts.rstrip() + "\n", encoding="utf-8")
        key.chmod(0o600); hosts.chmod(0o600)
        # Capture verbose authentication evidence privately, then emit only
        # categorical facts. Raw stderr, keys and stdin are never logged.
        args = ["ssh", "-v", "-F", "/dev/null", "-T", "-p", str(port), "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "IdentityAgent=none", "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "PreferredAuthentications=publickey",
            "-o", "UserKnownHostsFile=" + str(hosts), "gamerhq-staging-bridge@" + host, command]
        body = json.dumps(payload) if payload is not None else ""
        if len(body.encode()) > 16384:
            raise ValueError("Staging configuration exceeds its bound.")
        try:
            result = run(args, input=body, capture_output=True, text=True, timeout=2100)
        except subprocess.TimeoutExpired:
            raise StagingAccessError("SSH_OPERATION_TIMEOUT", request) from None
        stderr = getattr(result, "stderr", "") or ""
        authenticated = bool(re.search(r'^Authenticated to .+ using "publickey"', stderr, re.MULTILINE))
        if result.returncode:
            if failure_factory:
                raise failure_factory(request, result.returncode, authenticated, stderr, stdout=getattr(result, 'stdout', '') or '')
            raise StagingAccessError(failure_code(result.returncode, stderr), request,
                exit_code=result.returncode, authenticated=authenticated,worker_evidence=decode_evidence(stderr))
        if len(result.stdout.encode()) > 65536:
            raise StagingAccessError("REMOTE_OUTPUT_LIMIT", request, exit_code=0, authenticated=authenticated)
        return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("prepare", "update", "configure"))
    parser.add_argument("--operation", required=True)
    parser.add_argument("--approval", default="")
    parser.add_argument("--deployment-id", default="")
    args = parser.parse_args()
    payload = json.loads(os.environ["GAMERHQ_STAGING_CONFIGURATION"]) if args.phase == "configure" and args.operation in ("initial", "release", "credentials") else None
    if args.operation == "credentials" and payload is not None:
        payload = {"releaseToken": payload["releaseToken"]}
    output = invoke({"version": 1, "phase": args.phase, "operation": args.operation, "approval": args.approval,
        "deploymentId": args.deployment_id}, host=os.environ.get("GAMERHQ_STAGING_SSH_HOST", ""),
        port=int(os.environ.get("GAMERHQ_STAGING_SSH_PORT", "22")), private_key=os.environ.get("GAMERHQ_STAGING_SSH_KEY", ""),
        known_hosts=os.environ.get("GAMERHQ_STAGING_SSH_KNOWN_HOSTS", ""), payload=payload)
    print(output, end="")


if __name__ == "__main__":
    try:
        main()
    except StagingAccessError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        raise SystemExit('{"stagingError":"CLIENT_CONFIGURATION_OR_PROTOCOL_REJECTED"}') from None
