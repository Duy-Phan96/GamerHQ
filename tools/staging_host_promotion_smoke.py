"""Disposable CI overlay/provenance/UI probe; no credentials, DB or Gateway."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import uuid

from tools.staging_automation.backend import HostBackend
from tools.staging_automation.host_promotion import ARCHIVE_SHA256, COMMIT, build_overlay, source_commit


def quiesced_checkpoint_smoke(backend, root, image, fallback):
    from database import db
    from database.tenants import GuildTenantStore
    from tools.staging_tenant_rehearsal import retain_quiesced

    root = Path(root)
    data, backups = root / 'data', root / 'backups'
    data.mkdir(mode=0o755)
    backups.mkdir(mode=0o700)
    primary = data / 'primary.sqlite'
    primary.touch(mode=0o600, exist_ok=False)
    store = GuildTenantStore(primary_guild=1, primary_database=primary,
                             tenant_directory=data / 'tenants', additional_guilds=(2,))
    with store.scope(1):
        db.init_db()
        with db.connect() as conn:
            conn.execute("INSERT INTO skill_domain_authority VALUES(1,'event-manager','event-manager','old','new',1)")
            conn.execute("INSERT INTO skill_guild_state VALUES(1,'event-manager',1,1,0,'0.1.1',1)")
    store.provision(2)
    secondary = store.tenant_directory / '2.sqlite'
    for path in (store.tenant_directory, secondary):
        os.chown(path, 10001, 10001)
    before = primary.read_bytes(), secondary.read_bytes()
    name = 'pilot-checkpoint-' + uuid.uuid4().hex
    baseline = source_commit(backend, image)
    policy = {'hostPromotion': {'commit': baseline}, 'tenantPilot': {'applicationId': '90', 'guildIds': ['1', '2']}}
    profile = {'environment': 'staging', 'stagingGuildId': '1', 'stagingBotId': '90', 'productionGuildId': '3',
               'productionBotId': '99', 'service': 'gamerhq-staging', 'database': str(primary),
               'backupRoot': str(backups), 'workRoot': str(root)}
    fixture = HostBackend(profile, policy)
    try:
        container = backend.run(['docker', 'run', '-d', '--name', name, '--network', 'none', '--read-only',
                                 '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--user', '10001:10001',
                                 '--pids-limit', '32', '--memory', '128m', '--entrypoint', 'python', image,
                                 '-I', '-B', '-c', 'import time; time.sleep(90)'])
        backend.run(['docker', 'stop', '--time', '2', name])
        fixture.inspect = lambda: json.loads(backend.run(['docker', 'inspect', name]))[0]
        receipt = retain_quiesced(fixture, container, image)
        assert receipt['quiesced'] and [row['restore'] for row in receipt['tenantRestores']] == ['PASS', 'PASS']
        assert (primary.read_bytes(), secondary.read_bytes()) == before
        print('PILOT_QUIESCED_DUAL_RESTORE_PASS')
    finally:
        backend.run(['docker', 'rm', '--force', name])
    recovery_health_smoke(backend, profile, policy, image, fallback, container, receipt)
    assert (primary.read_bytes(), secondary.read_bytes()) == before


def recovery_health_smoke(docker, profile, policy, baseline, fallback, baseline_container, receipt):
    """Exercise the protected transaction on a loopback-only synthetic Docker Host."""
    from services.tenant_routing import PILOT_ENV
    from tools.skill_staging import host_request
    from tools.staging_automation import host_promotion
    from tools.staging_automation.bridge import execute
    from tools.staging_automation.pilot_cutover import PilotCutover, digest
    from tools.staging_automation.policy import Denied
    from tools.staging_health import verified_skill_health
    from tools.staging_tenant_rehearsal import startup_override

    root = Path(profile['workRoot'])
    release = root / 'fixture-release.json'
    release.write_text(json.dumps({'version': '0.1.2', 'reviewedCommit': host_promotion.BASELINE_SKILL_COMMIT,
                                   'sha256': host_promotion.BASELINE_SKILL_SHA256}))
    profile = {**profile, 'releaseFile': str(release), 'healthSecretEnv': 'GAMERHQ_CI_WATCHDOG_KEY'}
    policy = {**policy, 'tenantPilotCutover': {}}
    binding = {**profile, 'tenantPilot': policy['tenantPilot']}
    candidate = 'sha256:' + '2' * 64  # The candidate is ineligible in recovery mode.
    fixture_label = 'gamerhq-recovery-ci-' + uuid.uuid4().hex
    prepared = {'candidateImage': candidate, 'fallbackImage': fallback,
                'startupOverrideSha256': digest(startup_override(binding, policy, candidate)),
                'fallbackStartupOverrideSha256': digest(startup_override(binding, policy, fallback, primary_only=True))}
    service = """
from http.server import BaseHTTPRequestHandler, HTTPServer
import json,sys
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  assert self.path == '/api/v1/servers/1/skills/event-manager'
  assert self.headers['Authorization'] == 'Bearer test'
  assert self.headers['X-GamerHQ-Authorized-Guild-Ids'] == '1'
  self.send_response(int(sys.argv[1])); self.end_headers()
  self.wfile.write(json.dumps({'version':'0.1.2','missingCapabilities':[],
      'state':{'installed':True,'configured':True,'enabled':False,'healthy':False}}).encode())
 def log_message(self,*args): pass
HTTPServer(('0.0.0.0',8080), Handler).serve_forever()
"""
    containers = []
    def inspect():
        return json.loads(docker.run(['docker', 'inspect', containers[-1]]))[0]

    def start(status):
        name = fixture_label + '-' + str(status)
        containers.append(name)
        environment = startup_override(binding, policy, fallback, primary_only=True)['services'][profile['service']]['environment']
        docker.run(['docker', 'run', '-d', '--pull', 'never', '--name', name, '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges:true', '--user', '10001:10001', '--memory', '128m', '--pids-limit', '32',
                    '-p', '127.0.0.1::8080', '-e', PILOT_ENV + '=' + environment[PILOT_ENV],
                    '--mount', f"type=bind,src={Path(profile['database']).parent},dst=/app/runtime/data,readonly",
                    '--health-cmd', '''python -c "import socket; socket.create_connection(('127.0.0.1',8080),1).close()"''',
                    '--health-interval', '1s', '--health-retries', '15', '--entrypoint', 'python', fallback, '-I', '-B', '-c',
                    service, str(status)])
        profile['healthUrl'] = 'http://' + docker.run(['docker', 'port', name, '8080/tcp'])
        for _ in range(15):
            if inspect()['State']['Health']['Status'] == 'healthy':
                return
            time.sleep(1)
        raise RuntimeError('Disposable recovery fixture failed Docker readiness')

    def health():
        assert inspect()['State']['Health']['Status'] == 'healthy'
        verified_skill_health(profile, host_request)
        return {'state': 'HOST_READY'}

    def compose(action, *args, override=None):
        if action == 'stop':
            docker.run(['docker', 'stop', '--time', '2', containers[-1]])
        else:
            assert action == 'up' and inspect()['State']['Running'] is False
            assert override == startup_override(binding, policy, fallback, primary_only=True)
            start(200)

    backend = SimpleNamespace(profile=profile, policy=policy, inspect=inspect, health=health,
                              policy_integrity=lambda: None,
                              skill=lambda: host_request(profile, '/api/v1/servers/1/skills/event-manager'))
    transaction = PilotCutover(backend, prepared, baseline, 'original')
    transaction.inspect, transaction.compose = inspect, compose
    transaction.attest = lambda **_: None  # No Discord or external artifact calls in this fixture.
    backend.cutover_tenant_pilot = lambda *_, recovery_only: transaction.run(recovery_only=recovery_only)
    captured = int(time.time())
    checkpoint = {**receipt, 'capturedAt': captured, 'baselineHostCommit': policy['hostPromotion']['commit'],
                  'baselineImage': baseline, 'startupOverrideSha256': digest(startup_override(binding, policy, baseline))}
    transaction.save({'binding': transaction.binding, 'baselineContainer': baseline_container,
                      'activationRun': 'original', 'checkpoint': checkpoint}, 'ROLLING_BACK')
    state = {'enabled': True, 'bootstrapVerified': True, 'approvedImage': baseline, 'recoveryRequired': True,
             'tenantPilotActivationPrepared': prepared}
    os.environ[profile['healthSecretEnv']] = 'test'
    def protected_retry(now):
        state['challenge'] = {'operation': 'tenant-pilot-cutover', 'nonce': 'fixture', 'expires': now + 20}
        return execute('tenant-pilot-cutover', {'token': 'test', 'challenge': 'fixture'}, policy, state, backend,
                       lambda _: None, now=now, verifier=lambda *a, **k: {'jti': str(now), 'exp': now + 20, 'run_id': str(now)})

    try:
        start(503)
        try:
            protected_retry(captured)
        except Denied as error:
            assert error.code == 'RECOVERY_REQUIRES_OWNER'
        else:
            raise AssertionError('Failed Host API unexpectedly released recovery')
        assert state['recoveryRequired'] and inspect()['State']['Running'] is False
        assert transaction.load()['rollbackHealthFailure'] == {'reason': 'HEALTH_HTTP_REJECTED', 'httpStatus': 503}
        result = protected_retry(captured + 121)
        assert result['state'] == 'PILOT_ROLLED_BACK' and result['recoveryRequired'] is False
        assert result['recoveryVerified'] and result['activationAuthorized'] is False
        assert inspect()['Image'] == fallback and health()['state'] == 'HOST_READY'
        print('PILOT_DOCKER_WATCHDOG_RECOVERY_PASS')
    finally:
        os.environ.pop(profile['healthSecretEnv'], None)
        for name in containers:
            docker.run(['docker', 'rm', '--force', name])


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--base-image', required=True); args = parser.parse_args()
    from hosts.gamerhq.skill_deployment_docker import IMAGE
    if not IMAGE.fullmatch(args.base_image):
        raise ValueError('Immutable disposable image required')
    with tempfile.TemporaryDirectory(prefix='host-promotion-ci-') as root:
        backend = HostBackend({'workRoot': root}, {'hostPromotion': {'commit': COMMIT, 'archiveSha256': ARCHIVE_SHA256}})
        image = build_overlay(backend, args.base_image)
        probe = '''import asyncio,json
from bot import bot
from cogs.event_editor import SkillEventEditor,WeekdayView,EventPreviewView
from cogs.lfg import BuilderDateSelect,BuilderTimeSelect,BuilderPlayersSelect,WeekdayToggle
from skill_runtime import ManagementField,ManagementFieldOption
from services.server_configuration_preview import preview
from tools.game_catalog_import import manifest
from inspect import iscoroutinefunction
assert iscoroutinefunction(preview) and iscoroutinefunction(bot.setup_hook) and not bot.is_ready()
assert len(manifest('/app/data/game_catalog_expansion.json')) == 70
async def check():
 fields=(ManagementField(key='weekdays',label='Weekdays',type='multi_select',config_path='weekdays',options=tuple(ManagementFieldOption(str(i),name) for i,name in enumerate(('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')))),)
 draft=dict(name='Offline fixture',kind='recurring',weekdays=[0,2,4],startDate='2030-01-07',localTime='19:00',timezone='Europe/Berlin',maxPlayers=5,visibility='private')
 view=SkillEventEditor(10,fields,draft)
 assert len(view.children)==9
 assert isinstance(view.children[0],BuilderDateSelect) and isinstance(view.children[1],BuilderTimeSelect) and isinstance(view.children[2],BuilderPlayersSelect)
 assert len([b for b in WeekdayView(view).children if isinstance(b,WeekdayToggle)])==7
 assert EventPreviewView(view).confirm.disabled
 assert 'Every Monday, Wednesday, Friday' in view.content()
 print(json.dumps(dict(compactEditor='PASS',configurationPreview='PASS',startupImports='PASS',weekdayControls=7,gatewayStarted=False)))
asyncio.run(check())
'''
        result = backend.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--user', '10001:10001', '--memory', '256m', '--cpus', '1', '--pids-limit', '64',
            '--entrypoint', 'python', image, '-c', probe])
        assert all(json.loads(result)[check] == 'PASS' for check in ('compactEditor', 'configurationPreview', 'startupImports'))
        assert source_commit(backend, image) == COMMIT
        from tools.staging_automation.pilot_activation import rehearse_container_rollback
        rehearse_container_rollback(backend, args.base_image, image)
        print('PILOT_COMPOSE_ROLLBACK_PASS')
        quiesced_checkpoint_smoke(backend, root, args.base_image, image)
        print(json.dumps({'hostCommit': COMMIT, 'archiveSha256': ARCHIVE_SHA256, 'overlay': 'PASS',
            'compactEditor': 'PASS', 'configurationPreview': 'PASS', 'startupImports': 'PASS', 'gatewayStarted': False, 'credentialsUsed': False, 'databaseMounted': False}))


if __name__ == '__main__': main()
