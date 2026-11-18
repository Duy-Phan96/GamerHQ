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
ENROLLED_PREPARATION = 'd194013cda5da2f6bde032ec7d16c5fd0b6c42ec'
ENROLLED_CUTOVER = 'd18d0a2d696b30c36d6b971819b1950723ddf8a9'
STATUS_REPAIRED = 'a2d49804e94a1914b84711d1ffb6cb4e74dca2f3'
CUTOVER_DIGEST = 'f982aa0d7d2d76b4a9e8fe27e1a998535aa03fe61e27d9765eccf4348c7e9011'
PREVIEW_DIGEST = '7c932e34572fee9cb691301902de4a2533128ca4444ec514754e72da15ec9e1d'
PREPARATION_DIGEST = '487c3f213024d47435952345029d82854cc6089e8bccb46e1bc089e5ccc35290'
ACTIVATION_CANDIDATE = '12e0053f3af479235f7281b6f67637e564b962fc'
ACTIVATION_ARCHIVE = '264753b5d384bdabad7cfee5ba631dd8d5845da65f0c6ef144e28c286290d3fc'
ACTIVATION_CI = '44141632946'
FALLBACK = {'commit': 'ca6bc4ac5604987dcab9b5310b7af2b9c53cf652',
            'archiveSha256': 'e5e6e949f084671cff8a70abdaa92e64e18bdae2188701083554bbad3d03aca5', 'ciRun': '44267696433'}
BASELINE_IMAGE = 'sha256:1fac8871a93d79180eae0c11ace3735ace453714425516dbfe72df262a50caf8'
WORKER = '92db47042d58055cb0debdfd83f11f8508e19809'
HOST = '40c88fada10f7990ef3d2cddb81f30b6381fe73e'
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
APPROVAL = 'ENROLL STAGING TENANT PREVIEW'
PREPARATION_APPROVAL = 'ENROLL STAGING TENANT PREPARATION ONLY'
CUTOVER_APPROVAL = 'ENROLL STAGING TENANT CUTOVER POLICY ONLY'
CUTOVER_REPAIR_APPROVAL = 'REPAIR STAGING TENANT CUTOVER STATUS ONLY'
RECOVERY_REPAIR_APPROVAL = 'ENROLL STAGING PILOT RECOVERY VERIFICATION ONLY'
WORKFLOW = '.github/workflows/staging-tenant-pilot-preview.yml'
PREPARATION_WORKFLOW = '.github/workflows/staging-tenant-pilot-activation-prepare.yml'
CUTOVER_WORKFLOW = '.github/workflows/staging-tenant-pilot-cutover.yml'
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


def updated_policy(policy, source, workflow, profile, state, *, preparation=False, cutover=False,
                   cutover_repair=False, recovery_repair=False):
    digest = hashlib.sha256(workflow).hexdigest()
    previous = STATUS_REPAIRED if recovery_repair else ENROLLED_CUTOVER if cutover_repair else ENROLLED_PREPARATION if cutover else ENROLLED_PREVIEW if preparation else PREVIOUS
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
    if (state.get('enabled') is not True or state.get('bootstrapVerified') is not True
            or bool(state.get('recoveryRequired')) != recovery_repair
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', state.get('approvedImage', ''))
            or profile.get('baseImage') != state['approvedImage']):
        raise ValueError('ACTIVE_STAGING_REQUIRED')
    if preparation or cutover:
        if policy.get('tenantPilot') != BINDING or policy.get('tenantPilotWorkflowSha256') != PREVIEW_DIGEST:
            raise ValueError('PREVIEW_POLICY_CHANGED')
        activation = {
            'commit': ACTIVATION_CANDIDATE, 'archiveSha256': ACTIVATION_ARCHIVE,
            'ciRun': ACTIVATION_CI, 'workflowSha256': PREPARATION_DIGEST if cutover else digest,
        }
        if cutover:
            if policy.get('tenantPilotActivation') != activation or state['approvedImage'] != BASELINE_IMAGE:
                raise ValueError('PREPARATION_POLICY_CHANGED')
            new = {**policy, 'controllerCommit': source,
                   'tenantPilotCutover': {'workflowSha256': digest, 'fallback': FALLBACK}}
            if cutover_repair and (digest != CUTOVER_DIGEST or policy.get('tenantPilotCutover') != new['tenantPilotCutover']):
                raise ValueError('CUTOVER_POLICY_CHANGED')
        else:
            new = {**policy, 'controllerCommit': source, 'tenantPilotActivation': activation}
    else:
        new = {**policy, 'controllerCommit': source, 'tenantPilot': BINDING, 'tenantPilotWorkflowSha256': digest}
    if policy['controllerCommit'] == source and policy != new:
        raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
    return new


def verify_recovery_journal(state, journal):
    proof = state['tenantPilotActivationPrepared']
    expected = {'preparedSha256': hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                'baselineImage': BASELINE_IMAGE,
                'candidateImage': 'sha256:c9c6996a646b93412dc7931cf98b20256387b9d95aec36d359009aff82cf7320',
                'fallbackImage': 'sha256:78e394acafa55e9bbf9810530b69beaa2ef9d1f3fccfa7c736669f85db0d0e02'}
    if (journal.get('binding') != expected or journal.get('phase') != 'ROLLING_BACK'
            or journal.get('failedAt') != 'VERIFYING' or journal.get('activationRun') != '44283427505'
            or proof.get('runId') != '44282392511' or proof.get('fallbackRollbackRehearsal') != 'PASS'
            or journal.get('checkpoint', {}).get('backupBatch') != 'tenant-pilot-3bnrqu5l'):
        raise ValueError('REVIEWED_RECOVERY_JOURNAL_REQUIRED')


def enroll(source, workflow_digest, approval, *, preparation=False, cutover=False, cutover_repair=False,
           recovery_repair=False):
    import fcntl
    import pwd

    cutover_repair = cutover_repair or recovery_repair
    cutover = cutover or cutover_repair
    required = RECOVERY_REPAIR_APPROVAL if recovery_repair else CUTOVER_REPAIR_APPROVAL if cutover_repair else CUTOVER_APPROVAL if cutover else PREPARATION_APPROVAL if preparation else APPROVAL
    if os.geteuid() != 0 or approval != required:
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch(r'[0-9a-f]{40}', source) or not re.fullmatch(r'[0-9a-f]{64}', workflow_digest):
        raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    revisions = ROOT / 'automation-controller/revisions'
    entry = revisions.parent / 'entry.py'
    policy_file = CONTROL / 'automation-policy.json'
    previous_commit = STATUS_REPAIRED if recovery_repair else ENROLLED_CUTOVER if cutover_repair else ENROLLED_PREPARATION if cutover else ENROLLED_PREVIEW if preparation else PREVIOUS
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
        revisions_to_fetch = [source, PREVIOUS]
        if preparation or cutover:
            revisions_to_fetch.extend((previous_commit, ACTIVATION_CANDIDATE))
        if cutover:
            revisions_to_fetch.append(FALLBACK['commit'])
        for revision in (*revisions_to_fetch, 'refs/heads/main'):
            git('fetch', '--quiet', '--depth=1', REPOSITORY, revision)
        if cutover_repair:
            allowed = {'tools/staging_automation/pilot_activation.py', 'tools/staging_tenant_rehearsal.py',
                       'tools/enroll_staging_tenant_preview.py', 'tests/test_staging_pilot_activation.py',
                       'tests/test_staging_tenant_enrollment.py'}
            if recovery_repair:
                allowed |= {'hosts/gamerhq/skill_staging.py', 'tools/staging_automation/pilot_cutover.py',
                            'tools/staging_automation/bridge.py',
                            'tools/staging_pilot_cutover_diagnostics.py'}
            changes = set(git('diff', '--name-only', previous_commit, source, '--').decode().splitlines())
            if not changes <= allowed:
                raise ValueError('UNREVIEWED_REPAIR_SCOPE')
        path = CUTOVER_WORKFLOW if cutover else PREPARATION_WORKFLOW if preparation else WORKFLOW
        workflow = git('show', 'FETCH_HEAD:' + path)
        client_ref = ENROLLED_CUTOVER if cutover_repair else source
        if hashlib.sha256(workflow).hexdigest() != workflow_digest or f'ref: {client_ref}\n'.encode() not in workflow:
            raise ValueError('WORKFLOW_DIGEST_OR_SOURCE_REJECTED')
        if preparation or cutover:
            if hashlib.sha256(git('show', 'FETCH_HEAD:' + WORKFLOW)).hexdigest() != PREVIEW_DIGEST:
                raise ValueError('PREVIEW_POLICY_CHANGED')
            if cutover and hashlib.sha256(git('show', 'FETCH_HEAD:' + PREPARATION_WORKFLOW)).hexdigest() != PREPARATION_DIGEST:
                raise ValueError('PREPARATION_POLICY_CHANGED')
            pins = [(ACTIVATION_CANDIDATE, ACTIVATION_ARCHIVE)]
            if cutover:
                pins.append((FALLBACK['commit'], FALLBACK['archiveSha256']))
            for revision, checksum in pins:
                candidate_archive = Path(scratch) / 'candidate.tar'
                git('archive', '--format=tar', '--output', str(candidate_archive), revision)
                if hashlib.sha256(candidate_archive.read_bytes()).hexdigest() != checksum:
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
            journal_path = ROOT / 'worker/tenant-pilot-cutover.json'
            if (ROOT / 'worker/access-revoked').exists() or (cutover and not recovery_repair and os.path.lexists(journal_path)):
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
            if recovery_repair:
                verify_recovery_journal(state, json.loads(checked(journal_path, uid, private=True)))
            updated = updated_policy(policy, source, workflow, profile, state, preparation=preparation,
                                     cutover=cutover, cutover_repair=cutover_repair, recovery_repair=recovery_repair)
            if launcher not in (renderer(previous), renderer(target)) or (policy['controllerCommit'] == previous_commit and launcher != renderer(previous)):
                raise ValueError('INSTALLED_SOURCE_MISMATCH')
            blobs = {}
            overlay = OVERLAY | ({'pilot_activation.py'} if cutover else set()) | ({'pilot_cutover.py'} if cutover_repair else set())
            for member in (previous / 'staging_automation').iterdir():
                name = member.name
                relative = SOURCE_PATHS.get(name, 'tools/staging_automation/' + name)
                body = checked(member)
                expected = previous_commit if name in overlay else PREVIOUS
                if body != git('show', expected + ':' + relative):
                    raise ValueError('INSTALLED_SOURCE_MISMATCH')
                blobs[name] = git('show', source + ':' + relative) if name in OVERLAY else body
            if not OVERLAY <= blobs.keys():
                raise ValueError('INSTALLED_SOURCE_MISMATCH')
            if preparation or cutover:
                blobs['pilot_activation.py'] = git('show', source + ':tools/staging_automation/pilot_activation.py')
            if cutover:
                blobs['pilot_cutover.py'] = git('show', source + ':tools/staging_automation/pilot_cutover.py')
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
    return {'state': 'PILOT_RECOVERY_VERIFICATION_ENROLLED' if recovery_repair else 'PILOT_CUTOVER_STATUS_REPAIRED' if cutover_repair else 'PILOT_CUTOVER_POLICY_ENROLLED' if cutover else
            'PILOT_ACTIVATION_PREPARATION_ENROLLED' if preparation else 'PILOT_PREVIEW_ENROLLED',
            'controllerCommit': source, 'legacyWorkerCommit': WORKER,
            'hostCommit': HOST, 'workflowSha256': workflow_digest, 'binding': BINDING,
            'activationAuthorized': False, 'gatewayChanged': False, 'productionChanged': False,
            **({'fallbackCommit': FALLBACK['commit'], 'fallbackArchiveSha256': FALLBACK['archiveSha256'],
                'baselineImage': BASELINE_IMAGE} if cutover else {})}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--workflow-sha256', required=True)
    parser.add_argument('--approval', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--activation-preparation', action='store_true')
    mode.add_argument('--cutover-policy', action='store_true')
    mode.add_argument('--repair-cutover-status', action='store_true')
    mode.add_argument('--repair-cutover-recovery', action='store_true')
    args = parser.parse_args()
    try:
        receipt = enroll(args.commit, args.workflow_sha256, args.approval, preparation=args.activation_preparation,
                         cutover=args.cutover_policy, cutover_repair=args.repair_cutover_status,
                         recovery_repair=args.repair_cutover_recovery)
    except (OSError, subprocess.SubprocessError):
        raise SystemExit('Staging enrollment failed a protected filesystem, Git or lock check.') from None
    print(json.dumps(receipt))
