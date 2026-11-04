"""Read-only, bounded staging facts. No credential or database content is read."""
import os
from pathlib import Path
import subprocess

def _mapped_uid():
    try:
        with open('/etc/subuid',encoding='utf-8') as source:text=source.read(65537)
        if len(text)>65536:return None
        rows=[line.split(':') for line in text.splitlines() if line.startswith('gamerhq-staging-bridge:')]
        if len(rows)!=1 or int(rows[0][2])<65536:return None
        return int(rows[0][1])+10000
    except (OSError,ValueError,IndexError):return None

def _storage(path,worker_uid,mapped_uid):
    result={'present':False}
    try:
        if path.is_symlink():return {'present':True,'symlinkRejected':True}
        info=path.stat()
        result.update(present=True,mode=info.st_mode & 0o777,ownerIsWorker=info.st_uid==worker_uid,
            ownerIsContainerUid=mapped_uid is not None and info.st_uid==mapped_uid)
        try:result['containerFileLabel']=b':container_file_t:' in os.getxattr(path,'security.selinux')
        except (OSError,AttributeError):result['containerFileLabel']=None
        result['mappedAclReadWrite']=None
        if mapped_uid is not None:
            acl=subprocess.run(['getfacl','--omit-header','--numeric',str(path)],capture_output=True,text=True,timeout=10)
            if acl.returncode==0 and len(acl.stdout)<16384:
                entries=dict(line.split(':',2)[1:] for line in acl.stdout.splitlines() if line.startswith('user:') and line.split(':',2)[1])
                mask=next((line.split(':',2)[2].split()[0] for line in acl.stdout.splitlines() if line.startswith('mask::')),'rwx')
                entry=entries.get(str(mapped_uid),'').split('#',1)[0].strip()
                result['mappedAclReadWrite']=all(value in entry and value in mask for value in ('r','w'))
        return result
    except (OSError,ValueError,subprocess.TimeoutExpired):return result

def inspect_staging(profile):
    from hosts.gamerhq.skill_staging import validate_profile
    from hosts.gamerhq.skill_deployment_docker import IMAGE
    validate_profile(profile)
    mapped=_mapped_uid()
    try:
        value=Path('/sys/fs/selinux/enforce').read_text().strip()
        selinux={'1':'Enforcing','0':'Permissive'}.get(value,'Unknown')
    except OSError:selinux='Unavailable'
    return {'schemaVersion':1,'baseImageConfigured':bool(IMAGE.fullmatch(profile['baseImage'])),
        'verificationParentsWritable':all(os.access(Path(profile[key]).parent,os.W_OK) for key in ('verificationFile','offlineVerificationFile')),
        'subordinateMappingPresent':mapped is not None,'selinuxObserved':selinux,
        'storage':{name:_storage(path,profile['deploymentUid'],mapped) for name,path in (
            ('data',Path(profile['database']).parent),('database',Path(profile['database'])),('backups',Path(profile['backupRoot'])))},
        'credentialsPublished':False,'databaseContentsRead':False,'gatewayStarted':False,'productionChanged':False}
