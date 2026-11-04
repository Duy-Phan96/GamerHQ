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
from tools.staging_automation.host_promotion import (COMMIT, extract_application, retained_state,
    require_retained, PromotionDocker, promote)
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
    assert backup.exists()


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
        conn.execute("INSERT INTO skill_guild_state VALUES(1,'event-manager',1,1,1,'0.1.0',1)")
        conn.execute("INSERT INTO skill_storage VALUES(1,'event-manager','event.v2:one',?,1)",
            (json.dumps({'id': 'one', 'revision': 4, 'members': ['50'], 'config': {'name': 'Existing'}, 'chat': {'channelId': '100'}}),))


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
    with db.connect() as conn:
        conn.execute("UPDATE skill_storage SET value_json=?", (json.dumps({'id': 'one', 'revision': 5, 'members': [], 'config': {'name': 'Existing'}, 'chat': {'channelId': '100'}}),))
    with pytest.raises(Denied, match='RECOVERY'):
        require_retained(before, retained_state(db.DB_PATH, 1))


def test_legacy_or_wrong_authority_cannot_be_promoted(deployment_fixture):
    seed()
    with db.connect() as conn:
        conn.execute("UPDATE skill_domain_authority SET skill_id='legacy'")
    with pytest.raises(Denied, match='RECOVERY'):
        retained_state(db.DB_PATH, 1)


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
        for name in ('cogs/event_editor.py', 'bot.py', 'runtime/data/gamerhq.db', '.env', '.github/workflows/tests.yml'):
            data = b'synthetic'
            info = tarfile.TarInfo(name); info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    destination = tmp_path / 'context'; destination.mkdir()
    extract_application(archive, destination)
    assert sorted(str(p.relative_to(destination)).replace('\\', '/') for p in destination.rglob('*') if p.is_file()) == ['bot.py', 'cogs/event_editor.py']


def test_host_promotion_requires_distinct_protected_workflow_and_owner_review(trust):
    policy, claims, token, read, reviews, environment = trust
    policy['hostPromotion'] = {'commit': COMMIT, 'archiveSha256': 'a' * 64, 'workflowSha256': policy['securityWorkflowSha256']}
    assert validate_policy(policy)
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
        promote(backend)
