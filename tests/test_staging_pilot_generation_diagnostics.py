import ast
import copy
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import enroll_staging_pilot_generation as enrollment
from tools import staging_pilot_generation_diagnostics as diagnostics


@pytest.mark.parametrize('returned', [b'b' * 40 + b'\n', b'not-a-commit\n', b'\xff\n'])
def test_revision_fetch_rejects_wrong_or_malformed_fetch_head(returned):
    calls = []
    def git(*args):
        calls.append(args)
        return returned if args[0] == 'rev-parse' else b''
    with pytest.raises(ValueError, match='FETCHED_REVISION_REJECTED'):
        enrollment.fetch_revision(git, 'a' * 40)
    assert calls == [('fetch', '--quiet', '--depth=1', enrollment.REPOSITORY, 'a' * 40),
                     ('rev-parse', '--verify', 'FETCH_HEAD^{commit}')]


def test_all_source_revisions_are_fetched_and_verified_before_immutable_reads():
    calls, head = [], None
    def git(*args):
        nonlocal head
        calls.append(args)
        if args[0] == 'fetch':
            head = 'd' * 40 if args[-1] == 'refs/heads/main' else args[-1]
            return b''
        assert args == ('rev-parse', '--verify', 'FETCH_HEAD^{commit}')
        return (head + '\n').encode()
    enrollment.fetch_sources(git, 'a' * 40)
    assert [call[-1] for call in calls if call[0] == 'fetch'] == [
        'a' * 40, enrollment.PREVIOUS, enrollment.LEGACY_CONTROLLER_BASE, 'refs/heads/main']
    assert len(calls) == 8


def test_fresh_only_cli_preserves_the_explicit_guard(monkeypatch, capsys):
    def enroll(source, approval, *, require_absent):
        assert require_absent is True
        return {'state': 'RECOVERED_PRIMARY_GENERATION_ENROLLED'}
    monkeypatch.setattr(enrollment, 'enroll', enroll)
    monkeypatch.setattr(sys, 'argv', ['installer', '--commit', 'a' * 40,
                                    '--approval', enrollment.APPROVAL, '--require-absent-transition'])
    enrollment.main()
    assert json.loads(capsys.readouterr().out)['state'] == 'RECOVERED_PRIMARY_GENERATION_ENROLLED'


@pytest.mark.parametrize('error,reason,extra', [
    (ValueError('CONTROLLER_SOURCE_MISMATCH'), 'CONTROLLER_SOURCE_MISMATCH', {}),
    (ValueError('private-token-must-never-appear'), 'VALIDATION_REJECTED', {}),
    (PermissionError(errno.EACCES, 'private-token-must-never-appear', '/private/credential'), 'FILESYSTEM_REJECTED', {'errno': 13}),
    (subprocess.CalledProcessError(128, ['git', 'private-token-must-never-appear'], output=b'private output', stderr=b'private error'),
     'SUBPROCESS_REJECTED', {'exitCode': 128}),
    (subprocess.TimeoutExpired(['private-token-must-never-appear'], 10), 'SUBPROCESS_TIMED_OUT', {}),
])
def test_enrollment_exposes_exact_safe_stage_without_raw_error(monkeypatch, capsys, error, reason, extra):
    def fail(*args):
        enrollment.progress('OWNER_CHECK')
        enrollment.progress('INSTALLED_CONTROLLER')
        raise error
    monkeypatch.setattr(enrollment, 'enroll', fail)
    monkeypatch.setattr(sys, 'argv', ['installer', '--commit', 'a' * 40, '--approval', enrollment.APPROVAL])
    with pytest.raises(SystemExit) as caught:
        enrollment.main()
    assert caught.value.code == 1
    output = capsys.readouterr().out
    record = json.loads(output)
    assert record['stage'] == 'INSTALLED_CONTROLLER'
    assert record['completedStages'] == ['OWNER_CHECK']
    assert record['rejectionReason'] == reason
    assert all(record[key] == value for key, value in extra.items())
    assert 'private' not in output
    assert 'credential' not in output
    assert record['activationAuthorized'] is False


@pytest.mark.parametrize('phase,boundary', [('PREPARED', 0), ('PREPARED', 2), ('COMMITTED', 4)])
def test_diagnostic_reads_partial_transition_without_resuming_or_exposing_plan(tmp_path, phase, boundary):
    names = ['entry', 'profile', 'state', 'policy']
    before = {key: b'original-' + key.encode() + b'-private-token' for key in names}
    after = {key: b'new-' + key.encode() + b'-private-token' for key in names}
    current = {key: after[key] if number < boundary else before[key] for number, key in enumerate(names)}
    record = {'source': diagnostics.SOURCE, 'phase': phase, 'incidentSha256': 'a' * 64,
              'plan': {'files': [{'name': key, 'before': before[key].hex(), 'after': after[key].hex()} for key in names]}}
    original = copy.deepcopy(record)
    snapshot = dict(current)
    result = diagnostics.transition_report(json.dumps(record).encode(), current)
    assert result['phase'] == phase and result['sourceMatchesExpected']
    assert result['allCurrentBytesRecognized']
    assert result['files']['entry']['state'] == ('AFTER' if boundary else 'BEFORE')
    assert result['files']['policy']['state'] == ('AFTER' if phase == 'COMMITTED' else 'BEFORE')
    assert 'private-token' not in json.dumps(result)
    assert record == original and current == snapshot


def test_diagnostic_reports_unknown_contents_and_malformed_plan_without_fixing_them():
    record = {'source': diagnostics.SOURCE, 'phase': 'PREPARED', 'plan': {'files': [
        {'name': name, 'before': b'old'.hex(), 'after': b'new'.hex()} for name in ['entry', 'profile', 'state', 'policy']]}}
    current = {'entry': b'old', 'profile': b'unknown', 'state': b'old', 'policy': b'old'}
    assert diagnostics.transition_report(json.dumps(record).encode(), current)['files']['profile']['state'] == 'UNKNOWN'
    assert current['profile'] == b'unknown'
    record['plan']['files'][0]['name'] = '/private/credential'
    report = diagnostics.transition_report(json.dumps(record).encode(), current)
    assert report['planState'] == 'REJECTED'
    assert 'credential' not in json.dumps(report)


def test_status_rate_accounting_is_distinguished_from_changed_deployment_state():
    before = {'approvedImage': 'accepted', 'recoveryRequired': False, 'events': [], 'lastNow': 1}
    current = {**before, 'events': [{'op': 'status', 'at': 2}], 'lastNow': 2}
    plan = {'source': diagnostics.SOURCE, 'phase': 'PREPARED', 'plan': {'files': [
        {'name': name, 'before': json.dumps(before).encode().hex(), 'after': json.dumps(before).encode().hex()}
        for name in ['entry', 'profile', 'state', 'policy']]}}
    values = {name: json.dumps(before).encode() for name in ['entry', 'profile', 'state', 'policy']}
    values['state'] = json.dumps(current).encode()
    report = diagnostics.transition_report(json.dumps(plan).encode(), values)
    assert report['files']['state']['state'] == 'UNKNOWN'
    assert report['stateAuditComparison']['matchesBeforeExceptRequestAudit']
    current['approvedImage'] = 'unknown'
    values['state'] = json.dumps(current).encode()
    report = diagnostics.transition_report(json.dumps(plan).encode(), values)
    assert not report['stateAuditComparison']['matchesBeforeExceptRequestAudit']


def test_missing_legacy_revision_reproduces_git_128_and_explicit_fetch_repairs_only_scratch(tmp_path, monkeypatch):
    seed, scratch = tmp_path / 'seed', tmp_path / 'scratch'
    seed.mkdir()
    scratch.mkdir()
    def git(folder, *args, check=True):
        return subprocess.run(['git', '-C', str(folder), '-c', 'gc.auto=0', *args],
                              capture_output=True, check=check, timeout=30)
    git(seed, 'init', '--quiet', '--initial-branch=main')
    (seed / 'legacy.py').write_bytes(b'original immutable legacy source\n')
    git(seed, 'add', 'legacy.py')
    git(seed, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'legacy')
    original = git(seed, 'rev-parse', 'HEAD').stdout.strip().decode()
    (seed / 'current.py').write_bytes(b'current source\n')
    git(seed, 'add', 'current.py')
    git(seed, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'current')
    tip = git(seed, 'rev-parse', 'HEAD').stdout.strip().decode()
    git(scratch, 'init', '--quiet')
    remote = seed.as_uri()
    # Same depth-one dependency omission as the original b808 installer.
    for reference in [tip, tip, 'refs/heads/main', tip, tip]:
        git(scratch, 'fetch', '--quiet', '--depth=1', remote, reference)
    rejected = git(scratch, 'show', original + ':legacy.py', check=False)
    assert rejected.returncode == 128
    retained = tmp_path / 'held-transition.json'
    retained.write_bytes(b'unchanged protected transition fixture')
    before = retained.read_bytes(), retained.stat().st_mtime_ns
    monkeypatch.setattr(enrollment, 'PREVIOUS', tip)
    monkeypatch.setattr(enrollment, 'LEGACY_CONTROLLER_BASE', original)
    monkeypatch.setattr(enrollment, 'REPOSITORY', remote)
    enrollment.fetch_sources(lambda *args: git(scratch, *args).stdout, tip)
    assert git(scratch, 'show', original + ':legacy.py').stdout == b'original immutable legacy source\n'
    assert git(scratch, 'rev-parse', 'FETCH_HEAD').stdout.strip().decode() == tip
    assert (retained.read_bytes(), retained.stat().st_mtime_ns) == before


@pytest.mark.skipif(os.name != 'posix', reason='Real protected file ownership/mode contract is POSIX-only')
def test_protected_metadata_reader_preserves_bytes_and_refuses_unsafe_file(tmp_path):
    path = tmp_path / 'protected'
    path.write_bytes(b'private-token')
    path.chmod(0o600)
    uid = path.stat().st_uid
    before = path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns
    metadata, body = diagnostics.read(path, uid, private=True)
    assert metadata['state'] == 'VERIFIED' and body == b'private-token'
    assert 'private-token' not in json.dumps(metadata)
    assert (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) == before
    if os.name == 'posix':
        path.chmod(0o640)
        metadata, body = diagnostics.read(path, uid, private=True)
        assert metadata['state'] == 'UNSAFE' and body is None
    metadata, body = diagnostics.read(tmp_path / 'absent', uid)
    assert metadata['state'] == 'ABSENT' and body is None


def test_generation_diagnostic_and_reporting_remain_system_python_39_compatible():
    for filename in ['tools/staging_pilot_generation_diagnostics.py', 'tools/enroll_staging_pilot_generation.py']:
        ast.parse(Path(filename).read_text(), feature_version=(3, 9))
    tree = ast.parse(Path('tools/staging_pilot_generation_diagnostics.py').read_text())
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {'write_bytes', 'write_text', 'chmod', 'chown', 'replace', 'unlink'}
                   for node in ast.walk(tree))


def test_systemd_signals_identify_only_the_failed_generation_unit_without_messages():
    unit = 'gamerhq-staging-automation-generation-' + 'a' * 32 + '.service'
    raw = '\n'.join(json.dumps(row) for row in [
        {'_SYSTEMD_USER_UNIT': 'init.scope', 'USER_UNIT': unit, '__REALTIME_TIMESTAMP': '12345', 'MESSAGE': 'Failed to set up mount namespacing: Permission denied private-token'},
        {'USER_UNIT': unit, 'MESSAGE': 'Main process exited, code=exited, status=226/NAMESPACE private-token'},
        {'USER_UNIT': 'unrelated.service', 'MESSAGE': 'status=226/NAMESPACE private-token'},
        {'USER_UNIT': unit, 'MESSAGE': 'private command and credential values'},
    ])
    result = diagnostics.unit_signals(raw)
    assert [x['reason'] for x in result['signals']] == ['SYSTEMD_MOUNT_NAMESPACE_FAILED', 'SYSTEMD_NAMESPACE_FAILED']
    assert result['signals'][0]['timestampMicros'] == '12345'
    assert 'private' not in json.dumps(result)


def test_bounded_read_only_probes_isolate_namespace_failure_without_enrollment(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if '--property=ProtectSystem=strict' in command:
            return subprocess.CompletedProcess(command, 226, stdout='', stderr='Failed at step NAMESPACE private-credential')
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'uid': 1002, 'productionPathsInaccessible': True}), stderr='')
    monkeypatch.setattr(diagnostics.subprocess, 'run', run)
    outcome = diagnostics.sandbox_probes(SimpleNamespace(pw_name='fixture', pw_uid=1002), ['PATH=/usr/bin:/bin'])
    assert outcome['inheritedManagerSandbox']['probeCompleted']
    assert outcome['inheritedManagerSandbox']['productionPathsInaccessible']
    assert outcome['generationNamespaceProperties']['namespaceFailureObserved']
    assert outcome['generationNamespaceProperties']['exitCode'] == 226
    assert 'private-credential' not in json.dumps(outcome)
    for command in calls:
        if 'journalctl' in command:
            assert any(item.startswith('--unit=gamerhq-staging-diagnostic-') for item in command)
            continue
        assert 'systemd-run' in command
        assert '--property=RuntimeMaxSec=15s' in command
        assert command[-4:-1] == ['-S', '-B', '-c']
        assert 'os.access' in command[-1]
        assert 'enroll' not in command[-1] and 'sqlite' not in command[-1] and 'open(' not in command[-1]


@pytest.mark.skipif(os.name != 'posix', reason='Real operation-lock/read-only filesystem collection requires POSIX')
@pytest.mark.parametrize('phase', [None, 'PREPARED', 'COMMITTED'])
@pytest.mark.parametrize('git_failure', [False, True])
def test_full_collector_preserves_transition_and_tenants(tmp_path, monkeypatch, phase, git_failure):
    import pwd
    actual_uid, actual_gid = os.geteuid(), os.getegid()
    root, control = tmp_path / 'staging', tmp_path / 'control'
    worker = root / 'worker'
    previous = root / 'automation-controller/revisions' / diagnostics.PREVIOUS / 'staging_automation'
    for directory in [worker, previous, control / 'settings']:
        directory.mkdir(parents=True)
    monkeypatch.setattr(diagnostics, 'ROOT', root)
    monkeypatch.setattr(diagnostics, 'CONTROL', control)
    account = SimpleNamespace(pw_uid=actual_uid, pw_gid=actual_gid, pw_name='fixture', pw_dir=str(tmp_path))
    git_blobs, git_archives = {}, {}
    if not git_failure:
        for spec in [diagnostics.SOURCE + ':deploy/staging/tenant-pilot-generation.json',
                     diagnostics.PREVIOUS + ':tools/staging_automation/bridge.py']:
            git_blobs[spec] = subprocess.check_output(['git', 'show', spec])
        manifest = json.loads(git_blobs[diagnostics.SOURCE + ':deploy/staging/tenant-pilot-generation.json'])
        main = 'c581d01801e36d656343e47dae56bf9aea5ed211'
        for name in manifest['workflows']:
            spec = main + ':.github/workflows/' + name
            git_blobs[spec] = subprocess.check_output(['git', 'show', spec])
        for name in ['candidate', 'baseline']:
            revision = manifest[name]['commit']
            git_archives[revision] = subprocess.check_output(['git', 'archive', '--format=tar', revision])
        (previous / 'bridge.py').write_bytes(git_blobs[diagnostics.PREVIOUS + ':tools/staging_automation/bridge.py'])
        (previous / 'bridge.py').chmod(0o640)
    monkeypatch.setattr(pwd, 'getpwnam', lambda _: account)
    native_read = diagnostics.read
    # The fixture runs as the unprivileged CI user. Map synthetic root-owned
    # metadata to that user; the real ownership/mode reader is tested above.
    monkeypatch.setattr(diagnostics, 'read', lambda path, uid, **kwargs: native_read(path, actual_uid, **kwargs))
    monkeypatch.setattr(diagnostics.os, 'geteuid', lambda: 0)
    files = {'entry': root / 'automation-controller/entry.py', 'policy': control / 'automation-policy.json',
             'profile': control / 'settings/profile.json', 'state': worker / 'automation-state.json'}
    values = {'entry': b'original-entry-private-credential',
              'policy': json.dumps({'controllerCommit': diagnostics.PREVIOUS, 'legacyWorkerCommit': diagnostics.WORKER,
                                    'tenantPilot': diagnostics.COHORT, 'private': 'never-publish'}).encode(),
              'profile': json.dumps({'applicationHostCommit': diagnostics.BASELINE, 'baseImage': diagnostics.IMAGE}).encode(),
              'state': json.dumps({'recoveryRequired': False, 'approvedImage': diagnostics.IMAGE}).encode()}
    for name, path in files.items():
        path.write_bytes(values[name])
        path.chmod(0o600)
    incident = json.dumps({'phase': 'PILOT_ROLLED_BACK', 'activationRun': '44283427505'}).encode()
    (worker / 'tenant-pilot-cutover.json').write_bytes(incident)
    (worker / 'tenant-pilot-cutover.json').chmod(0o600)
    (worker / 'operation.lock').touch(mode=0o600)
    for name in ['primary.sqlite', 'secondary.sqlite']:
        (worker / name).write_bytes(b'fixture tenant bytes')
    if phase:
        plan = {'files': [{'name': name, 'before': values[name].hex(), 'after': (values[name] + b' ').hex()} for name in files]}
        (control / 'pilot-generation-transition.json').write_text(json.dumps({
            'phase': phase, 'source': diagnostics.SOURCE, 'incidentSha256': diagnostics.sha(incident), 'plan': plan}))
        (control / 'pilot-generation-transition.json').chmod(0o640)
    snapshots = {path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                 for path in tmp_path.rglob('*') if path.is_file()}
    calls = []
    legacy_fetched = [False]
    def run(command, **kwargs):
        calls.append(command)
        if command[0] == 'git':
            if git_failure and 'fetch' in command:
                raise subprocess.CalledProcessError(128, command, stderr=b'private-credential')
            if 'show' in command:
                if command[-1] == diagnostics.ORIGINAL + ':tools/staging_automation/__init__.py':
                    if not legacy_fetched[0]:
                        raise subprocess.CalledProcessError(128, command, stderr=b'private-credential')
                    return subprocess.CompletedProcess(command, 0, stdout=b'')
                return subprocess.CompletedProcess(command, 0, stdout=git_blobs[command[-1]])
            if 'fetch' in command and command[-1] == diagnostics.ORIGINAL:
                legacy_fetched[0] = True
            if 'archive' in command:
                return subprocess.CompletedProcess(command, 0, stdout=git_archives[command[-1]])
            if 'rev-parse' in command:
                return subprocess.CompletedProcess(command, 0, stdout=(diagnostics.WORKER if command[-1] == 'HEAD' else main).encode())
            return subprocess.CompletedProcess(command, 0, stdout=b'')
        if 'journalctl' in command:
            return subprocess.CompletedProcess(command, 0, stdout='')
        if command[0] == 'systemctl':
            return subprocess.CompletedProcess(command, 0, stdout='ActiveState=active\nProtectSystem=strict\nPrivateTmp=yes\n')
        assert 'docker' in command and 'inspect' in command
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
            'host': diagnostics.BASELINE, 'image': diagnostics.IMAGE, 'running': True, 'health': 'healthy'}))
    monkeypatch.setattr(diagnostics.subprocess, 'run', run)
    result = diagnostics.collect(diagnostics.SOURCE)
    assert result['transition']['phase'] == (phase or 'ABSENT')
    if git_failure:
        assert result['sourceEvidence'] == {'state': 'UNAVAILABLE', 'exitCode': 128}
    else:
        assert result['sourceEvidence']['state'] == 'VERIFIED'
        assert result['controllerSources'][0]['matches']
        assert result['originalFetchRehearsal']['exitCode'] == 128
        assert result['legacyReadAfterExplicitFetch'] == 'PASS'
        assert all(row['matches'] for row in result['workflows'].values())
        assert all(row['matches'] for row in result['archivePins'].values())
    assert result['container']['matchesRecoveredBaseline']
    assert not result['enrollmentInvoked'] and not result['transitionChanged']
    public = json.dumps(result)
    assert 'private-credential' not in public and 'never-publish' not in public
    assert snapshots == {path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                         for path in tmp_path.rglob('*') if path.is_file()}
    assert not any('systemd-run' in command or 'start' in command or 'stop' in command for command in calls)
