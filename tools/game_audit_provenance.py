"""Owner-operated source-only provenance evidence; never authorizes collection.

System Python 3.9. Uses the existing owner inspector from the same verified
source. No app imports, environment values, SQLite, Discord or service changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types

REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
SELF = 'tools/game_audit_provenance.py'
ROOT_FILES = {'bot.py', 'config.py', 'release_info.py', 'VERSION', 'CHANGELOG.md',
              'requirements.txt', 'requirements.lock', 'requirements-skills.lock'}
SOURCE_DIRS = {'cogs', 'database', 'services', 'tools', 'skill_runtime', 'hosts', 'skills'}
DATA_FILES = {'data/games_seed.json', 'data/game_catalog_expansion.json'}
STAGE = 'OWNER_CHECK'
REASONS = {'OWNER_CONTEXT_REQUIRED', 'SOURCE_REJECTED', 'PROBE_REJECTED', 'SOURCE_CHANGED',
           'SOURCE_MOUNT_REJECTED', 'HISTORY_LIMIT', 'CONTAINER_CHANGED', 'PUBLIC_SOURCE_REJECTED'}

# Fixed code, no caller-supplied paths. Never descends into runtime/backups or
# opens .env, database, logs, bytecode or installed dependency package files.
PROBE = r'''
import hashlib,json,os,stat
from pathlib import Path
root=Path('/app')
tops={'bot.py','config.py','release_info.py','VERSION','CHANGELOG.md','requirements.txt','requirements.lock','requirements-skills.lock'}
dirs={'cogs','database','services','tools','skill_runtime','hosts','skills'}
data={'data/games_seed.json','data/game_catalog_expansion.json'}
def safe_parent(p):
 for parent in (p,*p.parents):
  if parent==root.parent:break
  if parent.is_symlink():raise ValueError('PROBE_REJECTED')
paths=[root/p for p in sorted(tops|data) if os.path.lexists(root/p)]
for name in sorted(dirs):
 base=root/name
 if not os.path.lexists(base):continue
 safe_parent(base)
 if not base.is_dir():raise ValueError('PROBE_REJECTED')
 for folder,children,files in os.walk(base,followlinks=False):
  children[:]=sorted(p for p in children if p!='__pycache__' and not p.startswith('.'))
  for child in children:safe_parent(Path(folder)/child)
  paths.extend(Path(folder)/p for p in sorted(files) if p.endswith('.py') and not p.startswith('.'))
if len(paths)>4096:raise ValueError('PROBE_REJECTED')
rows={};total=0
for p in paths:
 safe_parent(p)
 name=p.relative_to(root).as_posix()
 if any(not part or part in ('.','..') for part in Path(name).parts):raise ValueError('PROBE_REJECTED')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as stream:
  before=os.fstat(stream.fileno())
  if not stat.S_ISREG(before.st_mode) or before.st_size>2*1024**2:raise ValueError('PROBE_REJECTED')
  body=stream.read(2*1024**2+1);after=os.fstat(stream.fileno())
  if (before.st_ino,before.st_dev,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_dev,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('SOURCE_CHANGED')
 total+=len(body)
 if len(body)!=before.st_size or total>64*1024**2:raise ValueError('PROBE_REJECTED')
 rows[name]={'sha256':hashlib.sha256(body).hexdigest(),'gitBlob':hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()}
if not {'bot.py','config.py'}<=set(rows):raise ValueError('PROBE_REJECTED')
marker_state='ABSENT';marker=root/'BUILD_COMMIT'
if os.path.lexists(marker):
 safe_parent(marker)
 with os.fdopen(os.open(marker,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
  if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('PROBE_REJECTED')
  value=stream.read(81).strip()
 marker_state='UNKNOWN_DEFAULT' if value==b'unknown' else 'VALID_HEX' if len(value)==40 and all(c in b'0123456789abcdef' for c in value) else 'INVALID'
print(json.dumps({'runtimeUid':os.geteuid(),'runtimeGid':os.getegid(),'files':rows,'buildMarkerState':marker_state},sort_keys=True))
'''


def selected(path):
    parts = path.split('/')
    return (path in ROOT_FILES or path in DATA_FILES or
            len(parts) > 1 and parts[0] in SOURCE_DIRS and path.endswith('.py')
            and all(p and p not in {'.', '..', '__pycache__'} and not p.startswith('.') for p in parts))


def validate_probe(raw):
    if len(raw.encode()) > 1024**2:
        raise ValueError('PROBE_REJECTED')
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'runtimeUid', 'runtimeGid', 'files', 'buildMarkerState'}
            or value['buildMarkerState'] not in {'ABSENT', 'UNKNOWN_DEFAULT', 'VALID_HEX', 'INVALID'}
            or any(type(value[k]) is not int or value[k] <= 0 for k in ('runtimeUid', 'runtimeGid'))
            or not isinstance(value['files'], dict) or not 2 <= len(value['files']) <= 4096
            or not {'bot.py', 'config.py'} <= set(value['files'])):
        raise ValueError('PROBE_REJECTED')
    for path, row in value['files'].items():
        if (not selected(path) or not isinstance(row, dict) or set(row) != {'sha256', 'gitBlob'}
                or not re.fullmatch('[0-9a-f]{64}', str(row['sha256']))
                or not re.fullmatch('[0-9a-f]{40}', str(row['gitBlob']))):
            raise ValueError('PROBE_REJECTED')
    return value


def verify_mounts(meta):
    # A runtime/data mount is out of scope; a source overlay must never be
    # mistaken for immutable image contents. Root/ancestor mounts also fail.
    for mount in meta['mounts']:
        target = mount.get('Destination', '').rstrip('/') or '/'
        if target == '/' or target == '/app' or any(
                target == '/app/' + item or target.startswith('/app/' + item + '/')
                or ('/app/' + item).startswith(target + '/')
                for item in ROOT_FILES | SOURCE_DIRS | DATA_FILES):
            raise ValueError('SOURCE_MOUNT_REJECTED')


def source_matches(git, commits, observed):
    """All candidate commits are searched; never pick newest/HEAD on a tie."""
    if len(commits) > 2048:
        raise ValueError('HISTORY_LIMIT')
    matches = []
    checked = set()
    for commit in commits:
        if not re.fullmatch('[0-9a-f]{40}', commit):
            raise ValueError('PUBLIC_SOURCE_REJECTED')
        tree = {}
        for entry in git('ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
            if not entry:
                continue
            header, path = entry.split(b'\t', 1)
            path = path.decode('utf-8')
            if not selected(path):
                continue
            mode, kind, blob = header.decode('ascii').split()
            if mode not in {'100644', '100755'} or kind != 'blob':
                raise ValueError('PUBLIC_SOURCE_REJECTED')
            tree[path] = blob
        if tree != {path: row['gitBlob'] for path, row in observed.items()}:
            continue
        # Git blob identities shortlist only. Independently verify SHA256 of
        # every selected file in every matching tree against trusted Git bytes.
        for path, blob in tree.items():
            key = (blob, observed[path]['sha256'])
            if key not in checked:
                if hashlib.sha256(git('cat-file', 'blob', blob)).hexdigest() != key[1]:
                    raise ValueError('PUBLIC_SOURCE_REJECTED')
                checked.add(key)
        matches.append(commit)
    return sorted(matches)


def evidence(meta, probe, commits, refs):
    fingerprint = hashlib.sha256(json.dumps(probe['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    identity = hashlib.sha256(json.dumps({'id': meta['id'], 'image': meta['image']}, sort_keys=True).encode()).hexdigest()
    return {'state': 'GAMES_SOURCE_PROVENANCE_EVIDENCE', 'schemaVersion': 1,
            'status': 'SOURCE_EQUIVALENCE_ONLY' if commits else 'NO_SOURCE_MATCH',
            'sourceEquivalentCommits': commits, 'matchingCommitCount': len(commits),
            'sourceManifestSha256': fingerprint, 'sourceFileCount': len(probe['files']),
            'containerImageBindingSha256': identity, 'containerIdentityUnchanged': True,
            'publicDiscoveryRefs': refs, 'hostRevisionVerified': False,
            'buildMarkerState': probe['buildMarkerState'],
            'buildOriginVerified': False, 'dependencyIntegrityVerified': False,
            'collectionExecuted': False, 'databaseAccessed': False, 'discordAccessed': False,
            'productionChanged': False}


def collect_source_evidence(operator, git, commits, refs, run=subprocess.run):
    global STAGE
    STAGE = 'SOURCE_ONLY_INSPECTION'
    meta = operator.inspect('gamerhq-bot')
    verify_mounts(meta)
    def probe():
        raw = run(['docker', 'exec', '--user', meta['user'], meta['id'],
                   'python', '-I', '-B', '-c', PROBE], capture_output=True,
                  text=True, check=True, timeout=45).stdout
        return validate_probe(raw)
    first = probe()
    STAGE = 'PUBLIC_TREE_COMPARISON'
    matches = source_matches(git, commits, first['files'])
    STAGE = 'UNCHANGED_SOURCE_VERIFICATION'
    if probe() != first:
        raise ValueError('SOURCE_CHANGED')
    after = operator.inspect('gamerhq-bot')
    if after != meta:
        raise ValueError('CONTAINER_CHANGED')
    return evidence(meta, first, matches, refs)


def main():
    global STAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise ValueError('OWNER_CONTEXT_REQUIRED')
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        raise ValueError('SOURCE_REJECTED')
    with tempfile.TemporaryDirectory(prefix='gamerhq-source-provenance-') as scratch:
        STAGE = 'PUBLIC_SOURCE_VERIFICATION'
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*values):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *values], env=env,
                                  capture_output=True, check=True, timeout=120).stdout
        git('init', '--quiet')
        git('fetch', '--quiet', '--depth=1', REPOSITORY, args.source_commit)
        if (git('rev-parse', 'FETCH_HEAD').strip().decode() != args.source_commit
                or git('show', args.source_commit + ':' + SELF) != Path(__file__).read_bytes()):
            raise ValueError('SOURCE_REJECTED')
        operator = types.ModuleType('verified_owner_inspector')
        exec(compile(git('show', args.source_commit + ':tools/game_audit_operator.py'),
                     '<verified-owner-inspector>', 'exec'), operator.__dict__)
        # Public history supplies candidates, not an assertion about deployment.
        refs = {}
        git('fetch', '--quiet', '--unshallow', '--no-tags', REPOSITORY,
            '+refs/heads/main:refs/heads/main', '+refs/heads/develop:refs/heads/develop')
        if git('rev-parse', '--is-shallow-repository').strip() != b'false':
            raise ValueError('PUBLIC_SOURCE_REJECTED')
        for branch in ('main', 'develop'):
            refs[branch] = git('rev-parse', 'refs/heads/' + branch).strip().decode()
        commits = git('rev-list', '--max-count=2049', *refs.values()).decode().splitlines()
        if len(commits) > 2048:
            raise ValueError('HISTORY_LIMIT')
        print(json.dumps(collect_source_evidence(operator, git, commits, refs), sort_keys=True))


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    try:
        main()
    except Exception as error:
        reason = error.args[0] if error.args and isinstance(error.args[0], str) and error.args[0] in REASONS else 'PROVENANCE_UNAVAILABLE'
        raise SystemExit(json.dumps({'state': 'GAMES_SOURCE_PROVENANCE_UNAVAILABLE', 'stage': STAGE,
                                    'reason': reason, 'hostRevisionVerified': False,
                                    'collectionExecuted': False})) from None
