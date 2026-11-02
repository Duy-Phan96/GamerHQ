"""Render a self-contained operator command from immutable Git bytes and a public key."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess


def render(commit, script, public_key):
    from tools.prepare_staging_access import public_key as validate_key
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("An exact immutable commit is required.")
    key = validate_key(public_key)
    digest = hashlib.sha256(script).hexdigest()
    url = "https://raw.githubusercontent.com/Duy-Phan96/GamerHQ/" + commit + "/tools/prepare_staging_access.py"
    # All embedded values are validated public metadata. No private key, token,
    # existing remote path, curl pipe or moving branch is used.
    return f'''python3 - <<'GAMERHQ_VERIFIED_BOOTSTRAP'
import hashlib, os, pathlib, subprocess, sys, tempfile, urllib.request
if not hasattr(os, 'geteuid') or os.geteuid() != 0:
    raise SystemExit('Run this command in your existing root SSH session.')
class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Unexpected bootstrap download redirect; nothing executed.')
url = {url!r}
expected = {digest!r}
with urllib.request.build_opener(NoRedirects()).open(url, timeout=60) as response:
    body = response.read(262145)
if len(body) > 262144 or hashlib.sha256(body).hexdigest() != expected:
    raise SystemExit('Bootstrap SHA-256 verification failed; nothing executed.')
with tempfile.TemporaryDirectory(prefix='gamerhq-verified-bootstrap-') as directory:
    installer = pathlib.Path(directory) / 'prepare_staging_access.py'
    descriptor = os.open(installer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(body)
    print('Verified immutable GamerHQ bootstrap SHA-256: ' + expected, flush=True)
    subprocess.run([sys.executable, str(installer), '--commit', {commit!r},
        '--ssh-public-key', {key!r}, '--apply', '--install-prerequisites',
        '--approval', 'PREPARE ISOLATED STAGING'], check=True)
GAMERHQ_VERIFIED_BOOTSTRAP
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--public-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise ValueError("Use an immutable commit.")
    script = subprocess.check_output(["git", "show", args.commit + ":tools/prepare_staging_access.py"])
    command = render(args.commit, script, args.public_key_file.read_text())
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        output.write(command)
    print("Rendered verified bootstrap command; no private credentials are included.")


if __name__ == "__main__":
    main()
