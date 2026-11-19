"""Read-only owner receipt for a rejected pilot-generation enrollment. Python 3.9.

Never calls enrollment, writes protected files, probes write access, opens SQLite
or credential files, starts containers, or changes a controller/recovery latch.
Git evidence uses an ephemeral private directory only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
SOURCE = 'b808b5b66f6c6f8b511640adeade8c4e114559e9'
PREVIOUS = '8d8e067463123f0b847d9daf7d5dd70a854a5930'
ORIGINAL = '876805290ef454523f9b36f1f9fbf90c3ec64a17'
WORKER = '92db47042d58055cb0debdfd83f11f8508e19809'
BASELINE = '8f450533ec0c9589b6fa375e34bd951f91dff419'
IMAGE = 'sha256:d308a43d80737c26b92a3aea2ef9d822f7aa2987fd701a56e354df722672818b'
COHORT = {'applicationId': '1567098530525351976', 'guildIds': ['1567098041641738331', '1571908030532948129']}
OVERLAYS = {'backend.py', 'bridge.py', 'oidc.py', 'policy.py', 'pilot_activation.py', 'pilot_cutover.py', 'recovery_api_repair.py'}
ALIASES = {'deployment.py': 'hosts/gamerhq/skill_update_controller.py',
           'catalog_import.py': 'tools/game_catalog_import.py', 'catalog_manifest.json': 'data/game_catalog_expansion.json'}


def sha(body):
    return hashlib.sha256(body).hexdigest()


def identity(value, size=40):
    return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) else 'UNVERIFIED'


def image(value):
    return value if isinstance(value, str) and re.fullmatch('sha256:[0-9a-f]{64}', value) else 'UNVERIFIED'


def directory(path, uid):
    try:
        info = path.lstat()
        return {'uid': info.st_uid, 'gid': info.st_gid, 'mode': format(stat.S_IMODE(info.st_mode), '04o'),
                'contractPass': stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022}
    except FileNotFoundError:
        return {'state': 'ABSENT'}
    except OSError as error:
        return {'state': 'UNAVAILABLE', 'errno': error.errno}


def read(path, uid, *, private=False):
    """Observe metadata first; never read contents through an unsafe contract."""
    metadata = {'state': 'UNAVAILABLE'}
    try:
        info = path.lstat()
        metadata = {'state': 'UNSAFE', 'uid': info.st_uid, 'gid': info.st_gid,
                    'mode': format(stat.S_IMODE(info.st_mode), '04o'), 'size': info.st_size,
                    'regular': stat.S_ISREG(info.st_mode), 'links': info.st_nlink}
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & (0o077 if private else 0o022) or info.st_size > 262144):
            return metadata, None
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)), 'rb') as stream:
            actual = os.fstat(stream.fileno())
            if not os.path.samestat(info, actual):
                return metadata, None
            body = stream.read(262145)
        if len(body) != info.st_size:
            return metadata, None
        metadata.update(state='VERIFIED', sha256=sha(body))
        return metadata, body
    except FileNotFoundError:
        return {'state': 'ABSENT'}, None
    except OSError as error:
        return {**metadata, 'errno': error.errno}, None


def document(body):
    try:
        value = json.loads(body) if body is not None else None
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def transition_report(body, current):
    record = document(body)
    if not record:
        return {'phase': 'UNAVAILABLE'}
    phase = record.get('phase')
    result = {'phase': phase if phase in {'PREPARED', 'COMMITTED'} else 'UNKNOWN',
              'source': identity(record.get('source')), 'sourceMatchesExpected': record.get('source') == SOURCE,
              'incidentSha256': identity(record.get('incidentSha256'), 64)}
    plan = record.get('plan', {}).get('files') if isinstance(record.get('plan'), dict) else None
    if (not isinstance(plan, list) or len(plan) != 4 or any(not isinstance(row, dict) for row in plan)
            or [row.get('name') for row in plan] != ['entry', 'profile', 'state', 'policy']):
        return {**result, 'planState': 'REJECTED'}
    states = {}
    state_audit = {}
    for row in plan:
        try:
            before, after = bytes.fromhex(row['before']), bytes.fromhex(row['after'])
        except (ValueError, TypeError, KeyError):
            return {**result, 'planState': 'REJECTED'}
        actual = current.get(row['name'])
        states[row['name']] = {'beforeSha256': sha(before), 'afterSha256': sha(after),
                              'currentSha256': sha(actual) if actual is not None else None,
                              'state': ('UNAVAILABLE' if actual is None else 'BOTH' if actual == before == after else
                                        'BEFORE' if actual == before else 'AFTER' if actual == after else 'UNKNOWN')}
        if row['name'] == 'state':
            volatile = {'events', 'lastNow', 'challenge', 'usedTokens'}
            values = [document(value) for value in (before, after, actual)]
            if all(values):
                stripped = [{key: value for key, value in item.items() if key not in volatile} for item in values]
                state_audit = {'matchesBeforeExceptRequestAudit': stripped[0] == stripped[2],
                               'matchesAfterExceptRequestAudit': stripped[1] == stripped[2]}
    recognized = all(row['state'] in {'BEFORE', 'AFTER', 'BOTH'} for row in states.values())
    committed = all(row['state'] in {'AFTER', 'BOTH'} for row in states.values())
    return {**result, 'planState': 'PARSED', 'files': states,
            'stateAuditComparison': state_audit,
            'planSha256': sha((json.dumps(record['plan'], sort_keys=True, indent=2) + '\n').encode()),
            'allCurrentBytesRecognized': recognized,
            'exactPhaseBytesConsistent': recognized if phase == 'PREPARED' else committed if phase == 'COMMITTED' else False}


def selected_state(policy, profile, state):
    return {'controllerCommit': identity(policy.get('controllerCommit')),
            'legacyWorkerCommit': identity(policy.get('legacyWorkerCommit')),
            'hostPromotionCommit': identity(policy.get('hostPromotion', {}).get('commit')),
            'profileHostCommit': identity(profile.get('applicationHostCommit')),
            'profileBaseImage': image(profile.get('baseImage')), 'approvedImage': image(state.get('approvedImage')),
            'profileMatchesApprovedImage': profile.get('baseImage') == state.get('approvedImage'),
            'recoveryRequired': state.get('recoveryRequired') if type(state.get('recoveryRequired')) is bool else None,
            'enabled': state.get('enabled') is True, 'bootstrapVerified': state.get('bootstrapVerified') is True,
            'cohortMatchesExpected': policy.get('tenantPilot') == COHORT}


def unit_signals(raw):
    """Allowlisted systemd failure facts, never journal messages or commands."""
    if len(raw.encode()) > 262144:
        return {'state': 'OUTPUT_LIMIT'}
    signals = []
    for line in raw.splitlines():
        event = document(line)
        unit = next((event.get(key) for key in ['USER_UNIT', 'UNIT', '_SYSTEMD_USER_UNIT']
                     if isinstance(event.get(key), str) and re.fullmatch(
                         r'gamerhq-staging-automation-generation-[0-9a-f]{32}\.service', event[key])), None)
        if unit is None:
            continue
        message = event.get('MESSAGE', '')
        if not isinstance(message, str):
            continue
        reason = next((code for fragment, code in (
            ('226/NAMESPACE', 'SYSTEMD_NAMESPACE_FAILED'),
            ('Failed to set up mount namespacing', 'SYSTEMD_MOUNT_NAMESPACE_FAILED'),
            ('Failed at step NAMESPACE', 'SYSTEMD_NAMESPACE_FAILED'),
            ('217/USER', 'SYSTEMD_USER_FAILED'), ('216/GROUP', 'SYSTEMD_GROUP_FAILED'),
            ('203/EXEC', 'SYSTEMD_EXEC_FAILED'),
        ) if fragment in message), None)
        if reason:
            stamp = event.get('__REALTIME_TIMESTAMP')
            signals.append({'unit': unit, 'reason': reason,
                            'timestampMicros': stamp if isinstance(stamp, str) and stamp.isdigit() else None})
    return {'state': 'COLLECTED', 'signals': signals[-16:]}


def sandbox_probes(account, environment):
    """Two bounded, read-only Python processes; no enrollment or Gateway code."""
    program = ('import json,os; print(json.dumps({"uid":os.getuid(),"productionPathsInaccessible":'
               'all(not os.access(p,os.R_OK|os.X_OK) for p in '
               '("/opt/gamerhq","/etc/gamerhq","/run/docker.sock","/var/lib/docker","/root"))}))')
    results = {}
    for guarded in (False, True):
        properties = ['--property=NoNewPrivileges=yes', '--property=RestrictSUIDSGID=yes', '--property=RuntimeMaxSec=15s']
        if guarded:
            properties += ['--property=ProtectSystem=strict', '--property=ProtectHome=yes',
                           '--property=ReadWritePaths=' + str(ROOT / 'worker') + ' ' + str(ROOT / 'backups')]
        name = 'gamerhq-staging-diagnostic-' + uuid.uuid4().hex
        command = ['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i', *environment,
                   'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet', '--unit=' + name,
                   *properties, '/usr/bin/python3', '-I', '-S', '-B', '-c', program]
        key = 'generationNamespaceProperties' if guarded else 'inheritedManagerSandbox'
        try:
            reply = subprocess.run(command, text=True, capture_output=True, timeout=25)
            content = reply.stdout if len(reply.stdout) < 4096 else ''
            facts = document(content)
            stderr = reply.stderr if len(reply.stderr) < 262144 else ''
            results[key] = {'exitCode': reply.returncode,
                'probeCompleted': reply.returncode == 0 and facts.get('uid') == account.pw_uid,
                'productionPathsInaccessible': facts.get('productionPathsInaccessible') is True,
                'namespaceFailureObserved': any(text in stderr for text in (
                    '226/NAMESPACE', 'Failed to set up mount namespacing', 'Failed at step NAMESPACE'))}
        except (OSError, subprocess.SubprocessError):
            results[key] = {'state': 'UNAVAILABLE'}
    return results


def collect(expected, *, probe_user_sandbox=False):
    import fcntl
    import pwd
    if os.geteuid() != 0 or expected != SOURCE:
        raise ValueError('OWNER_AND_EXACT_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid = account.pw_uid
    worker = ROOT / 'worker'
    paths = {'entry': ROOT / 'automation-controller/entry.py', 'policy': CONTROL / 'automation-policy.json',
             'profile': CONTROL / 'settings/profile.json', 'state': worker / 'automation-state.json'}
    result = {'state': 'GENERATION_ENROLLMENT_DIAGNOSTICS', 'expectedGenerationSource': SOURCE,
              'expectedPreviousController': PREVIOUS, 'expectedBaselineHost': BASELINE, 'expectedBaselineImage': IMAGE,
              'readOnly': True, 'enrollmentInvoked': False, 'gatewayChanged': False, 'databaseChanged': False,
              'transitionChanged': False, 'productionChanged': False}
    revisions = ROOT / 'automation-controller/revisions'
    result['directories'] = {name: directory(path, uid if name == 'worker' else 0) for name, path in {
        'root': ROOT, 'configuration': CONTROL, 'controller': revisions.parent, 'revisions': revisions,
        'previous': revisions / PREVIOUS, 'previousModules': revisions / PREVIOUS / 'staging_automation',
        'target': revisions / SOURCE, 'targetModules': revisions / SOURCE / 'staging_automation', 'worker': worker}.items()}
    metadata, raw = read(worker / 'operation.lock', uid, private=True)
    result['operationLock'] = metadata
    if raw is None or metadata['mode'] != '0600':
        return result
    with os.fdopen(os.open(worker / 'operation.lock', os.O_RDONLY | os.O_NOFOLLOW), 'rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            result['operationLock']['available'] = False
            return result
        result['operationLock']['available'] = True
        current = {}
        result['files'] = {}
        for name, path in paths.items():
            result['files'][name], current[name] = read(path, uid if name in {'profile', 'state'} else 0,
                                                       private=name in {'profile', 'state'})
        transition_meta, transition_body = read(CONTROL / 'pilot-generation-transition.json', 0)
        result['transition'] = {'metadata': transition_meta, **transition_report(transition_body, current)}
        if transition_meta['state'] == 'ABSENT':
            result['transition']['phase'] = 'ABSENT'
        policy, profile, state = [document(current[name]) for name in ['policy', 'profile', 'state']]
        result['selected'] = selected_state(policy, profile, state)
        result['priorMetadataChecks'] = {
            'controllerMatchesPrevious': policy.get('controllerCommit') == PREVIOUS,
            'profileMatchesLegacyWorker': profile.get('hostSourceCommit') == WORKER,
            'profileMatchesPriorHostPin': profile.get('applicationHostCommit') == '40c88fada10f7990ef3d2cddb81f30b6381fe73e',
            'priorWorkflowDigestMatches': policy.get('tenantPilotCutover', {}).get('workflowSha256') ==
                'f982aa0d7d2d76b4a9e8fe27e1a998535aa03fe61e27d9765eccf4348c7e9011'}
        incident_meta, incident_body = read(worker / 'tenant-pilot-cutover.json', uid, private=True)
        incident = document(incident_body)
        result['incident'] = {'metadata': incident_meta, 'terminalRolledBack': incident.get('phase') == 'PILOT_ROLLED_BACK',
                              'activationRunMatchesExpected': incident.get('activationRun') == '44283427505',
                              'repairHostMatchesExpected': incident.get('recoveryApiRepair', {}).get('commit') == BASELINE,
                              'repairImageMatchesExpected': incident.get('recoveryApiRepair', {}).get('image') == IMAGE,
                              'matchesTransitionHash': bool(incident_body is not None and
                                  sha(incident_body) == document(transition_body).get('incidentSha256'))}
        result['newJournalPresent'] = os.path.lexists(worker / 'tenant-pilot-cutover-v2.json')
        # Only public source and exact Git IDs are inspected. Never emit Git stderr.
        with tempfile.TemporaryDirectory(prefix='gamerhq-generation-readonly-') as scratch:
            env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
            def git(*args):
                return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *args], env=env,
                                      capture_output=True, check=True, timeout=120).stdout
            try:
                git('init', '--quiet')
                for revision in (SOURCE, PREVIOUS, 'refs/heads/main'):
                    git('fetch', '--quiet', '--depth=1', 'https://github.com/Duy-Phan96/GamerHQ.git', revision)
                main = git('rev-parse', 'FETCH_HEAD').strip().decode()
                result['sourceEvidence'] = {'state': 'VERIFIED', 'mainCommit': identity(main)}
                manifest = document(git('show', SOURCE + ':deploy/staging/tenant-pilot-generation.json'))
                result['archivePins'] = {}
                for name in ['candidate', 'baseline']:
                    pin = manifest[name]
                    git('fetch', '--quiet', '--depth=1', 'https://github.com/Duy-Phan96/GamerHQ.git', pin['commit'])
                    checksum = sha(git('archive', '--format=tar', pin['commit']))
                    result['archivePins'][name] = {'commit': pin['commit'], 'expectedSha256': pin['archiveSha256'],
                                                  'actualSha256': checksum, 'matches': checksum == pin['archiveSha256']}
                # Reproduce b808's actual five shallow fetches in a disposable
                # repository before adding the missing legacy dependency.
                try:
                    git('show', ORIGINAL + ':tools/staging_automation/__init__.py')
                    result['originalFetchRehearsal'] = {'legacyCommitPresent': True, 'exitCode': 0}
                except subprocess.CalledProcessError as error:
                    result['originalFetchRehearsal'] = {'legacyCommitPresent': False, 'exitCode': error.returncode,
                                                       'stage': 'INSTALLED_CONTROLLER', 'missingCommit': ORIGINAL}
                git('fetch', '--quiet', '--depth=1', 'https://github.com/Duy-Phan96/GamerHQ.git', ORIGINAL)
                git('show', ORIGINAL + ':tools/staging_automation/__init__.py')
                result['legacyReadAfterExplicitFetch'] = 'PASS'
                result['workflows'] = {}
                for name, pin in manifest['workflows'].items():
                    body = git('show', main + ':.github/workflows/' + name)
                    result['workflows'][name] = {'expectedSha256': pin['sha256'], 'actualSha256': sha(body),
                                                'matches': sha(body) == pin['sha256']}
                previous = ROOT / 'automation-controller/revisions' / PREVIOUS
                modules = previous / 'staging_automation'
                entries = list(modules.iterdir())
                result['controllerSources'] = []
                for entry in entries:
                    name = entry.name
                    if name not in ALIASES and not re.fullmatch(r'[a-z_]+\.py', name):
                        result['controllerSources'].append({'state': 'UNEXPECTED_ENTRY',
                                                           'isPythonCache': name == '__pycache__'})
                        continue
                    relative = ALIASES.get(name, 'tools/staging_automation/' + name)
                    revision = PREVIOUS if name in OVERLAYS else ORIGINAL
                    meta, body = read(entry, 0)
                    expected_body = git('show', revision + ':' + relative)
                    result['controllerSources'].append({'file': name, 'metadata': meta,
                        'expectedRevision': revision, 'expectedSha256': sha(expected_body),
                        'matches': body is not None and body == expected_body})
                imports = repr(str(previous)) + ',' + repr(str(previous / 'pilot-source')) + ',' + repr(str(ROOT / 'deployment-worker'))
                expected_entry = ('import sys\nsys.path[:0]=[' + imports + ']\nfrom staging_automation.bridge import entry\nentry()\n').encode()
                result['entryMatchesPrevious'] = current['entry'] == expected_entry
                actual_worker = git('-c', 'safe.directory=' + str(ROOT / 'deployment-worker'), '-C', str(ROOT / 'deployment-worker'),
                                    'rev-parse', 'HEAD').strip().decode()
                result['workerSource'] = {'actualCommit': identity(actual_worker), 'matches': actual_worker == WORKER}
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                result['sourceEvidence'] = {'state': 'UNAVAILABLE',
                    **({'exitCode': error.returncode} if isinstance(error, subprocess.CalledProcessError) else {})}
        environment = ['PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=' + account.pw_dir,
                       'XDG_RUNTIME_DIR=/run/user/' + str(uid), 'DOCKER_HOST=unix:///run/user/' + str(uid) + '/docker.sock',
                       'DOCKER_CONFIG=/etc/gamerhq-staging/docker-client']
        try:
            answer = subprocess.run(['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i', *environment,
                                     'journalctl', '--user', '--no-pager', '--since=-24h', '-n', '80', '--output=json',
                                     '--unit=gamerhq-staging-automation-generation-*.service'],
                                    text=True, capture_output=True, check=True, timeout=30)
            result['generationUnitFailures'] = unit_signals(answer.stdout)
        except (OSError, subprocess.SubprocessError):
            result['generationUnitFailures'] = {'state': 'UNAVAILABLE'}
        try:
            answer = subprocess.run(['systemctl', 'show', 'user@' + str(uid) + '.service',
                                     '--property=ActiveState', '--property=ProtectSystem', '--property=PrivateTmp', '--property=InaccessiblePaths'],
                                    text=True, capture_output=True, check=True, timeout=20)
            fields = dict(line.split('=', 1) for line in answer.stdout.splitlines() if '=' in line) if len(answer.stdout) < 16384 else {}
            blocked = {path.removeprefix('-') for path in fields.get('InaccessiblePaths', '').split()}
            result['parentSandbox'] = {'protectSystemStrict': fields.get('ProtectSystem') == 'strict',
                                       'privateTmp': fields.get('PrivateTmp') == 'yes',
                                       'active': fields.get('ActiveState') == 'active',
                                       'productionPathsConfigured': {'/opt/gamerhq', '/etc/gamerhq', '/run/docker.sock', '/var/lib/docker', '/root'} <= blocked}
        except (OSError, subprocess.SubprocessError):
            result['parentSandbox'] = {'state': 'UNAVAILABLE'}
        fmt = ('{"host":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
               '"image":{{json .Image}},"running":{{json .State.Running}},'
               '"health":{{with .State.Health}}{{json .Status}}{{else}}"none"{{end}}}')
        try:
            answer = subprocess.run(['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i', *environment,
                                     'docker', 'inspect', '--format', fmt, 'gamerhq-staging-bot'],
                                    text=True, capture_output=True, check=True, timeout=30)
            container = document(answer.stdout if len(answer.stdout) <= 4096 else None)
            result['container'] = {'hostCommit': identity(container.get('host')), 'image': image(container.get('image')),
                                   'running': container.get('running') is True,
                                   'dockerHealthy': container.get('health') == 'healthy',
                                   'matchesRecoveredBaseline': container.get('host') == BASELINE and container.get('image') == IMAGE}
        except (OSError, ValueError, subprocess.SubprocessError):
            result['container'] = {'state': 'UNAVAILABLE'}
        if probe_user_sandbox:
            result['readOnlySandboxProbes'] = sandbox_probes(account, environment)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-source', required=True, choices=[SOURCE])
    parser.add_argument('--probe-user-sandbox', action='store_true', help='Run two bounded read-only Python probes; never run enrollment.')
    args = parser.parse_args()
    try:
        print(json.dumps(collect(args.expected_source, probe_user_sandbox=args.probe_user_sandbox)))
    except Exception:
        raise SystemExit('{"diagnosticError":"GENERATION_DIAGNOSTIC_UNAVAILABLE","readOnly":true}') from None
