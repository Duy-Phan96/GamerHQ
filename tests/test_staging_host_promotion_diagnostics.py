"""Offline test of the owner read-only promotion receipt and its output scope."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import socket
from types import SimpleNamespace

import pytest

from tools import staging_host_promotion_diagnostics as diagnostic


def ledger(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE skill_domain_authority (guild_id, domain, skill_id);
            CREATE TABLE skill_guild_state (guild_id, skill_id, installed, configured, enabled, version, updated_at);
            CREATE TABLE skill_deployments
                (id, skill_id, state, created_at, updated_at, previous_image, evidence_json, image, version);
            INSERT INTO skill_domain_authority VALUES(12, 'event-manager', 'event-manager');
            INSERT INTO skill_guild_state VALUES(12, 'event-manager', 1, 1, 1, '0.1.2', 123);
            INSERT INTO skill_guild_state VALUES(34, 'event-manager', 1, 1, 1, '9.9.9', 123);
        """)


def record(path, ident, state, time, evidence):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO skill_deployments VALUES(?, 'event-manager', ?, ?, ?, ?, ?, 'approved-image', '0.1.2')",
                     (ident, state, time, time, 'private-image-id', json.dumps(evidence)))


def test_pre_apply_blocker_is_separate_from_candidate_attempts(tmp_path):
    db = tmp_path / 'stage.sqlite'
    ledger(db)
    record(db, 'a' * 32, 'RECOVERY_REQUIRED', diagnostic.RUN_STARTED - 60,
           {'hostSourceCommit': diagnostic.BASELINE, 'private': 'sensitive'})
    result = diagnostic.evidence_from_database(db, tmp_path, 12, os.getuid())
    assert result['eventManagerAuthority'] and result['integrityPass']
    assert result['skillVersion'] == '0.1.2'
    assert result['attemptsSinceFailedRun'] == []
    assert result['preexistingHostBlockers'][0]['state'] == 'RECOVERY_REQUIRED'
    assert 'sensitive' not in str(result) and 'private-image-id' not in str(result)
    assert not db.with_name(db.name + '-wal').exists()


def test_candidate_backup_and_restore_evidence_are_separately_verified(tmp_path):
    db = tmp_path / 'stage.sqlite'
    backup = tmp_path / ('b' * 32 + '.sqlite')
    ledger(db)
    ledger(backup)
    backup.chmod(0o600)
    checksum = hashlib.sha256(backup.read_bytes()).hexdigest()
    record(db, 'b' * 32, 'RECOVERY_REQUIRED', diagnostic.RUN_STARTED + 10,
           {'hostSourceCommit': diagnostic.TARGET, 'probe': {'healthy': True},
            'backup': {'sha256': checksum, 'verification': {'restoreProbe': 'PASS'}}})
    result = diagnostic.evidence_from_database(db, tmp_path, 12, os.getuid())
    attempt = result['attemptsSinceFailedRun'][0]
    assert attempt['previousImageRecorded'] and attempt['offlineProbePassed']
    assert attempt['backup'] == {'recorded': True, 'filePresent': True, 'checksumMatches': True,
                                 'integrityPass': True, 'restoreProbe': 'UNAVAILABLE', 'restoreProbeRecorded': True, 'mode': '0600'}
    assert checksum not in str(result) and 'b' * 32 not in str(result)


def test_backup_symlink_never_supplies_evidence(tmp_path):
    db = tmp_path / 'db.sqlite'
    ledger(db)
    alias = tmp_path / ('c' * 32 + '.sqlite')
    alias.symlink_to(db)
    evidence = diagnostic.receipt_for_backup(tmp_path, 'c' * 32, {}, os.getuid())
    assert evidence['inspection'] == 'UNAVAILABLE'
    assert evidence['integrityPass'] is None


def test_container_probe_uses_only_approved_rootless_daemon(tmp_path, monkeypatch):
    path = tmp_path / 'docker.sock'
    account = SimpleNamespace(pw_uid=os.geteuid(), pw_dir=str(tmp_path))
    state = {'approvedImage': 'stage-image'}
    profile = {'dockerDaemonId': 'approved-daemon', 'baseImage': 'stage-image'}
    (tmp_path / 'data').mkdir()
    host_db = tmp_path / 'data/gamerhq.db'
    host_db.write_bytes(b'test')
    info = host_db.stat()
    monkeypatch.setattr(diagnostic, 'ROOT', tmp_path)
    calls = []

    def run(argv, *, env, cwd, **kwargs):
        assert env['DOCKER_HOST'] == 'unix://' + str(path)
        assert cwd == diagnostic.ROOT
        calls.append(argv)
        if argv[1] == 'inspect' and '.Mounts' in argv[-2]:
            return SimpleNamespace(stdout='bind|' + str(tmp_path / 'data'))
        outputs = {
            'info': '["name=rootless"]|approved-daemon',
            'ps': 'container-present',
            'exec': json.dumps({'device': info.st_dev, 'inode': info.st_ino, 'version': '0.1.2',
                                         'wheelSha256': diagnostic.SKILL_WHEEL_SHA256}),
            'inspect': '/gamerhq-staging-bot|stage-image|running|healthy|gamerhq-staging|gamerhq-staging|'
                       + diagnostic.BASELINE,
        }
        return SimpleNamespace(stdout=outputs[argv[1]])

    monkeypatch.setattr(diagnostic.subprocess, 'run', run)
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(path))
        receipt = diagnostic.container_status(account, path, profile, state)
        assert receipt['hostCommit'] == diagnostic.BASELINE and receipt['dockerHealthy']
        assert receipt['matchesApprovedImage']
        assert receipt['runtimeDatabaseMatchesProfile'] and receipt['packageVersion'] == '0.1.2'
        assert receipt['wheelMatchesReviewedArtifact']
        assert [command[1] for command in calls] == ['info', 'ps', 'inspect', 'inspect', 'exec']
        assert 'stage-image' not in str(receipt)
        calls.clear()
        with pytest.raises(ValueError, match='ROOTLESS_SOCKET_SCOPE_REJECTED'):
            diagnostic.container_status(account, path, {**profile, 'dockerDaemonId': 'wrong-daemon'}, state)
        assert [command[1] for command in calls] == ['info']


def test_known_update_and_restore_probe_preserve_disk_bytes(tmp_path):
    db = tmp_path / 'stage.sqlite'
    backup = tmp_path / (diagnostic.KNOWN_SKILL_UPDATE + '.sqlite')
    ledger(db)
    ledger(backup)
    backup.chmod(0o600)
    before = backup.read_bytes()
    evidence = {'release': {'reviewedCommit': diagnostic.SKILL_WHEEL_COMMIT, 'sha256': diagnostic.SKILL_WHEEL_SHA256},
                'backup': {'sha256': hashlib.sha256(before).hexdigest()}}
    record(db, diagnostic.KNOWN_SKILL_UPDATE, 'APPLIED', diagnostic.RUN_STARTED - 60, evidence)
    result = diagnostic.evidence_from_database(db, tmp_path, 12, os.getuid(), approved_image='approved-image')
    assert result['knownSkillUpdateMatchesApprovedImageAndArtifact']
    assert result['latestPreRunBackup']['restoreProbe'] == 'PASS'
    assert result['latestPreRunBackup']['guildLifecycle']['skillLifecycleUpdatedAt'] == 123
    assert backup.read_bytes() == before
    sidecar = backup.with_name(backup.name + '-wal')
    sidecar.write_bytes(b'unreviewed')
    result = diagnostic.receipt_for_backup(tmp_path, diagnostic.KNOWN_SKILL_UPDATE, evidence, os.getuid(), guild_id=12)
    assert result['checksumMatches'] and result['inspection'] == 'SIDECARS_PRESENT'
    assert result['restoreProbe'] == 'UNAVAILABLE' and sidecar.read_bytes() == b'unreviewed'


def test_controller_verification_rejects_tampered_installed_source(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, 'ROOT', tmp_path)
    controller = tmp_path / 'automation-controller'
    revision = controller / 'revisions' / diagnostic.CONTROLLER
    package = revision / 'staging_automation'
    package.mkdir(parents=True)
    entry = controller / 'entry.py'
    entry.write_text("import sys\nsys.path[:0]=[" + repr(str(revision))
                     + ",'/opt/gamerhq-staging/deployment-worker']\nfrom staging_automation.bridge import entry\nentry()\n")
    module = package / 'host_promotion.py'
    module.write_bytes(b'reviewed code')
    monkeypatch.setattr(diagnostic, 'CONTROLLER_FILES', {module.name: hashlib.sha256(b'reviewed code').hexdigest()})
    fixed_file = diagnostic.fixed_file
    monkeypatch.setattr(diagnostic, 'fixed_file', lambda path, *, uid, limit: fixed_file(path, limit=limit))
    policy = {'controllerCommit': diagnostic.CONTROLLER,
              'hostPromotion': {'commit': diagnostic.TARGET, 'archiveSha256': diagnostic.ARCHIVE,
                                'workflowSha256': diagnostic.PROMOTION_WORKFLOW},
              'catalogPreviewWorkflowSha256': diagnostic.CATALOG_WORKFLOW}
    diagnostic.verify_controller(policy)
    module.write_bytes(b'changed code')
    with pytest.raises(ValueError, match='CONTROLLER_SOURCE_MISMATCH'):
        diagnostic.verify_controller(policy)


def test_verification_receipt_keeps_unsafe_backup_out_of_read_scope(tmp_path, monkeypatch):
    ident = 'd' * 32
    backup = tmp_path / (ident + '.sqlite')
    backup.write_bytes(b'unsafe')
    backup.chmod(0o640)
    def unexpected_read(_):
        pytest.fail('The unsafe backup was opened')
    monkeypatch.setattr(diagnostic, 'digest', unexpected_read)
    result = diagnostic.receipt_for_backup(tmp_path, ident, {'backup': {'sha256': 'f' * 64}}, os.getuid())
    assert result['mode'] == '0640' and result['inspection'] == 'MODE_UNSAFE'
    assert result['checksumMatches'] is None and result['restoreProbe'] == 'UNAVAILABLE'


def test_held_baseline_requires_exact_artifact_and_empty_promotion_history():
    report = {
        'controllerSourceVerified': True, 'recoveryRequired': True, 'automationEnabled': True,
        'profileHostCommit': diagnostic.BASELINE,
        'container': {'hostCommit': diagnostic.BASELINE, 'running': True, 'dockerHealthy': True,
                      'matchesApprovedImage': True, 'runtimeInspection': 'PASS',
                      'runtimeDatabaseMatchesProfile': True, 'packageVersion': '0.1.2',
                      'wheelMatchesReviewedArtifact': True},
        'database': {'integrityPass': True, 'eventManagerAuthority': True, 'skillInstalled': True,
                     'skillConfigured': True, 'skillEnabled': False, 'skillVersion': '0.1.1',
                     'knownSkillUpdateMatchesApprovedImageAndArtifact': True,
                     'attemptsSinceFailedRun': [], 'preexistingHostBlockers': [],
                     'lastVerifiedHostBackup': {'checksumMatches': True, 'integrityPass': True, 'restoreProbe': 'PASS'},
                     'latestPreRunBackupMatchesKnownSkillUpdate': True,
                     'latestPreRunBackup': {'mode': '0640', 'inspection': 'MODE_UNSAFE',
                                            'checksumMatches': None, 'restoreProbe': 'UNAVAILABLE'}},
    }
    assert all(diagnostic.held_baseline_checks(report).values())
    report['container']['wheelMatchesReviewedArtifact'] = False
    report['database']['attemptsSinceFailedRun'] = [{'state': 'READY'}]
    report['database']['skillEnabled'] = True
    checks = diagnostic.held_baseline_checks(report)
    assert not checks['verifiedSkillAndDatabaseBinding']
    assert not checks['noCandidateAttemptOrHostBlocker']
    assert not checks['guildLifecyclePreserved']
