"""Owner-only enrollment of the exact Staging tenant preview. Python 3.9 compatible."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile


ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
PREVIOUS = '876805290ef454523f9b36f1f9fbf90c3ec64a17'
ENROLLED_PREVIEW = '93fa8958cbaff97cf2152293765e70a4365fcadb'
PREVIEW_DIGEST = '7c932e34572fee9cb691301902de4a2533128ca4444ec514754e72da15ec9e1d'
ACTIVATION_CANDIDATE = '12e0053f3af479235f7281b6f67637e564b962fc'
ACTIVATION_ARCHIVE = '264753b5d384bdabad7cfee5ba631dd8d5845da65f0c6ef144e28c286290d3fc'
ACTIVATION_CI = '44141632946'
WORKER = '92db47042d58055cb0debdfd83f11f8508e19809'
HOST = '40c88fada10f7990ef3d2cddb81f30b6381fe73e'
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
APPROVAL = 'ENROLL STAGING TENANT PREVIEW'
PREPARATION_APPROVAL = 'ENROLL STAGING TENANT PREPARATION ONLY'
WORKFLOW = '.github/workflows/staging-tenant-pilot-preview.yml'
PREPARATION_WORKFLOW = '.github/workflows/staging-tenant-pilot-activation-prepare.yml'
BINDING = {'applicationId': '1567098530525351976', 'guildIds': ['1567098041641738331', '1571908030532948129']}
OVERLAY = {'backend.py', 'bridge.py', 'oidc.py', 'policy.py'}
SOURCE_PATHS = {'deployment.py': 'hosts/gamerhq/skill_update_controller.py',
                'catalog_import.py': 'tools/game_catalog_import.py', 'catalog_manifest.json': 'data/game_catalog_expansion.json'}


def checked(path, uid=0, *, private=False):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as fd:
        info = os.fstat(fd.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & (0o077 if private else 0o022) or info.st_size > 262144):
            raise ValueError('PROTECTED_FILE_REQUIRED')
        return fd.read(262145)


def directory(path, gid=None):
    if gid is not None and not os.path.lexists(path):
        path.mkdir(mode=0o750)
        os.chown(path, 0, gid)
        path.chmod(0o750)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('PROTECTED_DIRECTORY_REQUIRED')


def atomic(path, content, gid):
    directory(path.parent)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pilot-', delete=False) as stream:
        pending = Path(stream.name)
        os.fchown(stream.fileno(), 0, gid)
        os.fchmod(stream.fileno(), 0o640)
        if os.path.lexists(path):
            checked(path)
            if Path('/sys/fs/selinux/enforce').exists():
                os.setxattr(pending, 'security.selinux', os.getxattr(path, 'security.selinux', follow_symlinks=False))
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        pending.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        pending.unlink(missing_ok=True)


def renderer(path):
    return (f'import sys\nsys.path[:0]=[{str(path)!r},{str(ROOT / "deployment-worker")!r}]\n'
            'from staging_automation.bridge import entry\nentry()\n').encode()


def bundle_sources(archive, destination, gid):
    """Install only reviewed Python source and the packaged runtime version marker."""
    with tarfile.open(archive) as source:
        total = 0
        for member in source:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('SOURCE_PATH_REJECTED')
            if not (path.parts and path.parts[0] in {'database', 'hosts', 'services', 'skill_runtime', 'tools'}
                    and path.suffix == '.py' or member.name in {'config.py', 'release_info.py', 'VERSION'}):
                continue
            total += member.size
            if not member.isfile() or member.size > 262144 or total > 16 * 1024**2:
                raise ValueError('SOURCE_LIMIT')
            parent = destination
            for part in path.parts[:-1]:
                parent /= part
                directory(parent, gid)
            target = destination / path
            body = source.extractfile(member).read()
            if os.path.lexists(target):
                if checked(target) != body:
                    raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
            else:
                atomic(target, body, gid)


def updated_policy(policy, source, workflow, profile, state, *, preparation=False):
    digest = hashlib.sha256(workflow).hexdigest()
    previous = ENROLLED_PREVIEW if preparation else PREVIOUS
    if policy.get('controllerCommit') not in (previous, source) or policy.get('legacyWorkerCommit') != WORKER:
        raise ValueError('REVIEWED_ENROLLMENT_REQUIRED')
    if policy.get('repository') != 'Duy-Phan96/GamerHQ' or policy.get('hostPromotion', {}).get('commit') != HOST:
        raise ValueError('EXISTING_HOST_POLICY_CHANGED')
    if (profile.get('environment'), profile.get('stagingBotId'), profile.get('stagingGuildId'),
            profile.get('hostSourceCommit'), profile.get('applicationHostCommit')) != ('staging', BINDING['applicationId'], BINDING['guildIds'][0], WORKER, HOST):
        raise ValueError('INSTALLED_STAGING_BASELINE_REQUIRED')
    if ('tenantPilot' in profile or profile.get('productionBotId') == BINDING['applicationId']
            or profile.get('productionGuildId') in BINDING['guildIds']):
        raise ValueError('STAGING_BINDING_REJECTED')
    if (state.get('enabled') is not True or state.get('bootstrapVerified') is not True or state.get('recoveryRequired')
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', state.get('approvedImage', ''))
            or profile.get('baseImage') != state['approvedImage']):
        raise ValueError('ACTIVE_STAGING_REQUIRED')
    if preparation:
        if policy.get('tenantPilot') != BINDING or policy.get('tenantPilotWorkflowSha256') != PREVIEW_DIGEST:
            raise ValueError('PREVIEW_POLICY_CHANGED')
        new = {**policy, 'controllerCommit': source, 'tenantPilotActivation': {
            'commit': ACTIVATION_CANDIDATE, 'archiveSha256': ACTIVATION_ARCHIVE,
            'ciRun': ACTIVATION_CI, 'workflowSha256': digest,
        }}
    else:
        new = {**policy, 'controllerCommit': source, 'tenantPilot': BINDING, 'tenantPilotWorkflowSha256': digest}
    if policy['controllerCommit'] == source and policy != new:
        raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
    return new


def enroll(source, workflow_digest, approval, *, preparation=False):
    import fcntl
    import pwd

    if os.geteuid() != 0 or approval != (PREPARATION_APPROVAL if preparation else APPROVAL):
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch(r'[0-9a-f]{40}', source) or not re.fullmatch(r'[0-9a-f]{64}', workflow_digest):
        raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    revisions = ROOT / 'automation-controller/revisions'
    entry = revisions.parent / 'entry.py'
    policy_file = CONTROL / 'automation-policy.json'
    previous_commit = ENROLLED_PREVIEW if preparation else PREVIOUS
    previous, target = revisions / previous_commit, revisions / source
    for path in (ROOT, CONTROL, entry.parent, revisions, previous, previous / 'staging_automation'):
        directory(path)
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    with tempfile.TemporaryDirectory(prefix='gamerhq-pilot-enrollment-') as scratch:
        def git(*args):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *args], env=env, check=True,
                                  capture_output=True, timeout=180).stdout

        git('init', '--quiet')
        revisions_to_fetch = (source, PREVIOUS, ENROLLED_PREVIEW, ACTIVATION_CANDIDATE) if preparation else (source, PREVIOUS)
        for revision in (*revisions_to_fetch, 'refs/heads/main'):
            git('fetch', '--quiet', '--depth=1', REPOSITORY, revision)
        workflow = git('show', 'FETCH_HEAD:' + (PREPARATION_WORKFLOW if preparation else WORKFLOW))
        if hashlib.sha256(workflow).hexdigest() != workflow_digest or f'ref: {source}\n'.encode() not in workflow:
            raise ValueError('WORKFLOW_DIGEST_OR_SOURCE_REJECTED')
        if preparation:
            if hashlib.sha256(git('show', 'FETCH_HEAD:' + WORKFLOW)).hexdigest() != PREVIEW_DIGEST:
                raise ValueError('PREVIEW_POLICY_CHANGED')
            candidate_archive = Path(scratch) / 'candidate.tar'
            git('archive', '--format=tar', '--output', str(candidate_archive), ACTIVATION_CANDIDATE)
            if hashlib.sha256(candidate_archive.read_bytes()).hexdigest() != ACTIVATION_ARCHIVE:
                raise ValueError('CANDIDATE_ARCHIVE_MISMATCH')
        if git('show', source + ':tools/enroll_staging_tenant_preview.py') != Path(__file__).read_bytes():
            raise ValueError('INSTALLER_SOURCE_MISMATCH')
        if json.loads(git('show', source + ':deploy/staging/tenant-pilot-binding.json')) != BINDING:
            raise ValueError('REVIEWED_BINDING_MISMATCH')
        archive = Path(scratch) / 'source.tar'
        git('archive', '--output', str(archive), source)
        if archive.stat().st_size > 24 * 1024**2:
            raise ValueError('SOURCE_LIMIT')
        worker_info = (ROOT / 'worker').lstat()
        if (not stat.S_ISDIR(worker_info.st_mode) or worker_info.st_uid != uid or worker_info.st_gid != gid
                or worker_info.st_mode & 0o022):
            raise ValueError('PRIVATE_WORKER_DIRECTORY_REQUIRED')
        with os.fdopen(os.open(ROOT / 'worker/operation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
            info = os.fstat(lock.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('PRIVATE_OPERATION_LOCK_REQUIRED')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (ROOT / 'worker/access-revoked').exists():
                raise ValueError('ACCESS_REVOKED')
            # Check the physical legacy source revision without reading its environment.
            worker = ROOT / 'deployment-worker'
            installed = git('-c', 'safe.directory=' + str(worker), '-C', str(worker), 'rev-parse', 'HEAD').strip().decode()
            if installed != WORKER:
                raise ValueError('LEGACY_SOURCE_BINDING_REJECTED')
            before, launcher = checked(policy_file), checked(entry)
            policy = json.loads(before)
            profile = json.loads(checked(CONTROL / 'settings/profile.json', uid, private=True))
            state = json.loads(checked(ROOT / 'worker/automation-state.json', uid, private=True))
            updated = updated_policy(policy, source, workflow, profile, state, preparation=preparation)
            if launcher not in (renderer(previous), renderer(target)) or (policy['controllerCommit'] == previous_commit and launcher != renderer(previous)):
                raise ValueError('INSTALLED_SOURCE_MISMATCH')
            blobs = {}
            for member in (previous / 'staging_automation').iterdir():
                name = member.name
                relative = SOURCE_PATHS.get(name, 'tools/staging_automation/' + name)
                body = checked(member)
                expected = previous_commit if name in OVERLAY else PREVIOUS
                if body != git('show', expected + ':' + relative):
                    raise ValueError('INSTALLED_SOURCE_MISMATCH')
                blobs[name] = git('show', source + ':' + relative) if name in OVERLAY else body
            if not OVERLAY <= blobs.keys():
                raise ValueError('INSTALLED_SOURCE_MISMATCH')
            if preparation:
                blobs['pilot_activation.py'] = git('show', source + ':tools/staging_automation/pilot_activation.py')
            directory(target, gid)
            directory(target / 'staging_automation', gid)
            directory(target / 'pilot-source', gid)
            bundle_sources(archive, target / 'pilot-source', gid)
            for name, body in blobs.items():
                file = target / 'staging_automation' / name
                if os.path.lexists(file) and checked(file) != body:
                    raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
                atomic(file, body, gid)
            try:
                if Path('/sys/fs/selinux/enforce').exists():
                    subprocess.run(['/usr/sbin/restorecon', '-RF', str(target)], check=True, capture_output=True, timeout=45)
                atomic(policy_file, (json.dumps(updated, indent=2) + '\n').encode(), gid)
                atomic(entry, renderer(target), gid)
            except Exception:
                atomic(entry, launcher, gid)
                atomic(policy_file, before, gid)
                raise
    return {'state': 'PILOT_ACTIVATION_PREPARATION_ENROLLED' if preparation else 'PILOT_PREVIEW_ENROLLED',
            'controllerCommit': source, 'legacyWorkerCommit': WORKER,
            'hostCommit': HOST, 'workflowSha256': workflow_digest, 'binding': BINDING,
            'activationAuthorized': False, 'gatewayChanged': False, 'productionChanged': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--workflow-sha256', required=True)
    parser.add_argument('--approval', required=True)
    parser.add_argument('--activation-preparation', action='store_true')
    args = parser.parse_args()
    try:
        receipt = enroll(args.commit, args.workflow_sha256, args.approval, preparation=args.activation_preparation)
    except (OSError, subprocess.SubprocessError):
        raise SystemExit('Staging enrollment failed a protected filesystem, Git or lock check.') from None
    print(json.dumps(receipt))
