"""Dependency-only legacy eligibility evidence. Never opens a DB or collects Games.

System Python 3.9 owner launcher. Core wheels are independently pinned; installed
RECORD/version claims are not used as proof. Original revision enforcement stays.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import types
import urllib.request
from urllib.parse import urlsplit
import zipfile

REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
SELF = 'tools/game_audit_legacy_dependencies.py'
MANIFEST = 'deploy/production/games-legacy-dependencies.json'
EQUIVALENT = ['6f38ddc80b4a6a3a982c16441d3c7ca3db79790d', 'e45c62117b324ff270db2067dbe30167b41cdeb4']
GENERATED = {'RECORD', 'INSTALLER', 'REQUESTED', 'direct_url.json'}
STAGE = 'OWNER_CHECK'

PROBE = r'''
import hashlib,json,os,platform,stat,sys,sysconfig
from pathlib import Path
p=json.load(sys.stdin)
if sys.version_info[:2]!=(3,12) or sys.platform!='linux' or platform.machine()!='x86_64':raise ValueError('UNSUPPORTED_INTERPRETER')
base=Path(sysconfig.get_path('purelib'))
if str(base)!='/usr/local/lib/python3.12/site-packages' or base!=Path(sysconfig.get_path('platlib')):raise ValueError('UNSUPPORTED_LAYOUT')
if os.geteuid()<=0 or os.getegid()<=0 or len(p['roots'])>256:raise ValueError('PROBE_REJECTED')
def safe(path):
 for part in (path,*path.parents):
  if part==base.parent:break
  if part.is_symlink():raise ValueError('DEPENDENCY_SYMLINK')
rows={};total=0
for name in p['roots']:
 if not name or '/' in name or chr(92) in name or name.startswith('.') or name in ('.','..'):raise ValueError('PROBE_REJECTED')
 root=base/name
 if not os.path.lexists(root):continue
 safe(root)
 paths=[]
 if root.is_dir():
  for folder,dirs,files in os.walk(root,followlinks=False):
   dirs[:]=sorted(d for d in dirs if d!='__pycache__')
   for d in dirs:safe(Path(folder)/d)
   paths.extend(Path(folder)/f for f in sorted(files))
   if len(paths)>12000:raise ValueError('PROBE_REJECTED')
 else:paths=[root]
 for path in paths:
  safe(path)
  relative=path.relative_to(base).as_posix()
  if root.name.endswith('.dist-info') and path.parent==root and path.name in {'RECORD','INSTALLER','REQUESTED','direct_url.json'}:continue
  with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
   before=os.fstat(f.fileno())
   if not stat.S_ISREG(before.st_mode) or before.st_size>32*1024**2:raise ValueError('PROBE_REJECTED')
   body=f.read(32*1024**2+1);after=os.fstat(f.fileno())
   if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('DEPENDENCY_CHANGED')
  total+=len(body)
  if total>128*1024**2 or len(body)!=before.st_size:raise ValueError('PROBE_REJECTED')
  rows[relative]=hashlib.sha256(body).hexdigest()
if len(rows)>12000:raise ValueError('PROBE_REJECTED')
print(json.dumps({'files':rows,'runtimeUid':os.geteuid(),'runtimeGid':os.getegid()},sort_keys=True))
'''


def expected_files(body, wheel):
    if len(body) != wheel['size'] or hashlib.sha256(body).hexdigest() != wheel['sha256']:
        raise ValueError('WHEEL_CHECKSUM_REJECTED')
    result = {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = archive.infolist()
        if len(entries)>12000 or sum(e.file_size for e in entries)>128*1024**2:
            raise ValueError('WHEEL_BUDGET_REJECTED')
        for entry in entries:
            if entry.is_dir(): continue
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or chr(92) in entry.orig_filename
                    or entry.orig_filename!=entry.filename
                    or str(path)!=entry.filename or '.data' in path.parts[0]
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError('WHEEL_LAYOUT_REJECTED')
            if len(path.parts)==2 and path.parts[0].endswith('.dist-info') and path.name in GENERATED:
                continue
            if str(path) in result: raise ValueError('WHEEL_LAYOUT_REJECTED')
            result[str(path)] = hashlib.sha256(archive.read(entry)).hexdigest()
    if not result or not any(p.endswith('.dist-info/METADATA') for p in result):
        raise ValueError('WHEEL_LAYOUT_REJECTED')
    return result


def download(wheel):
    url = urlsplit(wheel['url'])
    if (url.scheme!='https' or url.netloc!='files.pythonhosted.org' or url.query or url.fragment
            or not url.path.endswith('/'+wheel['filename']) or not 0<wheel['size']<=16*1024**2
            or not re.fullmatch('[0-9a-f]{64}',wheel['sha256'])):
        raise ValueError('WHEEL_SOURCE_REJECTED')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(wheel['url'],timeout=30) as response:
        if response.status!=200: raise ValueError('WHEEL_SOURCE_REJECTED')
        return response.read(16*1024**2+1)


def compare(expected, observed):
    results=[]
    for package, candidates in expected.items():
        matches=[]
        # The full union includes binary-name variants. A second/foreign file
        # under a covered package root cannot hide outside a selected wheel.
        roots={p.split('/')[0] for files in candidates.values() for p in files}
        actual={p:h for p,h in observed.items() if p.split('/')[0] in roots}
        for filename, files in candidates.items():
            if files==actual:matches.append(filename)
        results.append({'package':package,'status':'PASS' if matches else 'UNVERIFIED',
                        'matchedWheels':sorted(matches)})
    return results


def validate_manifest(manifest, lock):
    wanted={}
    for line in lock.decode().splitlines():
        if not line or line.startswith('#') or line=='audioop-lts==0.2.2; python_version >= "3.13"': continue
        if not re.fullmatch('[A-Za-z0-9._-]+==[A-Za-z0-9._-]+',line):raise ValueError('LOCK_REJECTED')
        name,version=line.split('==');wanted[name]=version
    if (manifest.get('schemaVersion')!=1 or manifest.get('target')!='cpython-3.12-linux-x86_64'
            or manifest.get('sourceEquivalentCommits')!=EQUIVALENT
            or manifest.get('requirementsSha256')!=hashlib.sha256(lock).hexdigest()
            or len(manifest.get('packages',[]))!=len(wanted) or len(wanted)!=15):
        raise ValueError('LOCK_REJECTED')
    seen=set()
    for package in manifest['packages']:
        name=package['name']
        if name in seen or wanted.get(name)!=package['version'] or not 1<=len(package['wheels'])<=8:
            raise ValueError('LOCK_REJECTED')
        seen.add(name)
    if seen!=set(wanted):raise ValueError('LOCK_REJECTED')


def validate_observed(raw, roots):
    if len(raw.encode())>2*1024**2: raise ValueError('PROBE_REJECTED')
    value=json.loads(raw)
    if (set(value)!={'files','runtimeUid','runtimeGid'} or not isinstance(value['files'],dict)
            or len(value['files'])>12000 or any(type(value[k]) is not int or value[k]<=0 for k in ('runtimeUid','runtimeGid'))):
        raise ValueError('PROBE_REJECTED')
    for path,digest in value['files'].items():
        p=PurePosixPath(path)
        if (p.is_absolute() or '..' in p.parts or chr(92) in path or str(p)!=path
                or p.parts[0] not in roots or not re.fullmatch('[0-9a-f]{64}',str(digest))):
            raise ValueError('PROBE_REJECTED')
    return value


def summary(rows, binding):
    return {'state':'GAMES_LEGACY_DEPENDENCY_PREFLIGHT','schemaVersion':1,
            'lockedCoreDependenciesMatch':bool(rows) and all(r['status']=='PASS' for r in rows),
            'packages':rows,'sourceEvidence':binding,
            'verifiedScope':'PINNED_CORE_WHEEL_FILES_ONLY',
            'buildOriginVerified':False,'hostRevisionVerified':False,
            'wholeImageIntegrityVerified':False,'legacyCollectionEligible':False,
            'collectionExecuted':False,'databaseAccessed':False,'discordAccessed':False,'productionChanged':False}


def main():
    global STAGE
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    args=parser.parse_args()
    if not hasattr(os,'geteuid') or os.geteuid()!=0 or not re.fullmatch('[0-9a-f]{40}',args.source_commit):
        raise ValueError('OWNER_SOURCE_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='gamerhq-core-integrity-') as scratch:
        env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','GIT_CONFIG_GLOBAL':'/dev/null',
             'GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0'}
        def git(*a):return subprocess.run(['git','-C',scratch,*a],env=env,capture_output=True,check=True,timeout=120).stdout
        STAGE='SOURCE_VERIFICATION';git('init','--quiet')
        git('fetch','--quiet','--depth=1',REPOSITORY,args.source_commit)
        if git('rev-parse','FETCH_HEAD').strip().decode()!=args.source_commit or git('show',args.source_commit+':'+SELF)!=Path(__file__).read_bytes():
            raise ValueError('SOURCE_REJECTED')
        def module(path,name):
            m=types.ModuleType(name);exec(compile(git('show',args.source_commit+':'+path),'<verified-module>','exec'),m.__dict__);return m
        operator=module('tools/game_audit_operator.py','verified_inspector')
        provenance=module('tools/game_audit_provenance.py','verified_provenance')
        manifest=json.loads(git('show',args.source_commit+':'+MANIFEST))
        for commit in EQUIVALENT:
            git('fetch','--quiet','--depth=1',REPOSITORY,commit)
            if git('rev-parse','FETCH_HEAD').strip().decode()!=commit:
                raise ValueError('BASELINE_REJECTED')
            validate_manifest(manifest,git('show',commit+':requirements.lock'))
        STAGE='PINNED_WHEEL_VERIFICATION';expected={}
        for package in manifest['packages']:
            expected[package['name']]={w['filename']:expected_files(download(w),w) for w in package['wheels']}
        roots=sorted({p.split('/')[0] for candidates in expected.values() for files in candidates.values() for p in files})
        STAGE='BOUND_SOURCE_VERIFICATION'
        binding=provenance.collect_source_evidence(operator,git,EQUIVALENT,{})
        if binding['sourceEquivalentCommits']!=sorted(EQUIVALENT):raise ValueError('BASELINE_REJECTED')
        meta=operator.inspect('gamerhq-bot')
        # Package code must not be supplied by an unreviewed mounted overlay.
        for mount in meta['mounts']:
            target=mount.get('Destination','').rstrip('/') or '/'
            lib='/usr/local/lib/python3.12/site-packages'
            if target=='/' or target==lib or target.startswith(lib+'/') or lib.startswith(target+'/'):
                raise ValueError('DEPENDENCY_MOUNT_REJECTED')
        def probe():
            raw=subprocess.run(['docker','exec','--interactive','--user',meta['user'],meta['id'],
                'python','-I','-S','-B','-c',PROBE],input=json.dumps({'roots':roots}),capture_output=True,text=True,check=True,timeout=60).stdout
            return validate_observed(raw,roots)
        STAGE='INSTALLED_CORE_HASHES';first=probe();rows=compare(expected,first['files'])
        if probe()!=first or operator.inspect('gamerhq-bot')!=meta:raise ValueError('DEPENDENCY_CHANGED')
        STAGE='UNCHANGED_SOURCE_VERIFICATION'
        final=provenance.collect_source_evidence(operator,git,EQUIVALENT,{})
        if final!=binding:raise ValueError('BASELINE_CHANGED')
        print(json.dumps(summary(rows,binding),sort_keys=True))


if __name__=='__main__':
    sys.dont_write_bytecode=True
    try:main()
    except Exception:
        # No paths, package contents, subprocess output, environment or tokens.
        raise SystemExit(json.dumps({'state':'GAMES_LEGACY_DEPENDENCY_UNAVAILABLE','stage':STAGE,
                                    'collectionExecuted':False,'legacyCollectionEligible':False})) from None
