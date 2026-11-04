"""Owner-only additive enrollment. Preserves the installed worker and pending bootstrap.

Python 3.9 compatible. No dependency installation, service restart, production or
existing credential/SSH entry changes. The new capability is inactive until OIDC
owner activation and verified first bootstrap.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import shutil
import errno

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
LEGACY = ROOT / 'deployment-worker'
DEST = ROOT / 'automation-controller'
ENTRY = Path('/usr/local/libexec/gamerhq-staging-automation-entry')
POLICY = CONTROL / 'automation-policy.json'
FILES = ('__init__.py', 'policy.py', 'oidc.py', 'backend.py', 'bridge.py')
REPO = 'https://github.com/Duy-Phan96/GamerHQ.git'


def trusted(path, uid=0):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022 or info.st_size > 1048576: raise ValueError('PROTECTED_FILE_REQUIRED')
    return path.read_bytes()


def atomic(path, body, gid, mode):
    if path.is_symlink() or path.parent.is_symlink(): raise ValueError('REDIRECTION_REJECTED')
    label = None
    if path.exists():
        try: label = os.getxattr(path, 'security.selinux', follow_symlinks=False)
        except OSError as error:
            if error.errno not in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise ValueError('EXISTING_SECURITY_LABEL_UNAVAILABLE') from None
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name); os.fchown(f.fileno(), 0, gid); os.fchmod(f.fileno(), mode)
        if label: os.setxattr(f.fileno(), 'security.selinux', label)
        f.write(body); f.flush(); os.fsync(f.fileno())
    try: tmp.replace(path)
    finally:
        if tmp.exists(): tmp.unlink()


def enroll(commit, public_key, approval):
    if os.geteuid() != 0 or approval != 'ENROLL RESTRICTED STAGING AUTOMATION': raise PermissionError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch('[0-9a-f]{40}', commit): raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    fields = public_key.split()
    if len(fields) != 2 or fields[0] != 'ssh-ed25519': raise ValueError('DEDICATED_PUBLIC_KEY_REQUIRED')
    import base64
    raw = base64.b64decode(fields[1], validate=True)
    if len(raw) != 51 or raw[:19] != b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20': raise ValueError('DEDICATED_PUBLIC_KEY_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge'); uid, gid = account.pw_uid, account.pw_gid
    profile = json.loads(trusted(CONTROL / 'settings/profile.json', uid)); legacy_policy = json.loads(trusted(CONTROL / 'policy.json'))
    if profile.get('deploymentUid') != uid or profile.get('hostSourceCommit') != legacy_policy.get('hostSourceCommit') or profile.get('root') != str(ROOT) or profile.get('configurationRoot') != str(CONTROL): raise ValueError('EXISTING_IDENTITY_REQUIRED')
    if profile['hostSourceCommit'] != '92db47042d58055cb0debdfd83f11f8508e19809': raise ValueError('REVIEWED_LEGACY_COMPATIBILITY_REQUIRED')
    capability = subprocess.run(['runuser', '-u', account.pw_name, '--', 'env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin',
        'HOME=' + account.pw_dir, 'XDG_RUNTIME_DIR=/run/user/' + str(uid), 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(uid) + '/bus',
        'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet', str(LEGACY / '.venv/bin/python'), '-I', '-B', '-c',
        "import sys;from cryptography.hazmat.primitives.asymmetric import rsa;assert sys.version_info[:2]>=(3,12);print('CAPABLE')"], capture_output=True, text=True, timeout=60)
    if capability.returncode or capability.stdout.strip() != 'CAPABLE': raise ValueError('EXISTING_SANDBOX_VERIFIER_CAPABILITY_REQUIRED')
    state_path = ROOT / 'worker/automation-state.json'
    if state_path.exists() and json.loads(trusted(state_path, uid)).get('enabled'): raise ValueError('ACTIVE_POLICY_ALREADY_ENROLLED')
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    def git(folder, *args):
        result = subprocess.run(['git', '-C', str(folder), *args], env=env, capture_output=True, check=True, timeout=180)
        return result.stdout
    if git(LEGACY, 'rev-parse', 'HEAD').decode().strip() != legacy_policy['hostSourceCommit'] or git(LEGACY, 'status', '--porcelain').strip(): raise ValueError('CLEAN_INSTALLED_SOURCE_REQUIRED')
    keys = Path(account.pw_dir) / '.ssh/authorized_keys'
    ssh_info = keys.parent.lstat()
    if not stat.S_ISDIR(ssh_info.st_mode) or ssh_info.st_uid != 0 or ssh_info.st_mode & 0o022: raise ValueError('PROTECTED_SSH_DIRECTORY_REQUIRED')
    original_keys = trusted(keys)
    if any(fields[1].encode() in line for line in original_keys.splitlines() if not line.startswith(b'restrict,command="' + str(ENTRY).encode() + b'" ')): raise ValueError('DISTINCT_AUTOMATION_KEY_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='gamerhq-automation-source-') as temp:
        source = Path(temp)
        git(source, 'init', '--quiet'); git(source, 'remote', 'add', 'origin', REPO)
        git(source, 'fetch', '--quiet', '--depth=1', 'origin', commit)
        script = git(source, 'show', commit + ':tools/enroll_staging_automation.py')
        if script != Path(__file__).read_bytes(): raise ValueError('VERIFIED_INSTALLER_SOURCE_MISMATCH')
        blobs = {name: git(source, 'show', commit + ':tools/staging_automation/' + name) for name in FILES}
        workflows = {name: git(source, 'show', commit + ':.github/workflows/' + name) for name in ('staging-automation.yml', 'staging-automation-activate.yml')}
    policy = {'schemaVersion': 1, 'repository': 'Duy-Phan96/GamerHQ', 'repositoryId': '1375800851', 'ownerId': '272528289',
        'controllerCommit': commit, 'legacyWorkerCommit': legacy_policy['hostSourceCommit'], 'bootstrapRun': '41850674216',
        'bootstrapCommit': '6a444d2051568335a7abd13d872edddde16aae9c', 'publicKeySha256': hashlib.sha256(raw).hexdigest(),
        'routineWorkflowSha256': hashlib.sha256(workflows['staging-automation.yml']).hexdigest(),
        'securityWorkflowSha256': hashlib.sha256(workflows['staging-automation-activate.yml']).hexdigest(),
        'limits': {'mutationsPerHour': 12, 'buildsPerHour': 3, 'restartsPerHour': 2, 'restartCooldown': 120}}
    # Retry only the same reviewed enrollment; never silently rotate policy or key.
    if POLICY.exists() and json.loads(trusted(POLICY)) != policy: raise ValueError('EXISTING_POLICY_REQUIRES_OWNER_REVIEW')
    for directory in (DEST, DEST / 'staging_automation'):
        if directory.exists():
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022: raise ValueError('PROTECTED_DIRECTORY_REQUIRED')
        else: directory.mkdir(mode=0o750); os.chown(directory, 0, gid)
    for name, body in blobs.items(): atomic(DEST / 'staging_automation' / name, body, gid, 0o640)
    launch = ("import sys\nsys.path[:0]=['/opt/gamerhq-staging/automation-controller','/opt/gamerhq-staging/deployment-worker']\n"
        "from staging_automation.bridge import entry\nentry()\n")
    atomic(DEST / 'entry.py', launch.encode(), gid, 0o640)
    entry = '''#!/usr/bin/python3 -I
import base64,json,os,pwd,subprocess,sys,uuid
try:
 account=pwd.getpwnam('gamerhq-staging-bridge');uid=account.pw_uid
 if os.geteuid()!=uid or uid==0:raise ValueError()
 command=os.environ.get('SSH_ORIGINAL_COMMAND','')
 if len(command)>6000 or command.count(' ')!=1:raise ValueError()
 prefix,encoded=command.split(' ')
 if prefix!='gamerhq-staging-automation-v1':raise ValueError()
 raw=base64.b64decode(encoded,validate=True)
 if len(raw)>4096:raise ValueError()
 request=json.loads(raw)
 body=sys.stdin.buffer.read(32769)
 if len(body)>32768:raise ValueError()
 envelope=json.dumps({'request':request,'configuration':json.loads(body) if body else None})
 env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':account.pw_dir,'XDG_RUNTIME_DIR':'/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/'+str(uid)+'/bus'}
 result=subprocess.run(['systemd-run','--user','--pipe','--wait','--collect','--quiet','--unit=gamerhq-staging-automation-'+uuid.uuid4().hex,
 '/opt/gamerhq-staging/deployment-worker/.venv/bin/python','-I','-B','/opt/gamerhq-staging/automation-controller/entry.py'],input=envelope,text=True,env=env,timeout=2200)
 sys.exit(result.returncode)
except Exception:sys.exit('Restricted staging automation request rejected.')
'''
    atomic(ENTRY, entry.encode(), 0, 0o755)
    atomic(POLICY, (json.dumps(policy, indent=2) + '\n').encode(), gid, 0o640)
    if Path('/sys/fs/selinux/enforce').exists():
        restorecon = shutil.which('restorecon')
        if not restorecon: raise ValueError('SELINUX_CONTEXT_CAPABILITY_REQUIRED')
        subprocess.run([restorecon, '-F', str(ENTRY), str(POLICY), str(DEST), str(DEST / 'staging_automation'), str(DEST / 'entry.py'),
            *[str(DEST / 'staging_automation' / name) for name in FILES]], check=True, capture_output=True, timeout=30)
    line = ('restrict,command="' + str(ENTRY) + '" ' + public_key + '\n').encode()
    if line not in original_keys.splitlines(keepends=True):
        atomic(keys, original_keys + (b'' if not original_keys or original_keys.endswith(b'\n') else b'\n') + line, 0, 0o644)
    return {'operation': 'enroll-staging-automation', 'state': 'ENROLLED_INACTIVE', 'controllerCommit': commit,
        'legacyWorkerCommit': policy['legacyWorkerCommit'], 'automationEnabled': False, 'gatewayStarted': False,
        'productionChanged': False, 'existingCredentialsChanged': False, 'legacySshEntryChanged': False,
        'sandboxChanged': False, 'workerSourceChanged': False, 'newRestrictedIdentityRegistered': True,
        'publicKeySha256': policy['publicKeySha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); parser.add_argument('--public-key', required=True); parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try: print(json.dumps(enroll(args.commit, args.public_key, args.approval), indent=2))
    except (ValueError, PermissionError) as error:
        code = str(error)
        if not re.fullmatch('[A-Z_]{1,80}', code): code = 'ENROLLMENT_REJECTED'
        raise SystemExit(json.dumps({'enrollmentError': code, 'productionChanged': False})) from None
    except Exception: raise SystemExit('{"enrollmentError":"ENROLLMENT_REJECTED","productionChanged":false}') from None
