"""Read-only Actions diagnostics using the reviewed Host's existing adapters.

No VPS access, configuration writes, authorization proof, installation or Gateway.
The CLI runs with the immutable reviewed Host checkout on PYTHONPATH.
"""
import json
import os
import re
import tempfile
from urllib.error import HTTPError


class PreflightFailure(Exception):
    def __init__(self, code, step, **public):
        self.evidence = {"stagingPreflightError": code, "step": step, **public}
        super().__init__(code)


def verify_packet(packet, *, read, permissions, required, validate_release, fetch, progress=None):
    completed = []

    def stage(name, action):
        try:
            result = action()
        except PreflightFailure:
            raise
        except HTTPError as error:
            code = 'RELEASE_HTTP_REJECTED' if name.endswith('release') else 'DISCORD_HTTP_REJECTED'
            raise PreflightFailure(code, name, httpStatus=error.code) from None
        except Exception:
            raise PreflightFailure('CHECK_REJECTED', name) from None
        completed.append(name)
        if progress:
            progress({'livePreflightStep': name, 'state': 'PASS'})
        return result

    def configuration():
        ids = ('stagingBotId', 'stagingGuildId', 'productionBotId', 'productionGuildId', 'categoryId', 'voiceCategoryId')
        if not isinstance(packet, dict) or set(packet) != set(ids) | {'discordToken', 'releaseToken', 'sdkRelease', 'skillRelease'}:
            raise PreflightFailure('CONFIGURATION_REJECTED', 'configuration')
        if any(not isinstance(packet[key], str) or not re.fullmatch('[1-9][0-9]{0,19}', packet[key]) for key in ids):
            raise PreflightFailure('CONFIGURATION_REJECTED', 'configuration')
        if packet['stagingBotId'] == packet['productionBotId'] or packet['stagingGuildId'] == packet['productionGuildId']:
            raise PreflightFailure('PRODUCTION_IDENTITY_REJECTED', 'configuration')
        if any(not isinstance(packet[key], str) or not 20 <= len(packet[key]) <= 2048 or any(c.isspace() for c in packet[key]) for key in ('discordToken', 'releaseToken')):
            raise PreflightFailure('CONFIGURATION_REJECTED', 'configuration')
        validate_release(packet['sdkRelease'], sdk=True)
        validate_release(packet['skillRelease'])
    stage('configuration', configuration)
    token = packet['discordToken']
    def bot_identity():
        if read('/users/@me', token).get('id') != packet['stagingBotId']:
            raise PreflightFailure('BOT_IDENTITY_MISMATCH', 'bot-identity')
    stage('bot-identity', bot_identity)
    def gateway_members_intent():
        # The pinned Host requests Intents.members; message content is disabled.
        # Application flags 14/15 cover approved and limited GUILD_MEMBERS access.
        # https://github.com/discord/discord-api-docs/blob/main/developers/resources/application.mdx#application-flags
        application = read('/oauth2/applications/@me', token)
        flags = application.get('flags', 0)
        if type(flags) is not int or flags < 0:
            raise PreflightFailure('APPLICATION_FLAGS_REJECTED', 'gateway-members-intent')
        if not flags & ((1 << 14) | (1 << 15)):
            raise PreflightFailure('GATEWAY_MEMBERS_INTENT_REQUIRED', 'gateway-members-intent')
    stage('gateway-members-intent', gateway_members_intent)
    def guild_membership():
        guilds = read('/users/@me/guilds?limit=200', token)
        if not isinstance(guilds, list) or {str(row['id']) for row in guilds} != {packet['stagingGuildId']}:
            raise PreflightFailure('EXCLUSIVE_STAGING_GUILD_REQUIRED', 'guild-membership')
    stage('guild-membership', guild_membership)
    guild_id = packet['stagingGuildId']
    def guild_identity():
        if str(read('/guilds/' + guild_id, token).get('id')) != guild_id:
            raise PreflightFailure('GUILD_IDENTITY_MISMATCH', 'guild-identity')
    stage('guild-identity', guild_identity)
    member = stage('bot-membership', lambda: read(f'/guilds/{guild_id}/members/{packet["stagingBotId"]}', token))
    roles = stage('guild-roles', lambda: read(f'/guilds/{guild_id}/roles', token))
    for kind, key in (('text', 'categoryId'), ('voice', 'voiceCategoryId')):
        def category_check():
            category = read('/channels/' + packet[key], token)
            if str(category.get('guild_id')) != guild_id or category.get('type') != 4:
                raise PreflightFailure('CATEGORY_IDENTITY_MISMATCH', kind + '-category')
            actual = permissions(guild_id, packet['stagingBotId'], member, roles, category)
            missing = [name for name, bit in required[kind].items() if actual & bit != bit]
            if missing:
                raise PreflightFailure('CATEGORY_PERMISSIONS_MISSING', kind + '-category', missingPermissions=missing)
        stage(kind + '-category', category_check)
    for key in ('sdkRelease', 'skillRelease'):
        stage('sdk-release' if key == 'sdkRelease' else 'skill-release', lambda: fetch(packet[key], packet['releaseToken']))
    return {'state': 'LIVE_PREFLIGHT_PASS', 'completed': completed, 'gatewayStarted': False,
        'productionChanged': False, 'workerAuthorizationChanged': False, 'vpsConfigurationChanged': False,
        'gatewayMembersIntentVerified': True,
        'scope': 'GitHub-runner-only; does not attest VPS network, persisted credentials or live proof'}


def main():
    # Imports resolve exclusively from the separately checked-out, immutable Host.
    from hosts.gamerhq.skill_staging import discord_read, category_permissions
    from hosts.gamerhq.skill_release_artifacts import fetch_release
    from tools.staging_configuration import descriptor
    import discord
    common = ('view_channel', 'manage_channels', 'manage_roles')
    required = {kind: {name: getattr(discord.Permissions, name).flag for name in names}
        for kind, names in (('text', common + ('send_messages', 'read_message_history')),
                            ('voice', common + ('connect',)))}
    packet = json.loads(os.environ.get('GAMERHQ_STAGING_CONFIGURATION', ''))
    with tempfile.TemporaryDirectory(prefix='gamerhq-reviewed-releases-') as artifacts:
        def fetch(release, token):
            return fetch_release(release, artifact_root=artifacts,
                trusted_repositories={'Duy-Phan96/GamerHQ', 'Duy-Phan96/gamerhq-skill-event-manager'}, token=token)
        result = verify_packet(packet, read=discord_read, permissions=category_permissions, required=required,
            validate_release=descriptor, fetch=fetch, progress=lambda value: print(json.dumps(value), flush=True))
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except PreflightFailure as error:
        raise SystemExit(json.dumps(error.evidence)) from None
    except Exception:
        raise SystemExit('{"stagingPreflightError":"PREFLIGHT_UNAVAILABLE","step":"client"}') from None
