"""Owner-run, one-time isolated staging preparation. Default is a read-only plan.

Run a reviewed copy on the approved Linux VPS, never through an unreviewed curl pipe.
Creates only new staging paths and a restricted trigger account; starts no bot.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path("/opt/gamerhq-staging")
CONFIG = Path("/etc/gamerhq/staging")
WORKER = ROOT / "deployment-worker"
ACCOUNT = "gamerhq-staging-bridge"
HOME = Path("/var/lib/gamerhq-staging-bridge")
ENTRY = Path("/usr/local/libexec/gamerhq-staging-entry")
DISPATCH = Path("/usr/local/libexec/gamerhq-staging-dispatch")
SUDOERS = Path("/etc/sudoers.d/gamerhq-staging-bridge")
REPOSITORY = "https://github.com/Duy-Phan96/GamerHQ.git"


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=900, **kwargs).stdout.strip()


def public_key(text):
    # No options, certificates, alternate key types or embedded commands.
    import base64
    parts = text.strip().split()
    if len(parts) not in (2, 3) or parts[0] != "ssh-ed25519" or any("\n" in item or "\r" in item for item in parts):
        raise ValueError("Supply one dedicated Ed25519 public key, without authorized_keys options.")
    data = base64.b64decode(parts[1], validate=True)
    if len(data) != 51 or data[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
        raise ValueError("Invalid Ed25519 public key.")
    return "ssh-ed25519 " + parts[1]


def write_new(path, content, mode):
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(content)
    path.chmod(mode)


def preflight(commit, key):
    if platform.system() != "Linux" or sys.version_info < (3, 12) or os.geteuid() != 0:
        raise PermissionError("Use an approved root session with Linux Python 3.12 or newer.")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use the complete reviewed Host commit.")
    public_key(key)
    for command in ("git", "docker", "sudo", "visudo", "useradd", "getent", "ssh-keygen"):
        if not shutil.which(command):
            raise ValueError("Required administrator tool is unavailable: " + command)
    for path in (ROOT, CONFIG, HOME, ENTRY, DISPATCH, SUDOERS):
        if path.exists() or path.is_symlink():
            raise FileExistsError("Existing staging target must be reviewed; nothing will be overwritten: " + str(path))
    account = subprocess.run(["getent", "passwd", ACCOUNT], capture_output=True, text=True)
    if account.returncode != 2:
        raise ValueError("The dedicated staging account already exists or account lookup failed.")
    # An explicitly default, local context; never inherit an administrator's remote target.
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT", "default") != "default" or run(["docker", "context", "show"]) != "default":
        raise ValueError("An approved local default Docker context is required.")
    run(["docker", "compose", "version"])
    return run(["docker", "info", "--format", "{{.ID}}"])


def prepare(commit, key, *, apply=False, approval=""):
    daemon = preflight(commit, key)
    plan = {"commit": commit, "dockerDaemonId": daemon, "newPaths": [str(p) for p in (ROOT, CONFIG, HOME, ENTRY, DISPATCH, SUDOERS)],
        "workerEnabled": False, "gatewayStarted": False, "productionChanged": False}
    if not apply:
        return plan
    if approval != "PREPARE ISOLATED STAGING":
        raise PermissionError("Explicit infrastructure preparation approval is required.")
    os.umask(0o077)
    ROOT.mkdir(mode=0o700)
    CONFIG.mkdir(mode=0o700, parents=True)
    # Fresh public clone: no production checkout, .env, Git credentials or private DB copy.
    run(["git", "clone", "--no-checkout", REPOSITORY, str(WORKER)])
    run(["git", "-C", str(WORKER), "checkout", "--detach", commit])
    if run(["git", "-C", str(WORKER), "rev-parse", "HEAD"]) != commit:
        raise ValueError("Worker source does not match the reviewed commit.")
    # Git can honor shared-repository permission settings independently of umask.
    # Normalize only this newly created checkout, never an existing Host checkout.
    for path in [WORKER, *WORKER.rglob("*")]:
        if path.is_symlink():
            raise ValueError("Reviewed worker source must not contain symbolic links.")
        os.chown(path, 0, 0)
        path.chmod(path.stat().st_mode & 0o700)
    run([sys.executable, "-m", "venv", str(WORKER / ".venv")])
    python = str(WORKER / ".venv/bin/python")
    run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(WORKER / "requirements.lock")])
    for name in ("data", "backups"):
        path = ROOT / name
        path.mkdir(mode=0o700)
        os.chown(path, 10001, 10001)
        path.chmod(0o2770)
    for name in ("artifacts", "worker"):
        (ROOT / name).mkdir(mode=0o700)
    templates = WORKER / "deploy/staging"
    for source, target in (("compose.json", "compose.json"), ("bot.env.example", "bot.env"),
            ("event-manager.release.example.json", "event-manager.release.json"), ("sdk.release.example.json", "sdk.release.json")):
        write_new(CONFIG / target, (templates / source).read_text(), 0o600)
    profile = json.loads((templates / "profile.example.json").read_text())
    profile.update(hostSourceCommit=commit, dockerDaemonId=daemon, workerEnabled=False)
    write_new(CONFIG / "profile.json", json.dumps(profile, indent=2) + "\n", 0o600)
    write_new(CONFIG / "worker.env", "# New staging credentials only; never copy production values.\nGAMERHQ_SKILL_RELEASE_TOKEN=\nGAMERHQ_STAGING_WEB_API_SECRET=\n", 0o600)
    # Validate the narrow sudo rule before installing it. No arguments are allowed.
    rule = ACCOUNT + ' ALL=(root) NOPASSWD: ' + str(DISPATCH) + ' ""\n'
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sudoers") as output:
        output.write(rule); output.flush()
        run(["visudo", "-cf", output.name])
    if not ENTRY.parent.exists():
        ENTRY.parent.mkdir(mode=0o755, parents=True)
        ENTRY.parent.chmod(0o755)
    write_new(DISPATCH, "#!/bin/sh\nexec " + python + " -I " + str(WORKER / "tools/staging_dispatch.py") + "\n", 0o700)
    # System Python wrapper is self-contained; the SSH account cannot read the protected checkout.
    entry = '''#!/usr/bin/python3 -I
import base64, json, os, subprocess, sys
try:
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if len(command) > 6000 or command.count(" ") != 1:
        raise ValueError()
    prefix, encoded = command.split(" ")
    if prefix != "gamerhq-staging-v1":
        raise ValueError()
    body = base64.b64decode(encoded, validate=True)
    if len(body) > 4096 or not isinstance(json.loads(body), dict):
        raise ValueError()
    result = subprocess.run(["/usr/bin/sudo", "-n", "/usr/local/libexec/gamerhq-staging-dispatch"], input=body,
        env={"PATH": "/usr/bin:/bin"}, timeout=2200)
    sys.exit(result.returncode)
except Exception:
    sys.exit("Unsupported or unavailable staging operation.")
'''
    write_new(ENTRY, entry, 0o755)
    HOME.mkdir(mode=0o755); HOME.chmod(0o755)
    (HOME / ".ssh").mkdir(mode=0o755)
    (HOME / ".ssh").chmod(0o755)
    write_new(HOME / ".ssh/authorized_keys", 'restrict,command="' + str(ENTRY) + '" ' + public_key(key) + "\n", 0o644)
    run(["useradd", "--system", "--no-create-home", "--home-dir", str(HOME), "--shell", "/bin/sh", ACCOUNT])
    write_new(SUDOERS, rule, 0o440)
    write_new(ROOT / "preparation.json", json.dumps(plan, indent=2) + "\n", 0o600)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--public-key-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args()
    print(json.dumps(prepare(args.commit, args.public_key_file.read_text(), apply=args.apply, approval=args.approval), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Staging preparation failed. Review new staging paths locally; no existing target was overwritten and no bot was started. No credential values were logged.") from None
