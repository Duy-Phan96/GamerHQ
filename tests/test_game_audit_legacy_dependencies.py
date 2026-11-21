"""Eligibility evidence cannot become a revision bypass or collection permission."""
import copy
import hashlib
import io
import json
import subprocess
import sys
import os
import tarfile
import inspect
import zipfile
from pathlib import Path

import pytest
from tools import game_audit_legacy_dependencies as audit


def wheel(files):
    body=io.BytesIO()
    with zipfile.ZipFile(body,'w') as z:
        for name,data in files.items():
            entry=zipfile.ZipInfo('fixture')
            entry.filename=name  # Preserve malicious raw separators on Windows too.
            z.writestr(entry,data)
    value=body.getvalue()
    return value,{'size':len(value),'sha256':hashlib.sha256(value).hexdigest()}


def test_independent_wheel_bytes_are_proof_not_installed_record():
    body,pin=wheel({'example/__init__.py':b'known source','example-1.dist-info/METADATA':b'Name: example',
                    'example-1.dist-info/RECORD':b'not trusted as integrity proof'})
    expected=audit.expected_files(body,pin)
    assert not any(p.endswith('/RECORD') for p in expected)
    assert audit.compare({'example':{'reviewed.whl':expected}},expected)[0]['status']=='PASS'
    changed=dict(expected);changed['example/__init__.py']='f'*64
    assert audit.compare({'example':{'reviewed.whl':expected}},changed)[0]['status']=='UNVERIFIED'


@pytest.mark.parametrize('change',['extra','missing','binary'])
def test_extra_missing_or_modified_package_files_abort_eligibility(change):
    expected={'example/__init__.py':'a'*64,'example/native.so':'b'*64}
    observed=dict(expected)
    if change=='extra': observed['example/unreviewed.py']='c'*64
    elif change=='missing': del observed['example/__init__.py']
    else: observed['example/native.so']='c'*64
    rows=audit.compare({'example':{'pinned.whl':expected}},observed)
    assert rows[0]['status']=='UNVERIFIED'
    assert audit.summary(rows,{})['legacyCollectionEligible'] is False


def test_tampered_download_cannot_be_accepted_as_a_wheel():
    body,pin=wheel({'example-1.dist-info/METADATA':b'known'})
    with pytest.raises(ValueError,match='WHEEL_CHECKSUM_REJECTED'):audit.expected_files(body+b'changed',pin)


@pytest.mark.parametrize('path',['../private.db','/absolute.py','example/../../private','example.data/scripts/run','example\\secret'])
def test_archive_paths_cannot_escape_into_private_files(path):
    body,pin=wheel({'example-1.dist-info/METADATA':b'known',path:b'never executed'})
    with pytest.raises(ValueError,match='WHEEL_LAYOUT_REJECTED'):audit.expected_files(body,pin)


def test_manifest_is_exactly_bound_to_equivalent_sources_and_all_core_versions():
    manifest=json.loads(Path(audit.MANIFEST).read_text())
    lock=subprocess.check_output(['git','show',audit.EQUIVALENT[0]+':requirements.lock'])
    audit.validate_manifest(manifest,lock)
    changed=copy.deepcopy(manifest);changed['packages'].pop()
    with pytest.raises(ValueError,match='LOCK_REJECTED'):audit.validate_manifest(changed,lock)
    changed=copy.deepcopy(manifest);changed['packages'][0]['version']='unreviewed'
    with pytest.raises(ValueError,match='LOCK_REJECTED'):audit.validate_manifest(changed,lock)


def test_successful_dependency_evidence_never_authorizes_games_or_claims_provenance():
    result=audit.summary([{'package':'example','status':'PASS','matchedWheels':['reviewed.whl']}],{})
    assert result['lockedCoreDependenciesMatch'] is True
    for name in ('buildOriginVerified','hostRevisionVerified','legacyCollectionEligible',
                 'wholeImageIntegrityVerified','collectionExecuted','databaseAccessed','discordAccessed'):
        assert result[name] is False
    assert audit.summary([],{})['lockedCoreDependenciesMatch'] is False


@pytest.mark.parametrize('path',['../gamerhq.db','/app/config.py','example/../../.env','foreign/a.py'])
def test_container_probe_response_cannot_expand_verified_scope(path):
    packet={'runtimeUid':10001,'runtimeGid':10001,'files':{path:'a'*64}}
    with pytest.raises(ValueError,match='PROBE_REJECTED'):audit.validate_observed(json.dumps(packet),['example'])


def test_no_live_collection_or_arbitrary_package_path_switch_exists(monkeypatch):
    monkeypatch.setattr(sys,'argv',['check','--source-commit','a'*40,'--collect'])
    with pytest.raises(SystemExit) as error:audit.main()
    assert error.value.code==2
    assert 'import discord' not in audit.PROBE and 'import dotenv' not in audit.PROBE
    assert 'sqlite' not in audit.PROBE and 'os.environ' not in audit.PROBE
    assert 'RECORD' in audit.PROBE  # explicitly excluded, never used as proof


@pytest.mark.skipif(os.name!='posix' or sys.version_info[:2]!=(3,12),reason='Exact Linux Python 3.12 dependency probe fixture')
@pytest.mark.parametrize('symlink',[False,True])
def test_isolated_package_hash_probe_never_imports_site_or_reads_database(tmp_path,symlink):
    package=tmp_path/'example';package.mkdir()
    metadata=tmp_path/'example-1.dist-info';metadata.mkdir()
    (metadata/'RECORD').write_text('forged installed checksum: not evidence')
    (metadata/'METADATA').write_text('Name: example')
    (tmp_path/'private.db').write_bytes(b'never read')
    (tmp_path/'evil.pth').write_text('import nonexistent_unreviewed_module')
    if symlink:(package/'module.py').symlink_to(tmp_path/'private.db')
    else:(package/'module.py').write_bytes(b'raise RuntimeError("must never import")')
    # Only the source path is redirected to the disposable fixture. Version,
    # non-root, no-follow, hashing, read limits and -I/-S execution stay real.
    prefix='import sysconfig\nsysconfig.get_path=lambda name:'+repr(str(tmp_path))+'\n'
    body=audit.PROBE.replace("'/usr/local/lib/python3.12/site-packages'",repr(str(tmp_path)))
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',prefix+body],
        input=json.dumps({'roots':['example','example-1.dist-info']}),capture_output=True,text=True,timeout=10)
    if symlink:
        assert result.returncode!=0 and not result.stdout
    else:
        assert result.returncode==0,result.stderr
        observed=audit.validate_observed(result.stdout,['example','example-1.dist-info'])
        assert set(observed['files'])=={'example/module.py','example-1.dist-info/METADATA'}
    assert 'never read' not in result.stdout and 'forged' not in result.stdout


def test_exact_legacy_generation_has_a_separate_observer_capability_gap():
    # This is not permission to patch a live Host. It records why the current
    # normal observer must still refuse this source generation after dependency PASS.
    for commit in audit.EQUIVALENT:
        files=subprocess.check_output(['git','ls-tree','-r','--name-only',commit]).decode().splitlines()
        assert 'services/game_readiness_service.py' in files
        assert 'tools/game_catalog_import.py' not in files
    operator=Path('tools/game_audit_operator.py').read_text()
    assert "raise ValueError('HOST_REVISION_UNAVAILABLE')" in operator
    assert "'tools/game_catalog_import.py'} <= set(baseline)" in operator


def test_exact_legacy_source_cold_observer_rejects_before_any_collection(tmp_path):
    from tools import game_audit_execution as execution
    raw=subprocess.check_output(['git','archive',audit.EQUIVALENT[0]])
    source=tmp_path/'legacy-source';source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(source,filter='data')
    sources={p:Path(p).read_text() for p in execution.FILES}
    hashes={p:hashlib.sha256(body.encode()).hexdigest() for p,body in sources.items()}
    db=tmp_path/'must-not-create.db'
    packet=execution.packet(sources,hashes,{'environment':'production','guildId':1,
        'applicationId':100,'database':str(db)},source_commit='a'*40)
    prefix='import sys\nsys.path.insert(0,'+repr(str(source))+')\n'
    result=subprocess.run([sys.executable,'-I','-B','-c',prefix+execution.LAUNCH],
        input=json.dumps(packet),capture_output=True,text=True,timeout=30,
        env={**os.environ,'GUILD_ID':'1','GAMERHQ_DB_PATH':str(db),'DISCORD_TOKEN':'synthetic-no-network-token'})
    assert result.returncode!=0
    assert 'ModuleNotFoundError' in result.stderr and 'tools.game_catalog_import' in result.stderr
    assert 'synthetic-no-network-token' not in result.stderr
    assert not result.stdout and not db.exists()


def test_observer_only_normalizer_adapter_can_rehearse_legacy_reads_offline(tmp_path):
    from tools import game_audit_execution as execution
    from tools.game_catalog_import import name_key
    raw=subprocess.check_output(['git','archive',audit.EQUIVALENT[0]])
    source=tmp_path/'legacy';source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:archive.extractall(source,filter='data')
    sources={p:Path(p).read_text() for p in execution.FILES}
    packet=execution.packet(sources,{p:hashlib.sha256(v.encode()).hexdigest() for p,v in sources.items()},
        {'environment':'production','guildId':1,'applicationId':100,'database':str(tmp_path/'fixture.db')},source_commit='a'*40)
    def user(n,bot=False):return {'id':str(n),'username':'fixture','bot':bot,'discriminator':'0','avatar':None}
    packet['fixtureResponses']={'/users/@me':user(100,True),'/guilds/1':{'id':'1','name':'fixture','owner_id':'50','features':[],
        'verification_level':0,'default_message_notifications':0,'explicit_content_filter':0,'afk_timeout':60},
        '/guilds/1/roles':[{'id':'1','name':'@everyone','position':0,'permissions':'0','color':0}],
        '/guilds/1/channels':[], '/guilds/1/members/100':{'user':user(100,True),'roles':[],'flags':0},
        '/guilds/1/members/50':{'user':user(50),'roles':[],'flags':0}}
    prefix='import sys\nsys.path.insert(0,'+repr(str(source))+')\n'+r'''
import io,json,types,unicodedata,hashlib
import dotenv
dotenv.load_dotenv=lambda *a,**kw:False
from database import db
db.init_db()  # Disposable fixture only, never part of the observer.
before=hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
import tools
normalizer=types.ModuleType('tools.game_catalog_import')
normalizer.__dict__['unicodedata']=unicodedata
exec(NORMALIZER_SOURCE,normalizer.__dict__)
sys.modules[normalizer.__name__]=normalizer
tools.game_catalog_import=normalizer
import aiohttp
data=json.load(sys.stdin);responses=data.pop('fixtureResponses')
class Response:
 def __init__(self,body):self.body=json.dumps(body).encode();self.status=200;self.content=self
 async def read(self,n):chunk=self.body[:n];self.body=self.body[n:];return chunk
 async def __aenter__(self):return self
 async def __aexit__(self,*a):pass
class Session:
 def __init__(self,**kw):pass
 async def __aenter__(self):return self
 async def __aexit__(self,*a):pass
 def get(self,url,**kw):
  assert url.startswith('https://discord.com/api/v10/') and kw['allow_redirects'] is False
  return Response(responses[url.removeprefix('https://discord.com/api/v10')])
aiohttp.ClientSession=Session
sys.stdin=io.StringIO(json.dumps(data))
'''.replace('NORMALIZER_SOURCE',repr(inspect.getsource(name_key)))
    result=subprocess.run([sys.executable,'-I','-B','-c',prefix+execution.LAUNCH+
        '\nassert hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()==before\n'
        'assert not any(n.startswith("gamerhq_skill_") for n in sys.modules)\n'],input=json.dumps(packet),
        capture_output=True,text=True,timeout=30,env={**os.environ,'GUILD_ID':'1',
        'GAMERHQ_DB_PATH':str(tmp_path/'fixture.db'),'DISCORD_TOKEN':'synthetic-no-network-token'})
    assert result.returncode==0,result.stderr
    value=execution.output(result.stdout)
    assert value['state']=='GAMES_AUDIT_COLLECTED',value
    assert value['environment']=='production' and value['discordReads']==6
    assert value['databaseChanged'] is False
