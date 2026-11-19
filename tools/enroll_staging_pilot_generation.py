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
APPROVAL = 'ENROLL RECOVERED PRIMARY ONLY PILOT GENERATION'
MANIFEST = 'deploy/staging/tenant-pilot-generation.json'
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'


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
from tools.staging_dispatch import environment
from tools.staging_tenant_rehearsal import rehearsal_binding,verify_activation_checkpoint
from tools.staging_automation.host_promotion import require_baseline_skill,require_runtime_lifecycle,retained_state
packet=json.load(sys.stdin)
profile,policy=packet['profile'],packet['policy']
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


def enroll(source, approval):
    import fcntl
    import pwd

    if os.geteuid() != 0 or approval != APPROVAL:
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    if not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    revisions = ROOT / 'automation-controller/revisions'
    target, previous = revisions / source, revisions / PREVIOUS
    transaction_path = CONTROL / 'pilot-generation-transition.json'
    with tempfile.TemporaryDirectory(prefix='gamerhq-pilot-generation-') as scratch:
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*args):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *args], env=env,
                                  check=True, capture_output=True, timeout=180).stdout
        git('init', '--quiet')
        for revision in (source, PREVIOUS, 'refs/heads/main'):
            git('fetch', '--quiet', '--depth=1', REPOSITORY, revision)
        if git('show', source + ':tools/enroll_staging_pilot_generation.py') != Path(__file__).read_bytes():
            raise ValueError('INSTALLER_SOURCE_MISMATCH')
        manifest = json.loads(git('show', source + ':' + MANIFEST))
        main = git('rev-parse', 'FETCH_HEAD').strip().decode()
        for name, pin in manifest['workflows'].items():
            body = git('show', main + ':.github/workflows/' + name)
            if hashlib.sha256(body).hexdigest() != pin['sha256'] or ('ref: ' + pin['client'] + '\n').encode() not in body:
                raise ValueError('WORKFLOW_REGISTRATION_REQUIRED')
        for pin in (manifest['candidate'], manifest['baseline']):
            git('fetch', '--quiet', '--depth=1', REPOSITORY, pin['commit'])
            if hashlib.sha256(git('archive', '--format=tar', pin['commit'])).hexdigest() != pin['archiveSha256']:
                raise ValueError('ARCHIVE_PIN_REJECTED')
        module = types.ModuleType('verified_enrollment')
        module.__file__ = str(Path(scratch) / 'verified_enrollment.py')
        exec(compile(git('show', source + ':tools/enroll_staging_tenant_preview.py'), module.__file__, 'exec'), module.__dict__)
        for folder in (ROOT, CONTROL, revisions.parent, revisions, previous):
            module.directory(folder)
        if git('-c', 'safe.directory=' + str(ROOT / 'deployment-worker'), '-C', str(ROOT / 'deployment-worker'),
               'rev-parse', 'HEAD').strip().decode() != module.WORKER:
            raise ValueError('LEGACY_WORKER_MISMATCH')
        # Verify the actually enrolled mixed immutable bundle, not just its Git label.
        overlays = module.OVERLAY | {'pilot_activation.py', 'pilot_cutover.py', 'recovery_api_repair.py'}
        for member in (previous / 'staging_automation').iterdir():
            relative = module.SOURCE_PATHS.get(member.name, 'tools/staging_automation/' + member.name)
            expected = PREVIOUS if member.name in overlays else module.PREVIOUS
            if module.checked(member) != git('show', expected + ':' + relative):
                raise ValueError('CONTROLLER_SOURCE_MISMATCH')
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
        if manifest['baseline'] != {'commit': generation.BASELINE_COMMIT, 'archiveSha256': generation.BASELINE_ARCHIVE,
                                    'image': generation.BASELINE_IMAGE, 'ciRun': manifest['baseline']['ciRun']}:
            raise ValueError('BASELINE_PIN_REJECTED')
        if json.loads(git('show', source + ':deploy/staging/tenant-pilot-binding.json')) != generation.COHORT:
            raise ValueError('COHORT_PIN_REJECTED')
        parent = ROOT / 'worker'
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise ValueError('WORKER_DIRECTORY_REJECTED')
        with os.fdopen(os.open(parent / 'operation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('OPERATION_LOCK_REJECTED')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.path.lexists(parent / 'access-revoked') or os.path.lexists(parent / 'tenant-pilot-cutover-v2.json'):
                raise ValueError('GENERATION_ALREADY_IN_USE')
            paths = {'entry': revisions.parent / 'entry.py', 'policy': CONTROL / 'automation-policy.json',
                     'profile': CONTROL / 'settings/profile.json', 'state': parent / 'automation-state.json'}
            def read(name):
                return module.checked(paths[name], uid if name in {'profile', 'state'} else 0,
                                      private=name in {'profile', 'state'})
            original_incident = module.checked(parent / 'tenant-pilot-cutover.json', uid, private=True)
            if transaction_path.exists():
                record = json.loads(module.checked(transaction_path))
                if record.get('source') != source or record.get('incidentSha256') != hashlib.sha256(original_incident).hexdigest():
                    raise ValueError('GENERATION_TRANSACTION_CHANGED')
                plan = record['plan']
                original = {item['name']: bytes.fromhex(item['before']) for item in plan['files']}
                if record.get('phase') not in {'PREPARED', 'COMMITTED'}:
                    raise ValueError('GENERATION_TRANSACTION_CHANGED')
                if any(read(item['name']) not in ((bytes.fromhex(item['after']),) if record['phase'] == 'COMMITTED'
                                                 else (bytes.fromhex(item['before']), bytes.fromhex(item['after'])))
                       for item in plan['files'] if record['phase'] != 'COMMITTED' or item['name'] != 'state'):
                    raise ValueError('GENERATION_STATE_CHANGED')
                if record['phase'] == 'COMMITTED':
                    current = json.loads(read('state'))
                    if (current.get('pilotGeneration') != generation.GENERATION['id']
                            or current.get('approvedImage') != generation.BASELINE_IMAGE
                            or current.get('recoveryRequired') is not False):
                        raise ValueError('GENERATION_STATE_CHANGED')
            else:
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
                candidate = {**manifest['candidate'], 'baselineCiRun': manifest['baseline']['ciRun']}
                new_policy, new_profile, new_state = generation.generation_documents(
                    old_policy, old_profile, old_state, json.loads(original_incident), source=source, candidate=candidate,
                    cutover_digest=manifest['workflows']['staging-tenant-pilot-cutover.yml']['sha256'],
                    preparation_digest=manifest['workflows']['staging-tenant-pilot-activation-prepare.yml']['sha256'])
                from staging_automation.policy import validate_policy
                validate_policy(new_policy)
                desired = {'entry': gate(target), 'policy': generation.encode(new_policy),
                           'profile': generation.encode(new_profile), 'state': generation.encode(new_state)}
                # Entry goes first: after any state/policy write, old code cannot run.
                plan = {'files': [{'name': name, 'before': original[name].hex(), 'after': desired[name].hex()}
                                  for name in ('entry', 'profile', 'state', 'policy')]}
                record = {'phase': 'PREPARED', 'source': source, 'plan': plan,
                          'incidentSha256': hashlib.sha256(original_incident).hexdigest()}
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
            result = subprocess.run(command, input=json.dumps({'profile': probe_profile, 'policy': probe_policy,
                                      'image': generation.BASELINE_IMAGE}), text=True, capture_output=True, check=True, timeout=330)
            if len(result.stdout) > 16384:
                raise ValueError('PROBE_OUTPUT_LIMIT')
            proof = json.loads(result.stdout)
            if proof.get('state') != 'GENERATION_BASELINE_VERIFIED':
                return {'state': 'GENERATION_PREFLIGHT_REJECTED', 'diagnostic': proof,
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
                        'activationAuthorized': False, 'gatewayChanged': False, 'databaseChanged': False,
                        'productionChanged': False, 'verification': proof}
            archive_path = CONTROL / 'pilot-generation-incident.json'
            historical = generation.encode({'incidentHex': original_incident.hex(),
                                              'policyHex': original['policy'].hex(), 'profileHex': original['profile'].hex(),
                                              'stateHex': original['state'].hex()})
            if archive_path.exists() and module.checked(archive_path) != historical:
                raise ValueError('INCIDENT_HISTORY_CHANGED')
            replace(archive_path, historical, 0, gid, 0o640)
            replace(transaction_path, generation.encode(record), 0, gid, 0o640)
            def write(name, body):
                replace(paths[name], body, uid if name in {'state', 'profile'} else 0, gid,
                        0o600 if name in {'state', 'profile'} else 0o640)
            transition_digest = generation.transition(plan, read, write)
            if module.checked(parent / 'tenant-pilot-cutover.json', uid, private=True) != original_incident:
                raise ValueError('INCIDENT_HISTORY_CHANGED')
            replace(transaction_path, generation.encode({**record, 'phase': 'COMMITTED', 'verification': proof}), 0, gid, 0o640)
    return {'state': 'RECOVERED_PRIMARY_GENERATION_ENROLLED', 'controllerCommit': source,
            'baselineHostCommit': generation.BASELINE_COMMIT, 'baselineImage': generation.BASELINE_IMAGE,
            'generation': generation.GENERATION['id'], 'transitionSha256': transition_digest,
            'incidentSha256': record['incidentSha256'], 'incidentPreserved': True, 'recoveryRequired': False,
            'activationAuthorized': False, 'gatewayChanged': False, 'databaseChanged': False,
            'discordResourcesChanged': False, 'productionChanged': False, 'verification': proof}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try:
        receipt = enroll(args.commit, args.approval)
        print(json.dumps(receipt))
        if receipt.get('state') != 'RECOVERED_PRIMARY_GENERATION_ENROLLED':
            raise SystemExit(1)
    except (ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit('Pilot generation enrollment rejected; no activation authorized. Retain the protected transition for reviewed retry.') from None


if __name__ == '__main__':
    main()
