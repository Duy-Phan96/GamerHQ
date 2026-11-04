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

def public_artifacts(root,control,uid,gid,commit):
    """Only public CI fixtures; no private artifact/token is used or uploaded."""
    source=Path(__file__).resolve().parents[1]
    environment={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8','PIP_CONFIG_FILE':'/dev/null'}
    with tempfile.TemporaryDirectory(prefix='staging-public-wheels-') as directory:
        for project in (source,source/'examples/external-skill-template'):
            result=subprocess.run([sys.executable,'-m','pip','wheel','--index-url','https://pypi.org/simple','--no-deps',
                '--disable-pip-version-check','--wheel-dir',directory,str(project)],env=environment,capture_output=True,text=True,timeout=180)
            if result.returncode:raise RuntimeError('Public fixture wheel build: '+result.stderr[-2000:])
        hashes={}
        for name in ('gamerhq_skill_sdk-0.3.0-py3-none-any.whl','gamerhq_skill_example-0.1.0-py3-none-any.whl'):
            data=(Path(directory)/name).read_bytes();target=root/'artifacts'/name
            target.write_bytes(data);os.chown(target,uid,gid);target.chmod(0o600)
            hashes[name]=hashlib.sha256(data).hexdigest()
    profile_path=control/'settings/profile.json';profile=json.loads(profile_path.read_text())
    sdk={'repository':'Duy-Phan96/GamerHQ','distribution':'gamerhq-skill-sdk','version':'0.3.0','sourceReviewed':True,
        'reviewedCommit':commit,'sha256':hashes['gamerhq_skill_sdk-0.3.0-py3-none-any.whl']}
    release={**sdk,'distribution':'gamerhq-skill-example','version':'0.1.0','skillId':'example-skill','runtimeApi':'1',
        'capabilities':[],'migrationMode':'none','sha256':hashes['gamerhq_skill_example-0.1.0-py3-none-any.whl']}
    profile.update(sdkSha256=sdk['sha256']);profile['approvedCapabilities']['example-skill']=[]
    for path,value in ((Path(profile['sdkReleaseFile']),sdk),(Path(profile['releaseFile']),release),(profile_path,profile)):
        path.write_text(json.dumps(value));os.chown(path,uid,gid);path.chmod(0o600)
    return release

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True);args=parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.geteuid()!=0:raise PermissionError('DISPOSABLE_CI_ONLY')
    identity=pwd.getpwnam('gamerhq-staging-bridge');uid,gid=identity.pw_uid,identity.pw_gid
    assert uid>0
    root=Path('/opt/gamerhq-staging');control=Path('/etc/gamerhq-staging')
    launch=['runuser','-u','gamerhq-staging-bridge','--','env','-i','PATH=/usr/local/bin:/usr/bin:/bin',
        'HOME='+identity.pw_dir,'XDG_RUNTIME_DIR=/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus',
        'systemd-run','--user','--pipe','--wait','--collect','--quiet','--unit=staging-revision-ci-'+uuid.uuid4().hex,
        str(root/'deployment-worker/.venv/bin/python'),'-I','-B','-']
    old_record_probe="""import sys
sys.path.insert(0,'/opt/gamerhq-staging/deployment-worker')
from tools.skill_staging import write_record
try:write_record('/opt/gamerhq-staging/offline-verification.json',{'publicFixture':True})
except OSError as error:assert error.errno in (1,13,30)
else:raise AssertionError('Original root-owned proof destination must be unwritable')
print('ORIGINAL_RECORD_LOCATION_REJECTED')
"""
    old_result=subprocess.run(launch,input=old_record_probe,capture_output=True,text=True,timeout=120)
    if old_result.returncode:raise RuntimeError('Original record-location reproduction: '+old_result.stderr[-2000:])
    assert old_result.stdout.strip()=='ORIGINAL_RECORD_LOCATION_REJECTED'
    keys=Path(identity.pw_dir)/'.ssh/authorized_keys'
    entry=Path('/usr/local/libexec/gamerhq-staging-entry')
    snapshots={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (keys,entry,control/'docker-client/config.json',control/'settings/worker.env',control/'settings/bot.env')}
    database=root/'data/gamerhq.db'
    database.write_bytes(b'synthetic retained staging database sentinel')
    os.chown(database,uid,gid);database.chmod(0o600)
    previous=hashlib.sha256(database.read_bytes()).hexdigest()
    installed_before=json.loads((control/'policy.json').read_text())['hostSourceCommit']
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
    assert json.loads((control/'policy.json').read_text())['hostSourceCommit']==installed_before
    assert not (root/'worker/access-revoked').exists()
    command=[sys.executable,str(Path(__file__).with_name('upgrade_staging_worker.py')),'--commit',args.commit,'--approval','UPDATE ISOLATED STAGING WORKER']
    for expected in ('APPLIED','ALREADY_CURRENT'):
        result=subprocess.run(command,capture_output=True,text=True,timeout=240)
        if result.returncode:raise RuntimeError('Disposable source revision failed: '+result.stderr[-2000:])
        receipt=json.loads(result.stdout)
        assert receipt['state']==expected and receipt['workerCommit']==args.commit
        assert json.loads((control/'settings/profile.json').read_text())['baseImage']=='REQUIRES_REVIEWED_BASE_REBUILD'
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
    # Exercise the actual verifier, its phase codes and read-only inspection
    # using only this disposable runner's synthetic data and rootless engine.
    child=child.replace("print('INSTALLED_WORKER_BUILDX_PASS')",r'''
from hosts.gamerhq.skill_staging import verify_docker
from tools.staging_errors import StagingFailure
from tools.staging_inspect import inspect_staging
from tools.skill_staging import write_record
subprocess.run(['docker','pull','python:3.12-slim'],capture_output=True,text=True,check=True,timeout=180)
image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','python:3.12-slim'],text=True).strip()
profile_path=pathlib.Path('/etc/gamerhq-staging/settings/profile.json')
p=json.loads(profile_path.read_text());p['baseImage']=image
write_record(profile_path,p)
report=inspect_staging(p)
assert report['storage']['database']['mappedAclReadWrite'] is False
try:verify_docker(p)
except StagingFailure as error:assert error.evidence['stagingFailure']=='STAGING_DATABASE_ACCESS_REJECTED',error.evidence
else:raise AssertionError('Masked database ACL must be rejected')
print('INSTALLED_WORKER_BUILDX_PASS')
''')
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
            assert proof['authenticated'] is True and proof['workerFailure']=='STAGING_DATABASE_ACCESS_REJECTED',proof
            assert proof['workerStep']=='storage-probe' and proof['workerExitCode']==42
            ident=proof['workerAuditId'];assert len(ident)==32
            audit=subprocess.run(['journalctl','-t','gamerhq-staging','--since','-10min','--no-pager','--output=cat'],capture_output=True,text=True,check=True).stdout
            assert ident in audit
            # The full offline sequence must persist proof before building the
            # candidate. Unit verification alone missed the original RO path.
            database.chmod(0o660)
            release=public_artifacts(root,control,uid,gid,args.commit)
            for operation,approval in (('build-base','BUILD ISOLATED STAGING BASE'),('verify-offline',''),('build','')):
                output=invoke({'version':1,'phase':'prepare','operation':operation,'approval':approval},
                    host='127.0.0.1',port=22224,private_key=Path('/tmp/staging-prebuild-ci-key').read_text(),known_hosts='[127.0.0.1]:22224 '+host_key)
                result=json.loads(output.splitlines()[-1])
                assert result['result']=='PASS' and result['workerEnabled'] is False and result['gatewayMayStart'] is False
            final_profile=json.loads((control/'settings/profile.json').read_text())
            offline=Path(final_profile['offlineVerificationFile'])
            assert offline.parent==root/'worker' and offline.stat().st_uid==uid and offline.stat().st_mode & 0o077==0
            assert json.loads(offline.read_text())['verified'] is True
            candidate=json.loads((root/'worker/initial-candidate.json').read_text())
            assert candidate['healthy'] is True and candidate['isolated'] is True and candidate['gatewayStarted'] is False
            assert candidate['artifactSha256']==release['sha256']
            assert not (root/'offline-verification.json').exists()
        finally:
            server.terminate();server.wait(timeout=10)
    assert previous==hashlib.sha256(database.read_bytes()).hexdigest()
    print(json.dumps({'sourceRevision':'PASS','failedVerificationRollback':'PASS','retry':'PASS','retainedDatabase':'PASS','sshIdentityAndPolicyUnchanged':True,
        'protectedConfigurationUnchanged':True,'installedBuildx':'PASS','restrictedSshError':proof['workerFailure'],
        'auditCorrelation':'PASS','verifiedBindAccess':'PASS','originalProofWriteRejected':'PASS','offlineProofPersisted':'PASS',
        'fullOfflineCandidate':'PASS','previousSource':installed_before,'gatewayStarted':False,'productionChanged':False}))

if __name__=='__main__':main()
