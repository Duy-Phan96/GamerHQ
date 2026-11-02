"""GitHub-hosted client for a forced-command staging key; never a remote shell."""
import argparse
import json
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .staging_bridge_protocol import encode


def invoke(request, *, host, port, private_key, known_hosts, payload=None, run=subprocess.run):
    command = encode(request)
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
        args = ["ssh", "-F", "/dev/null", "-T", "-p", str(port), "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "PreferredAuthentications=publickey",
            "-o", "UserKnownHostsFile=" + str(hosts), "gamerhq-staging-bridge@" + host, command]
        body = json.dumps(payload) if payload is not None else ""
        if len(body.encode()) > 16384:
            raise ValueError("Staging configuration exceeds its bound.")
        result = run(args, input=body, capture_output=True, text=True, timeout=2100)
        if result.returncode or len(result.stdout.encode()) > 65536:
            raise RuntimeError("Staging bridge operation failed; inspect protected operation evidence.")
        return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("prepare", "update", "configure"))
    parser.add_argument("--operation", required=True)
    parser.add_argument("--approval", default="")
    parser.add_argument("--deployment-id", default="")
    args = parser.parse_args()
    payload = json.loads(os.environ["GAMERHQ_STAGING_CONFIGURATION"]) if args.phase == "configure" and args.operation in ("initial", "release") else None
    output = invoke({"version": 1, "phase": args.phase, "operation": args.operation, "approval": args.approval,
        "deploymentId": args.deployment_id}, host=os.environ.get("GAMERHQ_STAGING_SSH_HOST", ""),
        port=int(os.environ.get("GAMERHQ_STAGING_SSH_PORT", "22")), private_key=os.environ.get("GAMERHQ_STAGING_SSH_KEY", ""),
        known_hosts=os.environ.get("GAMERHQ_STAGING_SSH_KNOWN_HOSTS", ""), payload=payload)
    print(output, end="")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Staging access is unavailable or the request was rejected. No credential values were logged.") from None
