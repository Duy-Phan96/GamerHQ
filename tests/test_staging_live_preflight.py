import io
import json
from urllib.error import HTTPError

import pytest

from tools.staging_live_preflight import PreflightFailure, verify_packet


def fixture():
    packet = dict(zip(('stagingBotId', 'stagingGuildId', 'productionBotId', 'productionGuildId', 'categoryId', 'voiceCategoryId'), map(str, range(1, 7))))
    packet.update(discordToken='synthetic-staging-token', releaseToken='synthetic-release-token', sdkRelease={'id': 'sdk'}, skillRelease={'id': 'skill'})
    rows = {'/users/@me': {'id': '1'}, '/users/@me/guilds?limit=200': [{'id': '2'}], '/guilds/2': {'id': '2'},
        '/guilds/2/members/1': {'roles': []}, '/guilds/2/roles': [],
        '/channels/5': {'guild_id': '2', 'type': 4}, '/channels/6': {'guild_id': '2', 'type': 4}}
    calls, fetched, progress = [], [], []
    def read(path, token):
        assert token == packet['discordToken']
        calls.append(path)
        return rows[path]
    kwargs = dict(read=read, permissions=lambda *args: 63,
        required={'text': {'view_channel': 1, 'manage_channels': 2, 'manage_roles': 4, 'send_messages': 8, 'read_message_history': 16},
                  'voice': {'view_channel': 1, 'manage_channels': 2, 'manage_roles': 4, 'connect': 32}},
        validate_release=lambda value, **kwargs: None,
        fetch=lambda release, token: fetched.append(release['id']), progress=progress.append)
    return packet, rows, calls, fetched, progress, kwargs


def test_full_preflight_uses_only_approved_reads_and_never_mutates_packet():
    packet, _, calls, fetched, _, kwargs = fixture()
    before = json.dumps(packet, sort_keys=True)
    result = verify_packet(packet, **kwargs)
    assert result['state'] == 'LIVE_PREFLIGHT_PASS'
    assert result['gatewayStarted'] is result['productionChanged'] is result['workerAuthorizationChanged'] is result['vpsConfigurationChanged'] is False
    assert fetched == ['sdk', 'skill'] and len(calls) == 7
    assert json.dumps(packet, sort_keys=True) == before


@pytest.mark.parametrize('change', ['production-bot', 'production-guild', 'extra', 'newline-token', 'bad-id'])
def test_invalid_configuration_never_contacts_discord(change):
    packet, _, calls, fetched, _, kwargs = fixture()
    if change == 'production-bot': packet['stagingBotId'] = packet['productionBotId']
    if change == 'production-guild': packet['stagingGuildId'] = packet['productionGuildId']
    if change == 'extra': packet['command'] = 'not accepted'
    if change == 'newline-token': packet['discordToken'] += '\n'
    if change == 'bad-id': packet['categoryId'] = '../3'
    with pytest.raises(PreflightFailure): verify_packet(packet, **kwargs)
    assert calls == fetched == []


@pytest.mark.parametrize('change', ['wrong-bot', 'extra-guild', 'wrong-guild', 'wrong-category', 'missing-permissions'])
def test_failed_identity_or_permission_never_fetches_releases(change):
    packet, rows, _, fetched, progress, kwargs = fixture()
    if change == 'wrong-bot': rows['/users/@me']['id'] = '3'
    if change == 'extra-guild': rows['/users/@me/guilds?limit=200'].append({'id': '4'})
    if change == 'wrong-guild': rows['/guilds/2']['id'] = '4'
    if change == 'wrong-category': rows['/channels/5']['guild_id'] = '4'
    if change == 'missing-permissions': kwargs['permissions'] = lambda *args: 1
    with pytest.raises(PreflightFailure) as error: verify_packet(packet, **kwargs)
    assert not fetched
    assert error.value.evidence['step'] not in [row['livePreflightStep'] for row in progress]
    if change == 'missing-permissions':
        assert error.value.evidence['missingPermissions'] == ['manage_channels', 'manage_roles', 'send_messages', 'read_message_history']


@pytest.mark.parametrize('status', [401, 403, 404, 429])
def test_http_error_reports_only_status_and_phase_without_credentials_or_body(status):
    packet, _, _, _, _, kwargs = fixture()
    def rejected(*args):
        raise HTTPError('https://invalid/' + packet['discordToken'], status, packet['releaseToken'], {}, io.BytesIO(b'private response'))
    kwargs['read'] = rejected
    with pytest.raises(PreflightFailure) as error: verify_packet(packet, **kwargs)
    assert error.value.evidence == {'stagingPreflightError': 'DISCORD_HTTP_REJECTED', 'step': 'bot-identity', 'httpStatus': status}


def test_release_failure_is_distinct_and_does_not_emit_release_token():
    packet, _, _, _, _, kwargs = fixture()
    def rejected(*args): raise HTTPError('https://invalid', 404, packet['releaseToken'], {}, None)
    kwargs['fetch'] = rejected
    with pytest.raises(PreflightFailure) as error: verify_packet(packet, **kwargs)
    assert error.value.evidence == {'stagingPreflightError': 'RELEASE_HTTP_REJECTED', 'step': 'sdk-release', 'httpStatus': 404}
