"""Owner-reviewed additive read-only acceptance enrollment; system Python 3.9.

Preserves the committed generation, active cutover journal, private state,
credentials, database files, container and SSH/sandbox policy byte-for-byte.
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
import types

sys.dont_write_bytecode = True
ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
PREVIOUS = 'eff7cc14d2af0595ad7e80f99f0f24a7e4595bb9'
LEGACY = '876805290ef454523f9b36f1f9fbf90c3ec64a17'
APPROVAL = 'ENROLL READ ONLY TENANT ACCEPTANCE'
REVISION_PREVIOUS = 'd1eed91e3e1608176704407843243afbefc8fd7c'
REVISION_APPROVAL = 'REVISE READ ONLY TENANT ACCEPTANCE SOURCE'
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
WORKFLOW = '.github/workflows/staging-tenant-acceptance.yml'
FILES = ('services/tenant_acceptance.py', 'tools/tenant_acceptance_collect.py')
STAGE = 'OWNER_CHECK'
PROBE = """
import json,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/pilot-source','/opt/gamerhq-staging/deployment-worker']
from staging_automation.backend import HostBackend
from tools.staging_dispatch import environment
import os
values=environment();os.environ.clear();os.environ.update(values)
packet=json.load(sys.stdin)
b=HostBackend(packet['profile'],packet['policy']); c=b.inspect()
if c['Image']!=packet['image'] or c['Config']['Labels'].get('org.opencontainers.image.revision')!=packet['commit'] or b.health()['state']!='HOST_READY':
 raise ValueError('ACTIVE_PILOT_REQUIRED')
print(json.dumps({'state':'ACTIVE_PILOT_VERIFIED'}))
"""


def launcher(target, *, revise=False):
    body = ("import json,sys\nfrom pathlib import Path\n"
            "try:\n g=json.loads(Path(" + repr(str(CONTROL / 'pilot-generation-transition.json')) + ").read_bytes())\n"
            " a=json.loads(Path(" + repr(str(CONTROL / 'tenant-acceptance-transition.json')) + ").read_bytes())\n"
            "except Exception:\n raise SystemExit('ACCEPTANCE_ENROLLMENT_HELD')\n"
            "if g.get('phase')!='COMMITTED' or g.get('source')!=" + repr(PREVIOUS) + ": raise SystemExit('ACCEPTANCE_ENROLLMENT_HELD')\n"
            "if a.get('phase')!='COMMITTED' or a.get('source')!=" + repr(target.name) + ": raise SystemExit('ACCEPTANCE_ENROLLMENT_HELD')\n"
            "sys.path[:0]=" + repr([str(target), str(target / 'pilot-source'), str(ROOT / 'deployment-worker')]) + "\n"
            "from staging_automation.bridge import entry\nentry()\n").encode()
    if revise:
        original = CONTROL / 'tenant-acceptance-transition.json'
        replacement = CONTROL / 'tenant-acceptance-source-transition.json'
        body = body.replace(repr(str(original)).encode(), repr(str(replacement)).encode())
        guard = ("import json\nfrom pathlib import Path\ntry:\n p=json.loads(Path(" + repr(str(original)) + ").read_bytes())\n"
                 "except Exception:\n raise SystemExit('ACCEPTANCE_ENROLLMENT_HELD')\n"
                 "if p.get('phase')!='COMMITTED' or p.get('source')!=" + repr(REVISION_PREVIOUS) + ": raise SystemExit('ACCEPTANCE_ENROLLMENT_HELD')\n")
        body = guard.encode() + body
    return body


def enroll(source, approval, *, checkpoint=lambda _: None, revise=False):
    global STAGE
    STAGE = 'OWNER_CHECK'
    import fcntl
    import pwd
    if os.geteuid() != 0 or approval != (REVISION_APPROVAL if revise else APPROVAL) or not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    with tempfile.TemporaryDirectory(prefix='gamerhq-acceptance-enrollment-') as scratch:
        STAGE = 'SOURCE_VERIFICATION'
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*args):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *args], env=env,
                                  capture_output=True, check=True, timeout=180).stdout
        git('init', '--quiet')
        fetched = {}
        for pin in dict.fromkeys((source, PREVIOUS, LEGACY, *((REVISION_PREVIOUS,) if revise else ()), 'refs/heads/main', 'refs/heads/develop')):
            git('fetch', '--quiet', '--depth=1', REPOSITORY, pin)
            actual = git('rev-parse', '--verify', 'FETCH_HEAD^{commit}').strip().decode('ascii')
            if not re.fullmatch('[0-9a-f]{40}', actual) or not pin.startswith('refs/') and actual != pin:
                raise ValueError('SOURCE_REJECTED')
            fetched[pin] = actual
        if git('show', source + ':tools/enroll_staging_tenant_acceptance.py') != Path(__file__).read_bytes():
            raise ValueError('INSTALLER_SOURCE_REJECTED')
        workflow = git('show', source + ':' + WORKFLOW)
        for branch in ('refs/heads/main', 'refs/heads/develop'):
            if git('show', fetched[branch] + ':' + WORKFLOW) != workflow:
                raise ValueError('WORKFLOW_REGISTRATION_REQUIRED')
        client = re.search(rb'          ref: ([0-9a-f]{40})\n', workflow)
        if client is None:
            raise ValueError('WORKFLOW_REGISTRATION_REQUIRED')
        client = client.group(1).decode()
        git('fetch', '--quiet', '--depth=1', REPOSITORY, client)
        if git('rev-parse', '--verify', 'FETCH_HEAD^{commit}').strip().decode() != client:
            raise ValueError('SOURCE_REJECTED')
        for path in ('tools/staging_automation_client.py', 'tools/staging_automation/tenant_acceptance.py', 'tools/tenant_acceptance_report.py'):
            if git('show', client + ':' + path) != git('show', source + ':' + path):
                raise ValueError('SOURCE_REJECTED')
        def module(path, name):
            result = types.ModuleType(name)
            result.__file__ = str(Path(scratch) / (name + '.py'))
            exec(compile(git('show', source + ':' + path), result.__file__, 'exec'), result.__dict__)
            return result
        helper = module('tools/enroll_staging_tenant_preview.py', 'verified_helper')
        generation = module('tools/enroll_staging_pilot_generation.py', 'verified_generation')
        if helper.PREVIOUS != LEGACY or generation.PREVIOUS != '8d8e067463123f0b847d9daf7d5dd70a854a5930':
            raise ValueError('SOURCE_REJECTED')
        revisions = ROOT / 'automation-controller/revisions'
        installed = REVISION_PREVIOUS if revise else PREVIOUS
        previous, target = revisions / installed, revisions / source
        STAGE = 'INSTALLED_SOURCE_VERIFICATION'
        for folder in (ROOT, CONTROL, revisions.parent, revisions, previous):
            helper.directory(folder)
        worker = ROOT / 'deployment-worker'
        if git('-c', 'safe.directory=' + str(worker), '-C', str(worker), 'rev-parse', 'HEAD').strip().decode() != helper.WORKER:
            raise ValueError('SOURCE_REJECTED')
        overlays = helper.OVERLAY | {'pilot_activation.py', 'pilot_cutover.py', 'recovery_api_repair.py', 'pilot_generation.py'}
        for item in (previous / 'staging_automation').iterdir():
            expected = PREVIOUS if item.name in overlays else LEGACY
            if revise and item.name in {'bridge.py','policy.py','oidc.py','tenant_acceptance.py'}:
                expected = REVISION_PREVIOUS
            name = helper.SOURCE_PATHS.get(item.name, 'tools/staging_automation/' + item.name)
            if helper.checked(item) != git('show', expected + ':' + name):
                raise ValueError('CONTROLLER_SOURCE_MISMATCH')
        for folder in (target, target / 'staging_automation', target / 'pilot-source'):
            helper.directory(folder, gid)
        archive = Path(scratch) / 'source.tar'
        git('archive', '--format=tar', '--output', str(archive), source)
        helper.bundle_sources(archive, target / 'pilot-source', gid)
        changed = set() if revise else {'bridge.py', 'policy.py', 'oidc.py'}
        for item in (previous / 'staging_automation').iterdir():
            content = git('show', source + ':tools/staging_automation/' + item.name) if item.name in changed else helper.checked(item)
            file = target / 'staging_automation' / item.name
            if file.exists() and helper.checked(file) != content:
                raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
            helper.atomic(file, content, gid)
        new_file = target / 'staging_automation/tenant_acceptance.py'
        new_body = git('show', source + ':tools/staging_automation/tenant_acceptance.py')
        if new_file.exists() and helper.checked(new_file) != new_body:
            raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
        helper.atomic(new_file, new_body, gid)
        if Path('/sys/fs/selinux/enforce').exists():
            subprocess.run(['/usr/sbin/restorecon', '-RF', str(target)], capture_output=True, check=True, timeout=45)
        lock_path = ROOT / 'worker/operation.lock'
        STAGE = 'OPERATION_LOCK'
        helper.checked(lock_path, uid, private=True)
        with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('LOCK_REJECTED')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.path.lexists(ROOT / 'worker/access-revoked'):
                raise ValueError('ACCESS_REVOKED')
            generation_bytes = helper.checked(CONTROL / 'pilot-generation-transition.json')
            old_generation = json.loads(generation_bytes)
            if old_generation.get('phase') != 'COMMITTED' or old_generation.get('source') != PREVIOUS:
                raise ValueError('GENERATION_REJECTED')
            acceptance_bytes = None
            if revise:
                acceptance_bytes = helper.checked(CONTROL / 'tenant-acceptance-transition.json')
                accepted = json.loads(acceptance_bytes)
                if accepted.get('phase') != 'COMMITTED' or accepted.get('source') != REVISION_PREVIOUS:
                    raise ValueError('TRANSITION_REJECTED')
            incident = helper.checked(ROOT / 'worker/tenant-pilot-cutover-v2.json', uid, private=True)
            state_bytes = helper.checked(ROOT / 'worker/automation-state.json', uid, private=True)
            state = json.loads(state_bytes)
            journal = json.loads(incident)
            if (journal.get('phase') != 'PILOT_ACTIVE' or state.get('recoveryRequired') is not False
                    or state.get('enabled') is not True or state.get('bootstrapVerified') is not True):
                raise ValueError('ACTIVE_PILOT_REQUIRED')
            prepared = state.get('tenantPilotActivationPrepared', {})
            expected_binding = {'preparedSha256': hashlib.sha256(json.dumps(prepared, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                                'baselineImage': state.get('approvedImage'), 'candidateImage': prepared.get('candidateImage'),
                                'fallbackImage': prepared.get('fallbackImage'), 'generation': 'recovered-primary-v2'}
            if journal.get('binding') != expected_binding:
                raise ValueError('ACTIVE_PILOT_REQUIRED')
            paths = {'entry': revisions.parent / 'entry.py', 'policy': CONTROL / 'automation-policy.json'}
            def read(name): return helper.checked(paths[name])
            marker = CONTROL / ('tenant-acceptance-source-transition.json' if revise else 'tenant-acceptance-transition.json')
            STAGE = 'TRANSITION_VERIFICATION'
            if marker.exists():
                record = json.loads(helper.checked(marker))
                if record.get('source') != source or record.get('incidentSha256') != hashlib.sha256(incident).hexdigest() or record.get('phase') not in {'PREPARED', 'COMMITTED'}:
                    raise ValueError('TRANSITION_REJECTED')
                if revise and record.get('parentTransitionSha256') != hashlib.sha256(acceptance_bytes).hexdigest():
                    raise ValueError('TRANSITION_REJECTED')
            else:
                policy = json.loads(read('policy'))
                if (policy.get('controllerCommit') != installed or (not revise and 'tenantAcceptance' in policy)
                        or policy.get('tenantPilotGeneration', {}).get('id') != 'recovered-primary-v2'
                        or read('entry') != (launcher(previous) if revise else generation.gate(previous))):
                    raise ValueError('PREVIOUS_POLICY_REJECTED')
                if revise:
                    previous_pin = {'workflowSha256': hashlib.sha256(workflow).hexdigest(), 'sourceSha256': {}}
                    for path in FILES:
                        expected_body = git('show', REVISION_PREVIOUS + ':' + path)
                        if helper.checked(previous / 'pilot-source' / path) != expected_body:
                            raise ValueError('CONTROLLER_SOURCE_MISMATCH')
                        previous_pin['sourceSha256'][path] = hashlib.sha256(expected_body).hexdigest()
                    if policy.get('tenantAcceptance') != previous_pin:
                        raise ValueError('PREVIOUS_POLICY_REJECTED')
                pin = {'workflowSha256': hashlib.sha256(workflow).hexdigest(),
                       'sourceSha256': {path: hashlib.sha256(git('show', source + ':' + path)).hexdigest() for path in FILES}}
                after = {'entry': launcher(target, revise=revise), 'policy': (json.dumps({**policy, 'controllerCommit': source,
                                                                         'tenantAcceptance': pin}, sort_keys=True, indent=2) + '\n').encode()}
                record = {'source': source, 'phase': 'PREPARED', 'incidentSha256': hashlib.sha256(incident).hexdigest(),
                          'plan': {'files': [{'name': name, 'before': read(name).hex(), 'after': after[name].hex()} for name in ('entry', 'policy')]}}
                if revise:
                    record['parentTransitionSha256'] = hashlib.sha256(acceptance_bytes).hexdigest()
            if len(record['plan']['files']) != 2 or {row['name'] for row in record['plan']['files']} != set(paths):
                raise ValueError('TRANSITION_REJECTED')
            original = json.loads(bytes.fromhex(next(row['before'] for row in record['plan']['files'] if row['name'] == 'policy')))
            profile = json.loads(helper.checked(CONTROL / 'settings/profile.json', uid, private=True))
            binding = original['tenantPilot']
            if (binding != {'applicationId': '1567098530525351976', 'guildIds': ['1567098041641738331', '1571908030532948129']}
                    or profile.get('stagingBotId') != binding['applicationId'] or profile.get('stagingGuildId') != binding['guildIds'][0]
                    or profile.get('environment') != 'staging' or profile.get('productionGuildId') in binding['guildIds']
                    or profile.get('productionBotId') == binding['applicationId']
                    or profile.get('hostSourceCommit') != helper.WORKER or original.get('legacyWorkerCommit') != helper.WORKER
                    or original['tenantPilotActivation']['commit'] != '1899c6f8cc498fefa0510d0e3f70453f6f1aa082'):
                raise ValueError('ACTIVE_PILOT_REQUIRED')
            if record['phase'] == 'COMMITTED':
                selected = target
            else:
                selected = previous
            profile = {**profile, 'pilotGeneration': 'recovered-primary-v2'}
            STAGE = 'ACTIVE_PILOT_PROBE'
            command = ['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i',
                       'PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=' + account.pw_dir,
                       'XDG_RUNTIME_DIR=/run/user/' + str(uid), 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(uid) + '/bus',
                       'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet',
                       '--unit=gamerhq-staging-automation-acceptance-enrollment', '--property=NoNewPrivileges=yes',
                       '--property=ProtectSystem=strict', '--property=ProtectHome=yes',
                       '--property=ReadWritePaths=' + str(ROOT / 'worker') + ' ' + str(ROOT / 'backups'),
                       '--property=RestrictSUIDSGID=yes', '--property=RuntimeMaxSec=300s',
                       str(ROOT / 'deployment-worker/.venv/bin/python'), '-I', '-B', '-c', PROBE, str(selected)]
            proof = subprocess.run(command, input=json.dumps({'profile': profile, 'policy': original,
                                    'image': state['tenantPilotActivationPrepared']['candidateImage'],
                                    'commit': original['tenantPilotActivation']['commit']}),
                                    text=True, capture_output=True, check=True, timeout=330)
            if len(proof.stdout) > 4096 or json.loads(proof.stdout) != {'state': 'ACTIVE_PILOT_VERIFIED'}:
                raise ValueError('ACTIVE_PILOT_REQUIRED')
            STAGE = 'TRANSITION_APPLY'
            if record['phase'] == 'COMMITTED':
                if any(read(row['name']) != bytes.fromhex(row['after']) for row in record['plan']['files']):
                    raise ValueError('TRANSITION_REJECTED')
            else:
                # Reuse the original fsync/owner/SELinux-preserving writer and
                # reviewed known-before/after transition algorithm.
                sys.path.insert(0, str(target))
                from staging_automation.pilot_generation import transition
                generation.replace(marker, (json.dumps(record, sort_keys=True) + '\n').encode(), 0, gid, 0o640)
                checkpoint('PREPARED')
                def write(name, value):
                    generation.replace(paths[name], value, 0, gid, 0o640)
                    checkpoint(name)
                transition(record['plan'], read, write)
                if (helper.checked(ROOT / 'worker/tenant-pilot-cutover-v2.json', uid, private=True) != incident
                        or helper.checked(ROOT / 'worker/automation-state.json', uid, private=True) != state_bytes
                        or helper.checked(CONTROL / 'pilot-generation-transition.json') != generation_bytes):
                    raise ValueError('INCIDENT_CHANGED')
                if revise and helper.checked(CONTROL / 'tenant-acceptance-transition.json') != acceptance_bytes:
                    raise ValueError('INCIDENT_CHANGED')
                generation.replace(marker, (json.dumps({**record, 'phase': 'COMMITTED'}, sort_keys=True) + '\n').encode(), 0, gid, 0o640)
                checkpoint('COMMITTED')
    return {'state': 'TENANT_ACCEPTANCE_SOURCE_REVISED' if revise else 'TENANT_ACCEPTANCE_ENROLLED', 'controllerCommit': source,
            'recoveryRequired': False, 'activePilotPreserved': True, 'gatewayChanged': False,
            'databaseChanged': False, 'discordResourcesChanged': False, 'productionChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--approval', required=True)
    parser.add_argument('--revise', action='store_true', help='Owner-reviewed source-only revision of exact d1eed91 acceptance enrollment')
    args = parser.parse_args()
    try:
        print(json.dumps(enroll(args.commit, args.approval, revise=args.revise)))
    except Exception as error:
        known = {'OWNER_APPROVAL_REQUIRED','SOURCE_REJECTED','INSTALLER_SOURCE_REJECTED','WORKFLOW_REGISTRATION_REQUIRED',
                 'CONTROLLER_SOURCE_MISMATCH','IMMUTABLE_EXTENSION_CHANGED','LOCK_REJECTED','ACCESS_REVOKED',
                 'GENERATION_REJECTED','ACTIVE_PILOT_REQUIRED','TRANSITION_REJECTED','PREVIOUS_POLICY_REJECTED','INCIDENT_CHANGED',
                 'PROTECTED_FILE_REQUIRED','PROTECTED_DIRECTORY_REQUIRED','IMMUTABLE_RELEASE_CHANGED'}
        reason = error.args[0] if error.args and isinstance(error.args[0], str) and error.args[0] in known else 'VALIDATION_REJECTED'
        evidence = {}
        if isinstance(error, subprocess.CalledProcessError):
            reason, evidence = 'SUBPROCESS_REJECTED', {'exitCode': error.returncode}
        elif isinstance(error, OSError):
            reason, evidence = 'FILESYSTEM_REJECTED', {'errno': error.errno}
        print(json.dumps({'state': 'TENANT_ACCEPTANCE_ENROLLMENT_REJECTED', 'stage': STAGE, 'reason': reason, **evidence,
                          'activationAuthorized': False, 'productionChanged': False}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
