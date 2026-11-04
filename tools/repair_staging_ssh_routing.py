"""Owner-only, immutable repair of enrolled staging SSH routing (Python 3.9).

Keeps ForceCommand, both restricted identities, the installed worker/controller,
sandbox, data, credentials and running containers. No GitHub gate is changed.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

CONFIG = Path('/etc/ssh/sshd_config.d/90-gamerhq-staging.conf')
ROUTER = Path('/usr/local/libexec/gamerhq-staging-ssh-router')
LEGACY = '/usr/local/libexec/gamerhq-staging-entry'
ORIGINAL = ('Match User gamerhq-staging-bridge\n    AuthenticationMethods publickey\n    PasswordAuthentication no\n'
    '    KbdInteractiveAuthentication no\n    DisableForwarding yes\n    PermitTTY no\n    PermitTunnel no\n'
    '    PermitUserRC no\n    ForceCommand ' + LEGACY + '\nMatch all\n').encode()
REPAIRED = ORIGINAL.replace(('    ForceCommand ' + LEGACY).encode(), ('    ExposeAuthInfo yes\n    ForceCommand ' + ROUTER.as_posix()).encode())


def trusted(path, uid=0):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022 or info.st_size > 1048576:
        raise ValueError('PROTECTED_FILE_REQUIRED')
    return path.read_bytes()


def atomic(path, body, mode):
    # Same-directory replacement preserves existing SELinux context. New files
    # receive restorecon below; SELinux and system Python are never changed.
    if path.is_symlink() or path.parent.is_symlink(): raise ValueError('REDIRECTION_REJECTED')
    label = None
    if path.exists():
        import errno
        try: label = os.getxattr(path, 'security.selinux', follow_symlinks=False)
        except OSError as error:
            if error.errno not in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP): raise
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name); os.fchown(f.fileno(), 0, 0); os.fchmod(f.fileno(), mode)
        if label: os.setxattr(f.fileno(), 'security.selinux', label)
        f.write(body); f.flush(); os.fsync(f.fileno())
    try: temp.replace(path)
    finally:
        if temp.exists(): temp.unlink()


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60,
        env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent'}).stdout


def effective(user):
    return run(['/usr/sbin/sshd', '-T', '-C', 'user=' + user + ',host=localhost,addr=127.0.0.1'])


def verify_effective(value, repaired):
    lines = value.splitlines()
    expected = ['authenticationmethods publickey', 'passwordauthentication no', 'kbdinteractiveauthentication no',
        'disableforwarding yes', 'permittty no', 'permittunnel no', 'permituserrc no', 'permituserenvironment no',
        'forcecommand ' + (ROUTER.as_posix() if repaired else LEGACY)]
    if repaired: expected.append('exposeauthinfo yes')
    if any(x not in lines for x in expected): raise ValueError('EFFECTIVE_SSH_POLICY_REJECTED')
    import fnmatch
    for line in lines:
        if line.startswith('acceptenv ') and any(fnmatch.fnmatchcase('SSH_USER_AUTH', p) for p in line.split()[1:]):
            raise ValueError('AUTH_INFO_ENVIRONMENT_SPOOFING_REJECTED')
        if line.startswith('setenv ') and any(p.startswith('SSH_USER_AUTH=') for p in line.split()[1:]):
            raise ValueError('AUTH_INFO_ENVIRONMENT_SPOOFING_REJECTED')


def repair(commit, approval):
    import pwd
    if os.geteuid() != 0 or approval != 'REPAIR AUTHENTICATED STAGING SSH ROUTING': raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch('[a-f0-9]{40}', commit): raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    policy = json.loads(trusted(Path('/etc/gamerhq-staging/automation-policy.json')))
    if policy['controllerCommit'] != 'a1b50573850921e478a2f838e72868853bde6dd7' or policy['legacyWorkerCommit'] != '92db47042d58055cb0debdfd83f11f8508e19809':
        raise ValueError('REVIEWED_ENROLLMENT_REQUIRED')
    state = Path('/opt/gamerhq-staging/worker/automation-state.json')
    if state.exists() and json.loads(trusted(state, account.pw_uid)).get('enabled'): raise ValueError('INACTIVE_ENROLLMENT_REQUIRED')
    for path in (CONFIG.parent, ROUTER.parent, Path('/etc/gamerhq-staging'), Path(account.pw_dir) / '.ssh'):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022: raise ValueError('PROTECTED_PARENT_REQUIRED')
    before = trusted(CONFIG)
    if before not in (ORIGINAL, REPAIRED): raise ValueError('EXISTING_SSH_POLICY_REQUIRES_REVIEW')
    verify_effective(effective(account.pw_name), before == REPAIRED)
    other_policy = effective('root'); run(['/usr/sbin/sshd', '-t'])
    with tempfile.TemporaryDirectory(prefix='gamerhq-ssh-routing-') as temp:
        source = Path(temp)
        def git(*args): return run(['git', '-C', str(source), *args])
        git('init', '--quiet'); git('remote', 'add', 'origin', 'https://github.com/Duy-Phan96/GamerHQ.git')
        git('fetch', '--quiet', '--depth=1', 'origin', commit)
        installer = git('show', commit + ':tools/repair_staging_ssh_routing.py').encode()
        if installer != Path(__file__).read_bytes(): raise ValueError('VERIFIED_INSTALLER_SOURCE_MISMATCH')
        body = git('show', commit + ':tools/staging_ssh_router.py').encode()
        # Import only the checksum-verified immutable router, before installation.
        scope = {'__name__': 'verified_router'}; exec(compile(body, 'verified_router', 'exec'), scope)
        scope['routes'](trusted(Path(account.pw_dir) / '.ssh/authorized_keys').decode(), policy['publicKeySha256'])
        # Existing entries are not replaced; require their installed reviewed form.
        for file, variable, entry in [('tools/prepare_staging_access.py', 'entry', LEGACY),
                ('tools/enroll_staging_automation.py', 'entry', '/usr/local/libexec/gamerhq-staging-automation-entry')]:
            pinned = policy['legacyWorkerCommit'] if entry == LEGACY else policy['controllerCommit']
            git('fetch', '--quiet', '--depth=1', 'origin', pinned)
            tree = ast.parse(git('show', pinned + ':' + file))
            literals = [n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in n.targets)
                and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str) and n.value.value.startswith('#!/usr/bin/python3 -I')]
            if len(literals) != 1 or trusted(Path(entry)) != literals[0].encode(): raise ValueError('INSTALLED_ENTRY_SOURCE_MISMATCH')
    backup = CONFIG.with_name(CONFIG.name + '.pre-authenticated-routing')
    if backup.exists():
        if trusted(backup) != ORIGINAL: raise ValueError('ROLLBACK_RECORD_REJECTED')
    else: atomic(backup, ORIGINAL, 0o600)
    if ROUTER.exists() and trusted(ROUTER) != body: raise ValueError('EXISTING_ROUTER_REQUIRES_REVIEW')
    atomic(ROUTER, body, 0o755)
    if Path('/sys/fs/selinux/enforce').exists():
        restorecon = shutil.which('restorecon')
        if not restorecon: raise ValueError('SELINUX_CONTEXT_CAPABILITY_REQUIRED')
        run([restorecon, '-F', str(ROUTER)])
    service = next((s for s in ('sshd.service', 'ssh.service') if subprocess.run(['systemctl', 'is-active', '--quiet', s]).returncode == 0), None)
    if not service: raise ValueError('ACTIVE_SSH_SERVICE_REQUIRED')
    try:
        atomic(CONFIG, REPAIRED, 0o644)
        run(['/usr/sbin/sshd', '-t']); verify_effective(effective(account.pw_name), True)
        if effective('root') != other_policy: raise ValueError('OTHER_ACCOUNT_POLICY_CHANGED')
        run(['systemctl', 'reload', service])
    except Exception:
        atomic(CONFIG, before, 0o644); run(['/usr/sbin/sshd', '-t']); run(['systemctl', 'reload', service])
        raise ValueError('SSH_ROUTING_REPAIR_ROLLED_BACK') from None
    return {'operation': 'repair-staging-ssh-routing', 'state': 'APPLIED', 'repairCommit': commit,
        'controllerCommit': policy['controllerCommit'], 'publicKeySha256': policy['publicKeySha256'],
        'forceCommandRetained': True, 'authenticatedKeyRouting': True, 'automationEnabled': False,
        'githubEnrollmentFlagChanged': False, 'productionChanged': False, 'credentialsChanged': False,
        'workerChanged': False, 'databaseChanged': False, 'containerChanged': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try: print(json.dumps(repair(args.commit, args.approval), indent=2))
    except Exception as error:
        code = str(error)
        if not re.fullmatch('[A-Z_]{1,80}', code): code = 'SSH_ROUTING_REPAIR_REJECTED'
        raise SystemExit(json.dumps({'repairError': code, 'productionChanged': False})) from None
