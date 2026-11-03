import json
import subprocess
from types import SimpleNamespace

import pytest

from tools.staging_errors import StagingFailure,decode_evidence,docker_failure
from tools.staging_ssh_client import StagingAccessError,invoke

@pytest.mark.parametrize('stderr,code',[
    ('mkdir /etc/gamerhq-staging/docker-client/buildx: read-only file system','BUILDX_STATE_NOT_WRITABLE'),
    ('buildx: permission denied','BUILDX_STATE_NOT_WRITABLE'),
    ('no space left on device','DOCKER_STORAGE_FULL'),
    ('out of memory','DOCKER_MEMORY_EXHAUSTED'),
    ('pull access denied','DOCKER_REGISTRY_REJECTED'),
    ('TLS handshake timeout','DOCKER_NETWORK_FAILED'),
    ('unrecognized private failure','DOCKER_BUILD_FAILED'),
])
def test_docker_errors_expose_only_fixed_public_categories(stderr,code):
    error=docker_failure(subprocess.CalledProcessError(1,['docker','build'],stderr=stderr+' token-must-stay-private'),'docker-build')
    assert error.evidence=={'stagingFailure':code,'step':'docker-build','exitCode':1}
    assert 'token-must-stay-private' not in str(error)
    assert decode_evidence(str(error))==error.evidence

@pytest.mark.parametrize('extra',[{'detail':'private-token'},{'exitCode':True},{'auditId':'private-token'},{'stagingFailure':'private-token'},{'step':'private-token'}])
def test_worker_evidence_rejects_unbounded_or_private_fields(extra):
    value={'stagingFailure':'DOCKER_BUILD_FAILED','step':'docker-build',**extra}
    assert decode_evidence(json.dumps(value)) is None

def test_worker_evidence_survives_verbose_ssh_without_exposing_remote_text():
    request={'version':1,'phase':'prepare','operation':'build-base','approval':'BUILD ISOLATED STAGING BASE'}
    safe=StagingFailure('BUILDX_STATE_NOT_WRITABLE','docker-build',exit_code=1,audit_id='a'*32)
    def run(args,**kwargs):
        return SimpleNamespace(returncode=1,stdout='',stderr='private-server-line\nAuthenticated to fixture using "publickey".\n'+str(safe)+'\n')
    with pytest.raises(StagingAccessError) as caught:
        invoke(request,host='fixture.example',port=22,private_key='private-key',known_hosts='owner-pin',run=run)
    value=json.loads(str(caught.value))
    assert value['authenticated'] is True and value['workerFailure']=='BUILDX_STATE_NOT_WRITABLE'
    assert value['workerAuditId']=='a'*32 and value['workerExitCode']==1
    assert not any(secret in str(caught.value) for secret in ('private-server-line','private-key','owner-pin'))

def test_timeout_and_missing_docker_are_safe_categories():
    assert docker_failure(subprocess.TimeoutExpired(['private'],2)).evidence['stagingFailure']=='DOCKER_TIMEOUT'
    assert docker_failure(FileNotFoundError('private')).evidence['stagingFailure']=='DOCKER_COMMAND_UNAVAILABLE'
