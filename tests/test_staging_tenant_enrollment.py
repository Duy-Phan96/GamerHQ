import hashlib
import json
from pathlib import Path

import pytest

from tools import enroll_staging_tenant_preview as enrollment


def fixture():
    image = 'sha256:' + 'a' * 64
    policy = {'controllerCommit': enrollment.PREVIOUS, 'legacyWorkerCommit': enrollment.WORKER,
              'repository': 'Duy-Phan96/GamerHQ', 'hostPromotion': {'commit': enrollment.HOST},
              'routineWorkflowSha256': 'existing', 'catalogPreviewWorkflowSha256': 'existing-catalog'}
    profile = {'environment': 'staging', 'applicationHostCommit': enrollment.HOST,
               'hostSourceCommit': enrollment.WORKER, 'stagingBotId': enrollment.BINDING['applicationId'],
               'stagingGuildId': enrollment.BINDING['guildIds'][0], 'productionGuildId': '333',
               'productionBotId': '222', 'baseImage': image}
    state = {'enabled': True, 'bootstrapVerified': True, 'recoveryRequired': False, 'approvedImage': image}
    return policy, profile, state


def test_tenant_enrollment_sets_only_exact_reviewed_preview_policy():
    policy, profile, state = fixture()
    manifest = Path('deploy/staging/tenant-pilot-binding.json')
    assert json.loads(manifest.read_text()) == enrollment.BINDING
    source = '1' * 40
    workflow = b'reviewed-protected-preview'
    result = enrollment.updated_policy(policy, source, workflow, profile, state)
    assert result == {**policy, 'controllerCommit': source, 'tenantPilot': enrollment.BINDING,
                      'tenantPilotWorkflowSha256': hashlib.sha256(workflow).hexdigest()}
    assert enrollment.updated_policy(result, source, workflow, profile, state) == result
    with pytest.raises(ValueError, match='IMMUTABLE_EXTENSION_CHANGED'):
        enrollment.updated_policy(result, source, b'different-workflow', profile, state)


@pytest.mark.parametrize(('target', 'key', 'value', 'error'), [
    (0, 'legacyWorkerCommit', '2' * 40, 'REVIEWED_ENROLLMENT_REQUIRED'),
    (1, 'stagingGuildId', '123', 'INSTALLED_STAGING_BASELINE_REQUIRED'),
    (1, 'productionGuildId', enrollment.BINDING['guildIds'][1], 'STAGING_BINDING_REJECTED'),
    (2, 'recoveryRequired', True, 'ACTIVE_STAGING_REQUIRED'),
    (2, 'approvedImage', 'sha256:' + 'b' * 64, 'ACTIVE_STAGING_REQUIRED'),
])
def test_tenant_enrollment_fails_closed_on_changed_live_baseline(target, key, value, error):
    packet = fixture()
    packet[target][key] = value
    with pytest.raises(ValueError, match=error):
        enrollment.updated_policy(packet[0], '1' * 40, b'workflow', packet[1], packet[2])


def test_preparation_upgrade_keeps_existing_pins_and_requires_enrolled_preview():
    policy, profile, state = fixture()
    policy.update(controllerCommit=enrollment.ENROLLED_PREVIEW, tenantPilot=enrollment.BINDING,
                  tenantPilotWorkflowSha256=enrollment.PREVIEW_DIGEST)
    commit = 'b' * 40
    workflow = b'reviewed preparation'
    updated = enrollment.updated_policy(policy, commit, workflow, profile, state, preparation=True)
    assert {key: val for key, val in updated.items() if key not in {'controllerCommit', 'tenantPilotActivation'}} == {
        key: val for key, val in policy.items() if key != 'controllerCommit'}
    assert updated['tenantPilotActivation']['commit'] == enrollment.ACTIVATION_CANDIDATE
    assert updated['tenantPilotActivation']['archiveSha256'] == enrollment.ACTIVATION_ARCHIVE
    assert enrollment.updated_policy(updated, commit, workflow, profile, state, preparation=True) == updated
    with pytest.raises(ValueError, match='PREVIEW_POLICY_CHANGED'):
        enrollment.updated_policy({**policy, 'tenantPilotWorkflowSha256': '0' * 64},
                                  commit, workflow, profile, state, preparation=True)


def test_offline_secondary_provision_preserves_primary_and_is_repeatable(tmp_path, monkeypatch):
    from database import db
    from services.tenant_routing import pilot_environment
    from tools.provision_staging_pilot_tenant import provision

    primary = tmp_path / 'gamerhq.db'
    monkeypatch.setattr(db, 'DB_PATH', primary)
    db.init_db()
    baseline = primary.read_bytes()
    _, profile, _ = fixture()
    approved = tuple(map(int, enrollment.BINDING['guildIds']))
    pin = pilot_environment(profile, approved)
    assert provision(pin, approved[0], primary)['created'] is True
    assert provision(pin, approved[0], primary)['created'] is False
    assert (tmp_path / 'tenants' / f'{approved[1]}.sqlite').is_file()
    assert primary.read_bytes() == baseline


def test_cutover_enrollment_pins_exact_baseline_fallback_and_preparation():
    policy, profile, state = fixture()
    profile['baseImage'] = state['approvedImage'] = enrollment.BASELINE_IMAGE
    policy.update(controllerCommit=enrollment.ENROLLED_PREPARATION, tenantPilot=enrollment.BINDING,
                  tenantPilotWorkflowSha256=enrollment.PREVIEW_DIGEST,
                  tenantPilotActivation={'commit': enrollment.ACTIVATION_CANDIDATE,
                                         'archiveSha256': enrollment.ACTIVATION_ARCHIVE,
                                         'ciRun': enrollment.ACTIVATION_CI,
                                         'workflowSha256': enrollment.PREPARATION_DIGEST})
    workflow = b'exact cutover workflow'
    source = 'f' * 40
    result = enrollment.updated_policy(policy, source, workflow, profile, state, cutover=True)
    assert result == {**policy, 'controllerCommit': source, 'tenantPilotCutover': {
        'fallback': enrollment.FALLBACK, 'workflowSha256': hashlib.sha256(workflow).hexdigest()}}
    assert enrollment.updated_policy(result, source, workflow, profile, state, cutover=True) == result
    wrong = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='PREPARATION_POLICY_CHANGED'):
        enrollment.updated_policy(policy, source, workflow, {**profile, 'baseImage': wrong},
                                  {**state, 'approvedImage': wrong}, cutover=True)


def test_cutover_status_repair_preserves_the_enrolled_workflow_and_pins(monkeypatch):
    policy, profile, state = fixture()
    profile['baseImage'] = state['approvedImage'] = enrollment.BASELINE_IMAGE
    workflow = b'registered cutover'
    digest = hashlib.sha256(workflow).hexdigest()
    monkeypatch.setattr(enrollment, 'CUTOVER_DIGEST', digest)
    policy.update(controllerCommit=enrollment.ENROLLED_CUTOVER, tenantPilot=enrollment.BINDING,
                  tenantPilotWorkflowSha256=enrollment.PREVIEW_DIGEST,
                  tenantPilotActivation={'commit': enrollment.ACTIVATION_CANDIDATE,
                                         'archiveSha256': enrollment.ACTIVATION_ARCHIVE,
                                         'ciRun': enrollment.ACTIVATION_CI,
                                         'workflowSha256': enrollment.PREPARATION_DIGEST},
                  tenantPilotCutover={'fallback': enrollment.FALLBACK, 'workflowSha256': digest})
    source = '7' * 40
    result = enrollment.updated_policy(policy, source, workflow, profile, state, cutover=True, cutover_repair=True)
    assert result == {**policy, 'controllerCommit': source}
    with pytest.raises(ValueError, match='CUTOVER_POLICY_CHANGED'):
        enrollment.updated_policy(policy, source, b'different', profile, state, cutover=True, cutover_repair=True)

    held = {**state, 'recoveryRequired': True}
    policy['controllerCommit'] = enrollment.STATUS_REPAIRED
    repaired = enrollment.updated_policy(policy, source, workflow, profile, held, cutover=True,
                                         cutover_repair=True, recovery_repair=True)
    assert repaired == {**policy, 'controllerCommit': source}
    assert held['recoveryRequired'] is True
    policy['controllerCommit'] = enrollment.RECOVERY_VERIFICATION
    completed = enrollment.updated_policy(policy, source, workflow, profile, held, cutover=True,
                                          cutover_repair=True, recovery_repair=True, recovery_completion=True)
    assert completed == {**policy, 'controllerCommit': source}
    assert held['recoveryRequired'] is True
    with pytest.raises(ValueError, match='REVIEWED_ENROLLMENT_REQUIRED'):
        enrollment.updated_policy({**policy, 'controllerCommit': '0' * 40}, source, workflow, profile, held,
                                  cutover=True, cutover_repair=True, recovery_repair=True, recovery_completion=True)
    with pytest.raises(ValueError, match='ACTIVE_STAGING_REQUIRED'):
        enrollment.updated_policy(policy, source, workflow, profile, state, cutover=True,
                                  cutover_repair=True, recovery_repair=True, recovery_completion=True)

def test_recovery_completion_launcher_selects_the_enrolled_health_source(tmp_path):
    import ast

    previous = enrollment.renderer(tmp_path)
    expected = (f'import sys\nsys.path[:0]=[{str(tmp_path)!r},{str(enrollment.ROOT / "deployment-worker")!r}]\n'
                'from staging_automation.bridge import entry\nentry()\n').encode()
    assert previous == expected
    tree = ast.parse(enrollment.renderer(tmp_path, recovery_completion=True))
    assert ast.literal_eval(tree.body[1].value) == [str(tmp_path), str(tmp_path / 'pilot-source'),
                                                  str(enrollment.ROOT / 'deployment-worker')]


def test_api_context_repair_enrollment_preserves_original_cutover_and_latch(monkeypatch):
    policy, profile, state = fixture()
    profile['baseImage'] = state['approvedImage'] = enrollment.BASELINE_IMAGE
    state['recoveryRequired'] = True
    workflow = b'registered cutover'
    digest = hashlib.sha256(workflow).hexdigest()
    monkeypatch.setattr(enrollment, 'CUTOVER_DIGEST', digest)
    policy.update(controllerCommit=enrollment.RECOVERY_COMPLETION, tenantPilot=enrollment.BINDING,
                  tenantPilotWorkflowSha256=enrollment.PREVIEW_DIGEST,
                  tenantPilotActivation={'commit': enrollment.ACTIVATION_CANDIDATE,
                                         'archiveSha256': enrollment.ACTIVATION_ARCHIVE,
                                         'ciRun': enrollment.ACTIVATION_CI,
                                         'workflowSha256': enrollment.PREPARATION_DIGEST},
                  tenantPilotCutover={'fallback': enrollment.FALLBACK, 'workflowSha256': digest})
    updated = enrollment.updated_policy(policy, 'f' * 40, workflow, profile, state, cutover=True,
                                         cutover_repair=True, recovery_repair=True, recovery_completion=True, api_repair=True)
    assert updated == {**policy, 'controllerCommit': 'f' * 40, 'primaryRecoveryApiRepair': enrollment.API_REPAIR_PIN}
    assert state['recoveryRequired'] is True
