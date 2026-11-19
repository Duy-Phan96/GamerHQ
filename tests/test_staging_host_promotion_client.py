from pathlib import Path
import hashlib
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from tools import staging_automation_client as client
from tools.staging_automation.policy import Denied, request


def test_host_promotion_has_no_client_selected_source_image_or_path():
    assert request({'version': 1, 'phase': 'automation', 'operation': 'host-promote'}) == 'host-promote'
    with pytest.raises(Denied):
        request({'version': 1, 'phase': 'automation', 'operation': 'host-promote', 'commit': 'arbitrary'})


@pytest.mark.parametrize('status', [
    {'state': 'ACTIVE', 'bootstrapVerified': True},
    {'state': 'NOT_ACTIVATED', 'bootstrapVerified': True, 'hostPromotionSupported': True},
    {'state': 'ACTIVE', 'bootstrapVerified': False, 'hostPromotionSupported': True},
])
def test_missing_extension_never_requests_an_application_challenge(status):
    with patch('sys.argv', ['client', 'host-promote']), patch.object(client, 'exchange', return_value=status) as exchange, patch.object(client, 'perform') as perform:
        with pytest.raises(Denied, match='HOST_PROMOTION_NOT_INSTALLED'):
            client.main()
        exchange.assert_called_once_with('status')
        perform.assert_not_called()


def test_installed_active_extension_uses_only_fixed_protected_operation(capsys):
    with patch('sys.argv', ['client', 'host-promote']), patch.object(client, 'exchange', return_value={
            'state': 'ACTIVE', 'bootstrapVerified': True, 'hostPromotionSupported': True}), patch.object(client, 'perform', return_value={'state': 'APPLIED'}) as perform:
        client.main()
        perform.assert_called_once_with('host-promote')
    assert 'APPLIED' in capsys.readouterr().out


def test_workflow_preserves_protected_owner_review_and_existing_concurrency():
    text = (Path(__file__).resolve().parents[1] / '.github/workflows/staging-host-promotion.yml').read_text()
    assert 'environment: deployment-security' in text
    assert "github.ref == 'refs/heads/main'" in text
    assert 'group: host-staging-automation' in text
    assert 'id-token: write' in text
    assert 'GAMERHQ_STAGING_AUTOMATION_SSH_KEY' in text
    assert 'GAMERHQ_STAGING_SSH_KEY }}' not in text
    assert 'production' not in text.lower()
    assert 'inputs:' not in text


@pytest.mark.parametrize('case, expected', [
    ('verified-recovery', 0), ('held-terminal', 1), ('unfinished-journal', 1),
    ('unverified-receipt', 1), ('activation-authorized', 1), ('controller-changed', 1),
    ('tenant-binding-changed', 1), ('activation-rolled-back', 1), ('verified-activation', 0),
])
def test_protected_client_exit_code_distinguishes_recovery_from_failed_activation(tmp_path, case, expected):
    pin = {'applicationId': '90', 'guildIds': ['1', '2']}
    path = tmp_path / 'deploy/staging'
    path.mkdir(parents=True)
    (path / 'tenant-pilot-binding.json').write_text(json.dumps(pin))
    digest = hashlib.sha256(json.dumps(pin, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    initial = {'state': 'ACTIVE', 'bootstrapVerified': True, 'controllerCommit': 'reviewed-controller',
               'tenantPilotBindingSha256': digest, 'tenantPilotCutoverEnrolled': True,
               'tenantPilotCutoverJournal': True, 'recoveryRequired': True}
    current = {**initial, 'recoveryRequired': False,
               'tenantPilotJournalStatus': {'phase': 'PILOT_ROLLED_BACK'}}
    receipt = {'state': 'PILOT_ROLLED_BACK', 'recoveryVerified': True,
               'recoveryRequired': False, 'activationAuthorized': False}
    if case == 'held-terminal':
        current['recoveryRequired'] = True
    elif case == 'unfinished-journal':
        current['tenantPilotJournalStatus']['phase'] = 'ROLLING_BACK'
    elif case == 'unverified-receipt':
        receipt.pop('recoveryVerified')
    elif case == 'activation-authorized':
        receipt['activationAuthorized'] = True
    elif case == 'controller-changed':
        current['controllerCommit'] = 'different'
    elif case == 'tenant-binding-changed':
        current['tenantPilotBindingSha256'] = 'different'
    elif case in {'activation-rolled-back', 'verified-activation'}:
        initial.update(tenantPilotCutoverJournal=False, recoveryRequired=False, tenantPilotPreparationState='FRESH')
        if case == 'verified-activation':
            receipt = {'state': 'PILOT_ACTIVE', 'activationAuthorized': True}
    program = '''
import json,sys
sys.path.insert(0,sys.argv[1])
from tools import staging_automation_client as client
packet=json.load(sys.stdin)
states=iter(packet['statuses'])
def exchange(operation):
 assert operation=='status'
 return next(states)
def perform(operation):
 assert operation=='tenant-pilot-cutover'
 return packet['receipt']
client.exchange=exchange
client.perform=perform
sys.argv=['client','tenant-pilot-cutover']
client.main()
'''
    result = subprocess.run([sys.executable, '-I', '-c', program, str(Path(__file__).resolve().parents[1])],
                            input=json.dumps({'statuses': [initial, current], 'receipt': receipt}),
                            text=True, capture_output=True, cwd=tmp_path, timeout=20)
    assert result.returncode == expected, result.stderr
    output = json.loads(result.stdout)
    if case == 'verified-recovery':
        assert output['clientVerification'] == 'RECOVERY_TERMINAL_VERIFIED'
        assert output['recoveryRequired'] is False
    elif expected:
        assert output['clientVerification'] == 'RECOVERY_TERMINAL_NOT_VERIFIED'
