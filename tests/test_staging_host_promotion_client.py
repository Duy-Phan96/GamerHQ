from pathlib import Path
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
