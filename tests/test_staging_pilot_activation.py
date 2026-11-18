import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from database import db, tenants
from tools.staging_automation.pilot_activation import preparation_state, prepare_session, validate_pin
from tools.staging_automation.policy import Denied, admit, request
from tools.staging_tenant_rehearsal import PREVIEW_MAX_AGE, retain, verify_activation_checkpoint


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    database = tmp_path / 'primary.sqlite'
    monkeypatch.setattr(db, 'DB_PATH', database)
    db.init_db()
    store = tenants.GuildTenantStore(primary_guild=1, primary_database=database,
                                     tenant_directory=tmp_path / 'tenants', additional_guilds=(2,))
    store.provision(2)
    binding = {'applicationId': '90', 'guildIds': ['1', '2']}
    original = {'baselineHostCommit': 'a' * 40, 'baselineImage': 'sha256:' + 'b' * 64,
                'startupOverrideSha256': 'c' * 64}
    result = retain(store, tmp_path, tmp_path, binding=original)
    return tmp_path, {**result, **original}, binding, original


def test_activation_checkpoint_reopens_both_matching_backups(checkpoint):
    path, preview, binding, original = checkpoint
    proof = verify_activation_checkpoint(path, preview, binding, original, verified_at=900, now=1000)
    assert [(row['guildId'], row['restore']) for row in proof['tenants']] == [(1, 'PASS'), (2, 'PASS')]
    with pytest.raises(ValueError, match='PILOT_PREVIEW_EXPIRED'):
        verify_activation_checkpoint(path, preview, binding, original, verified_at=900, now=901 + PREVIEW_MAX_AGE)
    with pytest.raises(ValueError, match='PILOT_BASELINE_CHANGED'):
        verify_activation_checkpoint(path, preview, binding, {**original, 'baselineHostCommit': 'd' * 40}, verified_at=900, now=1000)


@pytest.mark.parametrize('target', ['verified.json', '1.sqlite', '2.sqlite'])
def test_activation_rejects_modified_receipt_or_either_backup(checkpoint, target):
    path, preview, binding, original = checkpoint
    with (path / preview['backupBatch'] / target).open('ab') as output:
        output.write(b'changed')
    with pytest.raises(ValueError, match='PILOT_CHECKPOINT_REJECTED'):
        verify_activation_checkpoint(path, preview, binding, original, verified_at=900, now=1000)


@pytest.mark.skipif(os.name != 'posix', reason='Requires POSIX mode enforcement')
def test_activation_rejects_shared_snapshot_mode(checkpoint):
    path, preview, binding, original = checkpoint
    (path / preview['backupBatch'] / '2.sqlite').chmod(0o640)
    with pytest.raises(ValueError, match='PILOT_CHECKPOINT_REJECTED'):
        verify_activation_checkpoint(path, preview, binding, original, verified_at=900, now=1000)


def test_activation_source_pin_and_owner_approved_preview_are_required():
    pin = {'commit': 'a' * 40, 'archiveSha256': 'b' * 64, 'workflowSha256': 'c' * 64, 'ciRun': '21'}
    validate_pin(pin)
    with pytest.raises(Denied, match='PILOT_ACTIVATION_PIN_REJECTED'):
        validate_pin({**pin, 'commit': 'main'})
    with pytest.raises(Denied, match='OPERATION_NOT_ALLOWED'):
        request({'version': 1, 'phase': 'automation', 'operation': 'tenant-pilot-activate'})
    with pytest.raises(Denied, match='RECOVERY_REQUIRES_OWNER'):
        admit('tenant-pilot-activation-prepare', {'enabled': True, 'bootstrapVerified': True, 'recoveryRequired': True})


def test_pilot_schema_check_is_read_only_and_rejects_unmigrated_database(tmp_path, monkeypatch):
    database = tmp_path / 'current.sqlite'
    monkeypatch.setattr(db, 'DB_PATH', database)
    db.init_db()
    before = database.read_bytes()
    db.verify_pilot_schema()
    assert database.read_bytes() == before
    with db.connect() as conn:
        conn.execute('PRAGMA user_version=0')
    before = database.read_bytes()
    with pytest.raises(ValueError, match='current database schema'):
        db.verify_pilot_schema()
    assert database.read_bytes() == before


def test_protected_preparation_builds_and_probes_without_live_apply(checkpoint, monkeypatch):
    from hosts.gamerhq import skill_staging
    from tools import staging_tenant_rehearsal
    from tools.staging_automation import host_promotion, pilot_activation

    path, proof, binding, original = checkpoint
    proof.update(state='PILOT_PREVIEW_VERIFIED', gatewayStarted=False, verifiedAt=900)
    source = 'a' * 40
    image = 'sha256:' + 'e' * 64
    policy = {'tenantPilot': binding, 'hostPromotion': {'commit': original['baselineHostCommit']},
              'tenantPilotActivation': {'commit': source, 'archiveSha256': 'f' * 64,
                                        'workflowSha256': '1' * 64, 'ciRun': '2'}}
    release = path / 'release.json'
    release.write_text(json.dumps({'version': '0.1.2', 'reviewedCommit': host_promotion.BASELINE_SKILL_COMMIT,
                                   'sha256': host_promotion.BASELINE_SKILL_SHA256}))
    profile = {'environment': 'staging', 'stagingGuildId': '1', 'stagingBotId': '90', 'productionBotId': '99',
               'productionGuildId': '3', 'service': 'gamerhq-staging', 'database': path / 'primary.sqlite',
               'backupRoot': path, 'releaseFile': release}
    backend = SimpleNamespace(policy=policy, profile=profile, inspect=lambda: {'Image': original['baselineImage'], 'Id': 'baseline'},
                              health=lambda: {'state': 'HOST_READY'}, run=Mock(return_value='PASS'),
                              skill=lambda: {'version': '0.1.2', 'state': {'installed': True, 'configured': True, 'enabled': False}})
    rollback = Mock()
    monkeypatch.setattr(pilot_activation, 'rehearse_container_rollback', rollback)
    monkeypatch.setattr(staging_tenant_rehearsal, 'rehearsal_binding', lambda *a: original)
    verify_remote = Mock()
    monkeypatch.setattr(skill_staging, 'verify_remote', verify_remote)
    monkeypatch.setattr(host_promotion, 'retained_state', lambda *a: {'installed': (1, 1, 0, '0.1.1')})
    build = Mock(return_value=image)
    monkeypatch.setattr(host_promotion, 'build_reviewed_overlay', build)
    ci = lambda *args: {'workflow_id': 361334574, 'head_sha': source, 'conclusion': 'success'}
    with pytest.raises(Denied, match='PILOT_PREVIEW_EXPIRED'):
        pilot_activation.prepare(backend, proof, original['baselineImage'], now=901 + PREVIEW_MAX_AGE, read=ci)
    build.assert_not_called()
    result = pilot_activation.prepare(backend, proof, original['baselineImage'], now=1000, read=ci)
    assert result['candidateImage'] == image and result['activationAuthorized'] is False
    assert result['backupReceiptSha256'] == proof['backupReceiptSha256']
    assert result['containerRollbackRehearsal'] == 'PASS'
    rollback.assert_called_once_with(backend, original['baselineImage'], image)
    assert verify_remote.call_args.kwargs == {'approved_pilot': (1, 2), 'read_only_artifacts': True}
    calls = [call.args[0] for call in backend.run.call_args_list]
    assert len(calls) == 2
    assert all(argv[:5] == ['docker', 'run', '--rm', '--network', 'none'] and '--volume' not in argv for argv in calls)
    assert '--env' in calls[0] and '--env' not in calls[1]


def test_preparation_session_returns_current_permission_failure_without_build():
    failure = {'state': 'PILOT_CORE_PERMISSIONS_REJECTED', 'missingPermissions': ['pin_messages']}
    backend = SimpleNamespace(_verify_tenant_pilot=Mock(return_value=failure))
    assert prepare_session(backend, 'image') is failure
    backend._verify_tenant_pilot.assert_called_once_with(expected_image='image')


def test_prepared_status_expires_from_live_observation_not_workflow_completion():
    from tools.staging_automation.bridge import execute

    pin = {'commit': 'a' * 40}
    receipt = {'state': 'PILOT_ACTIVATION_PREPARED', 'candidateCommit': pin['commit'],
               'gatewayStarted': False, 'activationAuthorized': False, 'containerRollbackRehearsal': 'PASS',
               'preview': {'state': 'PILOT_PREVIEW_VERIFIED', 'verifiedAt': 100}}
    save = Mock()
    state = {'tenantPilotActivationPrepared': receipt}
    policy = {'tenantPilotActivation': pin, 'controllerCommit': 'b' * 40, 'legacyWorkerCommit': 'c' * 40,
              'publicKeySha256': 'd' * 64, 'limits': {}}
    assert preparation_state(receipt, pin, 100 + PREVIEW_MAX_AGE) == 'FRESH'
    assert preparation_state(receipt, pin, 99) == 'REJECTED'
    assert preparation_state(receipt, {'commit': 'e' * 40}, 100) == 'REJECTED'
    result = execute('status', None, policy, state, None, save, now=101 + PREVIEW_MAX_AGE)
    assert result['tenantPilotPreparationState'] == 'EXPIRED'
    assert state['tenantPilotActivationPrepared'] is receipt


@pytest.mark.parametrize('changes', [None, 'before', 'during'])
def test_quiesced_backup_requires_same_stopped_host_and_readonly_secondary(checkpoint, changes):
    import shutil

    from tools.staging_tenant_rehearsal import retain_quiesced

    root, _, cohort, original = checkpoint
    profile = {'environment': 'staging', 'stagingGuildId': '1', 'stagingBotId': '90',
               'productionGuildId': '3', 'productionBotId': '99', 'service': 'gamerhq-staging',
               'database': str(root / 'primary.sqlite'), 'backupRoot': root, 'workRoot': root}
    baseline = {'Id': 'c' * 64, 'Image': original['baselineImage'],
                'Config': {'Labels': {'org.opencontainers.image.revision': original['baselineHostCommit']}},
                'State': {'Running': False, 'Status': 'exited'},
                'ProcessLabel': 'system_u:system_r:container_t:s0:c100,c200'}
    running = {**baseline, 'State': {'Running': True, 'Status': 'running'}}
    backend = SimpleNamespace(profile=profile, storage_budget=Mock(),
                              policy={'hostPromotion': {'commit': original['baselineHostCommit']},
                                      'tenantPilot': cohort},
                              inspect=Mock(side_effect=[running, baseline, baseline] if changes == 'before'
                                           else [baseline, baseline, running if changes else baseline]))
    primary = root / 'primary.sqlite'
    secondary = root / 'tenants' / '2.sqlite'
    before = primary.read_bytes(), secondary.read_bytes()

    def reader(args, *, stdout, **kwargs):
        assert args[:8] == ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only']
        assert args[args.index('--mount') + 1].endswith(',dst=/app/runtime/data,readonly')
        assert args[args.index('--user') + 1] == '10001:10001'
        assert args[args.index('--log-driver') + 1] == 'none'
        assert 'label=level:s0:c100,c200' in args
        assert args[-1] == '2' and '--env' not in args
        with secondary.open('rb') as raw:
            shutil.copyfileobj(raw, stdout)

    capture = Mock(side_effect=reader)
    if changes:
        with pytest.raises(Denied, match='PILOT_BASELINE_NOT_QUIESCED'):
            retain_quiesced(backend, baseline['Id'], baseline['Image'], run=capture)
        assert capture.call_count == (0 if changes == 'before' else 1)
        assert len(list(root.glob('tenant-pilot-*/verified.json'))) == 1
    else:
        result = retain_quiesced(backend, baseline['Id'], baseline['Image'], run=capture)
        assert result['quiesced'] is True
        assert [tenant['restore'] for tenant in result['tenantRestores']] == ['PASS', 'PASS']
    assert (primary.read_bytes(), secondary.read_bytes()) == before


@pytest.mark.parametrize('boundary', ['before-stop', 'after-stop', 'checkpointed', 'created',
                                      'readiness', 'postflight', 'complete'])
def test_cutover_restart_recovers_without_overwriting_tenants(tmp_path, monkeypatch, boundary):
    import time

    from tools import staging_dispatch, staging_tenant_rehearsal
    from tools.staging_automation.pilot_cutover import PilotCutover, digest

    class Interrupted(BaseException):
        pass

    primary = tmp_path / 'primary.sqlite'
    secondary = tmp_path / '2.sqlite'
    primary.write_bytes(b'primary')
    secondary.write_bytes(b'secondary')
    proof = tmp_path / 'old-verification.json'
    proof.write_text('{}')
    profile = {'environment': 'staging', 'stagingGuildId': '1', 'stagingBotId': '90',
               'productionGuildId': '3', 'productionBotId': '99', 'service': 'gamerhq-staging',
               'database': str(primary), 'backupRoot': str(tmp_path), 'workRoot': str(tmp_path),
               'verificationFile': str(proof)}
    candidate, baseline, fallback = ['sha256:' + c * 64 for c in 'abc']
    policy = {'tenantPilot': {'applicationId': '90', 'guildIds': ['1', '2']},
              'tenantPilotActivation': {'commit': 'd' * 40},
              'hostPromotion': {'commit': 'e' * 40}}
    override_args = ({**profile, 'tenantPilot': policy['tenantPilot']}, policy)
    override = staging_tenant_rehearsal.startup_override
    prepared = {'state': 'PILOT_ACTIVATION_PREPARED', 'gatewayStarted': False, 'activationAuthorized': False,
                'containerRollbackRehearsal': 'PASS', 'candidateCommit': 'd' * 40,
                'preview': {'state': 'PILOT_PREVIEW_VERIFIED', 'verifiedAt': int(time.time())},
                'candidateImage': candidate, 'fallbackImage': fallback,
                'startupOverrideSha256': digest(override(*override_args, candidate)),
                'fallbackStartupOverrideSha256': digest(override(*override_args, fallback, primary_only=True))}
    current = {'Id': '1' * 64, 'Image': baseline, 'State': {'Running': True},
               'Config': {'Labels': {'org.opencontainers.image.revision': 'e' * 40}}}
    backend = SimpleNamespace(profile=profile, policy=policy, inspect=lambda: current,
                              health=lambda: {'state': 'HOST_READY'})
    launches = []
    inject = True

    def compose(_, operation, *args, override=None):
        if operation == 'stop':
            if inject and boundary == 'before-stop':
                raise Interrupted
            current['State']['Running'] = False
            if inject and boundary == 'after-stop':
                raise Interrupted
        else:
            assert current['State']['Running'] is False
            current['Image'] = override['services'][profile['service']]['image']
            current['State']['Running'] = True
            launches.append(current['Image'])
            if inject and boundary == 'created':
                raise Interrupted

    def confirm(self, image, override, record, **kwargs):
        if inject and boundary == 'postflight':
            raise Interrupted
        if inject and boundary == 'readiness':
            raise Denied('HOST_NOT_HEALTHY')

    def verify_checkpoint(self, record, **kwargs):
        if inject and boundary == 'checkpointed':
            raise Interrupted

    monkeypatch.setattr(staging_dispatch, 'protected_file', lambda *a, **k: None)
    monkeypatch.setattr(os, 'geteuid', lambda: 1000, raising=False)
    monkeypatch.setattr(staging_tenant_rehearsal, 'verify_activation_checkpoint', lambda *a, **k: None)
    monkeypatch.setattr(PilotCutover, 'attest', lambda self: None)
    monkeypatch.setattr(PilotCutover, 'inspect', lambda self: current)
    monkeypatch.setattr(PilotCutover, 'compose', compose)
    monkeypatch.setattr(PilotCutover, 'confirm', confirm)
    monkeypatch.setattr(PilotCutover, 'verify_checkpoint', verify_checkpoint)
    mock_checkpoint = Mock(return_value={'backupBatch': 'verified', 'backupReceiptSha256': 'f' * 64})
    monkeypatch.setattr(PilotCutover, 'checkpoint', mock_checkpoint)

    first = PilotCutover(backend, prepared, baseline, '123')
    if boundary == 'complete':
        assert first.run()['state'] == 'PILOT_ACTIVE'
    elif boundary == 'readiness':
        # Fail the candidate health check, but let the immediate fallback prove healthy.
        def ready(self, image, *args, **kwargs):
            if image == candidate:
                raise Denied('HOST_NOT_HEALTHY')
        monkeypatch.setattr(PilotCutover, 'confirm', ready)
        assert first.run()['state'] == 'PILOT_ROLLED_BACK'
    else:
        with pytest.raises(Interrupted):
            first.run()
    inject = False
    retry = PilotCutover(backend, prepared, baseline, '124')
    expected = {'complete': 'PILOT_ACTIVE', 'before-stop': 'PILOT_STOP_CANCELLED'}.get(boundary, 'PILOT_ROLLED_BACK')
    assert retry.run()['state'] == expected
    assert retry.run()['activationRun'] == '123'
    assert launches.count(candidate) <= 1
    assert launches.count(fallback) == int(expected == 'PILOT_ROLLED_BACK')
    assert (primary.read_bytes(), secondary.read_bytes()) == (b'primary', b'secondary')


def test_cutover_requires_enrolled_workflow_and_holds_legacy_operations(tmp_path):
    from tools.staging_automation.bridge import execute

    challenge = {'operation': 'tenant-pilot-cutover'}
    with pytest.raises(Denied, match='OPERATION_NOT_ALLOWED'):
        execute('challenge', challenge, {}, {}, None, Mock(), now=1)
    (tmp_path / 'tenant-pilot-cutover.json').touch()
    backend = SimpleNamespace(profile={'workRoot': tmp_path})
    with pytest.raises(Denied, match='OPERATION_NOT_ALLOWED'):
        execute('challenge', {'operation': 'restart'}, {'tenantPilotCutover': {}}, {}, backend, Mock(), now=1)


def test_cutover_status_runs_with_only_the_installed_bridge_overlay(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path('tools/staging_automation')
    overlay = tmp_path / 'staging_automation'
    overlay.mkdir()
    for name in ('__init__.py', 'bridge.py', 'oidc.py', 'policy.py', 'pilot_activation.py'):
        shutil.copyfile(root / name, overlay / name)
    script = """
import json, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from staging_automation.bridge import execute
assert not any(k.startswith("tools.") for k in sys.modules)
policy = {'tenantPilotActivation': {}, 'tenantPilotCutover': {}, 'controllerCommit': 'controller',
          'legacyWorkerCommit': 'worker', 'publicKeySha256': 'public', 'limits': {}}
backend = SimpleNamespace(profile={'workRoot': sys.argv[1]})
print(json.dumps(execute('status', None, policy, {}, backend, lambda _: None, now=1)))
"""
    process = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path)], cwd=tmp_path,
                             capture_output=True, text=True, check=True)
    result = json.loads(process.stdout)
    assert result['tenantPilotCutoverEnrolled'] is True
    assert result['tenantPilotCutoverJournal'] is False
    assert result['tenantPilotPreparationState'] == 'MISSING'


def test_failed_cutover_diagnostic_binds_receipt_and_avoids_sqlite_sidecars(checkpoint):
    from tools import staging_pilot_cutover_diagnostics as diagnostic
    from tools.staging_automation.pilot_cutover import digest

    proof = {'runId': diagnostic.PREPARATION_RUN, 'fallbackRollbackRehearsal': 'PASS'}
    state = {'tenantPilotActivationPrepared': proof}
    record = {'activationRun': diagnostic.ACTIVATION_RUN, 'phase': 'STOPPING',
              'failedAt': 'STOPPING', 'failure': 'PILOT_SECONDARY_BACKUP_REJECTED',
              'baselineContainer': 'f' * 64,
              'binding': {'preparedSha256': digest(proof),
                          **{key + 'Image': value for key, value in diagnostic.IMAGES.items()}}}
    assert diagnostic.verify_journal(state, record) == {
        'phase': 'STOPPING', 'failedAt': 'STOPPING', 'failure': 'PILOT_SECONDARY_BACKUP_REJECTED'}
    with pytest.raises(ValueError, match='JOURNAL_BINDING_REJECTED'):
        diagnostic.verify_journal(state, {**record, 'activationRun': '1'})
    primary = checkpoint[0] / 'primary.sqlite'
    original = primary.read_bytes()
    assert diagnostic.inspect_database(primary)['state'] == 'INTEGRITY_PASS'
    primary.with_name(primary.name + '-wal').touch()
    assert diagnostic.inspect_database(primary)['state'] == 'SIDECAR_PRESENT'
    assert primary.read_bytes() == original
