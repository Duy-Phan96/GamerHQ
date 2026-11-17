import os

import pytest

from database import db
from services.tenant_routing import pilot_environment
from tools import recover_staging_pilot_secondary as owner
from tools.staging_tenant_recovery_probe import recover


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    primary = tmp_path / 'gamerhq.db'
    monkeypatch.setattr(db, 'DB_PATH', primary)
    db.init_db()
    pin = pilot_environment({'stagingBotId': '90', 'productionBotId': '99', 'productionGuildId': '3'}, (1, 2))
    return primary, pin


def test_recovery_confirms_absence_provisions_once_and_preserves_primary(tenant):
    primary, pin = tenant
    before = primary.read_bytes()
    first = recover(pin, 1, 2, primary)
    assert first['before'] == {'state': 'MISSING', 'step': 'tenant-directory'}
    assert first['created'] is True and first['after']['state'] == 'SNAPSHOT_READY'
    if os.name == 'posix':
        assert first['after']['mode'] == '0600'
    secondary = primary.parent / 'tenants/2.sqlite'
    saved = secondary.read_bytes()
    second = recover(pin, 1, 2, primary)
    assert second['created'] is False and second['before']['state'] == 'SNAPSHOT_READY'
    assert secondary.read_bytes() == saved and primary.read_bytes() == before


def test_recovery_keeps_unknown_existing_secondary_byte_for_byte(tenant):
    primary, pin = tenant
    directory = primary.parent / 'tenants'
    directory.mkdir(mode=0o700)
    secondary = directory / '2.sqlite'
    secondary.write_bytes(b'unknown database')
    secondary.chmod(0o600)
    result = recover(pin, 1, 2, primary)
    assert result['before']['state'] == 'SQLITE_REJECTED'
    assert result['created'] is False and secondary.read_bytes() == b'unknown database'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX mode enforcement')
def test_recovery_reports_wrong_mode_without_fixing_it(tenant):
    primary, pin = tenant
    recover(pin, 1, 2, primary)
    secondary = primary.parent / 'tenants/2.sqlite'
    secondary.chmod(0o640)
    result = recover(pin, 1, 2, primary)
    assert result['before']['state'] == 'UNSAFE_MODE_OR_OWNER'
    assert result['before']['mode'] == '0640'
    assert result['created'] is False and secondary.stat().st_mode & 0o777 == 0o640


def test_recovery_rejects_changed_enrolled_workflow_before_storage():
    with pytest.raises(ValueError, match='ENROLLED_POLICY_MISMATCH'):
        owner.verified_baseline({'tenantPilotWorkflowSha256': 'f' * 64}, {}, {})
