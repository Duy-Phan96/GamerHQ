import io
import json
import os
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pytest

from hosts.gamerhq.skill_staging import discord_read, NoRedirects, verify_remote
from tools.staging_configuration import configure
from tools.staging_errors import VerificationFailure, StagingFailure
from tests.test_skill_staging import staging


def test_discord_requests_identify_the_bot_client_and_never_allow_redirects():
    with patch('hosts.gamerhq.skill_staging.build_opener') as opener:
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"id":"1"}'
        assert discord_read('/users/@me', 'synthetic-token') == {'id': '1'}
        request = opener.return_value.open.call_args.args[0]
        assert request.get_method() == 'GET'
        assert request.get_header('User-agent') == 'DiscordBot (https://github.com/Duy-Phan96/GamerHQ, staging-1)'
        assert request.unredirected_hdrs['Authorization'] == 'Bot synthetic-token'
        assert isinstance(opener.call_args.args[0], NoRedirects)
        with pytest.raises(ValueError):
            opener.call_args.args[0].redirect_request(request, None, 302, 'ignored', {}, 'https://invalid/')


@pytest.mark.parametrize('status,code', [(401, 'DISCORD_UNAUTHORIZED'), (403, 'DISCORD_FORBIDDEN'),
    (404, 'DISCORD_NOT_FOUND'), (429, 'DISCORD_RATE_LIMITED'), (500, 'DISCORD_HTTP_REJECTED')])
def test_live_http_failure_preserves_phase_without_private_response(staging, status, code):
    profile = staging[0]
    def denied(*args):
        raise HTTPError('https://invalid/private-token', status, 'private-token', {}, io.BytesIO(b'private-body'))
    with patch('hosts.gamerhq.skill_staging.platform.system', return_value='Linux'), \
            patch.dict(os.environ, {profile['healthSecretEnv']: 's' * 40, profile['releaseTokenEnv']: 'synthetic-access'}):
        with pytest.raises(VerificationFailure) as error:
            verify_remote(profile, read=denied, fetch=Mock())
    assert error.value.evidence == {'stagingFailure': code, 'step': 'bot-identity'}
    assert 'private' not in str(error.value)


def test_failed_live_proof_persistence_never_enables_worker(tmp_path):
    profile = {'workerEnabled': False, 'verificationFile': str(tmp_path / 'verification.json')}
    with patch('tools.staging_configuration.audit'), patch('hosts.gamerhq.skill_staging.verify_remote', return_value={'verified': True}), \
            patch('tools.skill_staging.write_record', side_effect=StagingFailure('STAGING_RECORD_PERSISTENCE_FAILED', 'record')), \
            patch('tools.staging_configuration.atomic_json') as enabled:
        with pytest.raises(StagingFailure) as error:
            configure('authorize-live', None, profile)
    enabled.assert_not_called()
    assert profile['workerEnabled'] is False
    assert error.value.evidence['step'] == 'record' and len(error.value.evidence['auditId']) == 32


def test_live_failure_is_auditable_and_never_prints_exception_payload(tmp_path):
    with patch('tools.staging_configuration.audit') as audit, patch('hosts.gamerhq.skill_staging.verify_remote',
            side_effect=VerificationFailure('DISCORD_FORBIDDEN', 'bot-identity')):
        with pytest.raises(StagingFailure) as error:
            configure('authorize-live', None, {'workerEnabled': False})
    evidence = error.value.evidence
    assert evidence['stagingFailure'] == 'DISCORD_FORBIDDEN' and len(evidence['auditId']) == 32
    assert audit.call_args.kwargs['audit_id'] == evidence['auditId']


def test_worker_authorization_writes_candidate_proof_before_profile(tmp_path):
    profile = {'workerEnabled': False, 'verificationFile': str(tmp_path / 'proof.json')}
    calls = []
    with patch('tools.staging_configuration.audit'), patch('hosts.gamerhq.skill_staging.verify_remote', return_value={'verified': True}), \
            patch('tools.skill_staging.write_record', side_effect=lambda *args: calls.append('proof')), \
            patch('tools.staging_configuration.atomic_json', side_effect=lambda *args: calls.append('profile')):
        configure('authorize-live', None, profile)
    assert calls == ['proof', 'profile']
    assert profile['workerEnabled'] is False
