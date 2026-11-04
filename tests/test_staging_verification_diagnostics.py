import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hosts.gamerhq.skill_staging import verify_docker,verify_offline
from tools.staging_errors import StagingFailure,decode_evidence
from tools.staging_inspect import inspect_staging
from test_skill_staging import staging

def prepare_storage(profile):
    database=Path(profile['database']);database.parent.mkdir(exist_ok=True)
    database.write_bytes(b'retained-private-dataset')
    Path(profile['backupRoot']).mkdir(exist_ok=True)
    return database

@pytest.mark.parametrize('failure,code,step',[
    ('compose','STAGING_COMPOSE_CONFIG_REJECTED','compose-config'),
    (41,'STAGING_DATA_ACCESS_REJECTED','storage-probe'),
    (42,'STAGING_DATABASE_ACCESS_REJECTED','storage-probe'),
    (43,'STAGING_BACKUPS_ACCESS_REJECTED','storage-probe'),
    (125,'STAGING_STORAGE_PROBE_FAILED','storage-probe'),
])
def test_actual_verification_command_failures_keep_stage_and_hide_private_output(staging,failure,code,step):
    profile,_,_,_=staging;database=prepare_storage(profile)
    original=database.read_bytes()
    def run(command,**kwargs):
        if command[1]=='compose' and failure=='compose':
            raise subprocess.CalledProcessError(1,command,stderr='secret-composition-and-token')
        if command[1]=='run' and failure!='compose':
            raise subprocess.CalledProcessError(failure,command,stderr='secret-container-output')
        return SimpleNamespace(stdout='',returncode=0)
    with patch('hosts.gamerhq.skill_staging.verify_daemon'),patch('hosts.gamerhq.skill_staging.subprocess.run',side_effect=run):
        with pytest.raises(StagingFailure) as caught:verify_docker(profile)
    value=decode_evidence(str(caught.value))
    assert value['stagingFailure']==code and value['step']==step
    assert 'secret' not in str(caught.value)
    assert database.read_bytes()==original

def test_probe_uses_managed_volume_labels_and_preserves_isolation_and_data(staging):
    profile,compose,_,_=staging;database=prepare_storage(profile)
    digest=hashlib.sha256(database.read_bytes()).hexdigest()
    observed=[]
    def run(command,**kwargs):
        observed.append(command)
        if command[1]=='run':
            volumes=[command[i+1] for i,value in enumerate(command) if value=='--volume']
            expected={Path(v.rsplit(':',2)[0]).as_posix():v.rsplit(':',2)[2] for v in compose['services']['gamerhq-staging']['volumes']}
            assert {Path(v.rsplit(':',2)[0]).as_posix():v.rsplit(':',2)[2] for v in volumes}==expected
            assert command[command.index('--network')+1]=='none' and '--read-only' in command
            assert command[command.index('--user')+1]=='10001:10001'
            assert '--env-file' not in command and '--env' not in command
            assert 'label=disable' not in command and '--privileged' not in command
        return SimpleNamespace(stdout='',returncode=0)
    with patch('hosts.gamerhq.skill_staging.verify_daemon'),patch('hosts.gamerhq.skill_staging.subprocess.run',side_effect=run):verify_docker(profile)
    assert len(observed)==2 and hashlib.sha256(database.read_bytes()).hexdigest()==digest

def test_artifact_rejection_is_distinct_from_storage_failure(staging):
    profile,_,_,wheel=staging
    wheel.write_bytes(b'invalid-private-wheel')
    with pytest.raises(StagingFailure) as caught:verify_offline(profile)
    assert caught.value.evidence['stagingFailure']=='STAGING_ARTIFACT_REJECTED'
    assert caught.value.evidence['step']=='artifact' and 'invalid-private-wheel' not in str(caught.value)

def test_inspection_does_not_read_credentials_or_database_contents(staging):
    profile,_,_,_=staging;database=prepare_storage(profile)
    profile['deploymentUid']=database.stat().st_uid
    forbidden={Path(profile['botEnvFile']),database}
    original=Path.read_text
    def read(path,*args,**kwargs):
        assert path not in forbidden,'Inspection opened private file contents'
        return original(path,*args,**kwargs)
    before=database.stat().st_mtime_ns
    with patch('tools.staging_inspect._mapped_uid',return_value=None),patch.object(Path,'read_text',read):report=inspect_staging(profile)
    assert report['storage']['database']['present'] is True
    assert report['credentialsPublished'] is False and report['databaseContentsRead'] is False
    assert report['gatewayStarted'] is False and report['productionChanged'] is False
    assert database.stat().st_mtime_ns==before
    assert 'retained-private-dataset' not in json.dumps(report)
