"""Owner-run bounded pre-build diagnostics; no Gateway, build, secrets or policy writes.

The forced SSH identity deliberately cannot execute new diagnostic code. This
standalone collector must be run by the infrastructure owner (or in disposable
CI). Its child executes only as the existing staging account, inside that
account's existing systemd sandbox. No production command or path is accepted.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import uuid


CHILD = r'''
import importlib, json, os, pathlib, re, subprocess, sys
ROOT = pathlib.Path('/opt/gamerhq-staging/deployment-worker')
CONTROL = pathlib.Path('/etc/gamerhq-staging')
HOME = '/var/lib/gamerhq-staging-bridge'
EXPECTED = 'EXPECTED_WORKER_COMMIT'
profile = json.loads((CONTROL / 'settings/profile.json').read_text())
uid = os.geteuid()
assert uid > 0 and uid == profile['deploymentUid']
assert profile['dockerMode'] == 'rootless' and profile['deploymentHome'] == HOME
assert profile['dockerHost'] == 'unix:///run/user/' + str(uid) + '/docker.sock'
os.environ.clear()
os.environ.update({'PATH':'/usr/local/bin:/usr/bin:/bin', 'HOME':HOME, 'LANG':'C.UTF-8',
 'PYTHONDONTWRITEBYTECODE':'1', 'GIT_CONFIG_GLOBAL':str(CONTROL / 'gitconfig'), 'GIT_OPTIONAL_LOCKS':'0',
 'DOCKER_HOST':profile['dockerHost'], 'DOCKER_CONFIG':str(CONTROL / 'docker-client'),
 'XDG_RUNTIME_DIR':'/run/user/' + str(uid), 'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/' + str(uid) + '/bus'})
report = {'schemaVersion':1, 'uid':uid, 'expectedWorkerCommit':EXPECTED,
 'preBuildChecksPassed':False, 'dockerBuildInvoked':False, 'gatewayStarted':False,
 'credentialFilesOpened':False, 'productionChanged':False, 'trace':[]}
original_run = subprocess.run
def category(stderr):
 text = stderr.lower()
 for needle, code in [('dubious ownership','GIT_OWNERSHIP_REJECTED'), ('read-only file system','SANDBOX_READ_ONLY'),
  ('permission denied','PERMISSION_DENIED'), ('not a git repository','GIT_REPOSITORY_UNAVAILABLE'),
  ('cannot connect to the docker daemon','ROOTLESS_DAEMON_UNAVAILABLE')]:
  if needle in text: return code
 return 'COMMAND_REJECTED'
def read(args):
 try:
  value = original_run(args, capture_output=True, text=True, timeout=30)
  return value, None if value.returncode == 0 else category(value.stderr)
 except (OSError, subprocess.TimeoutExpired):
  return None, 'COMMAND_UNAVAILABLE_OR_TIMEOUT'
source = {}
for name, tail in [('head',['rev-parse','HEAD']), ('status',['status','--porcelain']), ('origin',['remote','get-url','origin'])]:
 value, error = read(['git','-C',str(ROOT),*tail])
 source[name + 'Error'] = error
 if value is not None and value.returncode == 0:
  if name == 'head': source['commit'] = value.stdout.strip() if re.fullmatch('[0-9a-f]{40}',value.stdout.strip()) else None
  elif name == 'status': source['clean'] = not value.stdout.strip()
  else: source['expectedOrigin'] = value.stdout.strip() == 'https://github.com/Duy-Phan96/GamerHQ.git'
source['localEnvFilePresent'] = (ROOT / '.env').exists()
report['source'] = source
report['profileSourceMatchesExpected'] = profile['hostSourceCommit'] == EXPECTED
report['capabilities'] = {}
for name, command in [('buildx',['docker','buildx','version']), ('compose',['docker','compose','version','--short'])]:
 value, error = read(command)
 item = {'available': value is not None and value.returncode == 0, 'error':error}
 if item['available']:
  version = re.search(r'\bv?(\d+\.\d+\.\d+)\b',value.stdout)
  item['version'] = version.group(1) if version else None
 report['capabilities'][name] = item
value, error = read(['getenforce'])
report['selinux'] = value.stdout.strip() if value is not None and value.stdout.strip() in ('Enforcing','Permissive','Disabled') else 'Unknown'
class BuildBoundaryReached(Exception): pass
def checked_run(args, **kwargs):
 if args[:2] == ['docker','build']:
  report['trace'].append({'command':'docker-build-boundary','executed':False})
  raise BuildBoundaryReached()
 if args[:3] == ['git','-C',str(ROOT)]:
  label = 'git-' + '-'.join(args[3:5])
 elif args[:2] == ['docker','info']:
  label = 'rootless-docker-info'
 else:
  raise PermissionError('DIAGNOSTIC_COMMAND_NOT_ALLOWED')
 event = {'command':label}
 report['trace'].append(event)
 try:
  result = original_run(args, **kwargs)
  event['exitCode'] = result.returncode
  return result
 except subprocess.CalledProcessError as error:
  event.update(exitCode=error.returncode, error=category(error.stderr or ''))
  raise
try:
 # Refuse to execute a different/unreviewed or dirty worker source. No code from
 # bot.env, worker.env, a profile-selected script or an SSH caller is imported.
 if source.get('commit') != EXPECTED or source.get('clean') is not True or source.get('expectedOrigin') is not True or source['localEnvFilePresent']:
  report['preBuildError'] = 'WORKER_SOURCE_PREFLIGHT_REJECTED'
 elif not report['profileSourceMatchesExpected']:
  report['preBuildError'] = 'PROFILE_SOURCE_REVIEW_REQUIRED'
 else:
  sys.path.insert(0,str(ROOT))
  module = importlib.import_module('tools.skill_staging')
  assert pathlib.Path(module.__file__).resolve() == ROOT / 'tools/skill_staging.py'
  subprocess.run = checked_run
  try:
   module.build_base(profile,'BUILD ISOLATED STAGING BASE')
   report['preBuildError'] = 'UNEXPECTED_PREFLIGHT_COMPLETION'
  except BuildBoundaryReached:
   report['preBuildChecksPassed'] = True
  except Exception as error:
   report['preBuildError'] = type(error).__name__
  finally:
   subprocess.run = original_run
except Exception as error:
 report['preBuildError'] = type(error).__name__
print(json.dumps(report,sort_keys=True))
'''


def collect(expected_commit):
    if platform.system() != 'Linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise PermissionError('OWNER_ROOT_SESSION_REQUIRED')
    if not re.fullmatch(r'[0-9a-f]{40}', expected_commit):
        raise ValueError('IMMUTABLE_WORKER_COMMIT_REQUIRED')
    import pwd
    identity = pwd.getpwnam('gamerhq-staging-bridge')
    if identity.pw_uid <= 0 or identity.pw_dir != '/var/lib/gamerhq-staging-bridge':
        raise PermissionError('STAGING_IDENTITY_MISMATCH')
    policy_path = Path('/etc/gamerhq-staging/policy.json')
    info = policy_path.stat()
    if policy_path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError('STAGING_POLICY_NOT_PROTECTED')
    policy = json.loads(policy_path.read_text())
    if policy.get('deploymentUid') != identity.pw_uid or policy.get('root') != '/opt/gamerhq-staging':
        raise PermissionError('STAGING_POLICY_IDENTITY_MISMATCH')
    values = {'PATH':'/usr/local/bin:/usr/bin:/bin', 'HOME':identity.pw_dir,
        'XDG_RUNTIME_DIR':'/run/user/' + str(identity.pw_uid),
        'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/' + str(identity.pw_uid) + '/bus'}
    args = ['runuser','-u','gamerhq-staging-bridge','--','env','-i',
        *[key + '=' + value for key,value in values.items()],
        'systemd-run','--user','--pipe','--wait','--collect','--quiet',
        '--unit=gamerhq-staging-prebuild-diagnostic-' + uuid.uuid4().hex,
        '--working-directory=/opt/gamerhq-staging/deployment-worker',
        '/opt/gamerhq-staging/deployment-worker/.venv/bin/python','-I','-B','-']
    result = subprocess.run(args, input=CHILD.replace('EXPECTED_WORKER_COMMIT', expected_commit),
        capture_output=True, text=True, timeout=180)
    if result.returncode or len(result.stdout.encode()) > 65536:
        raise RuntimeError('BOUNDED_DIAGNOSTIC_CHILD_FAILED')
    report = json.loads(result.stdout)
    if report.get('dockerBuildInvoked') is not False or report.get('gatewayStarted') is not False:
        raise RuntimeError('DIAGNOSTIC_BOUNDARY_REJECTED')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-worker-commit', required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.expected_worker_commit), indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Fixed codes or exception types only; raw stderr and private values stay private.
        code = str(error) if str(error) in {'OWNER_ROOT_SESSION_REQUIRED','IMMUTABLE_WORKER_COMMIT_REQUIRED',
            'STAGING_IDENTITY_MISMATCH','STAGING_POLICY_NOT_PROTECTED','STAGING_POLICY_IDENTITY_MISMATCH',
            'BOUNDED_DIAGNOSTIC_CHILD_FAILED','DIAGNOSTIC_BOUNDARY_REJECTED'} else type(error).__name__
        raise SystemExit(json.dumps({'stagingDiagnosticError':code})) from None
