"""Disposable hosted CI only: reproduce read-only Buildx state and verify repair."""
import json
import os
import pwd
import subprocess
import uuid

CHILD = r'''
import json, os, pathlib, subprocess, tempfile
uid=os.geteuid()
assert uid > 0
home='/var/lib/gamerhq-staging-bridge'
work=pathlib.Path('/opt/gamerhq-staging/worker')
config=pathlib.Path('/etc/gamerhq-staging/docker-client')
assert config.stat().st_uid == 0 and not config.stat().st_mode & 0o022
environment={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':home,'LANG':'C.UTF-8',
 'DOCKER_HOST':'unix:///run/user/'+str(uid)+'/docker.sock','DOCKER_CONFIG':str(config),
 'XDG_RUNTIME_DIR':'/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/'+str(uid)+'/bus'}
with tempfile.TemporaryDirectory(dir=work,prefix='buildx-ci-') as directory:
 root=pathlib.Path(directory)
 (root/'Dockerfile').write_text('FROM scratch\nLABEL gamerhq.fixture="buildx-permissions"\n')
 command=['docker','build','--progress=plain','--iidfile',str(root/'image-id'),str(root)]
 legacy=subprocess.run(command,env=environment,capture_output=True,text=True,timeout=180)
 blocked=legacy.returncode != 0 and 'buildx' in legacy.stderr.lower() and any(
  text in legacy.stderr.lower() for text in ('permission denied','read-only file system'))
 if not blocked:
  raise RuntimeError('Disposable legacy reproduction differed: '+legacy.stderr[-2000:])
 state=work/'buildx';state.mkdir(mode=0o700,exist_ok=True)
 fixed=subprocess.run(command,env={**environment,'BUILDX_CONFIG':str(state)},capture_output=True,text=True,timeout=180)
 if fixed.returncode:
  raise RuntimeError('Disposable isolated Buildx repair failed: '+fixed.stderr[-2000:])
 assert (root/'image-id').read_text().startswith('sha256:')
 assert config.stat().st_uid == 0 and not config.stat().st_mode & 0o022
 print(json.dumps({'legacyFailure':'BUILDX_STATE_NOT_WRITABLE','fixedBuild':'PASS',
  'protectedDockerConfigStillReadOnly':True,'gatewayStarted':False,'productionChanged':False}))
'''

def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
        raise PermissionError('DISPOSABLE_HOSTED_CI_ONLY')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid = account.pw_uid
    assert uid > 0
    args = ['runuser','-u','gamerhq-staging-bridge','--','env','-i',
        'HOME='+account.pw_dir,'PATH=/usr/local/bin:/usr/bin:/bin',
        'XDG_RUNTIME_DIR=/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus',
        'systemd-run','--user','--pipe','--wait','--collect','--quiet',
        '--unit=staging-buildx-fixture-'+uuid.uuid4().hex,
        '/opt/gamerhq-staging/deployment-worker/.venv/bin/python','-I','-B','-']
    result = subprocess.run(args,input=CHILD,capture_output=True,text=True,timeout=420)
    if result.returncode:
        raise RuntimeError('Disposable credential-free fixture: '+result.stderr[-3000:])
    print(result.stdout,end='')

if __name__ == '__main__':
    main()
