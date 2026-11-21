"""Fixed ephemeral legacy observer. Loaded only by the owner-confirmed launcher.

Existing Python/stdlib/kernel and authentic pinned native dependencies are the
operational trust base. This is not an adversarial native-code sandbox.
"""
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import sysconfig
import threading
import types
from contextlib import contextmanager
from urllib.parse import urlsplit

LIB = '/usr/local/lib/python3.12/site-packages'
DENIED_GUILDS = {1567098041641738331, 1571908030532948129}
DENIED_APP = 1567098530525351976
TYPING_SOURCE_SHA='433d11d170d3a24d2eb065ebc1bfe848cea7e3d7ce68567ab52bea2d4c2f7ed8'
TYPING_WHEEL_SHA='f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548'
FILES = ('tools/game_audit_execution.py','services/game_audit_context.py','services/game_launch_audit.py',
         'tools/game_launch_audit_collect.py','tools/game_launch_audit_report.py')


class Rejected(RuntimeError):
    def __init__(self, reason, module=None):
        super().__init__(reason)
        self.module=module if isinstance(module,str) and len(module)<=200 and all(c.isalnum() or c in '._' for c in module) else None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def approve_hash(binding):
    return hashlib.sha256(canonical(binding)).hexdigest()


class Guard:
    def __init__(self, packet):
        self.packet=packet;self.binding=packet['binding'];self.denied=[]
        self.database_phase=False;self.transport=None
        self.importing=True
        self.files=packet['verifiedFiles']
        self.imports={}
        self.uri=Path(self.binding['database']).as_uri()+'?mode=ro'

    def reject(self, reason, module=None):
        self.denied.append(reason)
        raise Rejected(reason,module)

    def audit(self, event, args):
        if event=='open':
            mode=args[1] or '';flags=args[2] or 0
            if any(c in str(mode) for c in 'wax+') or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
                self.reject('READ_ONLY_WRITE_REJECTED')
        if event=='sqlite3.connect' and (self.importing or not self.database_phase or args[0] not in (':memory:',self.uri)):
            self.reject('DATABASE_ACCESS_REJECTED')
        if event.startswith(('subprocess.','os.exec','os.spawn','os.fork')) or event in {
            'os.system','os.remove','os.rename','os.rmdir','os.mkdir','os.chmod','os.chown','os.utime',
            'os.link','os.symlink','os.truncate','socket.bind','socket.sendto'}:
            self.reject('MUTATION_REJECTED')
        if event in {'socket.connect','socket.getaddrinfo'}:
            if self.importing or self.transport is None:self.reject('IMPORT_NETWORK_REJECTED')
            if event=='socket.connect' and (not isinstance(args[1],tuple) or len(args[1])<2 or args[1][1]!=443):
                self.reject('NETWORK_TARGET_REJECTED')
            if event=='socket.getaddrinfo' and (args[0]!='discord.com' or args[1]!=443):
                self.reject('NETWORK_TARGET_REJECTED')

    def bytes(self, path, module=None):
        resolved=str(Path(path).resolve(strict=True))
        if resolved!=path or path not in self.files:self.reject('UNVERIFIED_IMPORT',module)
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
            body=stream.read(32*1024**2+1)
        if hashlib.sha256(body).hexdigest()!=self.files[path]:self.reject('IMPORT_HASH_REJECTED',module)
        return body

    def http(self, method, url, options):
        rest=self.transport
        if self.importing or rest is None:self.reject('IMPORT_NETWORK_REJECTED')
        gid=self.binding['guildId'];app=self.binding['applicationId']
        paths={'/users/@me',f'/guilds/{gid}',f'/guilds/{gid}/roles',f'/guilds/{gid}/channels',f'/guilds/{gid}/members/{app}'}
        if rest.owner:paths.add(f'/guilds/{gid}/members/{rest.owner}')
        parsed=urlsplit(str(url))
        if (method!='GET' or parsed.scheme!='https' or parsed.netloc!='discord.com'
                or parsed.query or parsed.fragment or parsed.path not in {'/api/v10'+p for p in paths}
                or options.get('allow_redirects') is not False
                or any(options.get(k) is not None for k in ('data','json','params','proxy'))):
            self.reject('DISCORD_REQUEST_REJECTED')


class VerifiedSource(importlib.machinery.SourceFileLoader):
    def __init__(self, name, path, guard):
        super().__init__(name,path);self.guard=guard
    def get_code(self, fullname):
        # Compile verified source directly; never consume or create cached pyc.
        return compile(self.guard.bytes(self.path,fullname),self.path,'exec',dont_inherit=True)
    def get_data(self, path):return self.guard.bytes(path)
    def exec_module(self, module):
        previous=self.guard.importing;self.guard.importing=True
        try:exec(self.get_code(module.__name__),module.__dict__)
        finally:self.guard.importing=previous


class VerifiedFinder:
    def __init__(self, guard):self.guard=guard
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'site','sitecustomize','usercustomize','pip'} or fullname.startswith('gamerhq_skill_'):
            self.guard.reject('UNVERIFIED_IMPORT',fullname)
        spec=importlib.machinery.PathFinder.find_spec(fullname,path)
        if spec is None:return None  # built-in/frozen importers retain standard behavior
        if spec.origin is None:
            for directory in spec.submodule_search_locations or ():
                prefix=str(Path(directory).resolve())+'/'
                if not any(p.startswith(prefix) for p in self.guard.files):self.guard.reject('UNVERIFIED_IMPORT',fullname)
            return spec
        origin=str(Path(spec.origin).resolve())
        if origin in self.guard.files:
            self.guard.bytes(origin,fullname)
            self.guard.imports[fullname]='verified-source' if origin.endswith('.py') else 'verified-core-native'
            if origin.endswith('.py'):spec.loader=VerifiedSource(fullname,origin,self.guard)
            elif not any(origin.endswith(s) for s in importlib.machinery.EXTENSION_SUFFIXES):
                self.guard.reject('UNVERIFIED_IMPORT',fullname)
            return spec
        # stdlib/native interpreter support remains the explicitly accepted TCB.
        stdlib=str(Path(sysconfig.get_path('stdlib')).resolve())+'/'
        if origin.startswith(stdlib) and not origin.startswith(LIB+'/'):return spec
        self.guard.reject('UNVERIFIED_IMPORT',fullname)


def query_authorizer(guard, denied, sqlite):
    # Other domains expose only channel-reference columns needed by the existing
    # Games ownership checks, never their event/member/message payloads.
    tables={'games':None,'settings':None,'game_legacy_hints':None,'game_channel_candidates':None,
        'sqlite_master':None,'sqlite_schema':None,
        'lfg_events':{'channel_id','dashboard_channel_id','private_channel_id','voice_channel_id',''},
        'lfg_event_messages':{'channel_id',''},'temp_voice_channels':{'channel_id',''},'streamer_channels':{'channel_id',''}}
    def authorize(action,first,second,*_):
        allow=action in {sqlite.SQLITE_SELECT,sqlite.SQLITE_TRANSACTION,sqlite.SQLITE_RECURSIVE}
        if action==sqlite.SQLITE_READ:
            allow=first in tables and (tables[first] is None or second in tables[first])
        if action==sqlite.SQLITE_FUNCTION:
            allow=second in {'count','coalesce','ifnull','lower','upper','length','trim','like','glob','json_extract'}
        if action==sqlite.SQLITE_PRAGMA:
            allow=first in {'integrity_check','user_version','query_only'} and second is None
        if not allow:
            denied.append(action);guard.denied.append('DATABASE_QUERY_SCOPE_REJECTED')
        return sqlite.SQLITE_OK if allow else sqlite.SQLITE_DENY
    return authorize


def limits():
    import resource
    os.nice(10)
    resource.setrlimit(resource.RLIMIT_CPU,(45,45))
    resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
    signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(Rejected('OBSERVER_BUDGET_EXCEEDED')))
    signal.alarm(120)


def validate(packet):
    binding=packet['binding']
    if (binding['environment']!='production' or type(binding['guildId']) is not int or binding['guildId']<=0
            or binding['guildId'] in DENIED_GUILDS or binding['applicationId']==DENIED_APP
            or type(binding['applicationId']) is not int or binding['applicationId']<=0
            or packet.get('ownerApprovalSha256')!=approve_hash(binding)
            or packet.get('observerCommit')!=binding.get('observerCommit')
            or not isinstance(binding.get('approvalNonce'),str) or len(binding['approvalNonce'])!=32
            or any(c not in '0123456789abcdef' for c in binding['approvalNonce'])
            or os.geteuid()!=binding['runtimeUid'] or os.getegid()!=binding['runtimeGid']
            or binding['runtimeUid']<=0 or binding['runtimeGid']<=0
            or set(packet['sources'])!=set(FILES) or set(packet['hashes'])!=set(FILES)):
        raise Rejected('LEGACY_BINDING_REJECTED')
    for path,body in packet['sources'].items():
        if hashlib.sha256(body.encode()).hexdigest()!=packet['hashes'][path]:raise Rejected('OBSERVER_SOURCE_REJECTED')
    if hashlib.sha256(packet['normalizer'].encode()).hexdigest()!=packet['normalizerSha256']:
        raise Rejected('OBSERVER_SOURCE_REJECTED')
    if set(packet.get('supplements',{}))!={'typing_extensions'}:raise Rejected('OBSERVER_SOURCE_REJECTED')
    extra=packet['supplements']['typing_extensions']
    if (hashlib.sha256(extra['source'].encode()).hexdigest()!=extra['sha256']
            or extra['sha256']!=TYPING_SOURCE_SHA or extra['wheelSha256']!=TYPING_WHEEL_SHA
            or extra['wheel']!='typing_extensions-4.15.0-py3-none-any.whl'):
        raise Rejected('OBSERVER_SOURCE_REJECTED')
    if (not isinstance(packet['verifiedFiles'],dict) or len(packet['verifiedFiles'])>6000
            or not {'/app/database/db.py','/app/config.py'}<=set(packet['verifiedFiles'])):
        raise Rejected('OBSERVER_SOURCE_REJECTED')
    for path,digest in packet['verifiedFiles'].items():
        if (not (path.startswith('/app/') or path.startswith(LIB+'/')) or '/..' in path
                or '/.env' in path or path.startswith('/app/runtime/') or len(digest)!=64
                or any(c not in '0123456789abcdef' for c in digest)):
            raise Rejected('OBSERVER_SOURCE_REJECTED')


def install_http(guard):
    import aiohttp
    original=aiohttp.ClientSession._request
    async def request(self, method, url, **options):
        guard.http(method,url,options)
        return await original(self,method,url,**options)
    aiohttp.ClientSession._request=request


def collect(packet):
    validate(packet);limits()
    if (sys.version_info[:2]!=(3,12) or sys.platform!='linux' or not sys.flags.isolated or not sys.flags.no_site):
        raise Rejected('ISOLATED_RUNTIME_REQUIRED')
    database=Path(packet['binding']['database'])
    if database.is_symlink() or str(database.resolve(strict=True))!=str(database):raise Rejected('DATABASE_ACCESS_REJECTED')
    dbstat=database.stat()
    if (dbstat.st_dev,dbstat.st_ino)!=(packet['binding']['databaseDevice'],packet['binding']['databaseInode']):
        raise Rejected('DATABASE_ACCESS_REJECTED')
    sys.dont_write_bytecode=True
    sys.pycache_prefix='/proc/self/fd/-1/gamerhq-legacy-observer-cache'
    sys.path[:0]=['/app',LIB]
    guard=Guard(packet)
    sys.meta_path.insert(0,VerifiedFinder(guard))
    sys.addaudithook(guard.audit)
    old_start=threading.Thread.start
    def start(thread,*a,**kw):
        if guard.importing:guard.reject('IMPORT_THREAD_REJECTED')
        return old_start(thread,*a,**kw)
    threading.Thread.start=start
    for name,entry in packet['supplements'].items():
        module=types.ModuleType(name);module.__file__='<verified-observer-wheel:'+entry['wheel']+'>'
        module.__spec__=importlib.util.spec_from_loader(name,loader=None,origin=module.__file__)
        sys.modules[name]=module
        exec(compile(entry['source'],module.__file__,'exec'),module.__dict__)
        guard.imports[name]='verified-observer-supplement'
    import dotenv
    dotenv.load_dotenv=lambda *a,**kw:False
    import discord
    async def no_gateway(*a,**kw):guard.reject('GATEWAY_REJECTED')
    for name in ('login','connect','start'):setattr(discord.Client,name,no_gateway)
    install_http(guard)
    import tools
    normalizer=types.ModuleType('tools.game_catalog_import')
    exec(compile(packet['normalizer'],'<reviewed-games-normalizer>','exec'),normalizer.__dict__)
    sys.modules[normalizer.__name__]=normalizer;tools.game_catalog_import=normalizer
    for path in FILES:
        name=path[:-3].replace('/','.')
        module=types.ModuleType(name);module.__file__='/app/'+path
        parent=importlib.import_module(name.rsplit('.',1)[0])
        sys.modules[name]=module;setattr(parent,name.rsplit('.',1)[1],module)
        exec(compile(packet['sources'][path],module.__file__,'exec'),module.__dict__)
    observer=sys.modules['tools.game_launch_audit_collect']
    original_request=observer.GamesDiscord.request
    async def request(rest,route,**kwargs):
        if rest.packet!={k:packet['binding'][k] for k in ('environment','guildId','applicationId','database')}:
            guard.reject('LEGACY_BINDING_REJECTED')
        guard.transport=rest
        try:return await original_request(rest,route,**kwargs)
        finally:guard.transport=None
    observer.GamesDiscord.request=request
    projection=sys.modules['services.game_launch_audit']
    original_snapshot=projection.snapshot
    @contextmanager
    def snapshot(database, denied, **kwargs):
        import sqlite3
        with original_snapshot(database,denied,**kwargs) as memory:
            memory.set_authorizer(query_authorizer(guard,denied,sqlite3))
            yield memory
    projection.snapshot=snapshot
    original_collect=projection.collect
    async def read(*a,**kw):
        guard.database_phase=True
        try:return await original_collect(*a,**kw)
        finally:guard.database_phase=False
    projection.collect=read
    if guard.denied:raise Rejected(guard.denied[0])
    guard.importing=False
    result=observer.main({k:packet['binding'][k] for k in ('environment','guildId','applicationId','database')})
    if guard.denied:raise Rejected(guard.denied[0])
    result.update(observerCommit=packet['observerCommit'],observerSourceSha256=packet['hashes'],
                  evidenceMode='LEGACY_VERIFIED_SOURCE_AND_CORE_FILES',hostCommit=None,
                  hostRevisionVerified=False,buildOriginVerified=False,wholeImageIntegrityVerified=False,
                  importEvidence={'nonStdlibModules':guard.imports,'externalSkillImports':False,
                                  'stdlibAssurance':'EXISTING_OPERATIONAL_TRUST_BASE',
                                  'observerSupplements':{k:v['wheelSha256'] for k,v in packet['supplements'].items()}})
    return result


if __name__=='__main__':
    try:
        raw=sys.stdin.read(2*1024**2+1)
        if len(raw)>2*1024**2:raise Rejected('OBSERVER_BUDGET_EXCEEDED')
        print(json.dumps(collect(json.loads(raw)),sort_keys=True))
    except Exception as error:
        reason=str(error) if isinstance(error,Rejected) else 'LEGACY_OBSERVER_UNAVAILABLE'
        print(json.dumps({'state':'GAMES_AUDIT_UNAVAILABLE','schemaVersion':1,'status':'UNVERIFIED',
            'reason':reason,'privateImportModule':error.module if isinstance(error,Rejected) else None,
            'databaseChanged':False,'discordResourcesChanged':False,'gatewayRestarted':False,
            'hostRevisionVerified':False,'buildOriginVerified':False}))
