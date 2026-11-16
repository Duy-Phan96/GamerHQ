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


def test_installed_active_extension_uses_only_fixed_protected_operation(capsys, monkeypatch):
    expected = 'a' * 40
    monkeypatch.setenv('EXPECTED_STAGING_HOST_COMMIT', expected)
    with patch('sys.argv', ['client', 'host-promote']), patch.object(client, 'exchange', return_value={
            'state': 'ACTIVE', 'bootstrapVerified': True, 'hostPromotionSupported': True,
            'hostPromotionCommit': expected, 'recoveryRequired': False}), patch.object(client, 'perform', return_value={'state': 'APPLIED'}) as perform:
        client.main()
        perform.assert_called_once_with('host-promote', expected_host=expected)
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
    assert 'EXPECTED_STAGING_HOST_COMMIT: ${{ inputs.candidate }}' in text
    assert 'run-name: Staging Host - ${{ inputs.candidate }}' in text


@pytest.mark.parametrize('expected,recovery,error', [
    ('b' * 40, False, 'HOST_PROMOTION_PIN_MISMATCH'),
    ('main', False, 'HOST_PROMOTION_PIN_MISMATCH'),
    ('a' * 40, True, 'RECOVERY_REQUIRES_OWNER'),
    ('a' * 40, None, 'RECOVERY_REQUIRES_OWNER'),
])
def test_dispatch_never_requests_challenge_when_pin_or_recovery_disagrees(monkeypatch, expected, recovery, error):
    monkeypatch.setenv('EXPECTED_STAGING_HOST_COMMIT', expected)
    status = {'state': 'ACTIVE', 'bootstrapVerified': True, 'hostPromotionSupported': True,
              'hostPromotionCommit': 'a' * 40, 'recoveryRequired': recovery}
    with patch('sys.argv', ['client', 'host-promote']), patch.object(client, 'exchange', return_value=status), patch.object(client, 'perform') as perform:
        with pytest.raises(Denied, match=error):
            client.main()
        perform.assert_not_called()


@pytest.mark.parametrize('expected', [None, 'a' * 39, 'b' * 40])
def test_controller_rejects_candidate_changed_after_workflow_preflight(expected):
    from tools.staging_automation.bridge import execute

    policy = {'hostPromotion': {'commit': 'a' * 40}}
    state = {'enabled': True, 'bootstrapVerified': True, 'approvedImage': 'sha256:' + '0' * 64,
             'challenge': {'nonce': 'nonce', 'operation': 'host-promote', 'expires': 100}}
    payload = {'challenge': 'nonce', 'token': 'private-token'}
    if expected is not None:
        payload['expectedHostCommit'] = expected
    verifier = lambda *args, **kwargs: {'jti': 'test-nonce', 'exp': 100, 'run_id': '12345'}
    with patch('tools.staging_automation.host_promotion.promote') as promote:
        with pytest.raises(Denied, match='HOST_PROMOTION_PIN_MISMATCH'):
            execute('host-promote', payload, policy, state, None, lambda _: None, verifier=verifier, now=10)
        promote.assert_not_called()


def test_controller_uses_enrolled_candidate_when_dispatch_commit_matches():
    from types import SimpleNamespace
    from tools.staging_automation.bridge import execute

    expected = 'a' * 40
    image = 'sha256:' + '0' * 64
    policy = {'hostPromotion': {'commit': expected}}
    state = {'enabled': True, 'bootstrapVerified': True, 'approvedImage': image,
             'challenge': {'nonce': 'nonce', 'operation': 'host-promote', 'expires': 100}}
    payload = {'challenge': 'nonce', 'token': 'private-token', 'expectedHostCommit': expected}
    verifier = lambda *args, **kwargs: {'jti': 'test-nonce', 'exp': 100, 'run_id': '12345'}
    backend = SimpleNamespace(policy=policy, policy_integrity=lambda: None)
    with patch('tools.staging_automation.host_promotion.promote', return_value={'state': 'APPLIED', 'image': image}) as promote:
        assert execute('host-promote', payload, policy, state, backend, lambda _: None, verifier=verifier, now=10) == {'state': 'APPLIED'}
        promote.assert_called_once_with(backend, expected_image=image)


def test_expected_commit_crosses_the_existing_challenge_transport():
    expected = 'a' * 40
    calls = []

    def exchange(operation, payload):
        calls.append((operation, payload))
        return {'challenge': 'nonce', 'audience': 'bound'} if operation == 'challenge' else {'state': 'APPLIED'}

    client.perform('host-promote', expected_host=expected, call=exchange, get_token=lambda _: 'private-token')
    assert calls == [
        ('challenge', {'operation': 'host-promote'}),
        ('host-promote', {'challenge': 'nonce', 'token': 'private-token', 'expectedHostCommit': expected}),
    ]
