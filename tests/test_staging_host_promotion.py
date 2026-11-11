import copy
import io
import json
from pathlib import Path
import sqlite3
import tarfile
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from database import db
from tools.staging_automation.host_promotion import (ARCHIVE_SHA256, BASELINE, BASELINE_SKILL_COMMIT, BASELINE_SKILL_SHA256,
    BASELINE_SKILL_VERSION, COMMIT, REVIEWED_HOST_CHANGES, extract_application, retained_state,
    require_baseline_skill, require_retained, require_runtime_lifecycle, PromotionDocker, promote, verify_compatibility)
from tools.staging_automation.policy import Denied, charge, validate_policy, admit
from tools.staging_automation.oidc import verify
from test_staging_automation_policy import trust, signing_key
from test_skill_update_controller import fixture as deployment_fixture


def test_same_skill_version_requires_explicit_host_source_review(deployment_fixture):
    controller, backend, release, wheel, root = deployment_fixture
    with pytest.raises(ValueError, match='policy'):
        controller.stage(release=release, wheel=wheel, current_version=release['version'], current_capabilities=[])
    for commit, reviewed in ((COMMIT, False), ('main', True)):
        with pytest.raises(ValueError, match='Host promotion'):
            controller.stage(release=release, wheel=wheel, current_version=release['version'], current_capabilities=[],
                manual_review=reviewed, host_source_commit=commit)
    ready = controller.stage(release=release, wheel=wheel, current_version=release['version'], current_capabilities=[],
        manual_review=True, host_source_commit=COMMIT)
    assert ready['state'] == 'READY'
    assert json.loads(ready['evidence_json'])['hostSourceCommit'] == COMMIT
    assert backend.calls == ['stage']
    applied = controller.apply(ready['id'], approval='APPROVE ' + ready['id'])
    assert applied['state'] == 'APPLIED'
    backup = root / 'backups' / (ready['id'] + '.sqlite')
    assert backup.exists() and backup.stat().st_mode & 0o777 == 0o600


def test_host_promotion_health_failure_uses_existing_safe_rollback(deployment_fixture):
    controller, backend, release, wheel, _ = deployment_fixture
    backend.health = [False, True]
    ready = controller.stage(release=release, wheel=wheel, current_version=release['version'], current_capabilities=[],
        manual_review=True, host_source_commit=COMMIT)
    with pytest.raises(ValueError, match='health'):
        controller.apply(ready['id'], approval='APPROVE ' + ready['id'])
    assert controller.get(ready['id'])['state'] == 'ROLLED_BACK'
    assert backend.calls[-1] == ('health', release['version'])


def test_host_promotion_refuses_automatic_database_restore_after_candidate_writes(deployment_fixture):
    controller, backend, release, wheel, _ = deployment_fixture
    backend.health = [False]
    backend.mutate = True
    ready = controller.stage(release=release, wheel=wheel, current_version=release['version'], current_capabilities=[],
        manual_review=True, host_source_commit=COMMIT)
    with pytest.raises(ValueError):
        controller.apply(ready['id'], approval='APPROVE ' + ready['id'])
    assert controller.get(ready['id'])['state'] == 'RECOVERY_REQUIRED'
    assert db.get_setting('synthetic-user-data') == 'new version wrote data'


def seed():
    with db.connect() as conn:
        conn.execute("INSERT INTO skill_domain_authority VALUES(1,'event-manager','event-manager','old','new',1)")
        conn.execute("INSERT INTO skill_guild_state VALUES(1,'event-manager',1,1,1,?,1)", (BASELINE_SKILL_VERSION,))
        conn.execute("INSERT INTO skill_storage VALUES(1,'event-manager','event.v2:one',?,1)",
            (json.dumps({'id': 'one', 'revision': 4, 'members': ['50'], 'config': {'name': 'Existing'}, 'chat': {'channelId': '100'}}),))
        conn.execute("INSERT INTO skill_managed_resources(guild_id,skill_id,resource_key,kind,state_json) "
            "VALUES(1,'event-manager','event:one','text',?)", (json.dumps({'channelId': 100, 'messageId': 200, 'marker': 'bound'}),))


def test_backup_restore_probe_preserves_actual_sqlite_authority_and_event_data(deployment_fixture):
    _, _, _, _, root = deployment_fixture
    seed()
    backup = root / 'backup.sqlite'
    with db.connect() as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    host = SimpleNamespace(profile={'workRoot': str(root), 'stagingGuildId': 1})
    gate = PromotionDocker(host, object(), COMMIT)
    assert gate.verify_backup(backup) == {'integrity': 'PASS', 'restoreProbe': 'PASS', 'skillAuthority': 'event-manager'}
    assert not list(root.glob('host-restore-probe-*'))
    before = retained_state(backup, 1)
    assert before['mappings']['event:one']['channelId'] == 100
    with db.connect() as conn:
        conn.execute("UPDATE skill_storage SET value_json=?", (json.dumps({'id': 'one', 'revision': 5, 'members': [], 'config': {'name': 'Existing'}, 'chat': {'channelId': '100'}}),))
    with pytest.raises(Denied, match='RECOVERY'):
        require_retained(before, retained_state(db.DB_PATH, 1))


def test_disabled_guild_retains_older_lifecycle_version_when_the_approved_package_differs(deployment_fixture):
    seed()
    with db.connect() as conn:
        conn.execute("UPDATE skill_guild_state SET version='0.1.1', enabled=0")
    before = retained_state(db.DB_PATH, 1)
    assert before['installed'] == (1, 1, 0, '0.1.1')
    release = {'version': BASELINE_SKILL_VERSION, 'reviewedCommit': BASELINE_SKILL_COMMIT,
               'sha256': BASELINE_SKILL_SHA256}
    runtime = {'version': '0.1.2', 'state': {'installed': True, 'configured': True, 'enabled': False}}
    require_baseline_skill(release, runtime['version'])
    require_runtime_lifecycle(before, runtime)
    require_retained(before, retained_state(db.DB_PATH, 1))
    with pytest.raises(Denied, match='RECOVERY_REQUIRES_OWNER'):
        require_runtime_lifecycle(before, {**runtime, 'state': {**runtime['state'], 'enabled': True}})
    with db.connect() as conn:
        conn.execute("UPDATE skill_guild_state SET version='0.1.2'")
    with pytest.raises(Denied, match='RECOVERY_REQUIRES_OWNER'):
        require_retained(before, retained_state(db.DB_PATH, 1))


def test_changed_discord_card_mapping_requires_review(deployment_fixture):
    seed()
    before = retained_state(db.DB_PATH, 1)
    with db.connect() as conn:
        conn.execute("UPDATE skill_managed_resources SET state_json=json_set(state_json,'$.messageId',201)")
    with pytest.raises(Denied, match='RECOVERY'):
        require_retained(before, retained_state(db.DB_PATH, 1))


def test_only_the_exact_reviewed_candidate_paths_can_change():
    host_changes = set(REVIEWED_HOST_CHANGES)
    changed_tree = None

    def git(command, *args):
        if command == 'diff':
            assert args[-3:] == (BASELINE, COMMIT, '--')
            return ('\0'.join(sorted(host_changes)) + '\0').encode()
        return b'changed' if args[0] == COMMIT + ':' + str(changed_tree) else b'same'

    verify_compatibility(git)
    host_changes.add('hosts/gamerhq/unreviewed.py')
    with pytest.raises(Denied, match='MIGRATION'):
        verify_compatibility(git)
    host_changes.remove('hosts/gamerhq/unreviewed.py')
    changed_path = 'services/server_operations.py'
    host_changes.remove(changed_path)
    with pytest.raises(Denied, match='MIGRATION'):
        verify_compatibility(git)
    host_changes.add(changed_path)
    changed_tree = 'database'
    with pytest.raises(Denied, match='MIGRATION'):
        verify_compatibility(git)


def test_existing_host_and_event_resource_bindings_are_retained(deployment_fixture):
    seed()
    db.set_setting('managed_channel:1:guide', '250')
    with db.connect() as conn:
        conn.execute("INSERT INTO skill_managed_resources(guild_id,skill_id,resource_key,kind,state_json) "
            "VALUES(1,'event-manager','voice:one','voice',?)", (json.dumps({'channelId': 300}),))
    before = retained_state(db.DB_PATH, 1)
    assert before['mappings']['voice:one']['channelId'] == 300
    db.set_setting('managed_channel:1:guide', '251')
    with pytest.raises(Denied, match='RECOVERY'):
        require_retained(before, retained_state(db.DB_PATH, 1))
    db.set_setting('managed_channel:1:guide', '250')
    with db.connect() as conn:
        conn.execute("UPDATE skill_managed_resources SET state_json=json_set(state_json,'$.channelId',301) "
                     "WHERE resource_key='voice:one'")
    with pytest.raises(Denied, match='RECOVERY'):
        require_retained(before, retained_state(db.DB_PATH, 1))


def test_legacy_or_wrong_authority_cannot_be_promoted(deployment_fixture):
    seed()
    with db.connect() as conn:
        conn.execute("UPDATE skill_domain_authority SET skill_id='legacy'")
    with pytest.raises(Denied, match='RECOVERY'):
        retained_state(db.DB_PATH, 1)


def test_host_promotion_keeps_the_exact_installed_skill_artifact():
    release = {'version': BASELINE_SKILL_VERSION, 'reviewedCommit': BASELINE_SKILL_COMMIT,
               'sha256': BASELINE_SKILL_SHA256}
    require_baseline_skill(release, BASELINE_SKILL_VERSION)
    for modified in ({'version': '0.1.1'}, {'reviewedCommit': 'a' * 40}, {'sha256': 'a' * 64}):
        with pytest.raises(Denied, match='VERSION_REQUIRES_OWNER'):
            require_baseline_skill({**release, **modified}, BASELINE_SKILL_VERSION)
    with pytest.raises(Denied, match='VERSION_REQUIRES_OWNER'):
        require_baseline_skill(release, '0.1.1')


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'traversal', 'absolute'])
def test_source_extraction_rejects_links_and_path_redirection(tmp_path, kind):
    archive = tmp_path / 'source.tar'
    with tarfile.open(archive, 'w') as output:
        info = tarfile.TarInfo({'traversal': 'cogs/../../outside', 'absolute': '/cogs/outside'}.get(kind, 'cogs/unsafe.py'))
        if kind in ('symlink', 'hardlink'):
            info.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
            info.linkname = '/outside'
        output.addfile(info)
    destination = tmp_path / 'context'; destination.mkdir()
    with pytest.raises(Denied):
        extract_application(archive, destination)
    assert not list(destination.rglob('*'))


def test_source_extraction_copies_application_only(tmp_path):
    archive = tmp_path / 'source.tar'
    with tarfile.open(archive, 'w') as output:
        for name in ('cogs/event_editor.py', 'bot.py', 'data/game_catalog_expansion.json',
                     'runtime/data/gamerhq.db', '.env', '.github/workflows/tests.yml'):
            data = b'synthetic'
            info = tarfile.TarInfo(name); info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    destination = tmp_path / 'context'; destination.mkdir()
    extract_application(archive, destination)
    assert sorted(str(p.relative_to(destination)).replace('\\', '/') for p in destination.rglob('*') if p.is_file()) == [
        'bot.py', 'cogs/event_editor.py', 'data/game_catalog_expansion.json']


def test_host_promotion_requires_distinct_protected_workflow_and_owner_review(trust):
    policy, claims, token, read, reviews, environment = trust
    policy['hostPromotion'] = {'commit': COMMIT, 'archiveSha256': ARCHIVE_SHA256, 'workflowSha256': policy['securityWorkflowSha256']}
    assert validate_policy(policy)
    wrong_archive = copy.deepcopy(policy)
    wrong_archive['hostPromotion']['archiveSha256'] = 'a' * 64
    with pytest.raises(Denied, match='POLICY'):
        validate_policy(wrong_archive)
    retired = copy.deepcopy(policy)
    retired['hostPromotion']['commit'] = BASELINE
    with pytest.raises(Denied, match='POLICY'):
        validate_policy(retired)
    with pytest.raises(Denied):
        verify(token(), policy, 'host-promote', 'd' * 32, read=read, now=1001)
    claims.update(environment='deployment-security', sub='repo:Duy-Phan96/GamerHQ:environment:deployment-security',
        workflow_ref=policy['repository'] + '/.github/workflows/staging-host-promotion.yml@refs/heads/main')
    assert verify(token(), policy, 'host-promote', 'd' * 32, read=read, now=1001)
    reviews.clear()
    with pytest.raises(Denied):
        verify(token(), policy, 'host-promote', 'd' * 32, read=read, now=1001)


def test_host_promotion_consumes_existing_build_restart_limits_and_cannot_bootstrap():
    state = {'enabled': False, 'bootstrapVerified': False}
    with pytest.raises(Denied): admit('host-promote', state)
    state.update(enabled=True, bootstrapVerified=True)
    admit('host-promote', state)
    charge(state, 'host-promote', 1000)
    with pytest.raises(Denied, match='COOLDOWN'): charge(state, 'restart', 1050)
    charge(state, 'recover', 1200)
    with pytest.raises(Denied, match='RATE_LIMIT'): charge(state, 'host-promote', 1400)


def test_unextended_controller_cannot_select_any_host_source():
    backend = SimpleNamespace(policy={})
    with pytest.raises(Denied, match='NOT_ALLOWED'):
        promote(backend, expected_image='sha256:' + 'a' * 64)
