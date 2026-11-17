"""Owner-only recovery for the exact enrolled Servertest DB. Python 3.9 compatible."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile


ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
CONTROLLER = '93fa8958cbaff97cf2152293765e70a4365fcadb'
WORKER = '92db47042d58055cb0debdfd83f11f8508e19809'
HOST = '40c88fada10f7990ef3d2cddb81f30b6381fe73e'
WORKFLOW_HASH = '7c932e34572fee9cb691301902de4a2533128ca4444ec514754e72da15ec9e1d'
BINDING = {'applicationId': '1567098530525351976', 'guildIds': ['1567098041641738331', '1571908030532948129']}
APPROVAL = 'RECOVER MISSING SERVERTEST DATABASE'


def read_protected(path, uid, mode_private=False):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as fd:
        info = os.fstat(fd.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & (0o077 if mode_private else 0o022) or info.st_size > 262144):
            raise ValueError('PROTECTED_FILE_REQUIRED')
        return fd.read(262145)


def verified_baseline(policy, profile, state):
    if (policy.get('repository'), policy.get('controllerCommit'), policy.get('legacyWorkerCommit'),
            policy.get('tenantPilot'), policy.get('tenantPilotWorkflowSha256'), policy.get('hostPromotion', {}).get('commit')) != (
            'Duy-Phan96/GamerHQ', CONTROLLER, WORKER, BINDING, WORKFLOW_HASH, HOST):
        raise ValueError('ENROLLED_POLICY_MISMATCH')
    if (profile.get('environment'), profile.get('stagingGuildId'), profile.get('stagingBotId'),
            profile.get('hostSourceCommit'), profile.get('applicationHostCommit'), profile.get('container')) != (
            'staging', BINDING['guildIds'][0], BINDING['applicationId'], WORKER, HOST, 'gamerhq-staging-bot'):
        raise ValueError('STAGING_PROFILE_MISMATCH')
    for key, path in {'database': ROOT / 'data/gamerhq.db', 'backupRoot': ROOT / 'backups', 'workRoot': ROOT / 'worker'}.items():
        if profile.get(key) != str(path) or path.resolve(strict=True) != path:
            raise ValueError('STAGING_PATH_MISMATCH')
    approved = state.get('approvedImage', '')
    if (state.get('enabled') is not True or state.get('bootstrapVerified') is not True or state.get('recoveryRequired')
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', approved) or profile.get('baseImage') != approved):
        raise ValueError('ACTIVE_BASELINE_REQUIRED')
    return approved


def verify_container(container, image):
    config = container['Config']
    settings = container['HostConfig']
    if (container.get('Name') != '/gamerhq-staging-bot' or container.get('Image') != image
            or config.get('User') != '10001:10001' or settings.get('Privileged') or settings.get('CapAdd')
            or settings.get('CapDrop') != ['ALL'] or settings.get('ReadonlyRootfs') is not True
            or container['State']['Health']['Status'] != 'healthy' or container['State']['Running'] is not True
            or not re.fullmatch(r'[0-9a-f]{64}', container.get('Id', ''))):
        raise ValueError('STAGING_CONTAINER_MISMATCH')
    labels = config['Labels']
    if (labels.get('com.docker.compose.project'), labels.get('com.docker.compose.service'),
            labels.get('org.opencontainers.image.revision')) != ('gamerhq-staging', 'gamerhq-staging', HOST):
        raise ValueError('STAGING_CONTAINER_LABEL_MISMATCH')
    mounts = {(row['Source'], row['Destination']) for row in container['Mounts'] if row['Type'] != 'tmpfs'}
    if (mounts != {(str(ROOT / 'data'), '/app/runtime/data'), (str(ROOT / 'backups'), '/app/backups')}
            or len([row for row in container['Mounts'] if row['Type'] != 'tmpfs']) != 2
            or set(container['NetworkSettings']['Networks']) != {'gamerhq-staging-private'}):
        raise ValueError('STAGING_MOUNT_MISMATCH')
    return container['Id']


def source_packet(archive):
    """Copy only reviewed code and the public seed into the disposable import directory."""
    result = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as source, zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as target:
        for member in source:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('SOURCE_PATH_REJECTED')
            if not ((path.parts[0] in {'database', 'hosts', 'services', 'skill_runtime', 'tools'} and path.suffix == '.py')
                    or member.name in {'config.py', 'release_info.py', 'VERSION', 'data/games_seed.json'}):
                continue
            total += member.size
            if not member.isfile() or total > 16 * 1024**2:
                raise ValueError('SOURCE_LIMIT')
            target.writestr(member.name, source.extractfile(member).read())
    return result.getvalue()


CONTAINER_PROBE = """
import io, json, os, sys, tempfile, zipfile
if (os.geteuid(), os.getegid()) != (10001, 10001):
    raise PermissionError('STAGING_APPLICATION_IDENTITY_REQUIRED')
with tempfile.TemporaryDirectory(prefix='gamerhq-pilot-recovery-') as source:
    with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read(20 * 1024**2))) as archive:
        archive.extractall(source)
    sys.path.insert(0, source)
    from tools.staging_tenant_recovery_probe import recover
    primary, secondary = map(int, sys.argv[2:])
    print(json.dumps(recover(sys.argv[1], primary, secondary, '/app/runtime/data/gamerhq.db')))
"""

WORKER_PROBE = """
import json, os, pathlib, stat, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
from database.tenants import GuildTenantStore
from tools.staging_tenant_rehearsal import capture_container_tenant, retain
primary, secondary = map(int, sys.argv[3:])
root = pathlib.Path('/opt/gamerhq-staging')
store = GuildTenantStore(primary_guild=primary, primary_database=root/'data/gamerhq.db',
                         tenant_directory=root/'data/tenants', additional_guilds=(secondary,))
def rehearse():
    step = 'backup-destination'
    try:
        with tempfile.TemporaryDirectory(prefix='pilot-recovery-probe-', dir=root/'backups') as durable:
            step = 'dual-capture-or-restore'
            result = retain(store, durable, root/'worker',
                            secondary_capture=lambda guild, target: capture_container_tenant(sys.argv[2], guild, target))
            step = 'backup-mode-or-owner'
            batch = pathlib.Path(durable) / result['backupBatch']
            for guild in (primary, secondary):
                info = (batch / f'{guild}.sqlite').stat()
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise PermissionError('PRIVATE_WORKER_BACKUP_REQUIRED')
            return {'state': 'DUAL_BACKUP_REHEARSAL_PASS', 'restores': result['tenantRestores'],
                    'rollbackRehearsal': result['rollbackRehearsal']}
    except subprocess.SubprocessError:
        return {'state': 'DUAL_BACKUP_REHEARSAL_REJECTED', 'step': 'container-snapshot-command'}
    except Exception:
        return {'state': 'DUAL_BACKUP_REHEARSAL_REJECTED', 'step': step}
print(json.dumps(rehearse()))
"""


def execute(source, approval):
    import fcntl
    import pwd

    if os.geteuid() != 0 or approval != APPROVAL:
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch(r'[0-9a-f]{40}', source):
        raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    git_env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    with tempfile.TemporaryDirectory(prefix='gamerhq-recovery-source-') as directory:
        def git(*args):
            return subprocess.run(['git', '-C', directory, *args], env=git_env, capture_output=True,
                                  check=True, timeout=120).stdout

        git('init', '--quiet')
        for ref in (source, CONTROLLER):
            git('fetch', '--quiet', '--depth=1', 'https://github.com/Duy-Phan96/GamerHQ.git', ref)
        if git('show', source + ':tools/recover_staging_pilot_secondary.py') != Path(__file__).read_bytes():
            raise ValueError('INSTALLER_SOURCE_MISMATCH')
        packet = source_packet(git('archive', source))
        worker = ROOT / 'deployment-worker'
        if git('-c', 'safe.directory=' + str(worker), '-C', str(worker), 'rev-parse', 'HEAD').strip().decode() != WORKER:
            raise ValueError('LEGACY_WORKER_MISMATCH')
        bundle = ROOT / 'automation-controller/revisions' / CONTROLLER / 'pilot-source'
        revision = bundle.parent
        for name in ('backend.py', 'bridge.py'):
            if read_protected(revision / 'staging_automation' / name, 0) != git('show', CONTROLLER + ':tools/staging_automation/' + name):
                raise ValueError('INSTALLED_CONTROLLER_SOURCE_MISMATCH')
        for name in ('tools/staging_tenant_rehearsal.py', 'tools/backup_database.py', 'database/tenants.py'):
            if read_protected(bundle / name, 0) != git('show', CONTROLLER + ':' + name):
                raise ValueError('CONTROLLER_BACKUP_SOURCE_MISMATCH')

    env = ['PATH=/usr/local/bin:/usr/bin:/bin', f'HOME={account.pw_dir}', f'XDG_RUNTIME_DIR=/run/user/{uid}',
           f'DOCKER_HOST=unix:///run/user/{uid}/docker.sock', 'DOCKER_CONFIG=/etc/gamerhq-staging/docker-client']

    def run(*args, data=None):
        result = subprocess.run(['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i', *env, *args],
                                input=data, capture_output=True, check=True, timeout=180)
        if len(result.stdout) > 262144:
            raise ValueError('OPERATION_OUTPUT_LIMIT')
        return json.loads(result.stdout)

    parent = ROOT / 'worker'
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o022:
        raise ValueError('PRIVATE_WORKER_DIRECTORY_REQUIRED')
    with os.fdopen(os.open(parent / 'operation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('PRIVATE_OPERATION_LOCK_REQUIRED')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(parent / 'access-revoked'):
            raise ValueError('ACCESS_REVOKED')
        policy = json.loads(read_protected(CONTROL / 'automation-policy.json', 0))
        profile = json.loads(read_protected(CONTROL / 'settings/profile.json', uid, True))
        state = json.loads(read_protected(parent / 'automation-state.json', uid, True))
        image = verified_baseline(policy, profile, state)
        container, = run('docker', 'inspect', 'gamerhq-staging-bot')
        ident = verify_container(container, image)
        pin = json.dumps({**BINDING, 'productionBotId': profile['productionBotId'], 'productionGuildId': profile['productionGuildId']})
        receipt = run('docker', 'exec', '-i', '--user', '10001:10001', ident, 'python', '-I', '-B', '-c', CONTAINER_PROBE,
                      pin, *BINDING['guildIds'], data=packet)
        receipt.update(state='PILOT_STORAGE_REVIEW_REQUIRED', controllerCommit=CONTROLLER, hostCommit=HOST,
                       gatewayChanged=False, activationAuthorized=False, productionChanged=False)
        if receipt['after']['state'] == 'SNAPSHOT_READY':
            try:
                proof = run(str(ROOT / 'deployment-worker/.venv/bin/python'), '-I', '-B', '-c', WORKER_PROBE,
                            str(bundle), ident, *BINDING['guildIds'])
            except (OSError, ValueError, subprocess.SubprocessError):
                proof = {'state': 'DUAL_BACKUP_REHEARSAL_REJECTED'}
            receipt['backupRehearsal'] = proof
            if proof['state'] == 'DUAL_BACKUP_REHEARSAL_PASS':
                current, = run('docker', 'inspect', 'gamerhq-staging-bot')
                if verify_container(current, image) != ident:
                    raise ValueError('STAGING_CONTAINER_CHANGED')
                receipt['state'] = 'PILOT_SECONDARY_RECOVERY_VERIFIED'
        return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try:
        receipt = execute(args.commit, args.approval)
        import syslog
        syslog.openlog('gamerhq-staging-security', syslog.LOG_PID, syslog.LOG_AUTHPRIV)
        syslog.syslog(syslog.LOG_NOTICE, json.dumps({'operation': 'recover-staging-pilot-secondary', **receipt}))
        print(json.dumps(receipt))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch(r'[A-Z_]+', str(error)) else 'PROTECTED_OPERATION_REJECTED'
        raise SystemExit(json.dumps({'state': 'PILOT_RECOVERY_REJECTED', 'diagnostic': code})) from None
