"""Owner-only existing-container Games audit; Python 3.9 launcher, no deployments.

Preflight inspects metadata only. Collection additionally requires an explicit
owner phrase and expected immutable Host/image/guild/application/database binding.
No agent SSH identity, sudo grant, Docker socket or production token is added.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
import re
import subprocess
import sys
import tempfile
import types

REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
SELF = 'tools/game_audit_operator.py'
PHRASE = 'COLLECT GAMES READ ONLY'
STAGE = 'OWNER_CHECK'
REASONS = {'OWNER_CONTEXT_REQUIRED', 'SOURCE_COMMIT_REJECTED', 'PRODUCTION_CONTAINER_REJECTED',
           'CONTAINER_IDENTITY_REJECTED', 'RUNNING_HOST_IDENTITY_UNVERIFIED', 'OPERATOR_SOURCE_REJECTED',
           'EXACT_OWNER_COLLECTION_APPROVAL_REQUIRED', 'PRIVATE_OUTPUT_DIRECTORY_REQUIRED',
           'DATABASE_IDENTITY_REJECTED', 'DATABASE_MOUNT_UNVERIFIED', 'PRODUCTION_DATABASE_MOUNT_UNVERIFIED',
           'HOST_AUDIT_CAPABILITIES_UNAVAILABLE', 'HOST_CHANGED_DURING_COLLECTION', 'DATABASE_READ_ACCESS_UNAVAILABLE'}
FORMAT = ('{"id":{{json .Id}},"image":{{json .Image}},"user":{{json .Config.User}},'
          '"workingDir":{{json .Config.WorkingDir}},"running":{{json .State.Running}},'
          '"health":{{json .State.Health.Status}},'
          '"hostCommit":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
          '"mounts":{{json .Mounts}}}')
CONFIG_PROBE = r'''
import json,os,stat
from pathlib import Path
guild=os.environ.get('GUILD_ID','')
raw=os.environ.get('GAMERHQ_DB_PATH','')
if not guild.isdecimal() or not raw or not Path(raw).is_absolute():raise SystemExit('CONFIGURED_DB_METADATA_UNAVAILABLE')
path=Path(raw)
if path.is_symlink():raise SystemExit('DATABASE_IDENTITY_REJECTED')
info=path.stat()
if not stat.S_ISREG(info.st_mode):raise SystemExit('DATABASE_IDENTITY_REJECTED')
print(json.dumps({'configuredGuildId':int(guild),'database':str(path),'mode':oct(stat.S_IMODE(info.st_mode)),
                  'uid':info.st_uid,'gid':info.st_gid,'readable':os.access(path,os.R_OK)}))
'''


def inspect(container, run=subprocess.run):
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', container):
        raise ValueError('CONTAINER_IDENTITY_REJECTED')
    raw = run(['docker', 'inspect', '--format', FORMAT, container], capture_output=True,
              text=True, check=True, timeout=20).stdout
    result = json.loads(raw)
    if (result.get('running') is not True or result.get('health') != 'healthy'
            or not re.fullmatch('[0-9a-f]{64}', str(result.get('id')))
            or not re.fullmatch('[0-9a-f]{40}', str(result.get('hostCommit')))
            or not re.fullmatch('sha256:[0-9a-f]{64}', str(result.get('image')))
            or result.get('workingDir') != '/app'
            or not re.fullmatch('[1-9][0-9]*(?::[1-9][0-9]*)?', str(result.get('user')))):
        raise ValueError('RUNNING_HOST_IDENTITY_UNVERIFIED')
    return result


def database_mount(meta, database, environment):
    path = PurePosixPath(database)
    if not path.is_absolute() or '..' in path.parts or str(path) != database:
        raise ValueError('DATABASE_IDENTITY_REJECTED')
    matches = [m for m in meta['mounts'] if path.is_relative_to(PurePosixPath(m['Destination']))]
    if len(matches) != 1 or matches[0].get('Type') != 'bind':
        raise ValueError('DATABASE_MOUNT_UNVERIFIED')
    if environment == 'production' and matches[0].get('Source') != '/opt/gamerhq/data':
        raise ValueError('PRODUCTION_DATABASE_MOUNT_UNVERIFIED')


def main():
    global STAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--environment', choices=['production', 'staging'], required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--approval')
    parser.add_argument('--expected-host-commit')
    parser.add_argument('--expected-image')
    parser.add_argument('--guild-id', type=int)
    parser.add_argument('--application-id', type=int)
    parser.add_argument('--database')
    parser.add_argument('--private-output', type=Path)
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise ValueError('OWNER_CONTEXT_REQUIRED')
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        raise ValueError('SOURCE_COMMIT_REJECTED')
    if args.environment == 'production' and args.container != 'gamerhq-bot':
        raise ValueError('PRODUCTION_CONTAINER_REJECTED')
    # Fetch only reviewed public source. No checkout, current repo or VPS service is changed.
    with tempfile.TemporaryDirectory(prefix='gamerhq-games-source-') as scratch:
        STAGE = 'SOURCE_VERIFICATION'
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*values):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *values], env=env,
                                  capture_output=True, check=True, timeout=120).stdout
        git('init', '--quiet')
        def fetch(commit):
            git('fetch', '--quiet', '--depth=1', REPOSITORY, commit)
            if git('rev-parse', '--verify', 'FETCH_HEAD^{commit}').strip().decode() != commit:
                raise ValueError('SOURCE_COMMIT_REJECTED')
        fetch(args.source_commit)
        if git('show', args.source_commit + ':' + SELF) != Path(__file__).read_bytes():
            raise ValueError('OPERATOR_SOURCE_REJECTED')
        STAGE = 'CONTAINER_INSPECTION'
        meta = inspect(args.container)
        if args.preflight:
            # Private owner output: metadata only; not a Games collection or permission grant.
            configured = subprocess.run(['docker', 'exec', '--user', meta['user'], meta['id'],
                'python', '-I', '-B', '-c', CONFIG_PROBE], capture_output=True, text=True, check=True, timeout=20).stdout
            if len(configured) > 4096:
                raise ValueError('DATABASE_IDENTITY_REJECTED')
            configured = json.loads(configured)
            if (set(configured) != {'configuredGuildId', 'database', 'mode', 'uid', 'gid', 'readable'}
                    or type(configured['configuredGuildId']) is not int or configured['configuredGuildId'] <= 0):
                raise ValueError('DATABASE_IDENTITY_REJECTED')
            database_mount(meta, configured['database'], args.environment)
            if configured['readable'] is not True:
                raise ValueError('DATABASE_READ_ACCESS_UNAVAILABLE')
            print(json.dumps({'state': 'GAMES_OWNER_PREFLIGHT', 'environment': args.environment,
                              'observedAt': datetime.now(timezone.utc).isoformat(),
                              'container': meta, 'configurationMetadata': configured, 'collectionExecuted': False}))
            return 0
        if (args.approval != PHRASE or not args.private_output or not args.database
                or not args.guild_id or not args.application_id
                or meta['hostCommit'] != args.expected_host_commit or meta['image'] != args.expected_image):
            raise ValueError('EXACT_OWNER_COLLECTION_APPROVAL_REQUIRED')
        parent = args.private_output.parent
        info = parent.stat()
        if parent.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('PRIVATE_OUTPUT_DIRECTORY_REQUIRED')
        database_mount(meta, args.database, args.environment)
        STAGE = 'BASELINE_SOURCE_VERIFICATION'
        fetch(meta['hostCommit'])
        # Verify the actual installed services/DB against the declared immutable Host.
        files = git('ls-tree', '-r', '--name-only', meta['hostCommit']).decode().splitlines()
        baseline = {name: hashlib.sha256(git('show', meta['hostCommit'] + ':' + name)).hexdigest()
                    for name in files if name.endswith('.py') and (name.startswith(('database/', 'services/'))
                    or name in {'config.py', 'tools/game_catalog_import.py', 'tools/backup_database.py', 'tools/instance_lock.py'})}
        if not {'config.py', 'database/db.py', 'services/game_readiness_service.py', 'tools/game_catalog_import.py'} <= set(baseline):
            raise ValueError('HOST_AUDIT_CAPABILITIES_UNAVAILABLE')
        module = types.ModuleType('sealed_execution')
        exec(compile(git('show', args.source_commit + ':tools/game_audit_execution.py'), '<sealed-execution>', 'exec'), module.__dict__)
        sources = {name: git('show', args.source_commit + ':' + name).decode() for name in module.FILES}
        hashes = {name: hashlib.sha256(body.encode()).hexdigest() for name, body in sources.items()}
        binding = {'environment': args.environment, 'guildId': args.guild_id,
                   'applicationId': args.application_id, 'database': args.database}
        packet = module.packet(sources, hashes, binding, source_commit=args.source_commit)
        packet['baseline'] = baseline
        guard = ("from pathlib import Path\nimport hashlib,json,sys\np=json.load(sys.stdin)\n"
                 "for name,digest in p['baseline'].items():\n"
                 " if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise SystemExit('HOST_SOURCE_REJECTED')\n"
                 "import io\nsys.stdin=io.StringIO(json.dumps(p))\n")
        STAGE = 'READ_ONLY_COLLECTION'
        raw = subprocess.run(['docker', 'exec', '--interactive', '--user', meta['user'], meta['id'],
                              'python', '-I', '-B', '-c', guard + module.LAUNCH], input=json.dumps(packet),
                             capture_output=True, text=True, check=True, timeout=180).stdout
        result = module.output(raw)
        after = inspect(args.container)
        if any(after[k] != meta[k] for k in ('id', 'image', 'hostCommit', 'user', 'mounts')):
            raise ValueError('HOST_CHANGED_DURING_COLLECTION')
        result.update(hostCommit=meta['hostCommit'], image=meta['image'])
        STAGE = 'PRIVATE_RECEIPT'
        fd = os.open(args.private_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=True, sort_keys=True)
            stream.write('\n')
        print(json.dumps({'state': result['state'], 'privateReceiptWritten': True,
                          'databaseChanged': False, 'discordResourcesChanged': False}))


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    try:
        raise SystemExit(main())
    except Exception as error:
        # Do not forward Docker inspect/exec output or arbitrary exception text.
        reason = error.args[0] if error.args and isinstance(error.args[0], str) and error.args[0] in REASONS else 'OWNER_EXECUTION_UNAVAILABLE'
        raise SystemExit(json.dumps({'state': 'GAMES_OWNER_COLLECTION_UNAVAILABLE', 'stage': STAGE,
                                    'reason': reason, 'repairAuthorized': False})) from None
