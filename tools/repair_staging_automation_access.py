"""Restore reviewed staging code-directory access under umask 077; Python 3.9.

No worker upgrade, SSH policy change, credential access, database migration,
container operation or activation. Owner-only fixed paths and immutable source.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

ROOT = Path('/opt/gamerhq-staging')
CONTROL = ROOT / 'automation-controller'
ENROLLED = 'a1b50573850921e478a2f838e72868853bde6dd7'
LEGACY = '92db47042d58055cb0debdfd83f11f8508e19809'


def protected(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 1048576:
        raise ValueError('PROTECTED_SOURCE_REQUIRED')
    return path.read_bytes()


def restore_directories(directories, gid):
    before = []
    for path in directories:
        info = path.lstat(); mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != gid or mode not in (0o700, 0o750):
            raise ValueError('EXISTING_DIRECTORY_POLICY_REQUIRES_OWNER_REVIEW')
        before.append(mode)
    for path in directories: path.chmod(0o750)
    return before


def repair(commit, approval):
    import pwd
    if os.geteuid() != 0 or approval != 'RESTORE REVIEWED STAGING CODE ACCESS': raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch('[a-f0-9]{40}', commit): raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    for path in (ROOT, Path('/etc/gamerhq-staging')):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022: raise ValueError('PROTECTED_PARENT_REQUIRED')
    policy = json.loads(protected(Path('/etc/gamerhq-staging/automation-policy.json')))
    if policy['controllerCommit'] != ENROLLED or policy['legacyWorkerCommit'] != LEGACY: raise ValueError('REVIEWED_ENROLLMENT_REQUIRED')
    state = ROOT / 'worker/automation-state.json'
    if state.exists():
        info = state.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077 or info.st_size > 131072: raise ValueError('PRIVATE_STATE_REQUIRED')
        if json.loads(state.read_text()).get('enabled'): raise ValueError('INACTIVE_ENROLLMENT_REQUIRED')
    env = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    def run(args): return subprocess.run(args, env=env, capture_output=True, check=True, timeout=180).stdout
    with tempfile.TemporaryDirectory(prefix='gamerhq-reviewed-access-') as temp:
        def git(*args): return run(['git', '-C', temp, *args])
        git('init', '--quiet'); git('remote', 'add', 'origin', 'https://github.com/Duy-Phan96/GamerHQ.git')
        git('fetch', '--quiet', '--depth=1', 'origin', commit)
        if git('show', commit + ':tools/repair_staging_automation_access.py') != Path(__file__).read_bytes():
            raise ValueError('VERIFIED_INSTALLER_SOURCE_MISMATCH')
        git('fetch', '--quiet', '--depth=1', 'origin', ENROLLED)
        for name in ('__init__.py', 'policy.py', 'oidc.py', 'backend.py', 'bridge.py'):
            if protected(CONTROL / 'staging_automation' / name) != git('show', ENROLLED + ':tools/staging_automation/' + name):
                raise ValueError('INSTALLED_SOURCE_MISMATCH')
    launch = ("import sys\nsys.path[:0]=['/opt/gamerhq-staging/automation-controller','/opt/gamerhq-staging/deployment-worker']\n"
        "from staging_automation.bridge import entry\nentry()\n").encode()
    if protected(CONTROL / 'entry.py') != launch: raise ValueError('INSTALLED_LAUNCHER_MISMATCH')
    directories = (CONTROL, CONTROL / 'staging_automation')
    before = restore_directories(directories, account.pw_gid)
    try:
        envelope = {'request': {'version': 1, 'phase': 'automation', 'operation': 'status'}, 'configuration': None}
        result = subprocess.run(['runuser', '-u', account.pw_name, '--', 'env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin',
            'HOME=' + account.pw_dir, 'XDG_RUNTIME_DIR=/run/user/' + str(account.pw_uid),
            'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(account.pw_uid) + '/bus',
            'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet',
            str(ROOT / 'deployment-worker/.venv/bin/python'), '-I', '-B', str(CONTROL / 'entry.py')],
            input=json.dumps(envelope), capture_output=True, text=True, timeout=90)
        if result.returncode or len(result.stdout) > 32768: raise ValueError('SANDBOX_STATUS_PROBE_FAILED')
        receipt = json.loads(result.stdout)
        if receipt.get('result') != 'PASS' or receipt.get('operation') != 'status' or receipt.get('state') != 'NOT_ACTIVATED' or receipt.get('controllerCommit') != ENROLLED or receipt.get('publicKeySha256') != policy['publicKeySha256'] or receipt.get('productionChanged') is not False:
            raise ValueError('SANDBOX_STATUS_PROBE_FAILED')
    except Exception:
        for path, mode in zip(directories, before): path.chmod(mode)
        raise ValueError('ACCESS_REPAIR_ROLLED_BACK') from None
    return {'operation': 'repair-staging-automation-access', 'state': 'APPLIED', 'repairCommit': commit,
        'controllerCommit': ENROLLED, 'publicKeySha256': policy['publicKeySha256'],
        'previousDirectoryModes': [oct(mode) for mode in before], 'directoryModes': ['0o750', '0o750'],
        'restrictedSandboxStatus': 'NOT_ACTIVATED', 'automationEnabled': False,
        'sshPolicyChanged': False, 'credentialsChanged': False, 'databaseChanged': False,
        'containersChanged': False, 'workerSourceChanged': False, 'productionChanged': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try: print(json.dumps(repair(args.commit, args.approval), indent=2))
    except Exception as error:
        code = str(error)
        if not re.fullmatch('[A-Z_]{1,80}', code): code = 'STAGING_ACCESS_REPAIR_REJECTED'
        raise SystemExit(json.dumps({'repairError': code, 'productionChanged': False})) from None
