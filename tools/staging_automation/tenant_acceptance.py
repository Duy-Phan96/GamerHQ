"""Protected read-only collection in the existing, already running container."""
import hashlib
import json
import base64
import zlib
from pathlib import Path

from .policy import Denied


OPERATION = 'tenant-acceptance'
WORKFLOW = 'staging-tenant-acceptance.yml'
FILES = ('services/tenant_acceptance.py', 'tools/tenant_acceptance_collect.py')


def pack_receipt(value):
    raw = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
    if len(raw) > 262144:
        raise Denied('ACCEPTANCE_RESPONSE_REJECTED')
    payload = base64.b64encode(zlib.compress(raw)).decode()
    if len(payload) > 48000:
        raise Denied('ACCEPTANCE_RESPONSE_REJECTED')
    return {'state': value['state'], 'status': value['status'], 'productionChanged': False,
            'reportEncoding': 'zlib-base64', 'reportLength': len(raw),
            'reportSha256': hashlib.sha256(raw).hexdigest(), 'report': payload}


def unpack_receipt(value):
    try:
        if value['reportEncoding'] != 'zlib-base64' or len(value['report']) > 48000:
            raise ValueError()
        stream = zlib.decompressobj()
        raw = stream.decompress(base64.b64decode(value['report'], validate=True), 262145)
        if (len(raw) > 262144 or len(raw) != value['reportLength'] or not stream.eof or stream.unused_data or stream.unconsumed_tail
                or hashlib.sha256(raw).hexdigest() != value['reportSha256']):
            raise ValueError()
        result = json.loads(raw)
        if result['status'] != value['status'] or result['state'] != value['state']:
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError, zlib.error):
        raise Denied('ACCEPTANCE_RESPONSE_REJECTED') from None
LAUNCH = r'''
import hashlib,json,sys,types
sys.dont_write_bytecode=True
sys.path.insert(0,'/app')
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
packet=json.load(sys.stdin)
for path in ('services/tenant_acceptance.py','tools/tenant_acceptance_collect.py'):
 body=packet['sources'][path]
 if hashlib.sha256(body.encode()).hexdigest()!=packet['hashes'][path]: raise SystemExit('SOURCE_REJECTED')
 name=path[:-3].replace('/','.')
 module=types.ModuleType(name);module.__file__='/app/'+path;sys.modules[name]=module
 exec(compile(body,module.__file__,'exec'),module.__dict__)
try:
 result=sys.modules['tools.tenant_acceptance_collect'].main(packet['binding'])
except Exception as error:
 known={'CREDENTIAL_UNAVAILABLE','GUILD_IDENTITY_REJECTED','DATABASE_IDENTITY_REJECTED','READ_ONLY_REQUEST_REJECTED','DISCORD_READ_REJECTED','ACCEPTANCE_BUDGET_EXCEEDED'}
 reason=error.args[0] if error.args and isinstance(error.args[0],str) and error.args[0] in known else 'COLLECTION_FAILED'
 result={'schemaVersion':1,'state':'TENANT_ACCEPTANCE_UNAVAILABLE','status':'UNVERIFIED','reason':reason,
         'productionChanged':False,'gatewayRestarted':False,'databaseChanged':False,'discordResourcesChanged':False}
print(json.dumps(result,separators=(',',':')))
'''


def validate_pin(pin):
    import re
    if (not isinstance(pin, dict) or set(pin) != {'workflowSha256', 'sourceSha256'}
            or not re.fullmatch('[0-9a-f]{64}', str(pin.get('workflowSha256', '')))
            or not isinstance(pin['sourceSha256'], dict) or set(pin['sourceSha256']) != set(FILES)
            or any(not re.fullmatch('[0-9a-f]{64}', str(value)) for value in pin['sourceSha256'].values())):
        raise Denied('ACCEPTANCE_POLICY_REJECTED')


def run(backend):
    from .pilot_cutover import journal_status
    pin = backend.policy.get('tenantAcceptance')
    validate_pin(pin)
    generation = backend.profile.get('pilotGeneration')
    if (backend.operation_lock is None or generation not in {'recovered-primary-v2', 'active-pilot-rc4-v3'}
            or journal_status(backend.profile).get('phase') != 'PILOT_ACTIVE'):
        raise Denied('ACCEPTANCE_BASELINE_REJECTED')
    container = backend.inspect()
    expected = backend.policy['tenantPilotActivation']['commit']
    if container['Config'].get('Labels', {}).get('org.opencontainers.image.revision') != expected or backend.health()['state'] != 'HOST_READY':
        raise Denied('ACCEPTANCE_BASELINE_REJECTED')
    root = Path(__file__).resolve().parents[1] / 'pilot-source'
    sources = {name: (root / name).read_text(encoding='utf-8') for name in FILES}
    if any(hashlib.sha256(body.encode()).hexdigest() != pin['sourceSha256'][name] for name, body in sources.items()):
        raise Denied('ACCEPTANCE_SOURCE_REJECTED')
    binding = backend.policy['tenantPilot']
    packet = {'sources': sources, 'hashes': pin['sourceSha256'],
              'binding': {**binding, 'primaryGuildId': backend.profile['stagingGuildId'],
                          'database': '/app/runtime/data/gamerhq.db'}}
    # Existing mounts/user/limits/daemon were verified by inspect/health. No
    # container, mount, secret, file or SELinux label is added or changed.
    raw = backend.run(['docker', 'exec', '--interactive', '--user', '10001:10001', container['Id'],
                       'python', '-I', '-B', '-c', LAUNCH], timeout=600,
                      stdin=json.dumps(packet))
    result = json.loads(raw)
    if (result.get('schemaVersion') != 1 or result.get('state') not in {'TENANT_ACCEPTANCE_COLLECTED', 'TENANT_ACCEPTANCE_UNAVAILABLE'}
            or result.get('status') not in {'PASS', 'FAIL', 'UNVERIFIED'}
            or any(result.get(key) is not False for key in ('productionChanged', 'gatewayRestarted', 'databaseChanged', 'discordResourcesChanged'))):
        raise Denied('ACCEPTANCE_RESPONSE_REJECTED')
    if backend.inspect()['Id'] != container['Id'] or backend.health()['state'] != 'HOST_READY':
        raise Denied('ACCEPTANCE_BASELINE_REJECTED')
    return pack_receipt({**result, 'hostCommit': expected, 'image': container['Image'],
                         'generation': generation})
