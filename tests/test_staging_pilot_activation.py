import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from database import db, tenants
from tools.staging_automation.pilot_activation import prepare_session, validate_pin
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
