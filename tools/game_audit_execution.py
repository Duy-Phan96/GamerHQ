"""Source-sealed observer packet shared by existing controller and owner launcher."""
import hashlib
import json
import re

FILES = ('tools/game_audit_execution.py', 'services/game_audit_context.py', 'services/game_launch_audit.py',
         'tools/game_launch_audit_collect.py', 'tools/game_launch_audit_report.py')

REASONS = frozenset({'GAMES_AUDIT_BINDING_REJECTED', 'CREDENTIAL_UNAVAILABLE', 'DATABASE_IDENTITY_REJECTED',
    'BOT_IDENTITY_REJECTED', 'GUILD_IDENTITY_REJECTED', 'DISCORD_SCHEMA_REJECTED', 'GUILD_MEMBERSHIP_REJECTED',
    'READ_ONLY_REQUEST_REJECTED', 'DISCORD_TIMEOUT', 'DISCORD_TRANSPORT_UNAVAILABLE', 'DISCORD_HTTP_REJECTED',
    'DISCORD_RATE_LIMITED', 'GAMES_AUDIT_BUDGET_EXCEEDED', 'GAMES_AUDIT_BODY_REJECTED',
    'GAMES_AUDIT_SCHEMA_REJECTED', 'GAMES_AUDIT_DATABASE_REJECTED', 'FOREIGN_INVENTORY_REJECTED',
    'GAMES_AUDIT_WRITE_ATTEMPTED', 'READ_SNAPSHOT_NESTING_REJECTED', 'ACCEPTANCE_BUDGET_EXCEEDED',
    'GUILD_UNAVAILABLE', 'RUNTIME_OR_SCHEMA_UNAVAILABLE'})


def packet(sources, hashes, binding, *, source_commit):
    if (set(sources) != set(FILES) or set(hashes) != set(FILES)
            or not re.fullmatch('[0-9a-f]{40}', source_commit)
            or sum(len(v.encode()) for v in sources.values()) > 262144
            or any(hashlib.sha256(v.encode()).hexdigest() != hashes[k] for k, v in sources.items())):
        raise ValueError('GAMES_AUDIT_SOURCE_REJECTED')
    return {'sources': sources, 'hashes': hashes, 'binding': binding, 'sourceCommit': source_commit}


LAUNCH = r'''
import hashlib,importlib,json,sys,types
sys.dont_write_bytecode=True
# Ignore pre-existing application bytecode without creating a cache directory.
sys.pycache_prefix='/proc/self/fd/-1/gamerhq-audit-cache'
sys.path.insert(0,'/app')
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
p=json.load(sys.stdin)
files=('tools/game_audit_execution.py','services/game_audit_context.py','services/game_launch_audit.py','tools/game_launch_audit_collect.py','tools/game_launch_audit_report.py')
if set(p['sources'])!=set(files) or set(p['hashes'])!=set(files):raise SystemExit('GAMES_AUDIT_SOURCE_REJECTED')
for path in files:
 body=p['sources'][path]
 if hashlib.sha256(body.encode()).hexdigest()!=p['hashes'][path]:raise SystemExit('GAMES_AUDIT_SOURCE_REJECTED')
 name=path[:-3].replace('/','.')
 module=types.ModuleType(name);module.__file__='/app/'+path
 parent=importlib.import_module(name.rsplit('.',1)[0])
 sys.modules[name]=module;setattr(parent,name.rsplit('.',1)[1],module)
 exec(compile(body,module.__file__,'exec'),module.__dict__)
result=sys.modules['tools.game_launch_audit_collect'].main(p['binding'])
result['observerCommit']=p['sourceCommit']
result['observerSourceSha256']=p['hashes']
print(json.dumps(result,separators=(',',':')))
'''


def output(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 2 * 1024**2:
        raise ValueError('GAMES_AUDIT_RESPONSE_REJECTED')
    result = json.loads(raw)
    if (not isinstance(result, dict) or result.get('schemaVersion') != 1
            or result.get('state') not in {'GAMES_AUDIT_COLLECTED', 'GAMES_AUDIT_UNAVAILABLE'}
            or any(result.get(k) is not False for k in ('databaseChanged', 'discordResourcesChanged', 'gatewayRestarted'))):
        raise ValueError('GAMES_AUDIT_RESPONSE_REJECTED')
    return result
