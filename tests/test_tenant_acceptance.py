import ast
import asyncio
import copy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.http import Route
import pytest

from database import db
from database.tenants import GuildTenantStore
from services import tenant_acceptance as acceptance, server_templates
from tools.tenant_acceptance_collect import ReadOnlyDiscord, gather
from tools.staging_automation.tenant_acceptance import pack_receipt, unpack_receipt, validate_pin
from tools.staging_automation.policy import Denied, request, admit
from tools.tenant_acceptance_report import summary


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'primary.sqlite')
    db.init_db()
    store = GuildTenantStore(primary_guild=1, primary_database=db.DB_PATH,
                            tenant_directory=tmp_path / 'tenants', additional_guilds=(2,))
    store.provision(2)
    return store


def model(guild_id, *, staff=False):
    application, owner_id = 100, 200
    client = discord.Client(intents=discord.Intents.none())
    client.get_cog = lambda _: None
    client._connection.user = discord.ClientUser(state=client._connection, data={'id': str(application), 'username': 'bot', 'discriminator': '0', 'avatar': None, 'bot': True})
    everyone = {'id': str(guild_id), 'name': '@everyone', 'permissions': str(discord.Permissions(view_channel=True, read_message_history=True).value), 'position': 0, 'color': 0, 'hoist': False, 'managed': False}
    from services.discord_permissions import CORE_INSTALL
    bot_role = {**everyone, 'id': str(guild_id * 1000 + 1), 'name': 'Host', 'position': 20, 'permissions': str(discord.Permissions(**dict.fromkeys(CORE_INSTALL, True)).value)}
    staff_role = {**everyone, 'id': str(guild_id * 1000 + 2), 'name': 'GamerHQ Staff', 'position': 10, 'permissions': '0'}
    def member(ident, bot=False):
        return {'user': {'id': str(ident), 'username': 'private-member-value', 'discriminator': '0', 'avatar': None, 'bot': bot},
                'roles': [bot_role['id']] if bot else [], 'joined_at': '2020-01-01T00:00:00+00:00', 'flags': 0}
    guild = discord.Guild(state=client._connection, data={'id': str(guild_id), 'name': 'private-guild-value', 'owner_id': str(owner_id),
        'roles': [everyone, bot_role] + ([staff_role] if staff else []), 'channels': [], 'members': [member(application, True), member(owner_id)],
        'verification_level': 0, 'default_message_notifications': 0, 'explicit_content_filter': 0, 'afk_timeout': 60, 'features': []})
    client._connection._guilds[guild_id] = guild
    guild._add_member(discord.Member(data=member(owner_id), guild=guild, state=client._connection))
    guild._add_member(discord.Member(data=member(application, True), guild=guild, state=client._connection))
    async def no_channels(): return list(guild.channels)
    async def roles(): return list(guild.roles)
    async def fetch_member(ident): return guild.get_member(ident)
    # Actual Guild REST methods call the model HTTP client, never Gateway/login.
    async def http(route, **kwargs):
        if route.path.endswith('/channels'): return []
        if route.path.endswith('/roles'): return [everyone, bot_role] + ([staff_role] if staff else [])
        if '/members/' in route.path: return member(int(route.url.rsplit('/', 1)[1]), int(route.url.rsplit('/', 1)[1]) == application)
        raise AssertionError('unexpected request')
    client.http.request = http
    return guild, guild.get_member(owner_id), client


def skill(installed=False, enabled=False):
    return {'id': 'event-manager', 'version': '0.1.2', 'state': {'available': True, 'installed': installed, 'configured': installed, 'enabled': enabled, 'healthy': False},
            'source': {'kind': 'external', 'distribution': 'gamerhq-skill-event-manager'}, 'health': {'detail': 'private-health-value'}}


def test_actual_two_guild_previews_are_stable_private_and_do_not_write(cohort):
    before = {path: path.read_bytes() for path in (cohort.primary_database, cohort._tenant(2).database)}
    async def load(guild): return model(guild)
    async def skills(guild): return [skill()]
    result = asyncio.run(acceptance.collect(cohort, 100, load, skills))
    assert len(result['guilds']) == 2
    for row in result['guilds']:
        assert row.get('previewStable') is True, row
        assert row['preview']['staff']['verified'] is False
        assert row['preview']['journal']['state'] == 'ABSENT'
        assert row['skills'][0]['runtime']['status'] == 'UNVERIFIED'
        assert not row['foreignMappings']
    encoded = json.dumps(result)
    assert 'private-member-value' not in encoded and 'private-guild-value' not in encoded and 'private-health-value' not in encoded
    assert result['mutationsAttempted'] is False
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('statement', ["INSERT INTO settings VALUES ('forbidden','private')", "PRAGMA query_only=OFF", 'CREATE TABLE forbidden(x)', "ATTACH DATABASE ':memory:' AS other"])
def test_sql_writes_and_guard_downgrades_are_denied(cohort, statement):
    denied = []
    with cohort.scope(2), acceptance.readonly_snapshot(denied) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement)
    assert denied
    with cohort.scope(1), db.read_only():
        assert db.get_setting('forbidden') is None


def test_cross_tenant_scope_and_wrong_live_identity_are_rejected(cohort):
    with cohort.scope(1):
        with pytest.raises(PermissionError):
            with cohort.scope(2): pass
    async def wrong(guild): return model(2)
    result = asyncio.run(acceptance.collect(cohort, 100, wrong, AsyncMock(return_value=[skill()])))
    assert result['guilds'][0]['reason'] == 'GUILD_IDENTITY_REJECTED'


def test_second_preview_failure_retains_only_sanitized_partial_evidence(cohort):
    count = 0
    async def load(guild):
        nonlocal count
        count += 1
        if count == 2: raise RuntimeError('private-token-value')
        return model(guild)
    result = asyncio.run(acceptance.collect(cohort, 100, load, AsyncMock(return_value=[skill()])))
    assert result['guilds'][0]['status'] == 'UNVERIFIED'
    assert result['guilds'][0]['passCount'] == 1
    assert 'lastVerifiedPass' in result['guilds'][0]
    assert 'private-token-value' not in json.dumps(result)


@pytest.mark.parametrize('method,path,kwargs', [('POST','/guilds/{guild_id}/channels',{'guild_id':1}), ('DELETE','/channels/{channel_id}',{'channel_id':3}),
                                              ('PATCH','/guilds/{guild_id}',{'guild_id':1}), ('GET','/guilds/{guild_id}/roles',{'guild_id':2}),
                                              ('GET','/channels/{channel_id}/messages',{'channel_id':999})])
def test_discord_writes_and_cross_guild_reads_never_reach_transport(method,path,kwargs):
    session = SimpleNamespace(get=AsyncMock())
    rest = ReadOnlyDiscord(session, 'private-token', 100, (1,2))
    rest.guild_id, rest.channels = 1, {3}
    with pytest.raises(ValueError, match='READ_ONLY_REQUEST_REJECTED'):
        asyncio.run(rest.request(Route(method,path,**kwargs)))
    session.get.assert_not_called()


def test_redirect_or_arbitrary_host_never_receives_token():
    session = SimpleNamespace(get=AsyncMock())
    rest = ReadOnlyDiscord(session, 'private-token', 100, (1,2))
    route = SimpleNamespace(method='GET',path='/users/@me',url='https://example.invalid/api/v10/users/@me',guild_id=None,channel_id=None)
    with pytest.raises(ValueError, match='READ_ONLY_REQUEST_REJECTED'):
        asyncio.run(rest.request(route))
    session.get.assert_not_called()


def test_legacy_ssh_limit_is_preserved_by_bounded_compressed_receipt():
    receipt = {'state': 'TENANT_ACCEPTANCE_COLLECTED', 'status': 'UNVERIFIED', 'guilds': [{'id':'1','roles':['1000']*10000}]}
    packed = pack_receipt(receipt)
    assert len(json.dumps(packed)) < 65536
    assert unpack_receipt(packed) == receipt
    packed['reportLength'] += 1
    with pytest.raises(Denied): unpack_receipt(packed)


def test_runtime_unavailable_is_not_inferred_from_enabled_or_health():
    row = skill(True, True)
    row['state']['healthy'] = True
    assert acceptance.skill_projection([row])[0]['runtime']['status'] == 'UNVERIFIED'
    row['runtime'] = {'running': False}
    assert acceptance.skill_projection([row])[0]['runtime']['running'] is False


def test_all_ingress_guards_reject_cross_tenant_in_memory_without_live_mutators(cohort):
    from services.tenant_routing import GuildRoutes
    async def run():
        models = {guild: model(guild)[0] for guild in (1, 2)}
        routes = GuildRoutes(application_id=100, store=cohort, get_guild=models.get)
        routes.verify({'id': '100', 'bot': True}, [{'id': '1'}, {'id': '2'}])
        routes.accepting = True
        return await acceptance.guard_probes(routes)
    result = asyncio.run(run())
    assert result['status'] == 'PASS', result
    assert set(result['paths']) == {'command', 'component', 'modal', 'scheduler', 'skillLifecycle'}
    assert result['liveMutatorsInvoked'] is False


def test_acceptance_is_protected_and_recovery_held_denies_it():
    assert request({'version':1,'phase':'automation','operation':'tenant-acceptance'}) == 'tenant-acceptance'
    with pytest.raises(Denied): admit('tenant-acceptance', {'enabled':True,'bootstrapVerified':True,'recoveryRequired':True})
    with pytest.raises(Denied): validate_pin({})


def test_owner_installer_remains_system_python_39_compatible():
    ast.parse(Path('tools/enroll_staging_tenant_acceptance.py').read_text(), feature_version=(3,9))


def test_full_rest_adapter_uses_real_discord_models_and_existing_previews(cohort, monkeypatch):
    from urllib.parse import urlsplit
    from tools import tenant_acceptance_collect as adapter
    requests = []
    class Response:
        def __init__(self, body, status=200):
            self.status, self.reason = status, 'fixture'
            self.content = SimpleNamespace(read=AsyncMock(return_value=json.dumps(body).encode()))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def get(self, url, *, headers, allow_redirects, params=None):
            assert allow_redirects is False
            requests.append(url)
            path = urlsplit(url).path
            if url.startswith('http://127.0.0.1:8080/'):
                guild = int(path.split('/')[4])
                return Response({'skills': [skill()]}, 200 if str(guild) == headers['X-GamerHQ-Authorized-Guild-Ids'] else 403)
            assert url.startswith('https://discord.com/api/v10/')
            if path.endswith('/users/@me/guilds'): return Response([{'id':'1'}, {'id':'2'}])
            if path.endswith('/users/@me'): return Response({'id':'100','username':'private-bot-name','discriminator':'0','avatar':None,'bot':True})
            parts = path.split('/')
            guild_id = int(parts[4])
            guild, owner, _ = model(guild_id)
            if path.endswith('/roles'):
                return Response([{'id':str(role.id),'name':role.name,'permissions':str(role.permissions.value),'position':role.position,
                                  'color':0,'managed':role.managed,'hoist':False} for role in guild.roles])
            if path.endswith('/channels'): return Response([])
            if '/members/' in path:
                ident = int(parts[-1])
                return Response({'user':{'id':str(ident),'username':'private-member-name','discriminator':'0','avatar':None,'bot':ident==100},
                                 'roles':[str(guild_id * 1000 + 1)] if ident == 100 else [], 'flags':0,'joined_at':'2020-01-01T00:00:00+00:00'})
            return Response({'id':str(guild_id),'owner_id':'200','name':'private-guild-name','verification_level':0,
                             'default_message_notifications':0,'explicit_content_filter':0,'afk_timeout':60,'features':[]})
    monkeypatch.setenv('DISCORD_TOKEN','private-test-token')
    monkeypatch.setenv('GAMERHQ_WEB_API_SECRET','private-api-credential')
    monkeypatch.setattr(adapter.aiohttp, 'ClientSession', lambda **kwargs: Session())
    before = {path:path.read_bytes() for path in (cohort.primary_database, cohort._tenant(2).database)}
    packet = {'applicationId':'100','guildIds':['1','2'],'primaryGuildId':'1','database':str(cohort.primary_database)}
    result = asyncio.run(gather(packet))
    assert result['isolationGuards']['status'] == 'PASS', result
    assert all(row.get('previewStable') for row in result['guilds']), result
    assert all(row['status'] == 'PASS' for row in result['hostCrossTenantReadDenials'])
    assert result['mutationsAttempted'] is False
    assert len([url for url in requests if url.endswith('/channels')]) >= 4
    assert 'private-' not in json.dumps(result)
    assert all(path.read_bytes() == body for path,body in before.items())


def test_staff_mapping_is_explicit_and_a_forged_name_does_not_grant_access(cohort):
    async def load(guild): return model(guild, staff=True)
    async def states(guild): return [skill()]
    with cohort.scope(1): db.set_setting('private_staff_role:1', '1002')
    result = asyncio.run(acceptance.collect(cohort,100,load,states))
    first,second = result['guilds']
    assert first['preview']['staff']['verified'] is True
    assert second['preview']['staff']['verified'] is False
    assert second['preview']['staff']['candidates'] == ['2002']
    assert any(row['reason'] == 'STAFF_MAPPING_ABSENT' for row in second['preview']['rows'])


def test_foreign_authoritative_role_mapping_is_reported_not_adopted(cohort):
    with cohort.scope(2),db.connect() as connection:
        connection.execute('INSERT INTO managed_roles(role_id,role_kind,role_key) VALUES (1001,?,?)',('base','fixture-role'))
    result=asyncio.run(acceptance.collect(cohort,100,lambda guild: async_model(guild),AsyncMock(return_value=[skill()])))
    secondary=result['guilds'][1]
    assert secondary['status']=='FAIL'
    assert any(row['id']=='1001' for row in secondary['foreignMappings'])
    with cohort.scope(2),db.read_only(),db.connect() as connection:
        assert connection.execute('SELECT role_id FROM managed_roles').fetchall()[0][0]==1001


async def async_model(guild):
    return model(guild)


def test_memory_snapshot_is_consistent_without_holding_live_database_locks(cohort):
    with cohort.scope(1):
        db.set_setting('fixture','old')
        with acceptance.readonly_snapshot():
            assert db.get_setting('fixture')=='old'
            with sqlite3.connect(cohort.primary_database) as outside:
                outside.execute("UPDATE settings SET value='new' WHERE key='fixture'")
            assert db.get_setting('fixture')=='old'
        with acceptance.readonly_snapshot():
            assert db.get_setting('fixture')=='new'


def test_read_only_schema_metadata_remains_available_to_existing_helpers(cohort):
    with cohort.scope(1),acceptance.readonly_snapshot() as connection:
        assert connection.execute('PRAGMA table_info(settings)').fetchall()
        assert connection.execute('PRAGMA query_only').fetchone()[0]==1
