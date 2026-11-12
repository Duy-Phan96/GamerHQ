"""Offline test of the owner read-only promotion receipt and its output scope."""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from tools import staging_host_promotion_diagnostics as diagnostic


@pytest.fixture
def live_lookup_profile():
    return {'stagingGuildId': '1567098041641738331', 'stagingBotId': '1567098530525351976',
            'productionGuildId': '100', 'productionBotId': '200', 'categoryId': '300', 'voiceCategoryId': '400'}


@pytest.mark.parametrize(('missing', 'category', 'step'), [
    ('/members/', None, 'bot-membership'), ('/channels/300', '300', 'text-category'),
    ('/channels/400', '400', 'voice-category'),
])
def test_discord_preflight_identifies_404_without_exposing_credential(live_lookup_profile, missing, category, step):
    profile = live_lookup_profile
    guild, bot = profile['stagingGuildId'], profile['stagingBotId']
    responses = {'/users/@me': {'id': bot}, '/users/@me/guilds?limit=200': [{'id': guild}],
                 '/guilds/' + guild: {'id': guild}, f'/guilds/{guild}/members/{bot}': {'roles': []},
                 f'/guilds/{guild}/roles': [], '/channels/300': {'guild_id': guild, 'type': 4},
                 '/channels/400': {'guild_id': guild, 'type': 4}, f'/guilds/{guild}/channels': []}
    seen = []

    def read(path, token):
        seen.append(path)
        assert token == 'private-token'
        if missing in path:
            raise HTTPError(path, 404, 'private-token', None,
                            io.BytesIO(b'{"code":10003,"message":"private-token"}'))
        return responses[path]

    result = diagnostic.discord_preflight(profile, 'private-token', read=read)
    assert (result['state'], result['step'], result['httpStatus']) == ('HTTP_REJECTED', step, 404)
    assert result['discordCode'] == 10003
    assert result.get('categoryId') == category
    if category is not None:
        assert seen[-2:] == [missing, f'/guilds/{guild}/channels']
        assert result['guildInventory'] == {'state': 'ABSENT_FROM_BOT_INVENTORY'}
    else:
        assert missing in seen[-1] and 'guildInventory' not in result
    assert 'private-token' not in json.dumps(result)


@pytest.mark.parametrize(('member_allow', 'visible'), [(True, True), (False, False)])
def test_category_inventory_reports_effective_view_without_returning_other_channels(member_allow, visible):
    guild, category, bot, role = '100', '200', '300', '400'
    overwrites = [{'id': guild, 'type': 0, 'allow': '0', 'deny': '1024'},
                  {'id': role, 'type': 0, 'allow': '1024', 'deny': '0'},
                  {'id': bot, 'type': 1, 'allow': '1024' if member_allow else '0',
                   'deny': '0' if member_allow else '1024'}]

    def read(path, token):
        assert path == f'/guilds/{guild}/channels'
        return [{'id': 'unrelated', 'name': 'unrelated private channel'},
                {'id': category, 'type': 4, 'permission_overwrites': overwrites}]

    assert diagnostic.inventory_category(read, 'private-token', guild, category, bot, {'roles': [role]},
                                         [{'id': guild, 'permissions': '0'}]) == {
        'state': 'CATEGORY_FOUND', 'botViewChannel': visible}


def test_discord_preflight_stops_before_guild_lookups_on_wrong_bot(live_lookup_profile):
    def read(path, _token):
        assert path == '/users/@me'
        return {'id': live_lookup_profile['productionBotId']}

    assert diagnostic.discord_preflight(live_lookup_profile, 'private-token', read=read) == {
        'step': 'bot-identity', 'completed': [], 'state': 'IDENTITY_REJECTED'}


def test_event_inventory_distinguishes_live_parent_and_stored_channel_ownership(tmp_path):
    database = tmp_path / 'staging.sqlite'
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executescript('''
            CREATE TABLE settings (key, value);
            CREATE TABLE skill_managed_resources (guild_id, skill_id, resource_key, kind, state_json);
            INSERT INTO settings VALUES ('skill_resources:1:event-manager','{"categoryId":10,"voiceCategoryId":20}');
            INSERT INTO skill_managed_resources VALUES
                (1,'event-manager','event:a','text','{"channelId":30,"marker":"private-marker"}'),
                (1,'event-manager','event:b','voice','{"channelId":40}'),
                (1,'other','event:c','text','{"channelId":70}'),
                (2,'event-manager','event:d','text','{"channelId":80}');
        ''')
    before = database.read_bytes()
    stored, bindings = diagnostic.event_resource_bindings({'database': str(database), 'stagingGuildId': '1'})
    assert stored == {'categoryId': '10', 'voiceCategoryId': '20'}
    result = diagnostic.event_inventory([
        {'id': '20', 'type': 4, 'name': 'VOICE TESTING'},
        {'id': '50', 'type': 4, 'name': '🧪 EVENT TESTING'},
        {'id': '60', 'type': 4, 'name': 'unrelated'},
        {'id': '30', 'type': 0, 'parent_id': '50', 'topic': 'private-marker'},
        {'id': '70', 'type': 0, 'parent_id': '60'},
    ], '1', ('10', '20'), bindings)
    assert result['categories'] == [
        {'categoryId': '20', 'matchesEventTestingName': False, 'parentOfStoredBinding': False},
        {'categoryId': '50', 'matchesEventTestingName': True, 'parentOfStoredBinding': True},
    ]
    assert result['storedChannelBindings'] == [
        {'channelId': '30', 'storedKind': 'text', 'presentInBotInventory': True,
         'parentCategoryId': '50', 'typeMatches': True, 'textMarkerMatches': True},
        {'channelId': '40', 'storedKind': 'voice', 'presentInBotInventory': False},
    ]
    assert result['childrenOfReportedAreas'] == [{'channelId': '30', 'parentCategoryId': '50', 'discordType': 0}]
    assert 'private-marker' not in json.dumps(result) and database.read_bytes() == before


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
    with pytest.raises(diagnostic.ControllerSourceMismatch, match='CONTROLLER_SOURCE_MISMATCH') as error:
        diagnostic.verify_controller(policy)
    assert error.value.evidence == {
        'component': module.name, 'reason': 'CHECKSUM_MISMATCH',
        'expectedSha256': hashlib.sha256(b'reviewed code').hexdigest(),
        'actualSha256': hashlib.sha256(b'changed code').hexdigest(),
    }


def test_localized_controller_manifest_matches_the_enrollment_bundle(tmp_path, monkeypatch):
    profile = diagnostic.LOCALIZED_PREFLIGHT
    for name, value in profile.items():
        monkeypatch.setattr(diagnostic, name, value)
    commit = profile['CONTROLLER']
    repo = Path(__file__).resolve().parents[1]
    ref = subprocess.run(['git', 'cat-file', '-e', commit + '^{commit}'], cwd=repo, capture_output=True)
    if ref.returncode and not os.environ.get('GITHUB_ACTIONS'):
        pytest.skip('Reviewed controller revision unavailable in this local checkout')
    ref.check_returncode()

    def blob(path):
        return subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=repo)

    monkeypatch.setattr(diagnostic, 'ROOT', tmp_path)
    controller = tmp_path / 'automation-controller'
    revision = controller / 'revisions' / commit
    package = revision / 'staging_automation'
    package.mkdir(parents=True)
    (controller / 'entry.py').write_text("import sys\nsys.path[:0]=[" + repr(str(revision))
        + ",'/opt/gamerhq-staging/deployment-worker']\nfrom staging_automation.bridge import entry\nentry()\n")
    installed_sources = {'deployment.py': 'hosts/gamerhq/skill_update_controller.py',
                         'catalog_import.py': 'tools/game_catalog_import.py',
                         'catalog_manifest.json': 'data/game_catalog_expansion.json'}
    for name in profile['CONTROLLER_FILES']:
        (package / name).write_bytes(blob(installed_sources.get(name, 'tools/staging_automation/' + name)))
    fixed_file = diagnostic.fixed_file
    monkeypatch.setattr(diagnostic, 'fixed_file', lambda path, *, uid, limit: fixed_file(path, limit=limit))
    policy = {'controllerCommit': commit,
              'hostPromotion': {'commit': profile['TARGET'], 'archiveSha256': profile['ARCHIVE'],
                                'workflowSha256': profile['PROMOTION_WORKFLOW']},
              'catalogPreviewWorkflowSha256': profile['CATALOG_WORKFLOW']}
    diagnostic.verify_controller(policy)

    # The checkout shim is valid source for development, but invalid on the VPS.
    (package / 'deployment.py').write_bytes(blob('tools/staging_automation/deployment.py'))
    with pytest.raises(diagnostic.ControllerSourceMismatch) as error:
        diagnostic.verify_controller(policy)
    assert error.value.evidence['component'] == 'deployment.py'


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


def healthy_held_report():
    return {
        'controllerSourceVerified': True, 'recoveryRequired': True, 'automationEnabled': True,
        'profileHostCommit': diagnostic.BASELINE,
        'container': {'hostCommit': diagnostic.BASELINE, 'running': True, 'dockerHealthy': True,
                      'matchesApprovedImage': True, 'runtimeInspection': 'PASS',
                      'runtimeDatabaseMatchesProfile': True, 'packageVersion': '0.1.2',
                      'wheelMatchesReviewedArtifact': True},
        'database': {'integrityPass': True, 'eventManagerAuthority': True, 'skillInstalled': True,
                     'skillConfigured': True, 'skillEnabled': False, 'skillVersion': '0.1.1',
                     'knownSkillUpdateMatchesApprovedImageAndArtifact': True,
                     'attemptsSinceFailedRun': [], 'preexistingHostBlockers': [], 'pendingDeploymentCount': 0,
                     'lastVerifiedHostBackup': {'checksumMatches': True, 'integrityPass': True, 'restoreProbe': 'PASS'},
                     'latestPreRunBackupMatchesKnownSkillUpdate': True,
                     'latestPreRunBackup': {'mode': '0640', 'inspection': 'MODE_UNSAFE',
                                            'checksumMatches': None, 'restoreProbe': 'UNAVAILABLE'}},
    }
def test_held_baseline_requires_exact_artifact_and_empty_promotion_history():
    report = healthy_held_report()
    assert all(diagnostic.held_baseline_checks(report).values())
    report['container']['wheelMatchesReviewedArtifact'] = False
    report['database']['attemptsSinceFailedRun'] = [{'state': 'READY'}]
    report['database']['skillEnabled'] = True
    checks = diagnostic.held_baseline_checks(report)
    assert not checks['verifiedSkillAndDatabaseBinding']
    assert not checks['noCandidateAttemptOrHostBlocker']
    assert not checks['guildLifecyclePreserved']


@pytest.mark.parametrize('corrupt_hash', [False, True])
def test_explicit_release_only_after_private_backup_verifies(tmp_path, monkeypatch, corrupt_hash):
    backup = tmp_path / (diagnostic.KNOWN_SKILL_UPDATE + '.sqlite')
    ledger(backup)
    original_bytes = backup.read_bytes()
    checksum = hashlib.sha256(original_bytes).hexdigest()
    backup.chmod(0o640)
    evidence = {'backup': {'sha256': 'f' * 64 if corrupt_hash else checksum}}
    state_path = tmp_path / 'automation-state.json'
    state = {'recoveryRequired': True, 'enabled': True, 'bootstrapVerified': True, 'approvedImage': 'existing', 'limits': {'restartsPerHour': 2}}
    state_path.write_text(json.dumps(state))
    state_path.chmod(0o600)
    report = healthy_held_report()
    phases = []

    def audit(_id, phase, **details):
        phases.append(phase)

    monkeypatch.setattr(diagnostic, 'audit_release', audit)
    uid = os.getuid()
    original_digest = diagnostic.digest
    def private_digest(path):
        assert path.stat().st_mode & 0o777 == 0o600
        return original_digest(path)
    monkeypatch.setattr(diagnostic, 'digest', private_digest)

    def refresh():
        report['database']['latestPreRunBackup'] = diagnostic.receipt_for_backup(
            tmp_path, diagnostic.KNOWN_SKILL_UPDATE, evidence, uid, guild_id=12)

    before = backup.stat()
    receipt = diagnostic.release_hold(report, state_path, state, tmp_path, uid, refresh)
    after = backup.stat()
    assert before.st_ino == after.st_ino and before.st_mtime_ns == after.st_mtime_ns
    assert backup.read_bytes() == original_bytes
    assert receipt['backupModeChanged'] and after.st_mode & 0o777 == 0o600
    assert json.loads(state_path.read_text()) == {**state, 'recoveryRequired': corrupt_hash}
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert receipt['recoveryReleaseAuthorized'] is not corrupt_hash
    if corrupt_hash:
        assert phases == ['STARTED', 'BACKUP_MODE_CHECK_STARTED', 'BACKUP_PRIVATE', 'VERIFICATION_REJECTED']
        assert receipt['state'] == 'RECOVERY_HELD' and receipt['recoveryRequired']
    else:
        assert phases == ['STARTED', 'BACKUP_MODE_CHECK_STARTED', 'BACKUP_PRIVATE', 'RELEASE_VERIFIED', 'RELEASED']
        assert receipt['state'] == 'RECOVERY_HOLD_RELEASED' and not receipt['recoveryRequired']


@pytest.mark.parametrize('blocker', ['no-latch', 'pending-deployment', 'unverified-host-backup'])
def test_release_rejects_before_touching_backup(tmp_path, monkeypatch, blocker):
    report = healthy_held_report()
    state = {'recoveryRequired': blocker != 'no-latch', 'bootstrapVerified': True}
    if blocker == 'pending-deployment':
        report['database']['pendingDeploymentCount'] = 1
    elif blocker == 'unverified-host-backup':
        report['database']['lastVerifiedHostBackup']['restoreProbe'] = 'FAILED'
    monkeypatch.setattr(diagnostic, 'audit_release', lambda *args: None)
    def unexpected(*args):
        pytest.fail('An unverified release touched recovery state or backup')
    monkeypatch.setattr(diagnostic, 'private_backup_mode', unexpected)
    monkeypatch.setattr(diagnostic, 'release_state', unexpected)
    receipt = diagnostic.release_hold(report, tmp_path / 'state', state, tmp_path, os.getuid(), unexpected)
    assert receipt['state'] in {'RECOVERY_HELD', 'REVIEW_REQUIRED'}
    assert not receipt['backupModeChanged']


@pytest.mark.parametrize('redirect', ['symlink', 'hardlink', 'sidecar', 'unexpected-mode'])
def test_backup_correction_refuses_other_inodes_or_modes(tmp_path, redirect):
    path = tmp_path / (diagnostic.KNOWN_SKILL_UPDATE + '.sqlite')
    target = tmp_path / 'other.sqlite' if redirect in {'symlink', 'hardlink'} else path
    target.write_bytes(b'fixture')
    mode = 0o644 if redirect == 'unexpected-mode' else 0o640
    target.chmod(mode)
    if redirect == 'symlink':
        path.symlink_to(target)
    elif redirect == 'hardlink':
        os.link(target, path)
    elif redirect == 'sidecar':
        path.with_name(path.name + '-wal').touch()
    with pytest.raises(ValueError):
        diagnostic.private_backup_mode(tmp_path, os.getuid())
    assert target.stat().st_mode & 0o777 == mode
