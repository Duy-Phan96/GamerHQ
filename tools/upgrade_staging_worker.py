"""Owner-authorized source-only revision of the existing offline staging worker.

No SSH policy, credentials, dependencies, daemon, database or container is changed.
The restricted SSH identity deliberately cannot upgrade its own trusted code.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import tempfile
import uuid

ROOT=Path('/opt/gamerhq-staging')
WORKER=ROOT/'deployment-worker'
CONTROL=Path('/etc/gamerhq-staging')
PROFILE=CONTROL/'settings/profile.json'
POLICY=CONTROL/'policy.json'
STATE=Path('/var/lib/gamerhq-staging-bootstrap/worker-upgrades')
OLD='2e6a0ea40c40447fd3bae1a3b14fb845d6675b77'
PREVIOUS='1c8ec294491f089c85a4f919a8bea5bf74e8f1f8'
VERIFIER='8390d4aefdbb48f2ed1f27717320f14401166bec'
SUPPORTED_PREVIOUS=frozenset((OLD,PREVIOUS,VERIFIER))
REPOSITORY='https://github.com/Duy-Phan96/GamerHQ.git'

def protected(path,uid,private=False):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & (0o077 if private else 0o022):
        raise PermissionError('PROTECTED_METADATA_REQUIRED')
    if info.st_size > 65536:raise ValueError('METADATA_LIMIT')
    return json.loads(path.read_text())

def atomic(path,value,uid,gid,mode):
    if path.is_symlink() or path.parent.is_symlink():raise PermissionError('METADATA_REDIRECTION_REJECTED')
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.worker-revision-',delete=False) as output:
        temporary=Path(output.name)
        # Operate on the open inode; staging-owned settings/state directories
        # must never turn a pathname swap into a privileged chown/chmod.
        os.fchown(output.fileno(),uid,gid);os.fchmod(output.fileno(),mode)
        json.dump(value,output,sort_keys=True);output.flush();os.fsync(output.fileno())
    try:
        temporary.replace(path)
        descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:
        if temporary.exists():temporary.unlink()

def git(*args):
    environment={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8',
        'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0','GIT_OPTIONAL_LOCKS':'0'}
    result=subprocess.run(['git','-C',str(WORKER),*args],env=environment,capture_output=True,text=True,timeout=180)
    if result.returncode:raise RuntimeError('PUBLIC_SOURCE_GIT_REJECTED')
    return result.stdout.strip()

def protect_source(gid):
    for path in [WORKER,*WORKER.rglob('*')]:
        if path.is_symlink():
            if not path.is_relative_to(WORKER/'.venv'):raise PermissionError('SOURCE_SYMLINK_REJECTED')
            os.lchown(path,0,gid)
        else:
            os.chown(path,0,gid)
            path.chmod(0o750 if path.is_dir() or path.stat().st_mode & 0o100 else 0o640)

def verify_fixed(account,commit):
    uid=account.pw_uid
    code="""import json,os,sys
sys.path.insert(0,'/opt/gamerhq-staging/deployment-worker')
from tools.staging_dispatch import base_environment
os.environ.clear();os.environ.update(base_environment())
assert os.environ['BUILDX_CONFIG']=='/opt/gamerhq-staging/worker/buildx'
from hosts.gamerhq.skill_staging import validate_profile,verify_daemon
from tools.staging_configuration import validate_binding
from pathlib import Path
p=json.loads(Path('/etc/gamerhq-staging/settings/profile.json').read_text())
q=json.loads(Path('/etc/gamerhq-staging/policy.json').read_text())
validate_profile(p);validate_binding(p,q);verify_daemon(p)
assert p['workerEnabled'] is False and p['hostSourceCommit']==EXPECTED
print('FIXED_OFFLINE_WORKER_READY')
""".replace('EXPECTED',repr(commit))
    args=['runuser','-u','gamerhq-staging-bridge','--','env','-i','PATH=/usr/local/bin:/usr/bin:/bin',
        'HOME='+account.pw_dir,'XDG_RUNTIME_DIR=/run/user/'+str(uid),
        'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus',
        'systemd-run','--user','--pipe','--wait','--collect','--quiet','--unit=staging-worker-revision-'+uuid.uuid4().hex,
        str(WORKER/'.venv/bin/python'),'-I','-B','-']
    result=subprocess.run(args,input=code,capture_output=True,text=True,timeout=120)
    if result.returncode or result.stdout.strip() != 'FIXED_OFFLINE_WORKER_READY':
        raise RuntimeError('FIXED_WORKER_OFFLINE_VERIFICATION_FAILED')

def remove_marker(marker,nonce):
    value=protected(marker,0,private=True)
    if value != {'workerUpgrade':nonce}:raise PermissionError('MAINTENANCE_MARKER_MISMATCH')
    marker.unlink()

def upgrade(commit,approval):
    if platform.system() != 'Linux' or os.geteuid() != 0:raise PermissionError('INFRASTRUCTURE_OWNER_SESSION_REQUIRED')
    if approval != 'UPDATE ISOLATED STAGING WORKER':raise PermissionError('EXPLICIT_WORKER_REVISION_APPROVAL_REQUIRED')
    if not re.fullmatch('[0-9a-f]{40}',commit) or commit in SUPPORTED_PREVIOUS:raise ValueError('NEW_IMMUTABLE_COMMIT_REQUIRED')
    account=pwd.getpwnam('gamerhq-staging-bridge');uid,gid=account.pw_uid,account.pw_gid
    if uid<=0 or gid<=0 or account.pw_dir!='/var/lib/gamerhq-staging-bridge':raise PermissionError('STAGING_IDENTITY_MISMATCH')
    for path,owner in ((ROOT,0),(WORKER,0),(CONTROL,0),(ROOT/'worker',uid),(STATE.parent,0)):
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner or info.st_mode & 0o022:raise PermissionError('STAGING_DIRECTORY_NOT_PROTECTED')
    if (ROOT/'worker').stat().st_mode & 0o077:raise PermissionError('PRIVATE_STAGING_STATE_REQUIRED')
    STATE.mkdir(mode=0o700,exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_uid!=0 or STATE.stat().st_mode & 0o077:raise PermissionError('UPGRADE_STATE_NOT_PROTECTED')
    descriptor=os.open(ROOT/'worker/operation.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,uid) or info.st_mode & 0o022:raise PermissionError('OPERATION_LOCK_NOT_PROTECTED')
        os.fchown(descriptor,uid,gid)
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return apply_locked(commit,account)
    finally:os.close(descriptor)

def apply_locked(commit,account):
    uid,gid=account.pw_uid,account.pw_gid
    journal=STATE/(commit+'.json');marker=ROOT/'worker/access-revoked'
    transaction=protected(journal,0,private=True) if journal.exists() else None
    if transaction and transaction.get('state')=='APPLYING':
        # A terminated owner session leaves a deny marker. Restore the recorded
        # public metadata/source before retrying; no database recovery is involved.
        if transaction['old'] not in SUPPORTED_PREVIOUS or transaction['new']!=commit:raise PermissionError('UPGRADE_RECOVERY_BINDING_REJECTED')
        if not marker.exists() and not marker.is_symlink():
            recovered_policy=protected(POLICY,0);recovered_profile=protected(PROFILE,uid,private=True)
            recovered_head=git('rev-parse','HEAD')
            if recovered_head==commit and recovered_policy.get('hostSourceCommit')==commit and recovered_profile.get('hostSourceCommit')==commit:
                verify_fixed(account,commit)
                transaction['state']='APPLIED';atomic(journal,transaction,0,0,0o600)
                return receipt(commit,'ALREADY_CURRENT',transaction['old'])
            if recovered_head!=transaction['old'] or recovered_policy!=transaction['policy'] or recovered_profile!=transaction['profile']:
                raise PermissionError('UPGRADE_RECOVERY_MARKER_MISSING')
        else:
            if protected(marker,0,private=True)!={'workerUpgrade':transaction['nonce']}:raise PermissionError('UPGRADE_RECOVERY_MARKER_REJECTED')
            git('checkout','--detach',transaction['old']);protect_source(gid)
            atomic(POLICY,transaction['policy'],0,gid,0o640)
            atomic(PROFILE,transaction['profile'],uid,gid,0o600)
            remove_marker(marker,transaction['nonce'])
        transaction['state']='ROLLED_BACK';atomic(journal,transaction,0,0,0o600)
    profile=protected(PROFILE,uid,private=True);policy=protected(POLICY,0)
    expected={'root':str(ROOT),'configurationRoot':str(CONTROL),'deploymentUid':uid,
        'deploymentHome':account.pw_dir,'dockerHost':'unix:///run/user/'+str(uid)+'/docker.sock'}
    if any(policy.get(k)!=v or profile.get(k)!=v for k,v in expected.items()):raise PermissionError('STAGING_BINDING_MISMATCH')
    if policy.get('schemaVersion')!=2 or profile.get('workerEnabled') is not False:raise PermissionError('OFFLINE_WORKER_REQUIRED')
    if (WORKER/'.env').exists() or (WORKER/'.env').is_symlink():raise PermissionError('LOCAL_RUNTIME_ENVIRONMENT_REJECTED')
    if git('remote','get-url','origin')!=REPOSITORY or git('status','--porcelain'):raise PermissionError('CLEAN_PUBLIC_WORKER_SOURCE_REQUIRED')
    current=git('rev-parse','HEAD')
    if current==commit and policy.get('hostSourceCommit')==commit and profile.get('hostSourceCommit')==commit:
        if marker.exists():raise PermissionError('EXISTING_ACCESS_REVOCATION_PRESERVED')
        verify_fixed(account,commit)
        return receipt(commit,'ALREADY_CURRENT',transaction['old'] if transaction else None)
    if current not in SUPPORTED_PREVIOUS or policy.get('hostSourceCommit')!=current or profile.get('hostSourceCommit')!=current:raise PermissionError('EXPECTED_INSTALLED_REVISION_REQUIRED')
    old=current
    if marker.exists() or marker.is_symlink():raise PermissionError('EXISTING_ACCESS_REVOCATION_PRESERVED')
    git('fetch','--no-tags','origin',commit)
    if git('rev-parse',commit)!=commit:raise PermissionError('FETCHED_COMMIT_MISMATCH')
    public_script=Path(__file__).read_bytes()
    # Git's SHA-1 blob identifier binds delivery to the reviewed source tree;
    # the operator independently verifies the downloaded script with SHA-256.
    blob=hashlib.sha1(b'blob '+str(len(public_script)).encode()+b'\0'+public_script,usedforsecurity=False).hexdigest()
    if git('rev-parse',commit+':tools/upgrade_staging_worker.py')!=blob:raise PermissionError('VERIFIED_DELIVERY_SOURCE_MISMATCH')
    changed=git('diff','--name-only',old,commit).splitlines()
    allowed=('tools/','tests/','.github/workflows/','deploy/staging/','docs/')
    if any(not p.startswith(allowed) and p not in ('hosts/gamerhq/skill_deployment_docker.py','hosts/gamerhq/skill_staging.py') for p in changed):raise PermissionError('SOURCE_ONLY_REVISION_SCOPE_REJECTED')
    for name in ('requirements.lock','requirements-skills.lock','Dockerfile','pyproject.toml'):
        if git('rev-parse',old+':'+name)!=git('rev-parse',commit+':'+name):raise PermissionError('DEPENDENCY_OR_IMAGE_CHANGE_NOT_AUTHORIZED')
    if any(line.startswith('120000 ') for line in git('ls-tree','-r',commit).splitlines()):raise PermissionError('SOURCE_SYMLINK_REJECTED')
    nonce=uuid.uuid4().hex
    transaction={'old':old,'new':commit,'nonce':nonce,'state':'APPLYING','policy':policy,'profile':profile}
    atomic(journal,transaction,0,0,0o600)
    atomic(marker,{'workerUpgrade':nonce},0,0,0o600)
    try:
        git('checkout','--detach',commit);protect_source(gid)
        buildx=ROOT/'worker/buildx'
        if buildx.is_symlink():raise PermissionError('BUILDX_STATE_REDIRECTION_REJECTED')
        if not buildx.exists():
            buildx.mkdir(mode=0o700);os.chown(buildx,uid,gid)
        info=buildx.stat()
        if not buildx.is_dir() or info.st_uid!=uid or info.st_mode & 0o077:raise PermissionError('PRIVATE_BUILDX_STATE_REQUIRED')
        atomic(POLICY,{**policy,'hostSourceCommit':commit},0,gid,0o640)
        # Retain the old image/data, but never present its previous source
        # binding as a freshly verified base for this worker revision.
        atomic(PROFILE,{**profile,'hostSourceCommit':commit,'baseImage':'REQUIRES_REVIEWED_BASE_REBUILD',
            'verificationFile':str(ROOT/'worker/verification.json'),
            'offlineVerificationFile':str(ROOT/'worker/offline-verification.json')},uid,gid,0o600)
        if git('status','--porcelain'):raise RuntimeError('REVISED_SOURCE_NOT_CLEAN')
        verify_fixed(account,commit)
    except Exception:
        git('checkout','--detach',old);protect_source(gid)
        atomic(POLICY,policy,0,gid,0o640);atomic(PROFILE,profile,uid,gid,0o600)
        remove_marker(marker,nonce)
        transaction['state']='ROLLED_BACK';atomic(journal,transaction,0,0,0o600)
        raise RuntimeError('WORKER_REVISION_ROLLED_BACK') from None
    remove_marker(marker,nonce)
    transaction['state']='APPLIED';atomic(journal,transaction,0,0,0o600)
    import syslog
    syslog.openlog('gamerhq-staging',syslog.LOG_PID,syslog.LOG_AUTHPRIV)
    syslog.syslog(syslog.LOG_NOTICE,json.dumps({'operation':'worker-source-revision','state':'APPLIED','id':nonce,'commit':commit}))
    return receipt(commit,'APPLIED',old)

def receipt(commit,state,previous):
    return {'operation':'worker-source-revision','state':state,'previousCommit':previous,'workerCommit':commit,
        'workerEnabled':False,'gatewayStarted':False,'productionChanged':False,'sshPolicyChanged':False,
        'credentialsChanged':False,'dependenciesChanged':False,'databaseChanged':False,'isolationPolicyChanged':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True);parser.add_argument('--approval',required=True)
    args=parser.parse_args()
    try:print(json.dumps(upgrade(args.commit,args.approval),indent=2))
    except Exception as error:
        # Codes/types only. Never expose command output or configuration values.
        code=str(error) if re.fullmatch('[A-Z_]{3,100}',str(error)) else type(error).__name__
        raise SystemExit(json.dumps({'workerRevisionError':code})) from None
