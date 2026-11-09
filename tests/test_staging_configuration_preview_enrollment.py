"""Offline enrollment rehearsal using disposable files and immutable Git fixtures."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import extend_staging_host_promotion as enrollment
from tools.staging_automation import host_promotion


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
    git[f'{commit}:{module_paths["host_promotion.py"]}'] = f'COMMIT = {host_promotion.COMMIT!r}'.encode()
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
        assert receipt['approvedHostCommit'] == host_promotion.COMMIT
        assert receipt['archiveSha256'] == host_promotion.ARCHIVE_SHA256
        assert receipt['catalogPreviewPolicyPreserved'] is True
        assert receipt['hostPromotionRequiresProtectedOwnerReview'] is True
        for key in ('databaseChanged', 'containersChanged', 'gatewayStarted', 'sshPolicyChanged', 'productionChanged'):
            assert receipt[key] is False
    policy = json.loads(installation.policy_path.read_bytes())
    assert policy == {**installation.policy, 'controllerCommit': installation.commit,
                      'hostPromotion': {'commit': host_promotion.COMMIT, 'archiveSha256': host_promotion.ARCHIVE_SHA256,
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
