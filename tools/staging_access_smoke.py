"""Ephemeral Linux CI only: exercise restricted SSH against a new local sshd.

Never run on the VPS. The installer refuses existing staging paths/accounts.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .staging_bridge_protocol import encode


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
        raise PermissionError("Only an ephemeral root GitHub Actions runner is supported.")
    with tempfile.TemporaryDirectory(prefix="staging-sshd-ci-") as directory:
        root = Path(directory)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "host")], check=True)
        host_key = (root / "host.pub").read_text().split()[:2]
        (root / "known_hosts").write_text("[127.0.0.1]:22222 " + " ".join(host_key) + "\n")
        config = root / "sshd_config"
        config.write_text(f"Port 22222\nListenAddress 127.0.0.1\nHostKey {root}/host\nPidFile {root}/pid\n"
            "UsePAM yes\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\n"
            "PermitRootLogin no\nAllowUsers gamerhq-staging-bridge\nStrictModes yes\nLogLevel ERROR\n")
        Path("/run/sshd").mkdir(exist_ok=True)
        server = subprocess.Popen(["/usr/sbin/sshd", "-D", "-f", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            base = ["ssh", "-F", "/dev/null", "-T", "-p", "22222", "-i", str(args.key), "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + str(root / "known_hosts"),
                "gamerhq-staging-bridge@127.0.0.1"]
            command = encode({"version": 1, "phase": "prepare", "operation": "inspect"})
            result = None
            for _ in range(20):
                result = run(base + [command])
                if result.returncode == 0:
                    break
                time.sleep(0.2)
            if result.returncode:
                # Only this disposable fixture has blank credential templates.
                # Diagnose locally as root; the real bridge never exposes stderr.
                diagnostic = run(["/opt/gamerhq-staging/deployment-worker/.venv/bin/python", "-c",
                    "import sys; sys.path.insert(0, '/opt/gamerhq-staging/deployment-worker'); "
                    "from tools.skill_staging import main; "
                    "sys.argv=['staging', '--profile', '/etc/gamerhq/staging/profile.json', 'inspect']; main()"])
                print("Disposable fixture local inspection:", diagnostic.stdout, diagnostic.stderr)
                diagnostic = subprocess.run(["/opt/gamerhq-staging/deployment-worker/.venv/bin/python", "-c",
                    "import sys; sys.path.insert(0, '/opt/gamerhq-staging/deployment-worker'); "
                    "from tools.staging_dispatch import main; main()"], input=json.dumps(
                        {"version": 1, "phase": "prepare", "operation": "inspect"}), capture_output=True, text=True, timeout=30)
                print("Disposable fixture dispatcher:", diagnostic.stdout, diagnostic.stderr)
                raise RuntimeError("Restricted SSH inspect failed: " + result.stderr[-1500:])
            assert json.loads(result.stdout)["productionChanged"] is False
            for rejected in ("id", "bash", encode({"version": 1, "phase": "prepare", "operation": "build"}),
                    "gamerhq-staging-v1 e30="):
                assert run(base + [rejected]).returncode != 0
            sudo = run(["sudo", "-n", "-u", "gamerhq-staging-bridge", "sudo", "-n", "/bin/true"])
            assert sudo.returncode != 0
            assert run(["id", "-nG", "gamerhq-staging-bridge"]).stdout.strip() == "gamerhq-staging-bridge"
            profile = json.loads(Path("/etc/gamerhq/staging/profile.json").read_text())
            assert profile["workerEnabled"] is False
            assert not Path(profile["database"]).exists()
            assert not run(["docker", "ps", "-aq", "--filter", "name=^/gamerhq-staging-bot$"]).stdout.strip()
            print("Restricted SSH: inspect PASS; shell/general sudo/unverified build denied; no Gateway or database created.")
        finally:
            server.terminate(); server.wait(timeout=10)


if __name__ == "__main__":
    main()
