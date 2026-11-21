"""One owner-confirmed Production Games read. No normal revision bypass.

Preparation/imports alone cannot execute collection. Exact binding approval is
read from the owner's real private TTY, never stdin, an env var or a CLI token.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import types
import uuid
import io
import zipfile
from contextlib import contextmanager

REPOSITORY='https://github.com/Duy-Phan96/GamerHQ.git'
SELF='tools/game_audit_legacy_collect.py'
STAGE='OWNER_CHECK'
COLLECTION_ATTEMPTED=False
PHRASE='COLLECT LEGACY GAMES READ ONLY '
DENIED_GUILDS={1567098041641738331,1571908030532948129}
DENIED_APP=1567098530525351976

METADATA=r'''
import json,os,stat
from pathlib import Path
guild=os.environ.get('GUILD_ID','');raw=os.environ.get('GAMERHQ_DB_PATH','')
if not guild.isdecimal() or int(guild)<=0 or not raw or not Path(raw).is_absolute():raise ValueError('PRODUCTION_BINDING_REJECTED')
p=Path(raw)
if p.is_symlink() or str(p.resolve(strict=True))!=raw:raise ValueError('PRODUCTION_BINDING_REJECTED')
s=p.stat()
if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_gid!=os.getegid() or not os.access(p,os.R_OK):raise ValueError('PRODUCTION_BINDING_REJECTED')
print(json.dumps({'guildId':int(guild),'database':raw,'databaseDevice':s.st_dev,'databaseInode':s.st_ino,'databaseMode':stat.S_IMODE(s.st_mode),
 'runtimeUid':os.geteuid(),'runtimeGid':os.getegid()}))
'''

IDENTITY=r'''
import json,os,urllib.request
token=os.environ.get('DISCORD_TOKEN') or os.environ.get('BOT_TOKEN')
if not token:raise ValueError('CREDENTIAL_UNAVAILABLE')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**kw):return None
guild=os.environ.get('GUILD_ID','')
if not guild.isdecimal() or int(guild) in {1567098041641738331,1571908030532948129}:raise ValueError('PRODUCTION_BINDING_REJECTED')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def get(path):
 request=urllib.request.Request('https://discord.com/api/v10'+path,headers={'Authorization':'Bot '+token,'User-Agent':'GamerHQ-readonly-identity'},method='GET')
 with opener.open(request,timeout=15) as reply:
  if reply.status!=200:raise ValueError('BOT_IDENTITY_REJECTED')
  raw=reply.read(65537)
  if len(raw)>65536:raise ValueError('BOT_IDENTITY_REJECTED')
  return json.loads(raw)
value=get('/users/@me')
if value.get('bot') is not True or not str(value.get('id','')).isdecimal():raise ValueError('BOT_IDENTITY_REJECTED')
if int(value['id'])==1567098530525351976:raise ValueError('PRODUCTION_BINDING_REJECTED')
server=get('/guilds/'+guild)
if server.get('id')!=guild or not isinstance(server.get('name'),str):raise ValueError('PRODUCTION_BINDING_REJECTED')
print(json.dumps({'applicationId':int(value['id']),'guildId':int(guild),'guildName':server['name']}))
'''


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def private_directory():
    import pwd
    path=Path(pwd.getpwuid(0).pw_dir)
    info=path.stat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o077:
        raise ValueError('PRIVATE_RECEIPT_DIRECTORY_REQUIRED')
    return path


@contextmanager
def owner_lock(directory):
    import fcntl
    fd=os.open(directory/'.gamerhq-games-readonly.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            raise ValueError('OWNER_LOCK_REJECTED')
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def confirm(tty,binding):
    if not os.isatty(tty.fileno()):raise ValueError('OWNER_TTY_REQUIRED')
    expected=PHRASE+digest(binding)
    tty.write('\nPRIVATE Production binding — do not post this preview publicly:\n'+json.dumps(binding,indent=2,sort_keys=True))
    tty.write('\nCore/source verification passed. Confirm only if these are your intended Production resources.\n')
    tty.write('This performs only Games SQLite reads and bounded Discord GETs; original build provenance remains unverified.\n')
    tty.write('The existing Python/stdlib, OS and Docker remain the operational trust base; whole-image integrity is not attested.\n')
    tty.write('Type exactly: '+expected+'\n> ');tty.flush()
    if tty.readline(256).strip()!=expected:raise ValueError('OWNER_CANCELLED')
    return digest(binding)


def metadata(meta):
    raw=subprocess.run(['docker','exec','--user',meta['user'],meta['id'],'python','-I','-S','-B','-c',METADATA],
        capture_output=True,text=True,check=True,timeout=20).stdout
    if len(raw)>4096:raise ValueError('PRODUCTION_BINDING_REJECTED')
    value=json.loads(raw)
    if (set(value)!={'guildId','database','databaseDevice','databaseInode','databaseMode','runtimeUid','runtimeGid'}
            or any(type(value[k]) is not int or value[k]<=0 for k in ('guildId','runtimeUid','runtimeGid'))
            or value['guildId'] in DENIED_GUILDS):raise ValueError('PRODUCTION_BINDING_REJECTED')
    return value


def supplement(deps, manifest):
    entries=manifest.get('observerSupplements',[])
    if len(entries)!=1 or entries[0]['module']!='typing_extensions' or entries[0]['version']!='4.15.0':
        raise ValueError('OBSERVER_SOURCE_REJECTED')
    item=entries[0];body=deps.download(item['wheel']);files=deps.expected_files(body,item['wheel'])
    with zipfile.ZipFile(io.BytesIO(body)) as archive:code=archive.read('typing_extensions.py')
    if hashlib.sha256(code).hexdigest()!=item['moduleSha256'] or files['typing_extensions.py']!=item['moduleSha256']:
        raise ValueError('OBSERVER_SOURCE_REJECTED')
    return {'typing_extensions':{'source':code.decode(),'sha256':item['moduleSha256'],
                                 'wheel':item['wheel']['filename'],'wheelSha256':item['wheel']['sha256']}}


def packet(git,source,context,provenance,binding,approval,supplements):
    execution=types.ModuleType('verified_execution')
    exec(compile(git('show',source+':tools/game_audit_execution.py'),'<verified-execution>','exec'),execution.__dict__)
    sources={p:git('show',source+':'+p).decode() for p in execution.FILES}
    normalizer=git('show',source+':tools/game_catalog_import.py').decode()
    functions=[node for node in ast.parse(normalizer).body if isinstance(node,ast.FunctionDef) and node.name=='name_key']
    if len(functions)!=1:raise ValueError('OBSERVER_SOURCE_REJECTED')
    normalizer='import unicodedata\n'+ast.get_source_segment(normalizer,functions[0])+'\n'
    verified={}
    for line in git('ls-tree','-rz','--full-tree',provenance['sourceEquivalentCommits'][0]).split(b'\0'):
        if not line:continue
        head,path=line.split(b'\t',1);name=path.decode()
        if context['provenanceModule'].selected(name):
            verified['/app/'+name]=hashlib.sha256(git('cat-file','blob',head.decode().split()[2])).hexdigest()
    for path,h in context['dependencyFiles'].items():verified['/usr/local/lib/python3.12/site-packages/'+path]=h
    value={'binding':binding,'ownerApprovalSha256':approval,'observerCommit':source,
           'sources':sources,'hashes':{p:hashlib.sha256(v.encode()).hexdigest() for p,v in sources.items()},
           'verifiedFiles':verified,'normalizer':normalizer,'normalizerSha256':hashlib.sha256(normalizer.encode()).hexdigest(),
           'supplements':supplements}
    if len(canonical(value))>2*1024**2:raise ValueError('OBSERVER_BUDGET_EXCEEDED')
    return value


def receipt(directory, value):
    identifier=uuid.uuid4().hex
    target=directory/('games-production-'+identifier+'.private-games.json')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=True,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    return identifier,target


def main():
    global STAGE,COLLECTION_ATTEMPTED
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-commit',required=True)
    args=parser.parse_args()
    if not hasattr(os,'geteuid') or os.geteuid()!=0 or not re.fullmatch('[0-9a-f]{40}',args.source_commit):raise ValueError('OWNER_SOURCE_REQUIRED')
    # Refuse unattended/piped execution before any Docker or Discord read.
    with open('/dev/tty','r',encoding='utf-8') as reader, open('/dev/tty','w',encoding='utf-8') as writer:
        tty=types.SimpleNamespace(fileno=reader.fileno,write=writer.write,flush=writer.flush,readline=reader.readline)
        if not os.isatty(tty.fileno()):raise ValueError('OWNER_TTY_REQUIRED')
        output_directory=private_directory()
        with owner_lock(output_directory), tempfile.TemporaryDirectory(prefix='gamerhq-legacy-games-') as scratch:
            env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0'}
            def git(*a):return subprocess.run(['git','-C',scratch,*a],env=env,capture_output=True,check=True,timeout=120).stdout
            STAGE='SOURCE_VERIFICATION';git('init','--quiet');git('fetch','--quiet','--depth=1',REPOSITORY,args.source_commit)
            if git('rev-parse','FETCH_HEAD').strip().decode()!=args.source_commit or git('show',args.source_commit+':'+SELF)!=Path(__file__).read_bytes():raise ValueError('SOURCE_REJECTED')
            def module(path,name):
                m=types.ModuleType(name);exec(compile(git('show',args.source_commit+':'+path),'<verified-helper>','exec'),m.__dict__);return m
            operator=module('tools/game_audit_operator.py','verified_inspector')
            provenance=module('tools/game_audit_provenance.py','verified_provenance')
            deps=module('tools/game_audit_legacy_dependencies.py','verified_dependencies')
            manifest=json.loads(git('show',args.source_commit+':'+deps.MANIFEST))
            for revision in deps.EQUIVALENT:
                git('fetch','--quiet','--depth=1',REPOSITORY,revision)
                if git('rev-parse','FETCH_HEAD').strip().decode()!=revision:raise ValueError('SOURCE_REJECTED')
                deps.validate_manifest(manifest,git('show',revision+':requirements.lock'))
            STAGE='FRESH_SOURCE_AND_CORE_INTEGRITY'
            context=deps.verified_context(git,manifest,operator,provenance)
            if not context['public']['lockedCoreDependenciesMatch']:raise ValueError('CORE_INTEGRITY_REJECTED')
            evidence=context['public']['sourceEvidence'];meta=context['container']
            supplements=supplement(deps,manifest)
            if meta['hostCommit'] is not None or evidence['buildMarkerState']=='VALID_HEX':raise ValueError('NORMAL_ATTESTED_ROUTE_REQUIRED')
            STAGE='PRIVATE_BINDING'
            config=metadata(meta);operator.database_mount(meta,config['database'],'production')
            identity=subprocess.run(['docker','exec','--user',meta['user'],meta['id'],'python','-I','-S','-B','-c',IDENTITY],capture_output=True,text=True,check=True,timeout=35).stdout
            if len(identity)>2048:raise ValueError('BOT_IDENTITY_REJECTED')
            identity=json.loads(identity);app=identity['applicationId']
            if type(app) is not int or app<=0 or app==DENIED_APP:raise ValueError('BOT_IDENTITY_REJECTED')
            if identity.get('guildId')!=config['guildId'] or not isinstance(identity.get('guildName'),str):raise ValueError('PRODUCTION_BINDING_REJECTED')
            binding={**config,'environment':'production','applicationId':app,'guildName':identity['guildName'],'containerId':meta['id'],'imageId':meta['image'],
                     'sourceManifestSha256':evidence['sourceManifestSha256'],
                     'coreFilesSha256':digest(context['dependencyFiles']),
                     'observerSupplementsSha256':digest({k:v['wheelSha256'] for k,v in supplements.items()}),
                     'approvalNonce':uuid.uuid4().hex,
                     'observerCommit':args.source_commit}
            STAGE='OWNER_CONFIRMATION';approval=confirm(tty,binding)
            if operator.inspect('gamerhq-bot')!=meta or metadata(meta)!=config:raise ValueError('BINDING_CHANGED')
            context['provenanceModule']=provenance
            sealed=packet(git,args.source_commit,context,evidence,binding,approval,supplements)
            runtime=git('show',args.source_commit+':tools/game_audit_legacy_runtime.py').decode()
            STAGE='READ_ONLY_GAMES_COLLECTION'
            COLLECTION_ATTEMPTED=True
            raw=subprocess.run(['docker','exec','--interactive','--user',str(config['runtimeUid'])+':'+str(config['runtimeGid']),
                meta['id'],'python','-I','-S','-B','-c',runtime],input=json.dumps(sealed),capture_output=True,text=True,check=True,timeout=150).stdout
            if len(raw.encode())>2*1024**2:raise ValueError('OBSERVER_RESPONSE_REJECTED')
            result=json.loads(raw)
            if (result.get('state') not in {'GAMES_AUDIT_COLLECTED','GAMES_AUDIT_UNAVAILABLE'}
                    or any(result.get(k) is not False for k in ('databaseChanged','discordResourcesChanged','gatewayRestarted'))):
                raise ValueError('OBSERVER_RESPONSE_REJECTED')
            if result['state']=='GAMES_AUDIT_COLLECTED' and any(result.get(k)!=binding[k] for k in ('environment','guildId','applicationId')):
                raise ValueError('OBSERVER_RESPONSE_REJECTED')
            if operator.inspect('gamerhq-bot')!=meta or metadata(meta)!=config:raise ValueError('BINDING_CHANGED')
            # Refresh independent byte evidence after execution too; never accept
            # a stale receipt or a concurrent source/package replacement.
            STAGE='POST_COLLECTION_INTEGRITY'
            final=deps.verified_context(git,manifest,operator,provenance)
            if final['public']!=context['public'] or final['container']!=meta:raise ValueError('BINDING_CHANGED')
            result.update(privateBinding=binding,ownerApprovalSha256=approval,sourceEquivalentCommits=deps.EQUIVALENT,
                          coreIntegrity=context['public'],identityPrecheckReads=2,runtimeSha256=hashlib.sha256(runtime.encode()).hexdigest())
            STAGE='PRIVATE_RECEIPT';identifier,target=receipt(output_directory,result)
            tty.write('\nPrivate receipt: '+str(target)+'\n');tty.flush()
            if result['state']=='GAMES_AUDIT_UNAVAILABLE':
                safe={'UNVERIFIED_IMPORT','IMPORT_HASH_REJECTED','GATEWAY_REJECTED','MUTATION_REJECTED',
                      'DATABASE_ACCESS_REJECTED','READ_ONLY_WRITE_REJECTED','IMPORT_NETWORK_REJECTED',
                      'DATABASE_QUERY_SCOPE_REJECTED',
                      'DISCORD_REQUEST_REJECTED','IMPORT_THREAD_REJECTED','NETWORK_TARGET_REJECTED',
                      'OBSERVER_BUDGET_EXCEEDED','LEGACY_OBSERVER_UNAVAILABLE','ISOLATED_RUNTIME_REQUIRED'}
                reason=result.get('reason') if result.get('reason') in safe else 'LEGACY_COLLECTION_UNVERIFIED'
                print(json.dumps({'state':'GAMES_LEGACY_COLLECTION_UNAVAILABLE','status':'UNVERIFIED',
                                  'reason':reason,'privateReceiptId':identifier,'collectionAttempted':True,
                                  'hostRevisionVerified':False,'buildOriginVerified':False}))
                raise SystemExit(1)
            counts=result.get('counts',{})
            if set(counts)-{'READY','NOT_SET_UP','HIDDEN','SETUP_NEEDED','REVIEW','UNVERIFIED'} or any(type(v) is not int or v<0 for v in counts.values()):raise ValueError('OBSERVER_RESPONSE_REJECTED')
            print(json.dumps({'state':'GAMES_LEGACY_COLLECTION_COMPLETE','privateReceiptId':identifier,'counts':counts,
                'hostRevisionVerified':False,'buildOriginVerified':False,'collectionExecuted':True,
                'databaseChanged':False,'discordResourcesChanged':False,'productionChanged':False}))


if __name__=='__main__':
    sys.dont_write_bytecode=True
    try:main()
    except Exception as error:
        allowed={'OWNER_SOURCE_REQUIRED','OWNER_TTY_REQUIRED','OWNER_CANCELLED','PRIVATE_RECEIPT_DIRECTORY_REQUIRED',
            'OWNER_LOCK_REJECTED',
            'SOURCE_REJECTED','CORE_INTEGRITY_REJECTED','NORMAL_ATTESTED_ROUTE_REQUIRED','PRODUCTION_BINDING_REJECTED',
            'BOT_IDENTITY_REJECTED','OBSERVER_SOURCE_REJECTED','OBSERVER_BUDGET_EXCEEDED','BINDING_CHANGED',
            'OBSERVER_RESPONSE_REJECTED','LEGACY_COLLECTION_UNVERIFIED'}
        reason=error.args[0] if error.args and isinstance(error.args[0],str) and error.args[0] in allowed else 'LEGACY_COLLECTION_ABORTED'
        raise SystemExit(json.dumps({'state':'GAMES_LEGACY_COLLECTION_UNAVAILABLE','stage':STAGE,'reason':reason,
                                    'collectionAttempted':COLLECTION_ATTEMPTED,
                                    'hostRevisionVerified':False,'buildOriginVerified':False})) from None
