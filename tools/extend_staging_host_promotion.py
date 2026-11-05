"""Python 3.9 owner-only replacement of the existing staging promotion pin.

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
OLD = '135bbf604926d6b1794503d17821ad00f1c09555'
LEGACY = '92db47042d58055cb0debdfd83f11f8508e19809'
PREVIOUS_HOST = '6a2c74a234d693abe70f5f69dfbb41dbce38bc75'
PREVIOUS_PIN = {'commit': PREVIOUS_HOST,
    'archiveSha256': 'ca3c9acbf7087d59881f130c903bf13b2c47fed1f2157933f6ca08e07df2fc97',
    'workflowSha256': '25314997e8d6e9f4b6affa64690ecfab8044be3c87e9c27d1555d827372a1c79'}
HOST = '17858f47480e914956dd241b9f37a328b4c6ac2b'
ARCHIVE = '77504247b70dee58c6f7d982eb4d65e50218a7fccb3581cd7e5723799fe888d9'
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
    if os.geteuid() != 0 or approval != 'REPLACE REVIEWED STAGING HOST PIN':
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
    revisions = ROOT / 'automation-controller/revisions'
    previous = revisions / OLD
    destination = revisions / commit
    for parent in (revisions, previous, previous / 'staging_automation'):
        directory(parent)

    def launcher(path):
        return ("import sys\nsys.path[:0]=[" + repr(str(path)) + ",'/opt/gamerhq-staging/deployment-worker']\n"
                "from staging_automation.bridge import entry\nentry()\n").encode()

    previous_launch, launch = launcher(previous), launcher(destination)
    if policy['controllerCommit'] == OLD and (policy.get('hostPromotion') != PREVIOUS_PIN or old_entry != previous_launch):
        raise ValueError('INSTALLED_PIN_MISMATCH')
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
        for name in MODULES:
            if trusted(previous / 'staging_automation' / name) != git('show', OLD + ':tools/staging_automation/' + name):
                raise ValueError('INSTALLED_SOURCE_MISMATCH')
        if trusted(previous / 'staging_automation/deployment.py') != git('show', OLD + ':hosts/gamerhq/skill_update_controller.py'):
            raise ValueError('INSTALLED_SOURCE_MISMATCH')
    directory(destination, gid); directory(destination / 'staging_automation', gid)
    for name, body in blobs.items():
        target = destination / 'staging_automation' / name
        if target.exists() and trusted(target) != body:
            raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
        atomic(target, body, gid)
    # Existing sandbox/SSH launcher still invokes the same entry file. Only its
    # root-owned import target and a narrowly scoped root-owned policy change.
    new_policy = {**policy, 'controllerCommit': commit,
        'hostPromotion': {'commit': HOST, 'archiveSha256': ARCHIVE, 'workflowSha256': hashlib.sha256(workflow).hexdigest()}}
    lock_path = ROOT / 'worker/operation.lock'
    parent_info = lock_path.parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != uid or parent_info.st_gid != gid or parent_info.st_mode & 0o002:
        raise ValueError('PRIVATE_WORKER_DIRECTORY_REQUIRED')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        # Some existing systemd user managers create 0666 locks. The enclosing
        # private staging directory protects this non-data inode; acquire the
        # exact owned single-link inode, then tighten it without path-based chmod.
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid or info.st_nlink != 1:
            raise ValueError('EXISTING_OPERATION_LOCK_REQUIRED')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fchmod(fd, 0o600)
        # Re-read under the same lock used by every existing deployment operation.
        if trusted(policy_path) != original_policy or trusted(entry) != old_entry:
            raise ValueError('CONCURRENT_POLICY_CHANGE_REJECTED')
        state = json.loads(trusted(ROOT / 'worker/automation-state.json', uid, private=True))
        if state.get('recoveryRequired'):
            raise ValueError('REVIEWED_RECOVERY_REQUIRED')
        if not state.get('enabled') or not state.get('bootstrapVerified') or not re.fullmatch('sha256:[0-9a-f]{64}', state.get('approvedImage', '')):
            raise ValueError('ACTIVE_STAGING_REQUIRED')
        # A crash after policy replace but before entry replace is recoverable
        # only with the same exact root-owned old/new entry and immutable policy.
        if policy['controllerCommit'] == commit and (policy != new_policy or old_entry not in (launch, previous_launch)):
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
    import syslog
    syslog.openlog('gamerhq-staging-security', syslog.LOG_PID, syslog.LOG_AUTHPRIV)
    syslog.syslog(syslog.LOG_NOTICE, json.dumps({'operation': 'replace-staging-host-promotion-pin', 'controllerCommit': commit,
        'approvedHostCommit': HOST, 'state': 'PIN_INSTALLED', 'productionChanged': False}))
    return {'operation': 'replace-staging-host-promotion-pin', 'state': 'PIN_INSTALLED', 'controllerCommit': commit,
        'previousHostCommit': PREVIOUS_HOST, 'approvedHostCommit': HOST, 'hostPromotionRequiresProtectedOwnerReview': True,
        'automationEnabled': bool(state.get('enabled')), 'gatewayStarted': False, 'productionChanged': False,
        'workerSourceChanged': False, 'sshPolicyChanged': False, 'credentialsChanged': False,
        'databaseChanged': False, 'containersChanged': False, 'deploymentGatesChanged': False, 'operationLockMode': '0600'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try: print(json.dumps(extension(args.commit, args.approval), indent=2))
    except Exception as error:
        code = str(error) if re.fullmatch('[A-Z_]{1,80}', str(error)) else 'HOST_EXTENSION_REJECTED'
        raise SystemExit(json.dumps({'extensionError': code, 'productionChanged': False})) from None
