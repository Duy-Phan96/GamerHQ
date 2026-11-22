"""Games-only REST, private comparison and owner/controller boundary regressions."""
import ast
import asyncio
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.http import Route
import pytest

from database import db
from services import game_launch_audit as projection
from tools import game_launch_audit_collect as collector, game_launch_audit_report as report
from tools import game_audit_execution as execution, game_audit_operator as operator
from tools.staging_automation import games_audit
from tools.staging_automation.policy import Denied, SECURITY, admit


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'primary.db')
    db.init_db()
    db.upsert_custom_game('Terraria', '🎮', 'Other')
    monkeypatch.setattr(config, 'GUILD_ID', 1)
    monkeypatch.setenv('DISCORD_TOKEN', 'synthetic-private-token')
    guild = {'id': '1', 'name': 'private-server-name', 'owner_id': '50', 'features': [],
             'verification_level': 0, 'default_message_notifications': 0,
             'explicit_content_filter': 0, 'afk_timeout': 60}
    roles = [{'id': '1', 'name': '@everyone', 'position': 0, 'permissions': '0', 'color': 0},
             {'id': '101', 'name': 'Host', 'position': 20, 'permissions': str(discord.Permissions.all().value), 'color': 0}]
    def user(uid, bot=False):
        return {'id': str(uid), 'username': 'private-owner-name', 'bot': bot, 'discriminator': '0', 'avatar': None}
    bodies = {'/users/@me': user(100, True), '/guilds/1': guild, '/guilds/1/roles': roles,
              '/guilds/1/channels': [],
              '/guilds/1/members/100': {'user': user(100, True), 'roles': ['101'], 'flags': 0},
              '/guilds/1/members/50': {'user': user(50), 'roles': [], 'flags': 0}}
    packet = {'environment': 'staging', 'guildId': 1, 'applicationId': 100, 'database': str(db.DB_PATH)}
    return packet, bodies


class Response:
    def __init__(self, value, status=200):
        raw = json.dumps(value).encode()
        self.status = status
        self.content = SimpleNamespace(read=AsyncMock(side_effect=[raw[:3], raw[3:], b'']))
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass


class Session:
    def __init__(self, bodies): self.bodies, self.reads = bodies, []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def get(self, url, *, headers, allow_redirects):
        assert headers == {'Authorization': 'Bot synthetic-private-token'}
        assert allow_redirects is False
        assert url.startswith('https://discord.com/api/v10/')
        self.reads.append(url)
        return Response(self.bodies[url.removeprefix('https://discord.com/api/v10')])


def run_collection(runtime):
    packet, bodies = runtime
    session = Session(bodies)
    result = asyncio.run(collector.gather(packet, session_factory=lambda **kwargs: session))
    return result, session


def test_unexpected_projection_failure_reports_safe_phase_without_private_details(runtime, monkeypatch):
    packet, bodies = runtime
    real_gather = collector.gather

    async def with_fixture(packet, *, diagnostic):
        return await real_gather(packet, session_factory=lambda **kwargs: Session(bodies),
                                 diagnostic=diagnostic)

    async def fail_projection(*args, **kwargs):
        raise RuntimeError('private token, database row and traceback must never be shown')

    monkeypatch.setattr(collector, 'gather', with_fixture)
    monkeypatch.setattr(projection, 'collect', fail_projection)
    result = collector.main(packet)

    assert result['state'] == 'GAMES_AUDIT_UNAVAILABLE'
    assert result['status'] == 'UNVERIFIED'
    assert result['reason'] == 'RUNTIME_OR_SCHEMA_UNAVAILABLE'
    assert result['diagnosticStage'] == 'GAMES_PROJECTION'
    assert result['databaseChanged'] is False
    assert result['discordResourcesChanged'] is False
    assert result['gatewayRestarted'] is False
    assert 'private token' not in json.dumps(result)
    assert 'traceback' not in json.dumps(result)


def test_unexpected_model_construction_failure_is_distinct_from_games_projection(runtime, monkeypatch):
    packet, bodies = runtime
    real_gather = collector.gather

    async def with_fixture(packet, *, diagnostic):
        return await real_gather(packet, session_factory=lambda **kwargs: Session(bodies),
                                 diagnostic=diagnostic)

    def invalid_model(*args, **kwargs):
        raise ValueError('private discord user and role payload')

    monkeypatch.setattr(collector, 'gather', with_fixture)
    monkeypatch.setattr(collector.discord, 'Guild', invalid_model)
    result = collector.main(packet)

    assert result['state'] == 'GAMES_AUDIT_UNAVAILABLE'
    assert result['reason'] == 'RUNTIME_OR_SCHEMA_UNAVAILABLE'
    assert result['diagnosticStage'] == 'DISCORD_MODEL'
    assert 'private discord' not in json.dumps(result)


def test_allowlisted_schema_failure_keeps_exact_reason_and_phase(runtime, monkeypatch):
    packet, bodies = runtime
    real_gather = collector.gather

    async def with_fixture(packet, *, diagnostic):
        return await real_gather(packet, session_factory=lambda **kwargs: Session(bodies),
                                 diagnostic=diagnostic)

    async def schema_rejected(*args, **kwargs):
        raise ValueError('GAMES_AUDIT_SCHEMA_REJECTED')

    monkeypatch.setattr(collector, 'gather', with_fixture)
    monkeypatch.setattr(projection, 'collect', schema_rejected)
    result = collector.main(packet)

    assert result['reason'] == 'GAMES_AUDIT_SCHEMA_REJECTED'
    assert result['diagnosticStage'] == 'GAMES_PROJECTION'
    assert result['databaseChanged'] is False


def test_real_projection_schema_gate_reports_database_schema_phase(runtime, monkeypatch):
    packet, bodies = runtime
    real_gather = collector.gather

    async def with_fixture(packet, *, diagnostic):
        return await real_gather(packet, session_factory=lambda **kwargs: Session(bodies),
                                 diagnostic=diagnostic)

    monkeypatch.setattr(collector, 'gather', with_fixture)
    monkeypatch.setattr(db, 'SCHEMA_VERSION', db.SCHEMA_VERSION + 1)
    result = collector.main(packet)

    assert result['reason'] == 'GAMES_AUDIT_SCHEMA_REJECTED'
    assert result['diagnosticStage'] == 'DATABASE_SCHEMA'
    assert result['databaseChanged'] is False
    assert result['discordResourcesChanged'] is False


def test_real_catalog_failure_is_scoped_and_never_leaks_exception(runtime, monkeypatch):
    packet, bodies = runtime
    real_gather = collector.gather

    async def with_fixture(packet, *, diagnostic):
        return await real_gather(packet, session_factory=lambda **kwargs: Session(bodies),
                                 diagnostic=diagnostic)

    def bad_catalog(*args, **kwargs):
        raise RuntimeError('private games and hidden database details')

    monkeypatch.setattr(collector, 'gather', with_fixture)
    monkeypatch.setattr(db, 'get_all_games', bad_catalog)
    result = collector.main(packet)

    assert result['reason'] == 'RUNTIME_OR_SCHEMA_UNAVAILABLE'
    assert result['diagnosticStage'] == 'GAME_CATALOG'
    assert 'private games' not in json.dumps(result)
    assert result['databaseChanged'] is False


def test_one_guild_collection_uses_six_gets_and_never_enumerates_other_guilds(runtime):
    before = db.DB_PATH.read_bytes()
    result, session = run_collection(runtime)
    assert result['state'] == 'GAMES_AUDIT_COLLECTED'
    assert result['guildId'] == 1 and len(session.reads) == 6
    assert all('/guilds/2' not in url and not url.endswith('/users/@me/guilds') for url in session.reads)
    assert result['counts'].get('NOT_SET_UP') == len(result['games']), (result['counts'], result['games'][:1])
    assert db.DB_PATH.read_bytes() == before
    assert 'private-owner-name' not in json.dumps(result)
    assert 'synthetic-private-token' not in json.dumps(result)


@pytest.mark.parametrize('method,path', [('POST','/guilds/1/channels'),('GET','/guilds/2'),
    ('GET','/users/@me/guilds'),('GET','/channels/3/messages'),('GET','/guilds/1/members/999')])
def test_read_allowlist_rejects_writes_other_guilds_and_private_members(runtime, method, path):
    transport = collector.GamesDiscord(None, 'not-exported', runtime[0])
    with pytest.raises(collector.AuditUnavailable, match='READ_ONLY_REQUEST_REJECTED'):
        asyncio.run(transport.request(Route(method, path)))


def test_wrong_application_or_missing_database_fail_before_projection(runtime):
    packet, bodies = runtime
    bodies['/users/@me']['id'] = '200'
    with pytest.raises(collector.AuditUnavailable, match='BOT_IDENTITY_REJECTED'):
        run_collection(runtime)
    packet['database'] = str(db.DB_PATH.parent / 'absent.db')
    with pytest.raises(collector.AuditUnavailable, match='DATABASE_IDENTITY_REJECTED'):
        run_collection(runtime)
    assert not Path(packet['database']).exists()


def test_comparison_uses_aliases_and_never_snowflake_or_local_catalog_ids(runtime):
    staging, _ = run_collection(runtime)
    # Synthetic aliases demonstrate the existing GTA shared-entry contract.
    row = copy.deepcopy(staging['games'][0])
    row.update(name='GTA Online', logicalKey='gta online', aliases=['GTA V', 'Grand Theft Auto V'],
               roleId=20, canonicalChannelId=30)
    staging['games'] = [row]
    production = copy.deepcopy(staging)
    production.update(environment='production', guildId=9, applicationId=900)
    production['games'][0].update(name='Grand Theft Auto V', logicalKey='grand theft auto v',
                                  aliases=[], catalogId=999, roleId=2000, canonicalChannelId=3000)
    result = report.compare(production, staging)
    assert result['counts'] == {'MATCHED': 1} and result['games'][0]['differentFields'] == []
    public = json.dumps(report.summary(result))
    assert 'GTA' not in public and '2000' not in public
    production['games'].append(copy.deepcopy(production['games'][0]))
    assert report.compare(production, staging)['counts'] == {'REVIEW': 1}


def test_unavailable_production_never_becomes_missing_games(runtime):
    staging, _ = run_collection(runtime)
    unknown = {'schemaVersion': 1, 'state': 'GAMES_AUDIT_UNAVAILABLE', 'databaseChanged': False,
               'discordResourcesChanged': False, 'gatewayRestarted': False}
    result = report.compare(unknown, staging)
    assert result['coverage']['production'] == 'UNVERIFIED'
    assert set(result['counts']) == {'UNVERIFIED'}
    assert 'STAGING_ONLY' not in result['counts']


def test_private_file_cannot_overwrite_or_follow_symlink(tmp_path):
    output = tmp_path / 'private.json'
    report.private_write(output, {'only': 'synthetic'})
    with pytest.raises(FileExistsError): report.private_write(output, {'overwrite': True})
    if os.name != 'nt': assert output.stat().st_mode & 0o777 == 0o600


def test_legacy_primary_adapter_reuses_existing_read_session_and_preserves_database(runtime, monkeypatch):
    # Facade reproduces legacy DB API without replacing its real connection helpers.
    class Legacy:
        def __getattr__(self, key):
            if key == 'current_path': raise AttributeError(key)
            return getattr(db, key)
    packet, bodies = runtime
    packet['environment'] = 'production'
    legacy = Legacy()
    monkeypatch.setattr(projection, 'db', legacy)
    # Collection context must also see the legacy capability surface.
    import database
    monkeypatch.setattr(database, 'db', legacy)
    before = db.DB_PATH.read_bytes()
    result, _ = run_collection(runtime)
    assert result['environment'] == 'production' and result['databaseIntegrity'] == 'PASS'
    assert db.DB_PATH.read_bytes() == before


def test_source_packet_and_controller_require_explicit_enrollment():
    sources = {name: Path(name).read_text(encoding='utf-8') for name in execution.FILES}
    hashes = {name: hashlib.sha256(value.encode()).hexdigest() for name, value in sources.items()}
    sealed = execution.packet(sources, hashes, {}, source_commit='a'*40)
    assert sealed['hashes'] == hashes
    sources[execution.FILES[0]] += '# tampered'
    with pytest.raises(ValueError): execution.packet(sources, hashes, {}, source_commit='a'*40)
    with pytest.raises(Denied): games_audit.validate_pin({})
    assert 'games-audit' in SECURITY
    with pytest.raises(Denied): admit('games-audit', {'enabled': True, 'bootstrapVerified': True, 'recoveryRequired': True})
    with pytest.raises(Denied): games_audit.run(SimpleNamespace(policy={}))
    ast.parse(Path('tools/game_audit_operator.py').read_text(), feature_version=(3, 9))


def test_controller_only_collects_primary_and_retains_details_privately(runtime, tmp_path, monkeypatch):
    from tools.staging_automation import pilot_cutover
    monkeypatch.setattr(pilot_cutover, 'journal_status', lambda _: {'phase': 'PILOT_ACTIVE'})
    monkeypatch.setattr(games_audit, '__file__', str(tmp_path/'staging_automation'/'games_audit.py'))
    sources = {name: Path(name).read_text(encoding='utf-8') for name in execution.FILES}
    hashes = {name: hashlib.sha256(value.encode()).hexdigest() for name, value in sources.items()}
    for name, value in sources.items():
        path = tmp_path/'games-audit-source'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
    work = tmp_path/'private-worker'; work.mkdir()
    result, _ = run_collection(runtime)
    result.update(observerCommit='a'*40, observerSourceSha256=hashes)
    current = {'Id': 'container', 'Image': 'sha256:'+'b'*64,
               'Config': {'Labels': {'org.opencontainers.image.revision': 'c'*40}}}
    calls = []
    def execute(command, **kwargs):
        packet = json.loads(kwargs['stdin'])
        assert packet['binding']['guildId'] == 1
        assert packet['binding']['environment'] == 'staging'
        assert command[:3] == ['docker', 'exec', '--interactive']
        calls.append(command)
        return json.dumps(result)
    backend = SimpleNamespace(operation_lock=1,
        policy={'gamesAudit': {'sourceCommit': 'a'*40, 'sourceSha256': hashes, 'workflowSha256': 'd'*64},
                'tenantPilot': {'applicationId': '100'}, 'tenantPilotActivation': {'commit': 'c'*40}},
        profile={'stagingGuildId': '1', 'productionGuildId': '9', 'workRoot': str(work)},
        inspect=lambda: current, health=lambda: {'state': 'HOST_READY'}, run=execute)
    public = games_audit.run(backend)
    assert len(calls) == 1 and public['privateReceiptStored'] is True
    assert 'Terraria' not in json.dumps(public) and 'guildId' not in public
    receipt = work/('games-audit-'+public['privateReceiptId']+'.json')
    assert json.loads(receipt.read_text())['guildId'] == 1
    if os.name != 'nt': assert receipt.stat().st_mode & 0o777 == 0o600


def test_http_failure_is_bounded_redacted_and_unknown(runtime):
    class Rejected(Session):
        def get(self, *args, **kwargs):
            return Response({'private': 'never expose the API response'}, 403)
    with pytest.raises(collector.AuditUnavailable) as error:
        asyncio.run(collector.gather(runtime[0], session_factory=lambda **kwargs: Rejected(runtime[1])))
    assert error.value.reason == 'DISCORD_HTTP_REJECTED' and error.value.http_status == 403
    assert 'private' not in str(error.value)


def test_operator_does_not_read_docker_environment_or_accept_root_user():
    assert '.Config.Env' not in operator.FORMAT
    def run(*args, **kwargs):
        return SimpleNamespace(stdout=json.dumps({'running': True, 'health': 'healthy', 'workingDir': '/app',
            'hostCommit': 'a'*40, 'image': 'sha256:'+'b'*64, 'user': '0'}))
    with pytest.raises(ValueError, match='RUNNING_HOST_IDENTITY_UNVERIFIED'):
        operator.inspect('gamerhq-bot', run=run)
    with pytest.raises(ValueError): operator.inspect('gamerhq-bot; other-command', run=run)


def test_production_dockerfile_named_user_and_build_commit_are_supported_without_labels():
    # The real repository Dockerfile uses USER gamerhq and /app/BUILD_COMMIT;
    # OCI revision labels/numeric Config.User are Staging overlays, not assumed.
    meta = {'id': 'a'*64, 'image': 'sha256:'+'b'*64, 'user': 'gamerhq', 'workingDir': '/app',
            'running': True, 'health': 'healthy', 'hostCommit': None, 'mounts': []}
    actual = operator.inspect('gamerhq-bot', run=lambda *a, **k: SimpleNamespace(stdout=json.dumps(meta)))
    configured = {'configuredGuildId': 1, 'database': '/app/runtime/data/gamerhq.db',
                  'mode': '0o660', 'uid': 10001, 'gid': 10001, 'readable': True,
                  'runtimeUid': 10001, 'runtimeGid': 10001, 'buildCommit': 'c'*40}
    def run(*args, **kwargs):
        assert args[0][0:2] == ['docker','exec'] and args[0][3] == 'gamerhq'
        assert 'DISCORD_TOKEN' not in args[0][-1] and 'sqlite3' not in args[0][-1]
        return SimpleNamespace(stdout=json.dumps(configured))
    assert operator.configured_metadata(actual, run=run)['buildCommit'] == 'c'*40
    configured['runtimeUid'] = 0
    with pytest.raises(ValueError, match='RUNTIME_USER_UNVERIFIED'): operator.configured_metadata(actual, run=run)
    configured['runtimeUid'] = 10001
    actual['hostCommit'] = 'd'*40
    with pytest.raises(ValueError, match='HOST_REVISION_CONFLICT'): operator.configured_metadata(actual, run=run)


def test_owner_launcher_cannot_route_staging_or_servertest(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['game_audit_operator', '--source-commit', 'a'*40,
                                    '--environment', 'staging', '--container', 'synthetic-test-container', '--preflight'])
    with pytest.raises(SystemExit) as error:
        operator.main()
    assert error.value.code == 2  # Argument rejection precedes Docker, Git and data access.


def test_cold_source_sealed_launch_uses_installed_db_read_only_without_gateway(runtime):
    sources = {name: Path(name).read_text(encoding='utf-8') for name in execution.FILES}
    hashes = {name: hashlib.sha256(body.encode()).hexdigest() for name, body in sources.items()}
    p = execution.packet(sources, hashes, runtime[0], source_commit='a'*40)
    p['fixtureResponses'] = runtime[1]
    prefix = r'''
import asyncio,io,json,sys
sys.path.insert(0,REPOSITORY_ROOT)
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
import aiohttp
data=json.load(sys.stdin)
bodies=data.pop('fixtureResponses')
class Response:
 def __init__(self,body):
  self.body=json.dumps(body).encode();self.status=200
  self.content=self
 async def read(self,n):
  chunk=self.body[:n];self.body=self.body[n:];return chunk
 async def __aenter__(self):return self
 async def __aexit__(self,*a):pass
class Session:
 def __init__(self,**kw):pass
 async def __aenter__(self):return self
 async def __aexit__(self,*a):pass
 def get(self,url,**kw):
  assert url.startswith('https://discord.com/api/v10/')
  assert kw['allow_redirects'] is False
  return Response(bodies[url.removeprefix('https://discord.com/api/v10')])
aiohttp.ClientSession=Session
sys.stdin=io.StringIO(json.dumps(data))
'''.replace('REPOSITORY_ROOT', repr(str(Path.cwd())))
    before = db.DB_PATH.read_bytes()
    result = subprocess.run([sys.executable, '-I', '-B', '-c', prefix+execution.LAUNCH],
        input=json.dumps(p), capture_output=True, text=True, timeout=30, check=True,
        env={**os.environ, 'DISCORD_TOKEN': 'synthetic-private-token', 'GUILD_ID': '1', 'GAMERHQ_DB_PATH': str(db.DB_PATH)})
    value = execution.output(result.stdout)
    assert value['state'] == 'GAMES_AUDIT_COLLECTED', value
    assert value['observerCommit'] == 'a'*40 and value['observerSourceSha256'] == hashes
    assert value['discordReads'] == 6 and value['gatewayRestarted'] is False
    assert db.DB_PATH.read_bytes() == before
