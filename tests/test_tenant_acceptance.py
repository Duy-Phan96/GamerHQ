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


def channel_payloads(guild_id):
    parent = guild_id * 10000 + 10
    overwrites = [{'id':str(guild_id),'type':0,'allow':'0','deny':'1024'},
                  {'id':'100','type':1,'allow':'68608','deny':'0'},
                  {'id':'300','type':1,'allow':'1024','deny':'2048'}]
    return [{'id':str(parent),'guild_id':str(guild_id),'type':4,'name':'private-category',
             'position':0,'permission_overwrites':overwrites},
            {'id':str(parent+1),'guild_id':str(guild_id),'type':0,'name':'private-text',
             'position':1,'parent_id':str(parent),'permission_overwrites':overwrites},
            {'id':str(parent+2),'guild_id':str(guild_id),'type':2,'name':'private-voice',
             'position':2,'parent_id':str(parent),'bitrate':64000,'user_limit':0,'permission_overwrites':overwrites}]


def model(guild_id, *, staff=False, populated=False):
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
        'roles': [everyone, bot_role] + ([staff_role] if staff else []), 'channels': channel_payloads(guild_id) if populated else [], 'members': [member(application, True), member(owner_id)],
        'verification_level': 0, 'default_message_notifications': 0, 'explicit_content_filter': 0, 'afk_timeout': 60, 'features': []})
    client._connection._guilds[guild_id] = guild
    guild._add_member(discord.Member(data=member(owner_id), guild=guild, state=client._connection))
    guild._add_member(discord.Member(data=member(application, True), guild=guild, state=client._connection))
    async def no_channels(): return list(guild.channels)
    async def roles(): return list(guild.roles)
    async def fetch_member(ident): return guild.get_member(ident)
    # Actual Guild REST methods call the model HTTP client, never Gateway/login.
    async def http(route, **kwargs):
        if route.path.endswith('/channels'): return channel_payloads(guild_id) if populated else []
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
        assert row['templateAcceptance'] == {'status':'UNVERIFIED','selected':False}
        assert row['status'] == 'UNVERIFIED'
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


@pytest.mark.parametrize('historical_failure',[False,True,'schema'])
@pytest.mark.parametrize('configured',[False,True])
def test_full_rest_adapter_uses_real_discord_models_and_existing_previews(cohort, monkeypatch,historical_failure,configured):
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
            if '/channels/' in path:
                if path.endswith('/pins'):return Response({'items':[],'has_more':False})
                if path.endswith('/messages'):return Response([])
                raise AssertionError('Unexpected message request')
            parts = path.split('/')
            guild_id = int(parts[4])
            guild, owner, _ = model(guild_id)
            if path.endswith('/roles'):
                return Response([{'id':str(role.id),'name':role.name,'permissions':str(role.permissions.value),'position':role.position,
                                  'color':0,'managed':role.managed,'hoist':False} for role in guild.roles])
            if path.endswith('/channels'):
                rows=channel_payloads(guild_id)
                if configured:
                    rows[0]['name']='COMMUNITY';rows[1]['name']='guide'
                    for row in rows:row['permission_overwrites']=[]
                return Response(rows)
            if '/members/' in path:
                ident = int(parts[-1])
                return Response({'user':{'id':str(ident),'username':'private-member-name','discriminator':'0','avatar':None,'bot':ident==100},
                                 'roles':[str(guild_id * 1000 + 1)] if ident == 100 else [], 'flags':0,'joined_at':'2020-01-01T00:00:00+00:00'})
            return Response({'id':str(guild_id),'owner_id':'200','name':'private-guild-name','verification_level':0,
                             'default_message_notifications':0,'explicit_content_filter':0,'afk_timeout':60,'features':[]})
    monkeypatch.setenv('DISCORD_TOKEN','private-test-token')
    monkeypatch.setenv('GAMERHQ_WEB_API_SECRET','private-api-credential')
    monkeypatch.setattr(adapter.aiohttp, 'ClientSession', lambda **kwargs: Session())
    if configured:
        for guild_id in (1,2):
            with cohort.scope(guild_id):
                db.set_setting(f'managed_category:{guild_id}:community',guild_id*10000+10)
                db.set_setting(f'managed_channel:{guild_id}:guide',guild_id*10000+11)
    run=gather
    if historical_failure is True:
        import subprocess,types
        original=types.ModuleType('original_readonly_acceptance')
        body=subprocess.check_output(['git','show','d1eed91e3e1608176704407843243afbefc8fd7c:services/tenant_acceptance.py'])
        exec(compile(body,'original_readonly_acceptance.py','exec'),original.__dict__)
        monkeypatch.setattr(adapter,'collect',original.collect)
    elif historical_failure=='schema':
        import subprocess,types
        core=types.ModuleType('original_core');original=types.ModuleType('original_adapter')
        for module,path in [(core,'services/tenant_acceptance.py'),(original,'tools/tenant_acceptance_collect.py')]:
            body=subprocess.check_output(['git','show','9935969d1039958e667420f9dac13dcef3e8a958:'+path])
            exec(compile(body,path,'exec'),module.__dict__)
        original.collect=core.collect
        run=original.gather
    before = {path:path.read_bytes() for path in (cohort.primary_database, cohort._tenant(2).database)}
    packet = {'applicationId':'100','guildIds':['1','2'],'primaryGuildId':'1','database':str(cohort.primary_database)}
    result = asyncio.run(run(packet))
    assert result['isolationGuards']['status'] == 'PASS', result
    if historical_failure is True or historical_failure=='schema' and configured:
        # Reproduce the complete live signature with the exact installed code,
        # not just a synthetic TypeError: 2 identity reads + 5 per-guild reads,
        # cached admission models, no successful preview, and no mutation.
        assert result['discordReads']==(12 if historical_failure is True else 22)
        reason,stage=('COLLECTION_UNAVAILABLE','DISCORD_INVENTORY') if historical_failure is True else ('COLLECTION_SCHEMA_INVALID','TEMPLATE_PREVIEW')
        assert all(row['reason']==reason and row['stage']==stage and row['passCount']==0 for row in result['guilds'])
        assert all(row['status']=='PASS' for row in result['hostCrossTenantReadDenials'])
        assert result['mutationsAttempted'] is False
        assert all(path.read_bytes()==body for path,body in before.items())
        return
    assert all(row.get('previewStable') for row in result['guilds']), result
    for row in result['guilds']:
        assert sorted(item['type'] for item in row['inventory']['channels']) == [0,2,4]
        assert len(row['inventory']['channels']) == 3
        assert all(len(item['overwrites']) == (0 if configured else 3) for item in row['inventory']['channels'])
        if configured:assert row['preview']['rows'] and any(item['kind']=='message' for item in row['preview']['rows'])
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


def test_host_api_failure_keeps_exact_stage_and_http_status_without_body(cohort):
    from tools.tenant_acceptance_collect import AcceptanceReadError
    result=asyncio.run(acceptance.collect(cohort,100,async_model,
                      AsyncMock(side_effect=AcceptanceReadError('HOST_SKILL_READ_REJECTED',403))))
    assert result['guilds'][0]['stage']=='HOST_SKILL_STATE'
    assert result['guilds'][0]['reason']=='HOST_SKILL_READ_REJECTED'
    assert result['guilds'][0]['httpStatus']==403


def test_populated_inventory_preserves_ids_parents_and_overwrites(cohort):
    # Exact failure in the original collector, using a real discord.py enum.
    with pytest.raises(TypeError): int(discord.ChannelType.text)
    async def load(guild): return model(guild,populated=True)
    before={path:path.read_bytes() for path in (cohort.primary_database,cohort._tenant(2).database)}
    result=asyncio.run(acceptance.collect(cohort,100,load,AsyncMock(return_value=[skill()])))
    for row in result['guilds']:
        assert row['previewStable'], row
        channels={item['id']:item for item in row['inventory']['channels']}
        parent=int(row['guildId'])*10000+10
        assert channels[str(parent)]['type']==4
        assert channels[str(parent+1)]['parentId']==str(parent)
        assert channels[str(parent+2)]['type']==2
        assert all(len(channel['overwrites'])==3 for channel in channels.values())
    assert all(path.read_bytes()==body for path,body in before.items())


@pytest.mark.parametrize('failure,reason',[(TypeError('private-value'),'COLLECTION_TYPE_INVALID'),
                                         (KeyError('private-value'),'COLLECTION_SCHEMA_INVALID'),
                                         (TimeoutError('private-value'),'READ_TIMEOUT')])
def test_inventory_projection_errors_are_specific_but_never_print_values(cohort,monkeypatch,failure,reason):
    def fail(*_): raise failure
    monkeypatch.setattr(acceptance,'inventory',fail)
    result=asyncio.run(acceptance.collect(cohort,100,async_model,AsyncMock(return_value=[skill()])))
    assert all(row['stage']=='INVENTORY_PROJECTION' and row['reason']==reason for row in result['guilds'])
    assert 'private-value' not in json.dumps(result)


@pytest.mark.parametrize('status,code,reason',[(401,0,'DISCORD_READ_REJECTED'),(403,50001,'DISCORD_READ_REJECTED'),
                                             (404,10003,'DISCORD_READ_REJECTED'),(429,0,'DISCORD_RATE_LIMITED')])
def test_discord_failures_report_exact_status_operation_and_code_without_body(cohort,status,code,reason):
    class Response:
        reason='fixture'
        def __init__(self):
            self.status=status
            self.content=SimpleNamespace(read=AsyncMock(return_value=json.dumps({'code':code,'message':'private-body'}).encode()))
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
    rest=ReadOnlyDiscord(SimpleNamespace(get=lambda *a,**k:Response()),'private-token',100,(1,2))
    async def load(guild):
        rest.guild_id=guild
        await rest.request(Route('GET','/guilds/{guild_id}/channels',guild_id=guild))
    result=asyncio.run(acceptance.collect(cohort,100,load,AsyncMock(return_value=[skill()])))
    for row in result['guilds']:
        assert row['reason']==reason and row['httpStatus']==status
        assert row['operation']=='GUILD_CHANNELS'
        if status in (403,404): assert row['discordCode']==code
    assert 'private-body' not in json.dumps(result) and 'private-token' not in json.dumps(result)


def test_non_json_success_response_is_redacted_and_not_inventory_success(cohort):
    class Response:
        status=200
        content=SimpleNamespace(read=AsyncMock(return_value=b'private non-json response'))
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
    rest=ReadOnlyDiscord(SimpleNamespace(get=lambda *a,**k:Response()),'private-token',100,(1,2))
    async def load(guild):
        rest.guild_id=guild
        await rest.request(Route('GET','/guilds/{guild_id}/roles',guild_id=guild))
    result=asyncio.run(acceptance.collect(cohort,100,load,AsyncMock(return_value=[skill()])))
    assert all(row['reason']=='DISCORD_RESPONSE_INVALID' and row['httpStatus']==200 and row['operation']=='GUILD_ROLES' for row in result['guilds'])
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('legacy_client',[True,False])
def test_configured_guide_preview_uses_real_bot_contract(cohort,monkeypatch,legacy_client):
    async def run():
        guild,owner,client=model(1,populated=True)
        if not legacy_client:
            client.tree=discord.app_commands.CommandTree(client)
        payloads=channel_payloads(1)
        payloads[0]['name']='COMMUNITY'
        payloads[1]['name']='guide'
        for item in payloads:item['permission_overwrites']=[]
        client.http.get_all_guild_channels=AsyncMock(return_value=payloads)
        native=client.http.request
        async def reads(route,**kwargs):
            if route.path.endswith('/pins'):return {'items':[],'has_more':False}
            if route.path.endswith('/messages'):return []
            return await native(route,**kwargs)
        client.http.request=reads
        guild._channels.clear()
        for item in payloads:
            factory,_=discord.channel._guild_channel_factory(item['type'])
            current=factory(state=client._connection,guild=guild,data=item)
            guild._channels[current.id]=current
        with cohort.scope(1):
            db.set_setting('managed_category:1:community','10010')
            db.set_setting('managed_channel:1:guide','10011')
            with acceptance.readonly_snapshot():
                return await server_templates.preview(guild,owner,server_templates.GAMERHQ,client)
    if legacy_client:
        with pytest.raises(AttributeError,match='tree'):asyncio.run(run())
    else:
        draft=asyncio.run(run())
        assert draft['boards']['rows']
        assert all(not row['key'].startswith('server_staff_commands_message_id') for row in draft['boards']['rows'])


def test_command_derived_guide_is_never_verified_from_empty_registry(cohort):
    guild,_,_=model(1)
    with pytest.raises(ValueError,match='COMMAND_REGISTRY_UNAVAILABLE'):
        acceptance.project_preview(guild,{'boards':{'rows':[{'key':'server_staff_commands_message_id'}]}},100)
