"""Owner-only, resumable adoption of the accepted recovered Staging baseline.

Uses the existing controller, identity, operation lock and sandbox. Python 3.9.
Never starts/stops a live container, writes SQLite or changes Discord resources.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import types
import uuid

sys.dont_write_bytecode = True

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
PREVIOUS = '8d8e067463123f0b847d9daf7d5dd70a854a5930'
LEGACY_CONTROLLER_BASE = '876805290ef454523f9b36f1f9fbf90c3ec64a17'
APPROVAL = 'ENROLL RECOVERED PRIMARY ONLY PILOT GENERATION'
MANIFEST = 'deploy/staging/tenant-pilot-generation.json'
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
STAGES = ('OWNER_CHECK', 'SOURCE_FETCH', 'SOURCE_SELF_VERIFICATION', 'WORKFLOW_REGISTRATION',
          'ARCHIVE_VERIFICATION', 'ENROLLMENT_HELPER', 'INSTALLED_DIRECTORIES', 'INSTALLED_WORKER',
          'INSTALLED_CONTROLLER', 'BUNDLE_STAGING', 'BASELINE_PIN', 'OPERATION_LOCK',
          'TRANSITION_VALIDATION', 'BASELINE_STATE', 'GENERATION_DOCUMENTS', 'INCIDENT_VERIFICATION',
          'BASELINE_PROBE', 'INCIDENT_ARCHIVE', 'TRANSITION_PREPARE', 'TRANSITION_APPLY', 'TRANSITION_COMMIT')
_stage_trace = []
REJECTIONS = frozenset('''OWNER_APPROVAL_REQUIRED IMMUTABLE_SOURCE_REQUIRED INSTALLER_SOURCE_MISMATCH
WORKFLOW_REGISTRATION_REQUIRED ARCHIVE_PIN_REJECTED PROTECTED_DIRECTORY_REQUIRED PROTECTED_FILE_REQUIRED
LEGACY_WORKER_MISMATCH CONTROLLER_SOURCE_MISMATCH IMMUTABLE_EXTENSION_CHANGED SOURCE_PATH_REJECTED
SOURCE_LIMIT BASELINE_PIN_REJECTED COHORT_PIN_REJECTED WORKER_DIRECTORY_REJECTED OPERATION_LOCK_REJECTED
GENERATION_ALREADY_IN_USE GENERATION_TRANSACTION_CHANGED GENERATION_STATE_CHANGED ENROLLED_CONTROLLER_REQUIRED
PREVIOUS_GENERATION_CHANGED COMPLETED_INCIDENT_REQUIRED REVIEWED_RECOVERY_JOURNAL_REQUIRED
INCIDENT_BACKUP_REJECTED PROBE_OUTPUT_LIMIT INCIDENT_HISTORY_CHANGED RECOVERY_REQUIRES_OWNER
PILOT_CHECKPOINT_REJECTED IMMUTABLE_RELEASE_CHANGED FETCHED_REVISION_REJECTED
EXISTING_TRANSITION_REQUIRES_REVIEW'''.split())


def progress(stage):
    if stage not in STAGES:
        raise ValueError('UNCLASSIFIED_REJECTED')
    _stage_trace.append(stage)


def fetch_revision(git, revision):
    git('fetch', '--quiet', '--depth=1', REPOSITORY, revision)
    try:
        actual = git('rev-parse', '--verify', 'FETCH_HEAD^{commit}').strip().decode('ascii')
    except (UnicodeError, AttributeError):
        raise ValueError('FETCHED_REVISION_REJECTED') from None
    if (not re.fullmatch('[0-9a-f]{40}', actual)
            or revision != 'refs/heads/main' and actual != revision):
        raise ValueError('FETCHED_REVISION_REJECTED')


def fetch_sources(git, source):
    # The installed bundle deliberately retains modules from the original
    # controller. A depth-one fetch of PREVIOUS does not supply that commit.
    for revision in (source, PREVIOUS, LEGACY_CONTROLLER_BASE, 'refs/heads/main'):
        fetch_revision(git, revision)


def rejection(error):
    """No exception text, command, subprocess output or private value is logged."""
    code = getattr(error, 'code', None)
    value = error.args[0] if error.args else None
    reason = code if isinstance(code, str) and code in REJECTIONS else (
        value if isinstance(value, str) and value in REJECTIONS else 'VALIDATION_REJECTED')
    evidence = {}
    if isinstance(error, subprocess.TimeoutExpired):
        reason = 'SUBPROCESS_TIMED_OUT'
    elif isinstance(error, subprocess.SubprocessError):
        reason = 'SUBPROCESS_REJECTED'
        if isinstance(error, subprocess.CalledProcessError) and type(error.returncode) is int:
            evidence['exitCode'] = max(-255, min(255, error.returncode))
    elif isinstance(error, OSError):
        reason = 'FILESYSTEM_REJECTED'
        if type(error.errno) is int and 0 < error.errno <= 4096:
            evidence['errno'] = error.errno
    return {'state': 'GENERATION_ENROLLMENT_REJECTED',
            'stage': _stage_trace[-1] if _stage_trace else 'INITIALIZING',
            'completedStages': _stage_trace[:-1], 'rejectionReason': reason,
            'activationAuthorized': False, **evidence}


def gate(target):
    """One fail-closed launcher, also used after the transaction commits."""
    return ("import json,sys\nfrom pathlib import Path\n"
            "try:\n p=json.loads(Path(" + repr(str(CONTROL / 'pilot-generation-transition.json')) + ").read_bytes())\n"
            "except Exception:\n raise SystemExit('PILOT_GENERATION_HELD')\n"
            "if p.get('phase')!='COMMITTED' or p.get('source')!=" + repr(target.name) + ": raise SystemExit('PILOT_GENERATION_HELD')\n"
            "sys.path[:0]=" + repr([str(target), str(target / 'pilot-source'), str(ROOT / 'deployment-worker')]) + "\n"
            "from staging_automation.bridge import entry\nentry()\n").encode()


def replace(path, body, uid, gid, mode):
    """Preserve label and exact owner/mode; no shell, links or in-place writes."""
    old = path.lstat() if os.path.lexists(path) else None
    if old and (not stat.S_ISREG(old.st_mode) or old.st_nlink != 1 or old.st_uid != uid):
        raise ValueError('PROTECTED_FILE_REQUIRED')
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.generation-', delete=False) as stream:
        pending = Path(stream.name)
        os.fchown(stream.fileno(), uid, gid)
        os.fchmod(stream.fileno(), mode)
        if old and Path('/sys/fs/selinux/enforce').exists():
            os.setxattr(pending, 'security.selinux', os.getxattr(path, 'security.selinux', follow_symlinks=False))
        stream.write(body)
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


PROBE = r'''
import json,os,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/pilot-source', '/opt/gamerhq-staging/deployment-worker']
from staging_automation.backend import HostBackend
from staging_automation.policy import validate_policy
from tools.staging_dispatch import environment
from tools.staging_tenant_rehearsal import rehearsal_binding,verify_activation_checkpoint
from tools.staging_automation.host_promotion import require_baseline_skill,require_runtime_lifecycle,retained_state
packet=json.load(sys.stdin)
profile,policy=packet['profile'],packet['policy']
validate_policy(policy)
values=environment();os.environ.clear();os.environ.update(values)
backend=HostBackend(profile,policy)
c=backend.inspect()
if c['Image']!=packet['image'] or backend.health()['state']!='HOST_READY': raise ValueError('BASELINE_NOT_READY')
runtime=backend.skill()
require_baseline_skill(json.loads(open(profile['releaseFile']).read()),runtime['version'])
require_runtime_lifecycle(retained_state(profile['database'],profile['stagingGuildId']),runtime)
binding=rehearsal_binding({**profile,'tenantPilot':policy['tenantPilot']},policy,c)
if binding['baselineHostCommit']!=policy['hostPromotion']['commit']: raise ValueError('BASELINE_SOURCE_CHANGED')
proof=backend._verify_tenant_pilot(expected_image=packet['image'])
if proof.get('state')!='PILOT_PREVIEW_VERIFIED':
 print(json.dumps({k:proof[k] for k in ('state','guildId','missingPermissions') if k in proof}));sys.exit(0)
verify_activation_checkpoint(profile['backupRoot'],proof,policy['tenantPilot'],binding,
                             verified_at=proof['verifiedAt'],now=proof['verifiedAt'])
after=backend.inspect()
if after['Id']!=c['Id'] or backend.health()['state']!='HOST_READY': raise ValueError('BASELINE_CHANGED')
print(json.dumps({'state':'GENERATION_BASELINE_VERIFIED','hostCommit':binding['baselineHostCommit'],
                 'backupBatch':proof['backupBatch'],'backupReceiptSha256':proof['backupReceiptSha256'],
                 'bothTenantRestores':'PASS','primaryOnlyVerified':True,'gatewayChanged':False}))
'''
PROBE = 'try:\n' + textwrap.indent(PROBE, ' ') + r'''
except Exception as error:
 from tools.staging_health import HostHealthFailure,HEALTH_FAILURE_REASONS
 from staging_automation.policy import Denied,ERROR_CODES
 result={'state':'GENERATION_BASELINE_REJECTED','reason':'PROBE_REJECTED'}
 if isinstance(error,HostHealthFailure) and error.reason in HEALTH_FAILURE_REASONS:
  result['reason']=error.reason
  if type(error.status) is int and 300<=error.status<=599: result['httpStatus']=error.status
 elif getattr(error,'code',None) in ERROR_CODES: result['reason']=error.code
 print(json.dumps(result))
'''


def enroll(source, approval, *, require_absent=False):
    import fcntl
    import pwd

    progress('OWNER_CHECK')
    if os.geteuid() != 0 or approval != APPROVAL:
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    revisions = ROOT / 'automation-controller/revisions'
    target, previous = revisions / source, revisions / PREVIOUS
    transaction_path = CONTROL / 'pilot-generation-transition.json'
    if require_absent and os.path.lexists(transaction_path):
        raise ValueError('EXISTING_TRANSITION_REQUIRES_REVIEW')
    with tempfile.TemporaryDirectory(prefix='gamerhq-pilot-generation-') as scratch:
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*args):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *args], env=env,
                                  check=True, capture_output=True, timeout=180).stdout
        progress('SOURCE_FETCH')
        git('init', '--quiet')
        fetch_sources(git, source)
        progress('SOURCE_SELF_VERIFICATION')
        if git('show', source + ':tools/enroll_staging_pilot_generation.py') != Path(__file__).read_bytes():
            raise ValueError('INSTALLER_SOURCE_MISMATCH')
        manifest = json.loads(git('show', source + ':' + MANIFEST))
        main = git('rev-parse', 'FETCH_HEAD').strip().decode()
        progress('WORKFLOW_REGISTRATION')
        for name, pin in manifest['workflows'].items():
            body = git('show', main + ':.github/workflows/' + name)
            if hashlib.sha256(body).hexdigest() != pin['sha256'] or ('ref: ' + pin['client'] + '\n').encode() not in body:
                raise ValueError('WORKFLOW_REGISTRATION_REQUIRED')
        progress('ARCHIVE_VERIFICATION')
        for pin in (manifest['candidate'], manifest['baseline']):
            fetch_revision(git, pin['commit'])
            if hashlib.sha256(git('archive', '--format=tar', pin['commit'])).hexdigest() != pin['archiveSha256']:
                raise ValueError('ARCHIVE_PIN_REJECTED')
        progress('ENROLLMENT_HELPER')
        module = types.ModuleType('verified_enrollment')
        module.__file__ = str(Path(scratch) / 'verified_enrollment.py')
        exec(compile(git('show', source + ':tools/enroll_staging_tenant_preview.py'), module.__file__, 'exec'), module.__dict__)
        if module.PREVIOUS != LEGACY_CONTROLLER_BASE:
            raise ValueError('CONTROLLER_SOURCE_MISMATCH')
        progress('INSTALLED_DIRECTORIES')
        for folder in (ROOT, CONTROL, revisions.parent, revisions, previous):
            module.directory(folder)
        progress('INSTALLED_WORKER')
        if git('-c', 'safe.directory=' + str(ROOT / 'deployment-worker'), '-C', str(ROOT / 'deployment-worker'),
               'rev-parse', 'HEAD').strip().decode() != module.WORKER:
            raise ValueError('LEGACY_WORKER_MISMATCH')
        # Verify the actually enrolled mixed immutable bundle, not just its Git label.
        progress('INSTALLED_CONTROLLER')
        overlays = module.OVERLAY | {'pilot_activation.py', 'pilot_cutover.py', 'recovery_api_repair.py'}
        for member in (previous / 'staging_automation').iterdir():
            relative = module.SOURCE_PATHS.get(member.name, 'tools/staging_automation/' + member.name)
            expected = PREVIOUS if member.name in overlays else module.PREVIOUS
            if module.checked(member) != git('show', expected + ':' + relative):
                raise ValueError('CONTROLLER_SOURCE_MISMATCH')
        progress('BUNDLE_STAGING')
        for folder in (target, target / 'staging_automation', target / 'pilot-source'):
            module.directory(folder, gid)
        archive = Path(scratch) / 'source.tar'
        git('archive', '--format=tar', '--output', str(archive), source)
        module.bundle_sources(archive, target / 'pilot-source', gid)
        # Keep unchanged installed adapters; update the reviewed controller surface only.
        names = overlays | {'pilot_generation.py'}
        for member in (previous / 'staging_automation').iterdir():
            body = git('show', source + ':tools/staging_automation/' + member.name) if member.name in names else module.checked(member)
            file = target / 'staging_automation' / member.name
            if file.exists() and module.checked(file) != body:
                raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
            module.atomic(file, body, gid)
        module.atomic(target / 'staging_automation/pilot_generation.py',
                      git('show', source + ':tools/staging_automation/pilot_generation.py'), gid)
        if Path('/sys/fs/selinux/enforce').exists():
            subprocess.run(['/usr/sbin/restorecon', '-RF', str(target)], check=True, capture_output=True, timeout=45)
        sys.path.insert(0, str(target))
        from staging_automation import pilot_generation as generation
        progress('BASELINE_PIN')
        if manifest['baseline'] != {'commit': generation.BASELINE_COMMIT, 'archiveSha256': generation.BASELINE_ARCHIVE,
                                    'image': generation.BASELINE_IMAGE, 'ciRun': manifest['baseline']['ciRun']}:
            raise ValueError('BASELINE_PIN_REJECTED')
        if json.loads(git('show', source + ':deploy/staging/tenant-pilot-binding.json')) != generation.COHORT:
            raise ValueError('COHORT_PIN_REJECTED')
        progress('OPERATION_LOCK')
        parent = ROOT / 'worker'
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise ValueError('WORKER_DIRECTORY_REJECTED')
        with os.fdopen(os.open(parent / 'operation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('OPERATION_LOCK_REJECTED')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if require_absent and os.path.lexists(transaction_path):
                raise ValueError('EXISTING_TRANSITION_REQUIRES_REVIEW')
            if os.path.lexists(parent / 'access-revoked') or os.path.lexists(parent / 'tenant-pilot-cutover-v2.json'):
                raise ValueError('GENERATION_ALREADY_IN_USE')
            paths = {'entry': revisions.parent / 'entry.py', 'policy': CONTROL / 'automation-policy.json',
                     'profile': CONTROL / 'settings/profile.json', 'state': parent / 'automation-state.json'}
            def read(name):
                return module.checked(paths[name], uid if name in {'profile', 'state'} else 0,
                                      private=name in {'profile', 'state'})
            progress('TRANSITION_VALIDATION')
            original_incident = module.checked(parent / 'tenant-pilot-cutover.json', uid, private=True)
            initial_phase = 'ABSENT'
            if transaction_path.exists():
                record = json.loads(module.checked(transaction_path))
                if record.get('source') != source or record.get('incidentSha256') != hashlib.sha256(original_incident).hexdigest():
                    raise ValueError('GENERATION_TRANSACTION_CHANGED')
                plan = record['plan']
                original = {item['name']: bytes.fromhex(item['before']) for item in plan['files']}
                if record.get('phase') not in {'PREPARED', 'COMMITTED'}:
                    raise ValueError('GENERATION_TRANSACTION_CHANGED')
                initial_phase = record['phase']
                if any(read(item['name']) not in ((bytes.fromhex(item['after']),) if record['phase'] == 'COMMITTED'
                                                 else (bytes.fromhex(item['before']), bytes.fromhex(item['after'])))
                       for item in plan['files'] if record['phase'] != 'COMMITTED' or item['name'] != 'state'):
                    raise ValueError('GENERATION_STATE_CHANGED')
                if record['phase'] == 'COMMITTED':
                    current = json.loads(read('state'))
                    if (current.get('pilotGeneration') != generation.GENERATION['id']
                            or current.get('approvedImage') != generation.BASELINE_IMAGE
                            or current.get('recoveryRequired') is not False
                            or current.get('enabled') is not True or current.get('bootstrapVerified') is not True):
                        raise ValueError('GENERATION_STATE_CHANGED')
            else:
                progress('BASELINE_STATE')
                original = {name: read(name) for name in paths}
                old_policy, old_profile, old_state = [json.loads(original[key]) for key in ('policy', 'profile', 'state')]
                if old_policy.get('controllerCommit') != PREVIOUS or original['entry'] != module.renderer(previous, recovery_completion=True):
                    raise ValueError('ENROLLED_CONTROLLER_REQUIRED')
                if (old_policy.get('primaryRecoveryApiRepair') != module.API_REPAIR_PIN
                        or old_policy.get('tenantPilotCutover', {}).get('workflowSha256') != module.CUTOVER_DIGEST
                        or old_profile.get('applicationHostCommit') != module.HOST
                        or old_profile.get('baseImage') != old_state.get('approvedImage')
                        or old_profile.get('hostSourceCommit') != module.WORKER):
                    raise ValueError('PREVIOUS_GENERATION_CHANGED')
                progress('GENERATION_DOCUMENTS')
                candidate = {**manifest['candidate'], 'baselineCiRun': manifest['baseline']['ciRun']}
                new_policy, new_profile, new_state = generation.generation_documents(
                    old_policy, old_profile, old_state, json.loads(original_incident), source=source, candidate=candidate,
                    cutover_digest=manifest['workflows']['staging-tenant-pilot-cutover.yml']['sha256'],
                    preparation_digest=manifest['workflows']['staging-tenant-pilot-activation-prepare.yml']['sha256'])
                desired = {'entry': gate(target), 'policy': generation.encode(new_policy),
                           'profile': generation.encode(new_profile), 'state': generation.encode(new_state)}
                # Entry goes first: after any state/policy write, old code cannot run.
                plan = {'files': [{'name': name, 'before': original[name].hex(), 'after': desired[name].hex()}
                                  for name in ('entry', 'profile', 'state', 'policy')]}
                record = {'phase': 'PREPARED', 'source': source, 'plan': plan,
                          'incidentSha256': hashlib.sha256(original_incident).hexdigest()}
            progress('INCIDENT_VERIFICATION')
            desired = {item['name']: bytes.fromhex(item['after']) for item in plan['files']}
            original_state = json.loads(original['state'])
            incident = json.loads(original_incident)
            if incident.get('phase') != 'PILOT_ROLLED_BACK':
                raise ValueError('COMPLETED_INCIDENT_REQUIRED')
            # Reuse the original immutable transaction contract; only the
            # terminal phase differs from its enrollment-time ROLLING_BACK.
            module.verify_recovery_journal(original_state, {**incident, 'phase': 'ROLLING_BACK'})
            import runpy
            inspector = runpy.run_path(str(target / 'pilot-source/tools/staging_pilot_cutover_diagnostics.py'))
            original_profile = json.loads(original['profile'])
            historical_backups = [inspector['inspect_backup'](Path(original_profile['backupRoot']), uid, value)
                                  for value in (incident['checkpoint'], original_state['tenantPilotActivationPrepared']['preview'])]
            if any(value.get('state') != 'VERIFIED' for value in historical_backups):
                raise ValueError('INCIDENT_BACKUP_REJECTED')
            probe_profile, probe_policy = [json.loads(desired[key]) for key in ('profile', 'policy')]
            probe_profile = generation.effective_profile(probe_profile, probe_policy)
            # This is the existing restricted systemd sandbox, with live writes absent.
            environment = ['PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=' + account.pw_dir,
                           'XDG_RUNTIME_DIR=/run/user/' + str(uid), 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(uid) + '/bus']
            command = ['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i', *environment,
                       'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet',
                       '--unit=gamerhq-staging-automation-generation-' + uuid.uuid4().hex,
                       '--property=NoNewPrivileges=yes', '--property=ProtectSystem=strict', '--property=ProtectHome=yes',
                       '--property=ReadWritePaths=' + str(ROOT / 'worker') + ' ' + str(ROOT / 'backups'),
                       '--property=RestrictSUIDSGID=yes', '--property=RuntimeMaxSec=300s',
                       str(ROOT / 'deployment-worker/.venv/bin/python'), '-I', '-B', '-c', PROBE, str(target)]
            progress('BASELINE_PROBE')
            result = subprocess.run(command, input=json.dumps({'profile': probe_profile, 'policy': probe_policy,
                                      'image': generation.BASELINE_IMAGE}), text=True, capture_output=True, check=True, timeout=330)
            if len(result.stdout) > 16384:
                raise ValueError('PROBE_OUTPUT_LIMIT')
            proof = json.loads(result.stdout)
            if proof.get('state') != 'GENERATION_BASELINE_VERIFIED':
                return {'state': 'GENERATION_PREFLIGHT_REJECTED', 'stage': 'BASELINE_PROBE', 'diagnostic': proof,
                        'activationAuthorized': False, 'gatewayChanged': False, 'databaseChanged': False,
                        'productionChanged': False, 'policyChanged': False}
            if record['phase'] == 'COMMITTED':
                expected_history = generation.encode({'incidentHex': original_incident.hex(),
                                                       'policyHex': original['policy'].hex(), 'profileHex': original['profile'].hex(),
                                                       'stateHex': original['state'].hex()})
                if module.checked(CONTROL / 'pilot-generation-incident.json') != expected_history:
                    raise ValueError('INCIDENT_HISTORY_CHANGED')
                return {'state': 'RECOVERED_PRIMARY_GENERATION_ENROLLED', 'alreadyEnrolled': True,
                        'controllerCommit': source, 'generation': generation.GENERATION['id'],
                        'initialTransitionPhase': initial_phase,
                        'activationAuthorized': False, 'gatewayChanged': False, 'databaseChanged': False,
                        'productionChanged': False, 'verification': proof}
            progress('INCIDENT_ARCHIVE')
            archive_path = CONTROL / 'pilot-generation-incident.json'
            historical = generation.encode({'incidentHex': original_incident.hex(),
                                              'policyHex': original['policy'].hex(), 'profileHex': original['profile'].hex(),
                                              'stateHex': original['state'].hex()})
            if archive_path.exists() and module.checked(archive_path) != historical:
                raise ValueError('INCIDENT_HISTORY_CHANGED')
            replace(archive_path, historical, 0, gid, 0o640)
            progress('TRANSITION_PREPARE')
            replace(transaction_path, generation.encode(record), 0, gid, 0o640)
            def write(name, body):
                replace(paths[name], body, uid if name in {'state', 'profile'} else 0, gid,
                        0o600 if name in {'state', 'profile'} else 0o640)
            progress('TRANSITION_APPLY')
            transition_digest = generation.transition(plan, read, write)
            if module.checked(parent / 'tenant-pilot-cutover.json', uid, private=True) != original_incident:
                raise ValueError('INCIDENT_HISTORY_CHANGED')
            progress('TRANSITION_COMMIT')
            replace(transaction_path, generation.encode({**record, 'phase': 'COMMITTED', 'verification': proof}), 0, gid, 0o640)
    return {'state': 'RECOVERED_PRIMARY_GENERATION_ENROLLED', 'controllerCommit': source,
            'initialTransitionPhase': initial_phase,
            'baselineHostCommit': generation.BASELINE_COMMIT, 'baselineImage': generation.BASELINE_IMAGE,
            'generation': generation.GENERATION['id'], 'transitionSha256': transition_digest,
            'incidentSha256': record['incidentSha256'], 'incidentPreserved': True, 'recoveryRequired': False,
            'activationAuthorized': False, 'gatewayChanged': False, 'databaseChanged': False,
            'discordResourcesChanged': False, 'productionChanged': False, 'verification': proof}



ACTIVE_PREVIOUS = 'd765265612fe6aece0d91d21fa3eb5b02a359e2f'
ACTIVE_GENERATION_SOURCE = 'eff7cc14d2af0595ad7e80f99f0f24a7e4595bb9'
ACTIVE_APPROVAL = 'ENROLL RC4 ACTIVE PILOT UPGRADE'
ACTIVE_TRANSITION = 'pilot-rc4-upgrade-transition.json'
ACTIVE_WORKFLOWS = ('staging-rc4-pilot-prepare.yml', 'staging-rc4-pilot-cutover.yml')
ACTIVE_CLIENT = '2a2b08b8d3c8165d7a70ced3c667024ba219b0ac'
ACTIVE_PROBE = r'''
import json,os,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/pilot-source','/opt/gamerhq-staging/deployment-worker']
from staging_automation.backend import HostBackend
from staging_automation.policy import validate_policy
from tools.staging_dispatch import environment
from tools.staging_tenant_rehearsal import rehearsal_binding
from tools.staging_automation.host_promotion import require_baseline_skill, require_runtime_lifecycle, retained_state
from hosts.gamerhq.staging_rootless import verify_rootless
from hosts.gamerhq.skill_staging import verify_local_artifacts
packet=json.load(sys.stdin)
p,q=packet['profile'],packet['policy']
validate_policy(q)
values=environment();os.environ.clear();os.environ.update(values)
verify_rootless(p)
b=HostBackend(p,q)
c=b.inspect()
binding=rehearsal_binding({**p,'tenantPilot':q['tenantPilot']},q,c)
if c['Image']!=p['baseImage'] or binding['baselineHostCommit']!=q['hostPromotion']['commit'] or binding['baselineAdmission']!='PILOT_ACTIVE' or b.health()['state']!='HOST_READY':
 raise ValueError('ACTIVE_PILOT_REQUIRED')
actual=b.run(['docker','exec','--user','10001:10001',c['Id'],'python','-I','-B','-c',
               "from pathlib import Path;print(Path('/app/BUILD_COMMIT').read_text().strip())"])
if actual!=binding['baselineHostCommit']: raise ValueError('ACTIVE_PILOT_REQUIRED')
verify_local_artifacts(p)
runtime=b.skill()
require_baseline_skill(json.loads(open(p['releaseFile']).read()),runtime['version'])
require_runtime_lifecycle(retained_state(p['database'],p['stagingGuildId']),runtime)
if b.inspect()['Id']!=c['Id'] or b.health()['state']!='HOST_READY': raise ValueError('ACTIVE_PILOT_REQUIRED')
print(json.dumps({'state':'ACTIVE_PILOT_VERIFIED','admissionPreserved':True,'hostCommit':actual}))
'''


def active_gate(target, history):
    """Hold the existing launcher until this exact transition and its ancestors commit."""
    return ("import hashlib,json,sys\nfrom pathlib import Path\n"
            "try:\n p=json.loads(Path(" + repr(str(CONTROL / ACTIVE_TRANSITION)) + ").read_bytes())\n"
            " if p.get('phase')!='COMMITTED' or p.get('source')!=" + repr(target.name) + ": raise ValueError()\n"
            " for name,digest in " + repr(history) + ".items():\n"
            "  if hashlib.sha256((Path(" + repr(str(CONTROL)) + ")/name).read_bytes()).hexdigest()!=digest: raise ValueError()\n"
            "except Exception:\n raise SystemExit('PILOT_GENERATION_HELD')\n"
            "sys.path[:0]=" + repr([str(target), str(target / 'pilot-source'), str(ROOT / 'deployment-worker')]) + "\n"
            "from staging_automation.bridge import entry\nentry()\n").encode()


def enroll_active(source, approval, *, require_absent=False, checkpoint=lambda _: None):
    """Next fixed epoch using the existing reviewed bundle and fsynced transition.

    No live Gateway start/stop, database write, Discord mutation or worker/SSH change.
    The separately protected prepare and cutover workflows must already exist on main.
    """
    import fcntl
    import pwd
    progress('OWNER_CHECK')
    if os.geteuid() != 0 or approval != ACTIVE_APPROVAL or not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    revisions = ROOT / 'automation-controller/revisions'
    previous, target = revisions / ACTIVE_PREVIOUS, revisions / source
    marker = CONTROL / ACTIVE_TRANSITION
    if require_absent and os.path.lexists(marker):
        raise ValueError('EXISTING_TRANSITION_REQUIRES_REVIEW')
    with tempfile.TemporaryDirectory(prefix='gamerhq-rc4-enrollment-') as scratch:
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*args):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *args], env=env,
                                  capture_output=True, check=True, timeout=180).stdout
        progress('SOURCE_FETCH')
        git('init', '--quiet')
        for pin in (source, ACTIVE_PREVIOUS, ACTIVE_GENERATION_SOURCE, LEGACY_CONTROLLER_BASE, ACTIVE_CLIENT, 'refs/heads/main'):
            fetch_revision(git, pin)
        main = git('rev-parse', 'FETCH_HEAD').strip().decode()
        progress('SOURCE_SELF_VERIFICATION')
        if git('show', source + ':tools/enroll_staging_pilot_generation.py') != Path(__file__).read_bytes():
            raise ValueError('INSTALLER_SOURCE_MISMATCH')
        def module(path, name, pin):
            result = types.ModuleType(name)
            result.__file__ = str(Path(scratch) / (name + '.py'))
            exec(compile(git('show', pin + ':' + path), result.__file__, 'exec'), result.__dict__)
            return result
        helper = module('tools/enroll_staging_tenant_preview.py', 'verified_helper', source)
        predecessor = module('tools/enroll_staging_tenant_acceptance.py', 'verified_predecessor', ACTIVE_PREVIOUS)
        predecessor.ROOT, predecessor.CONTROL = ROOT, CONTROL
        if helper.PREVIOUS != LEGACY_CONTROLLER_BASE or predecessor.PREVIOUS != ACTIVE_GENERATION_SOURCE:
            raise ValueError('CONTROLLER_SOURCE_MISMATCH')
        progress('WORKFLOW_REGISTRATION')
        workflow_hashes = {}
        for name in ACTIVE_WORKFLOWS:
            path = '.github/workflows/' + name
            body = git('show', source + ':' + path)
            if (git('show', main + ':' + path) != body or ('ref: ' + ACTIVE_CLIENT + '\n').encode() not in body
                    or b'GAMERHQ_STAGING_PILOT_GENERATION: active-pilot-rc4-v3' not in body):
                raise ValueError('WORKFLOW_REGISTRATION_REQUIRED')
            workflow_hashes[name] = hashlib.sha256(body).hexdigest()
        progress('INSTALLED_DIRECTORIES')
        for folder in (ROOT, CONTROL, revisions.parent, revisions, previous, previous / 'staging_automation'):
            helper.directory(folder)
        progress('INSTALLED_WORKER')
        worker = ROOT / 'deployment-worker'
        if git('-c', 'safe.directory=' + str(worker), '-C', str(worker), 'rev-parse', 'HEAD').strip().decode() != helper.WORKER:
            raise ValueError('LEGACY_WORKER_MISMATCH')
        progress('INSTALLED_CONTROLLER')
        overlays = helper.OVERLAY | {'pilot_activation.py', 'pilot_cutover.py', 'recovery_api_repair.py', 'pilot_generation.py'}
        changed = {'bridge.py','policy.py','oidc.py','tenant_acceptance.py','pilot_generation.py','pilot_activation.py','pilot_cutover.py'}
        members = tuple((previous / 'staging_automation').iterdir())
        if not changed <= {item.name for item in members}:
            raise ValueError('CONTROLLER_SOURCE_MISMATCH')
        for item in members:
            expected = (ACTIVE_PREVIOUS if item.name in {'bridge.py','policy.py','oidc.py','tenant_acceptance.py'}
                        else ACTIVE_GENERATION_SOURCE if item.name in overlays else LEGACY_CONTROLLER_BASE)
            relative = helper.SOURCE_PATHS.get(item.name, 'tools/staging_automation/' + item.name)
            if helper.checked(item) != git('show', expected + ':' + relative):
                raise ValueError('CONTROLLER_SOURCE_MISMATCH')
        progress('BUNDLE_STAGING')
        for folder in (target, target / 'staging_automation', target / 'pilot-source'):
            helper.directory(folder, gid)
        archive = Path(scratch) / 'source.tar'
        git('archive', '--format=tar', '--output', str(archive), source)
        helper.bundle_sources(archive, target / 'pilot-source', gid)
        for item in members:
            body = git('show', source + ':tools/staging_automation/' + item.name) if item.name in changed else helper.checked(item)
            path = target / 'staging_automation' / item.name
            if os.path.lexists(path) and helper.checked(path) != body:
                raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
            if not os.path.lexists(path):
                helper.atomic(path, body, gid)
        if Path('/sys/fs/selinux/enforce').exists():
            subprocess.run(['/usr/sbin/restorecon','-RF',str(target)], capture_output=True, check=True, timeout=45)
        sys.path.insert(0, str(target))
        from staging_automation import pilot_generation as generation
        progress('ARCHIVE_VERIFICATION')
        for commit,digest in ((generation.ACTIVE_BASELINE_COMMIT,generation.ACTIVE_BASELINE_ARCHIVE),
                              (generation.RC4_PIN['commit'],generation.RC4_PIN['archiveSha256'])):
            fetch_revision(git, commit)
            if hashlib.sha256(git('archive','--format=tar',commit)).hexdigest()!=digest:
                raise ValueError('ARCHIVE_PIN_REJECTED')
        progress('OPERATION_LOCK')
        parent = ROOT / 'worker'
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise ValueError('WORKER_DIRECTORY_REJECTED')
        with os.fdopen(os.open(parent / 'operation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('OPERATION_LOCK_REJECTED')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if require_absent and os.path.lexists(marker):
                raise ValueError('EXISTING_TRANSITION_REQUIRES_REVIEW')
            if os.path.lexists(parent / 'access-revoked'):
                raise ValueError('RECOVERY_REQUIRES_OWNER')
            historical = (('pilot-generation-transition.json', ACTIVE_GENERATION_SOURCE),
                          *predecessor.ANCESTRY, (predecessor.SOURCE_TRANSITION, ACTIVE_PREVIOUS))
            history = {}
            for name,pin in historical:
                raw = helper.checked(CONTROL / name)
                value = json.loads(raw)
                if value.get('phase') != 'COMMITTED' or value.get('source') != pin:
                    raise ValueError('PREVIOUS_GENERATION_CHANGED')
                history[name] = hashlib.sha256(raw).hexdigest()
            incident_path = parent / 'tenant-pilot-cutover-v2.json'
            incident = helper.checked(incident_path, uid, private=True)
            incident_sha = hashlib.sha256(incident).hexdigest()
            paths = {'entry': revisions.parent / 'entry.py', 'policy': CONTROL / 'automation-policy.json',
                     'profile': CONTROL / 'settings/profile.json', 'state': parent / 'automation-state.json'}
            def read(name):
                return helper.checked(paths[name], uid if name in {'profile','state'} else 0, private=name in {'profile','state'})
            progress('TRANSITION_VALIDATION')
            if os.path.lexists(marker):
                record = json.loads(helper.checked(marker))
                if (record.get('source') != source or record.get('phase') not in {'PREPARED','COMMITTED'}
                        or record.get('incidentSha256') != incident_sha or record.get('ancestorSha256') != history):
                    raise ValueError('GENERATION_TRANSACTION_CHANGED')
                if record['phase'] == 'COMMITTED':
                    # The state may have advanced through protected cutover. Never replay a committed enrollment.
                    if any(read(row['name']) != bytes.fromhex(row['after']) for row in record['plan']['files'] if row['name'] != 'state'):
                        raise ValueError('GENERATION_STATE_CHANGED')
                    return {'state':'RC4_ACTIVE_GENERATION_ENROLLED','alreadyEnrolled':True,
                            'controllerCommit':source,'activationAuthorized':False,'productionChanged':False}
                original = {row['name']:bytes.fromhex(row['before']) for row in record['plan']['files']}
            else:
                if os.path.lexists(parent / 'tenant-pilot-cutover-rc4-v3.json'):
                    raise ValueError('GENERATION_ALREADY_IN_USE')
                original = {name:read(name) for name in paths}
                if original['entry'] != predecessor.launcher(previous, revise=True):
                    raise ValueError('CONTROLLER_SOURCE_MISMATCH')
            progress('GENERATION_DOCUMENTS')
            old_policy,old_profile,old_state = (json.loads(original[k]) for k in ('policy','profile','state'))
            if (old_policy.get('controllerCommit') != ACTIVE_PREVIOUS or old_profile.get('hostSourceCommit') != helper.WORKER
                    or old_policy.get('legacyWorkerCommit') != helper.WORKER or 'gamesAudit' in old_policy):
                raise ValueError('PREVIOUS_GENERATION_CHANGED')
            # Do not replace the separately reviewed acceptance collector source.
            for name in predecessor.FILES:
                prior = helper.checked(previous / 'pilot-source' / name)
                if hashlib.sha256(prior).hexdigest() != old_policy['tenantAcceptance']['sourceSha256'][name] or helper.checked(target / 'pilot-source' / name) != prior:
                    raise ValueError('CONTROLLER_SOURCE_MISMATCH')
            policy,profile,state = generation.active_upgrade_documents(old_policy,old_profile,old_state,json.loads(incident),
                source=source,preparation_digest=workflow_hashes[ACTIVE_WORKFLOWS[0]],cutover_digest=workflow_hashes[ACTIVE_WORKFLOWS[1]])
            desired = {'entry':active_gate(target,history),'policy':generation.encode(policy),
                       'profile':generation.encode(profile),'state':generation.encode(state)}
            plan = {'files':[{'name':name,'before':original[name].hex(),'after':desired[name].hex()}
                             for name in ('entry','profile','state','policy')]}
            expected = {'source':source,'phase':'PREPARED','incidentSha256':incident_sha,
                        'ancestorSha256':history,'plan':plan}
            if os.path.lexists(marker) and record != expected:
                raise ValueError('GENERATION_TRANSACTION_CHANGED')
            if any(read(row['name']) not in (bytes.fromhex(row['before']),bytes.fromhex(row['after'])) for row in plan['files']):
                raise ValueError('GENERATION_STATE_CHANGED')
            progress('BASELINE_PROBE')
            probe = ['/usr/sbin/runuser','-u',account.pw_name,'--','/usr/bin/env','-i',
                     'PATH=/usr/local/bin:/usr/bin:/bin','HOME='+account.pw_dir,
                     'XDG_RUNTIME_DIR=/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus',
                     'systemd-run','--user','--pipe','--wait','--collect','--quiet',
                     '--unit=gamerhq-staging-automation-rc4-enrollment-'+uuid.uuid4().hex,
                     '--property=NoNewPrivileges=yes','--property=ProtectSystem=strict','--property=ProtectHome=yes',
                     '--property=ReadWritePaths='+str(ROOT/'worker')+' '+str(ROOT/'backups'),
                     '--property=RestrictSUIDSGID=yes','--property=RuntimeMaxSec=300s',
                     str(ROOT/'deployment-worker/.venv/bin/python'),'-I','-B','-c',ACTIVE_PROBE,str(target)]
            proof = subprocess.run(probe,input=json.dumps({'profile':generation.effective_profile(profile,policy),'policy':policy}),
                                   capture_output=True,text=True,check=True,timeout=330)
            if len(proof.stdout)>4096 or json.loads(proof.stdout)!={'state':'ACTIVE_PILOT_VERIFIED','admissionPreserved':True,
                                                                   'hostCommit':generation.ACTIVE_BASELINE_COMMIT}:
                raise ValueError('PILOT_CHECKPOINT_REJECTED')
            progress('TRANSITION_PREPARE')
            if len(generation.encode(expected)) > 262144:
                raise ValueError('SOURCE_LIMIT')
            replace(marker,generation.encode(expected),0,gid,0o640)
            checkpoint('PREPARED')
            def write(name,body):
                replace(paths[name],body,uid if name in {'profile','state'} else 0,gid,0o600 if name in {'profile','state'} else 0o640)
                checkpoint(name)
            progress('TRANSITION_APPLY')
            transition_digest = generation.transition(plan,read,write)
            if (helper.checked(incident_path,uid,private=True)!=incident or
                    any(hashlib.sha256(helper.checked(CONTROL/name)).hexdigest()!=digest for name,digest in history.items())):
                raise ValueError('INCIDENT_HISTORY_CHANGED')
            progress('TRANSITION_COMMIT')
            replace(marker,generation.encode({**expected,'phase':'COMMITTED'}),0,gid,0o640)
            checkpoint('COMMITTED')
    return {'state':'RC4_ACTIVE_GENERATION_ENROLLED','controllerCommit':source,'generation':generation.ACTIVE_GENERATION['id'],
            'transitionSha256':transition_digest,'incidentPreserved':True,'activationAuthorized':False,
            'gatewayChanged':False,'databaseChanged':False,'discordResourcesChanged':False,'productionChanged':False}


def main():
    _stage_trace.clear()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--approval', required=True)
    parser.add_argument('--require-absent-transition', action='store_true',
                        help='Fresh enrollment only; fail closed if a marker exists at admission or under the lock.')
    parser.add_argument('--active-upgrade', action='store_true', help='Enroll the exact active RC4 upgrade epoch.')
    args = parser.parse_args()
    try:
        options = {'require_absent': True} if args.require_absent_transition else {}
        operation = enroll_active if args.active_upgrade else enroll
        receipt = operation(args.commit, args.approval, **options)
        print(json.dumps(receipt))
        if receipt.get('state') != ('RC4_ACTIVE_GENERATION_ENROLLED' if args.active_upgrade else 'RECOVERED_PRIMARY_GENERATION_ENROLLED'):
            raise SystemExit(1)
    except Exception as error:
        print(json.dumps(rejection(error)))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
