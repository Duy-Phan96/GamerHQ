"""Disposable hosted CI: source revision, retained data/key and real SSH evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from tools.staging_ssh_client import invoke,StagingAccessError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True);args=parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.geteuid()!=0:raise PermissionError('DISPOSABLE_CI_ONLY')
    identity=pwd.getpwnam('gamerhq-staging-bridge');uid,gid=identity.pw_uid,identity.pw_gid
    assert uid>0
    root=Path('/opt/gamerhq-staging');control=Path('/etc/gamerhq-staging')
    keys=Path(identity.pw_dir)/'.ssh/authorized_keys'
    entry=Path('/usr/local/libexec/gamerhq-staging-entry')
    snapshots={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (keys,entry,control/'docker-client/config.json',control/'settings/worker.env',control/'settings/bot.env')}
    database=root/'data/gamerhq.db'
    database.write_bytes(b'synthetic retained staging database sentinel')
    os.chown(database,uid,gid);database.chmod(0o600)
    previous=hashlib.sha256(database.read_bytes()).hexdigest()
    # Exercise actual source/metadata rollback after a failed candidate check,
    # without adding a failure-injection switch to the shipped owner installer.
    from tools import upgrade_staging_worker as revision
    original_verify=revision.verify_fixed
    def reject_candidate(*unused):raise RuntimeError('synthetic offline verification failure')
    revision.verify_fixed=reject_candidate
    try:
        try:revision.upgrade(args.commit,'UPDATE ISOLATED STAGING WORKER')
        except RuntimeError as error:assert str(error)=='WORKER_REVISION_ROLLED_BACK'
        else:raise AssertionError('Candidate verification must fail closed')
    finally:revision.verify_fixed=original_verify
    assert json.loads((control/'policy.json').read_text())['hostSourceCommit']==revision.OLD
    assert not (root/'worker/access-revoked').exists()
    command=[sys.executable,str(Path(__file__).with_name('upgrade_staging_worker.py')),'--commit',args.commit,'--approval','UPDATE ISOLATED STAGING WORKER']
    for expected in ('APPLIED','ALREADY_CURRENT'):
        result=subprocess.run(command,capture_output=True,text=True,timeout=240)
        if result.returncode:raise RuntimeError('Disposable source revision failed: '+result.stderr[-2000:])
        receipt=json.loads(result.stdout)
        assert receipt['state']==expected and receipt['workerCommit']==args.commit
        assert not any(receipt[k] for k in ('workerEnabled','gatewayStarted','productionChanged','sshPolicyChanged','credentialsChanged','dependenciesChanged','databaseChanged'))
    assert previous==hashlib.sha256(database.read_bytes()).hexdigest()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in snapshots.items())
    child=r'''
import json,os,pathlib,subprocess,sys,tempfile
sys.path.insert(0,'/opt/gamerhq-staging/deployment-worker')
from tools.staging_dispatch import base_environment
os.environ.clear();os.environ.update(base_environment())
assert os.environ['BUILDX_CONFIG']=='/opt/gamerhq-staging/worker/buildx'
with tempfile.TemporaryDirectory(dir='/opt/gamerhq-staging/worker') as folder:
 root=pathlib.Path(folder)
 (root/'Dockerfile').write_text('FROM scratch\nLABEL gamerhq.fixture="installed-fixed-worker"\n')
 subprocess.run(['docker','build','--iidfile',str(root/'image-id'),str(root)],capture_output=True,text=True,check=True,timeout=180)
 assert (root/'image-id').read_text().startswith('sha256:')
print('INSTALLED_WORKER_BUILDX_PASS')
'''
    launch=['runuser','-u','gamerhq-staging-bridge','--','env','-i','PATH=/usr/local/bin:/usr/bin:/bin',
        'HOME='+identity.pw_dir,'XDG_RUNTIME_DIR=/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus',
        'systemd-run','--user','--pipe','--wait','--collect','--quiet','--unit=staging-revision-ci-'+uuid.uuid4().hex,
        str(root/'deployment-worker/.venv/bin/python'),'-I','-B','-']
    result=subprocess.run(launch,input=child,capture_output=True,text=True,timeout=240)
    if result.returncode:raise RuntimeError('Disposable installed build failed: '+result.stderr[-2000:])
    assert result.stdout.strip()=='INSTALLED_WORKER_BUILDX_PASS'
    with tempfile.TemporaryDirectory(prefix='staging-revision-sshd-') as directory:
        folder=Path(directory)
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(folder/'host')],check=True)
        host_key=' '.join((folder/'host.pub').read_text().split()[:2])
        config=folder/'sshd_config'
        config.write_text('Port 22224\nListenAddress 127.0.0.1\nHostKey '+str(folder/'host')+'\nPidFile '+str(folder/'pid')+'\n'
            'UsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\n'
            'PermitRootLogin no\nAllowUsers gamerhq-staging-bridge\nStrictModes yes\nLogLevel ERROR\nPermitUserRC no\nDisableForwarding yes\n')
        server=subprocess.Popen(['/usr/sbin/sshd','-D','-f',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(30):
                try:
                    with socket.create_connection(('127.0.0.1',22224),timeout=1):break
                except OSError:time.sleep(.1)
            request={'version':1,'phase':'prepare','operation':'verify-offline','approval':''}
            try:
                invoke(request,host='127.0.0.1',port=22224,private_key=Path('/tmp/staging-prebuild-ci-key').read_text(),known_hosts='[127.0.0.1]:22224 '+host_key)
            except StagingAccessError as error:
                proof=error.evidence
            else:raise AssertionError('Unconfigured candidate must fail offline verification')
            assert proof['authenticated'] is True and proof['workerFailure']=='PREPARATION_REJECTED',proof
            ident=proof['workerAuditId'];assert len(ident)==32
            audit=subprocess.run(['journalctl','-t','gamerhq-staging','--since','-10min','--no-pager','--output=cat'],capture_output=True,text=True,check=True).stdout
            assert ident in audit
        finally:
            server.terminate();server.wait(timeout=10)
    print(json.dumps({'sourceRevision':'PASS','failedVerificationRollback':'PASS','retry':'PASS','retainedDatabase':'PASS','sshIdentityAndPolicyUnchanged':True,
        'protectedConfigurationUnchanged':True,'installedBuildx':'PASS','restrictedSshError':proof['workerFailure'],
        'auditCorrelation':'PASS','gatewayStarted':False,'productionChanged':False}))

if __name__=='__main__':main()
