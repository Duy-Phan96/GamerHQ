"""Exact legacy sources + pinned Core wheels, disposable Docker, no network."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest
from tools import game_audit_legacy_dependencies as deps
from tools import game_audit_provenance as provenance
from tools import game_audit_execution as execution
from tools.game_catalog_import import name_key
import inspect

pytestmark=pytest.mark.skipif(os.environ.get('GAMERHQ_LEGACY_GAMES_DOCKER_CI')!='true',reason='Explicit disposable Linux Docker fixture only')
LIB='/usr/local/lib/python3.12/site-packages'


def docker(source,data,code,stdin='',flags=()):
    return subprocess.run(['docker','run','--rm','--network','none','--read-only','--user','10001:10001',
        '--mount',f'type=bind,src={source},dst=/app,readonly',
        '--mount',f'type=bind,src={data},dst=/app/runtime/data',
        '-e','GUILD_ID=1','-e','GAMERHQ_DB_PATH=/app/runtime/data/fixture.db','-e','DISCORD_TOKEN=synthetic-never-live',
        '--interactive','--entrypoint','python','gamerhq-bot:local','-I','-S','-B','-c',code],
        input=stdin,text=True,capture_output=True,timeout=150)


@pytest.fixture(scope='module')
def core(tmp_path_factory):
    root=tmp_path_factory.mktemp('legacy-core');root.chmod(0o755)
    source=root/'source';source.mkdir();source.chmod(0o755)
    raw=subprocess.check_output(['git','archive',deps.EQUIVALENT[0]])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:archive.extractall(source,filter='data')
    (source/'runtime/data').mkdir(parents=True)
    data=root/'data';data.mkdir();data.chmod(0o777)
    manifest=json.loads(Path(deps.MANIFEST).read_text())
    expected={p['name']:{w['filename']:deps.expected_files(deps.download(w),w) for w in p['wheels']} for p in manifest['packages']}
    roots=sorted({n.split('/')[0] for candidates in expected.values() for files in candidates.values() for n in files})
    result=docker(source,data,deps.PROBE,json.dumps({'roots':roots}))
    assert result.returncode==0,result.stderr
    observed=deps.validate_observed(result.stdout,roots)['files'];rows=deps.compare(expected,observed)
    assert all(r['status']=='PASS' for r in rows),rows
    verified={LIB+'/'+p:h for row in rows for p,h in expected[row['package']][row['matchedWheels'][0]].items()}
    return root,source,verified


@pytest.mark.parametrize('attack,expected',[
    ('none','GAMES_AUDIT_COLLECTED'),('write','READ_ONLY_WRITE_REJECTED'),
    ('schema','DATABASE_ACCESS_REJECTED'),('skill','UNVERIFIED_IMPORT'),
    ('hash','IMPORT_HASH_REJECTED'),('gateway','GATEWAY_REJECTED'),('post','IMPORT_NETWORK_REJECTED'),
])
def test_guarded_legacy_observer_preserves_fixture_database(core,tmp_path,attack,expected):
    import shutil
    _,original,core_files=core
    tmp_path.chmod(0o755)
    source=tmp_path/'source';shutil.copytree(original,source);source.chmod(0o755)
    data=tmp_path/'data';data.mkdir();data.chmod(0o777)
    init="import sys;sys.path[:0]=['/app',"+repr(LIB)+"];import dotenv;dotenv.load_dotenv=lambda *a,**k:False\nfrom database import db\ndb.init_db()\nimport os,json\ns=os.stat('/app/runtime/data/fixture.db');print(json.dumps({'device':s.st_dev,'inode':s.st_ino}))"
    initialized=docker(source,data,init);assert initialized.returncode==0,initialized.stderr
    identity=json.loads(initialized.stdout)
    before=hashlib.sha256((data/'fixture.db').read_bytes()).hexdigest()
    verified=dict(core_files)
    for path in source.rglob('*'):
        if path.is_file() and provenance.selected(path.relative_to(source).as_posix()):
            verified['/app/'+path.relative_to(source).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    target=source/'services/authorization_service.py'
    attacks={'write':"\nopen('/app/runtime/data/fixture.db','wb')\n",
        'schema':'\nfrom database import db\ndb.init_db()\n',
        'skill':'\nimport gamerhq_skill_event_manager\n',
        'gateway':'\nimport asyncio,discord\nasyncio.run(discord.Client(intents=discord.Intents.none()).start("synthetic"))\n',
        'post':'\nimport urllib.request\nurllib.request.urlopen(urllib.request.Request("https://discord.com/api/v10/channels/1/messages",data=b"{}",method="POST"))\n',
        'hash':'\nUNREVIEWED_CHANGE=True\n'}
    if attack!='none':
        target.write_text(target.read_text()+attacks[attack])
        if attack!='hash':verified['/app/services/authorization_service.py']=hashlib.sha256(target.read_bytes()).hexdigest()
    bind={'environment':'production','guildId':1,'applicationId':100,'database':'/app/runtime/data/fixture.db',
        'runtimeUid':10001,'runtimeGid':10001,'databaseDevice':identity['device'],'databaseInode':identity['inode']}
    normalizer='import unicodedata\n'+inspect.getsource(name_key)
    sources={p:Path(p).read_text() for p in execution.FILES}
    packet={'binding':bind,'ownerApprovalSha256':hashlib.sha256(json.dumps(bind,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'observerCommit':'a'*40,'verifiedFiles':verified,'normalizer':normalizer,
        'normalizerSha256':hashlib.sha256(normalizer.encode()).hexdigest(),'sources':sources,
        'hashes':{p:hashlib.sha256(v.encode()).hexdigest() for p,v in sources.items()}}
    # Models/real verified modules stay real; only REST responses are mocked.
    runtime_source=Path('tools/game_audit_legacy_runtime.py').read_text()
    fixture=r'''
import types,json,sys
m=types.ModuleType('fixture_runtime');exec(RUNTIME_SOURCE,m.__dict__)
real_install=m.install_http
def install(guard):
 real_install(guard)
 import aiohttp
 def user(n,bot=False):return {'id':str(n),'username':'fixture','bot':bot,'discriminator':'0','avatar':None}
 responses={'/users/@me':user(100,True),'/guilds/1':{'id':'1','name':'fixture','owner_id':'50','features':[],'verification_level':0,'default_message_notifications':0,'explicit_content_filter':0,'afk_timeout':60},
 '/guilds/1/roles':[{'id':'1','name':'@everyone','position':0,'permissions':'0','color':0}],'/guilds/1/channels':[],
 '/guilds/1/members/100':{'user':user(100,True),'roles':[],'flags':0},'/guilds/1/members/50':{'user':user(50),'roles':[],'flags':0}}
 class Response:
  def __init__(self,value):self.body=json.dumps(value).encode();self.status=200;self.content=self
  async def read(self,n):value=self.body[:n];self.body=self.body[n:];return value
  async def __aenter__(self):return self
  async def __aexit__(self,*a):pass
 class Session:
  def __init__(self,**kw):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*a):pass
  def get(self,url,**kw):
   guard.http('GET',url,kw)
   return Response(responses[url.removeprefix('https://discord.com/api/v10')])
 aiohttp.ClientSession=Session
m.install_http=install
try:result=m.collect(json.load(sys.stdin));print(json.dumps(result))
except Exception as error:
 print(json.dumps({'state':'GAMES_AUDIT_UNAVAILABLE','reason':str(error) if isinstance(error,m.Rejected) else 'FIXTURE_UNAVAILABLE','type':type(error).__name__}))
'''.replace('RUNTIME_SOURCE',repr(runtime_source))
    result=docker(source,data,fixture,json.dumps(packet))
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value.get('state')==expected or value.get('reason')==expected,(value,result.stderr)
    assert hashlib.sha256((data/'fixture.db').read_bytes()).hexdigest()==before
    if attack=='none':
        assert value['discordReads']==6 and value['hostCommit'] is None
        assert value['buildOriginVerified'] is False and value['databaseChanged'] is False
