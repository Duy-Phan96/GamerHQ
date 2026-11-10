"""Offline enrollment rehearsal using disposable files and immutable Git fixtures."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import extend_staging_host_promotion as enrollment


@pytest.fixture
def installation(tmp_path, monkeypatch):
    import pwd

    uid, gid = os.getuid(), os.getgid()
    root, control = tmp_path / 'root', tmp_path / 'control'
    revisions = root / 'automation-controller' / 'revisions'
    old = enrollment.CATALOG_CONTROLLER
    commit = '1' * 40
    previous = revisions / old / 'staging_automation'
    previous.mkdir(parents=True)
    control.mkdir()
    worker = root / 'worker'
    worker.mkdir(mode=0o700)
    lock = worker / 'operation.lock'
    lock.touch(mode=0o600)
    state = worker / 'automation-state.json'
    state.write_text(json.dumps({'enabled': True, 'bootstrapVerified': True, 'approvedImage': 'sha256:' + 'a' * 64}))
    entry = root / 'automation-controller' / 'entry.py'
    entry.write_text("import sys\nsys.path[:0]=[" + repr(str(previous.parent))
                     + ",'/opt/gamerhq-staging/deployment-worker']\nfrom staging_automation.bridge import entry\nentry()\n")
    workflow_root = '.github/workflows/'
    workflow_path = workflow_root + 'staging-host-promotion.yml'
    catalog_path = workflow_root + 'staging-catalog-preview.yml'
    routine_path = workflow_root + 'staging-automation.yml'
    security_path = workflow_root + 'staging-automation-activate.yml'
    policy = {
        'controllerCommit': old, 'legacyWorkerCommit': enrollment.LEGACY, 'repository': 'Duy-Phan96/GamerHQ',
        'hostPromotion': {'commit': enrollment.HOST, 'archiveSha256': enrollment.ARCHIVE,
                          'workflowSha256': hashlib.sha256(b'old workflow').hexdigest()},
        'catalogPreviewWorkflowSha256': hashlib.sha256(b'catalog workflow').hexdigest(),
        'routineWorkflowSha256': hashlib.sha256(b'routine workflow').hexdigest(),
        'securityWorkflowSha256': hashlib.sha256(b'security workflow').hexdigest(),
        'bootstrapRun': '101', 'limits': {'restartsPerHour': 2},
    }
    policy_path = control / 'automation-policy.json'
    policy_path.write_text(json.dumps(policy))
    git = {
        f'{old}:{workflow_path}': b'old workflow',
        f'{old}:{catalog_path}': b'catalog workflow',
        f'{commit}:{workflow_path}': b'new workflow',
        f'{commit}:{routine_path}': b'routine workflow',
        f'{commit}:{security_path}': b'security workflow',
        f'{commit}:tools/extend_staging_host_promotion.py': Path(enrollment.__file__).read_bytes(),
    }
    module_paths = {name: f'tools/staging_automation/{name}' for name in enrollment.MODULES}
    module_paths['deployment.py'] = 'hosts/gamerhq/skill_update_controller.py'
    for name, path in {**module_paths, **enrollment.CATALOG_SOURCES}.items():
        original = name.encode()
        (previous / name).write_bytes(original)
        git[f'{old}:{path}'] = original
        git[f'{commit}:{path}'] = original
    git[f'{commit}:{module_paths["host_promotion.py"]}'] = f'COMMIT = {enrollment.CONFIGURATION_HOST!r}'.encode()
    git[f'{commit}:{module_paths["policy.py"]}'] = b'new validation'

    def run(argv, **kwargs):
        if argv[0] == '/usr/sbin/restorecon':
            return SimpleNamespace(stdout=b'')
        assert argv[0] == 'git'
        return SimpleNamespace(stdout=git[argv[-1]] if argv[-2] == 'show' else b'')

    def directory(path, gid=None):
        if not path.is_dir():
            assert gid is not None
            path.mkdir()

    monkeypatch.setattr(enrollment, 'ROOT', root)
    monkeypatch.setattr(enrollment, 'CONTROL', control)
    monkeypatch.setattr(enrollment.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=uid, pw_gid=gid))
    monkeypatch.setattr(enrollment.subprocess, 'run', run)
    monkeypatch.setattr(enrollment, 'directory', directory)
    monkeypatch.setattr(enrollment, 'trusted', lambda path, *args, **kwargs: path.read_bytes())
    monkeypatch.setattr(enrollment, 'atomic', lambda path, data, gid: path.write_bytes(data))
    return SimpleNamespace(commit=commit, git=git, revisions=revisions, previous=previous,
                           policy=policy, policy_path=policy_path, entry=entry, state=state)


def enroll(fixture):
    return enrollment.extension(fixture.commit, 'ENROLL STAGING CONFIGURATION PREVIEW', configuration_preview=True)


def test_enrollment_reuses_the_catalog_controller_and_is_retry_safe(installation):
    state_before = installation.state.read_bytes()
    for _ in range(2):
        receipt = enroll(installation)
        assert receipt['state'] == 'PIN_INSTALLED'
        assert receipt['approvedHostCommit'] == enrollment.CONFIGURATION_HOST
        assert receipt['archiveSha256'] == enrollment.CONFIGURATION_ARCHIVE
        assert receipt['catalogPreviewPolicyPreserved'] is True
        assert receipt['hostPromotionRequiresProtectedOwnerReview'] is True
        for key in ('databaseChanged', 'containersChanged', 'gatewayStarted', 'sshPolicyChanged', 'productionChanged'):
            assert receipt[key] is False
    policy = json.loads(installation.policy_path.read_bytes())
    assert policy == {**installation.policy, 'controllerCommit': installation.commit,
                      'hostPromotion': {'commit': enrollment.CONFIGURATION_HOST, 'archiveSha256': enrollment.CONFIGURATION_ARCHIVE,
                                        'workflowSha256': hashlib.sha256(b'new workflow').hexdigest()}}
    destination = installation.revisions / installation.commit / 'staging_automation'
    for name in (*enrollment.CATALOG_SOURCES, 'bridge.py', 'oidc.py', 'deployment.py'):
        assert (destination / name).read_bytes() == (installation.previous / name).read_bytes()
    assert installation.state.read_bytes() == state_before


@pytest.mark.parametrize('change, reason', [
    ('prior-catalog-policy', 'EXISTING_CATALOG_POLICY_CHANGED'),
    ('prior-host-pin', 'EXISTING_HOST_POLICY_CHANGED'),
    ('installed-catalog-source', 'INSTALLED_SOURCE_MISMATCH'),
    ('unrelated-module', 'UNRELATED_CONTROLLER_SOURCE_CHANGED'),
])
def test_enrollment_fails_before_switching_policy_or_entry(installation, change, reason):
    if change == 'prior-catalog-policy':
        installation.policy['catalogPreviewWorkflowSha256'] = 'f' * 64
    elif change == 'prior-host-pin':
        installation.policy['hostPromotion']['archiveSha256'] = 'f' * 64
    elif change == 'installed-catalog-source':
        (installation.previous / 'catalog_import.py').write_bytes(b'unreviewed planner')
    else:
        installation.git[f'{installation.commit}:tools/staging_automation/oidc.py'] = b'unreviewed authentication'
    installation.policy_path.write_text(json.dumps(installation.policy))
    policy, entry = installation.policy_path.read_bytes(), installation.entry.read_bytes()
    with pytest.raises(ValueError, match=reason):
        enroll(installation)
    assert installation.policy_path.read_bytes() == policy
    assert installation.entry.read_bytes() == entry
    assert not (installation.revisions / installation.commit).exists()


def held_configuration(installation, monkeypatch, *, latched=True):
    old = installation.policy['controllerCommit']
    commit = installation.commit
    monkeypatch.setattr(enrollment, 'FAILED_CONFIGURATION_CONTROLLER', old)
    workflow = '.github/workflows/staging-host-promotion.yml'
    reviewed_workflow = installation.git[f'{old}:{workflow}']
    installation.git[f'{commit}:{workflow}'] = reviewed_workflow
    policy_module = 'tools/staging_automation/policy.py'
    installation.git[f'{commit}:{policy_module}'] = installation.git[f'{old}:{policy_module}']
    installation.policy['hostPromotion'] = {'commit': enrollment.CONFIGURATION_HOST, 'archiveSha256': enrollment.CONFIGURATION_ARCHIVE,
                                             'workflowSha256': hashlib.sha256(reviewed_workflow).hexdigest()}
    installation.policy_path.write_text(json.dumps(installation.policy))
    state = json.loads(installation.state.read_bytes())
    state['recoveryRequired'] = latched
    installation.state.write_text(json.dumps(state))


def install_held(fixture):
    return enrollment.extension(fixture.commit, 'INSTALL STAGING PROMOTION FIX WITH RECOVERY HELD',
                                configuration_recovery=True)


def test_recovery_revision_preserves_the_exact_latch_pin_and_unsafe_backup(installation, monkeypatch):
    held_configuration(installation, monkeypatch)
    backup_dir = enrollment.ROOT / 'backups'
    backup_dir.mkdir()
    unsafe = backup_dir / enrollment.SKILL_UPDATE_BACKUP
    unsafe.write_bytes(b'private fixture')
    unsafe.chmod(0o640)
    before = installation.state.read_bytes()
    for _ in range(2):
        receipt = install_held(installation)
        assert receipt['state'] == 'FIX_INSTALLED_RECOVERY_HELD'
        assert receipt['recoveryRequired'] is True
        assert receipt['skillUpdateBackupMode'] == '0640'
        assert receipt['promotionPinChanged'] is False
        assert installation.state.read_bytes() == before
        assert json.loads(installation.policy_path.read_bytes()) == {
            **installation.policy, 'controllerCommit': installation.commit}
    assert unsafe.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize('change, reason', [
    ('latch', 'RECOVERY_LATCH_REQUIRED'),
    ('workflow', 'EXISTING_HOST_POLICY_CHANGED'),
    ('policy', 'UNRELATED_CONTROLLER_SOURCE_CHANGED'),
])
def test_recovery_revision_refuses_unreviewed_changes(installation, monkeypatch, change, reason):
    held_configuration(installation, monkeypatch, latched=change != 'latch')
    if change == 'workflow':
        installation.git[f'{installation.commit}:.github/workflows/staging-host-promotion.yml'] = b'changed'
    elif change == 'policy':
        installation.git[f'{installation.commit}:tools/staging_automation/policy.py'] = b'changed'
    before = [file.read_bytes() for file in (installation.state, installation.policy_path, installation.entry)]
    with pytest.raises(ValueError, match=reason):
        install_held(installation)
    assert [file.read_bytes() for file in (installation.state, installation.policy_path, installation.entry)] == before
    assert not (installation.revisions / installation.commit).exists()


@pytest.mark.parametrize('baseline', [True, False])
def test_structure_enrollment_requires_the_installed_managed_message_host(installation, monkeypatch, baseline):
    old, commit = installation.policy['controllerCommit'], installation.commit
    monkeypatch.setattr(enrollment, 'ACTIVE_MANAGED_MESSAGES_CONTROLLER', old)
    installation.policy['hostPromotion'].update(
        commit=enrollment.MANAGED_MESSAGES_HOST, archiveSha256=enrollment.MANAGED_MESSAGES_ARCHIVE)
    installation.policy_path.write_text(json.dumps(installation.policy))
    for name in ('policy.py', 'host_promotion.py'):
        path = f'tools/staging_automation/{name}'
        installation.git[f'{commit}:{path}'] = (installation.git[f'{old}:{path}'] if name == 'policy.py'
                                                 else f'COMMIT = {enrollment.STRUCTURE_HOST!r}'.encode())
    profile = enrollment.CONTROL / 'settings/profile.json'
    profile.parent.mkdir()
    profile.write_text(json.dumps({
        'environment': 'staging',
        'applicationHostCommit': enrollment.MANAGED_MESSAGES_HOST if baseline else enrollment.CONFIGURATION_HOST,
        'baseImage': json.loads(installation.state.read_bytes())['approvedImage'],
    }))
    before = [file.read_bytes() for file in (installation.state, profile)]
    if not baseline:
        with pytest.raises(ValueError, match='INSTALLED_STAGING_BASELINE_REQUIRED'):
            enrollment.extension(commit, 'ENROLL STAGING STRUCTURE PHASES', structure=True)
        assert not (installation.revisions / commit).exists()
    else:
        for _ in range(2):
            receipt = enrollment.extension(commit, 'ENROLL STAGING STRUCTURE PHASES', structure=True)
            assert receipt['previousHostCommit'] == enrollment.MANAGED_MESSAGES_HOST
            assert receipt['approvedHostCommit'] == enrollment.STRUCTURE_HOST
            assert receipt['archiveSha256'] == enrollment.STRUCTURE_ARCHIVE
            assert receipt['catalogPreviewPolicyPreserved'] is True
            assert receipt['hostPromotionRequiresProtectedOwnerReview'] is True
    assert [file.read_bytes() for file in (installation.state, profile)] == before
