"""Python 3.9 owner-only, versioned extension of the enrolled staging controller.

No credential reads, SSH changes, worker replacement, database or Docker actions.
The active automation state is preserved. Host apply still needs signed GitHub
workload identity and an actual protected deployment-security owner review.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
OLD = 'a1b50573850921e478a2f838e72868853bde6dd7'
LEGACY = '92db47042d58055cb0debdfd83f11f8508e19809'
HOST = '6a2c74a234d693abe70f5f69dfbb41dbce38bc75'
ARCHIVE = 'ca3c9acbf7087d59881f130c903bf13b2c47fed1f2157933f6ca08e07df2fc97'
MODULES = ('__init__.py', 'policy.py', 'oidc.py', 'backend.py', 'bridge.py', 'host_promotion.py')


def trusted(path, uid=0, private=False, limit=262144):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or info.st_mode & (0o077 if private else 0o022) or info.st_size > limit:
            raise ValueError('PROTECTED_FILE_REQUIRED')
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise ValueError('PROTECTED_FILE_REQUIRED')
        return data
    finally:
        os.close(fd)


def directory(path, gid=None):
    if not path.exists():
        if gid is None:
            raise ValueError('PROTECTED_PARENT_REQUIRED')
        path.mkdir(mode=0o750)
        os.chown(path, 0, gid)
        path.chmod(0o750)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('PROTECTED_PARENT_REQUIRED')


def atomic(path, body, gid):
    directory(path.parent)
    label = None
    if path.exists() or path.is_symlink():
        trusted(path)
        try: label = os.getxattr(path, 'security.selinux', follow_symlinks=False)
        except OSError: pass
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.host-extension-', delete=False) as output:
        temporary = Path(output.name)
        os.fchown(output.fileno(), 0, gid); os.fchmod(output.fileno(), 0o640)
        if label: os.setxattr(output.fileno(), 'security.selinux', label)
        output.write(body); output.flush(); os.fsync(output.fileno())
    try:
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if temporary.exists(): temporary.unlink()


def extension(commit, approval):
    import pwd
    if os.geteuid() != 0 or approval != 'INSTALL REVIEWED STAGING HOST PROMOTION':
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    for parent in (ROOT, CONTROL, ROOT / 'automation-controller'):
        directory(parent)
    policy_path = CONTROL / 'automation-policy.json'
    original_policy = trusted(policy_path)
    policy = json.loads(original_policy)
    if policy.get('controllerCommit') not in (OLD, commit) or policy.get('legacyWorkerCommit') != LEGACY or policy.get('repository') != 'Duy-Phan96/GamerHQ':
        raise ValueError('REVIEWED_ENROLLMENT_REQUIRED')
    entry = ROOT / 'automation-controller/entry.py'
    old_entry = trusted(entry)
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    with tempfile.TemporaryDirectory(prefix='gamerhq-host-extension-') as temp:
        def git(*args):
            result = subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-C', temp, *args], env=env, capture_output=True, check=True, timeout=180)
            if len(result.stdout) > 262144:
                raise ValueError('SOURCE_LIMIT')
            return result.stdout
        git('init', '--quiet'); git('remote', 'add', 'origin', 'https://github.com/Duy-Phan96/GamerHQ.git')
        for source in (commit, OLD): git('fetch', '--quiet', '--depth=1', 'origin', source)
        if git('show', commit + ':tools/extend_staging_host_promotion.py') != Path(__file__).read_bytes():
            raise ValueError('VERIFIED_INSTALLER_SOURCE_MISMATCH')
        blobs = {name: git('show', commit + ':tools/staging_automation/' + name) for name in MODULES}
        # Same canonical state machine, not a second deployment implementation.
        blobs['deployment.py'] = git('show', commit + ':hosts/gamerhq/skill_update_controller.py')
        for name in ('staging-automation.yml', 'staging-automation-activate.yml'):
            body = git('show', commit + ':.github/workflows/' + name)
            key = 'routineWorkflowSha256' if name == 'staging-automation.yml' else 'securityWorkflowSha256'
            if hashlib.sha256(body).hexdigest() != policy[key]:
                raise ValueError('EXISTING_WORKFLOW_POLICY_CHANGED')
        workflow = git('show', commit + ':.github/workflows/staging-host-promotion.yml')
        if policy['controllerCommit'] == OLD:
            for name in ('__init__.py', 'policy.py', 'oidc.py', 'backend.py', 'bridge.py'):
                if trusted(ROOT / 'automation-controller/staging_automation' / name) != git('show', OLD + ':tools/staging_automation/' + name):
                    raise ValueError('INSTALLED_SOURCE_MISMATCH')
            expected_entry = ("import sys\nsys.path[:0]=['/opt/gamerhq-staging/automation-controller','/opt/gamerhq-staging/deployment-worker']\n"
                "from staging_automation.bridge import entry\nentry()\n").encode()
            if old_entry != expected_entry:
                raise ValueError('INSTALLED_ENTRY_MISMATCH')
    revisions = ROOT / 'automation-controller/revisions'
    directory(revisions, gid)
    destination = revisions / commit
    directory(destination, gid); directory(destination / 'staging_automation', gid)
    for name, body in blobs.items():
        target = destination / 'staging_automation' / name
        if target.exists() and trusted(target) != body:
            raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
        atomic(target, body, gid)
    # Existing sandbox/SSH launcher still invokes the same entry file. Only its
    # root-owned import target and a narrowly scoped root-owned policy change.
    launch = ("import sys\nsys.path[:0]=[" + repr(str(destination)) + ",'/opt/gamerhq-staging/deployment-worker']\n"
        "from staging_automation.bridge import entry\nentry()\n").encode()
    new_policy = {**policy, 'controllerCommit': commit,
        'hostPromotion': {'commit': HOST, 'archiveSha256': ARCHIVE, 'workflowSha256': hashlib.sha256(workflow).hexdigest()}}
    lock_path = ROOT / 'worker/operation.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        # Existing user services can create a group-writable lock under umask
        # 0002. It contains no code/data; retain that mode, requiring the exact
        # dedicated UID/GID, one inode, no symlink and no world-write access.
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid or info.st_nlink != 1 or info.st_mode & 0o002:
            raise ValueError('EXISTING_OPERATION_LOCK_REQUIRED')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Re-read under the same lock used by every existing deployment operation.
        if trusted(policy_path) != original_policy or trusted(entry) != old_entry:
            raise ValueError('CONCURRENT_POLICY_CHANGE_REJECTED')
        state = json.loads(trusted(ROOT / 'worker/automation-state.json', uid, private=True))
        if state.get('recoveryRequired'):
            raise ValueError('REVIEWED_RECOVERY_REQUIRED')
        if policy['controllerCommit'] == commit and (policy != new_policy or old_entry != launch):
            raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
        try:
            atomic(policy_path, (json.dumps(new_policy, indent=2) + '\n').encode(), gid)
            atomic(entry, launch, gid)
            if Path('/sys/fs/selinux/enforce').exists():
                subprocess.run(['/usr/sbin/restorecon', '-RF', str(revisions), str(entry), str(policy_path)], check=True, capture_output=True, timeout=30)
        except Exception:
            atomic(entry, old_entry, gid); atomic(policy_path, original_policy, gid)
            raise
    finally:
        os.close(fd)
    return {'operation': 'extend-staging-host-promotion', 'state': 'INSTALLED', 'controllerCommit': commit,
        'approvedHostCommit': HOST, 'hostPromotionRequiresProtectedOwnerReview': True,
        'automationEnabled': bool(state.get('enabled')), 'gatewayStarted': False, 'productionChanged': False,
        'workerSourceChanged': False, 'sshPolicyChanged': False, 'credentialsChanged': False,
        'databaseChanged': False, 'containersChanged': False, 'deploymentGatesChanged': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try: print(json.dumps(extension(args.commit, args.approval), indent=2))
    except Exception as error:
        code = str(error) if re.fullmatch('[A-Z_]{1,80}', str(error)) else 'HOST_EXTENSION_REJECTED'
        raise SystemExit(json.dumps({'extensionError': code, 'productionChanged': False})) from None
