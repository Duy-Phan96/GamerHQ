import json
from types import SimpleNamespace

import pytest

from tools.staging_ssh_client import invoke,StagingAccessError

@pytest.mark.parametrize('code,step,status',[
    ('STAGING_COMPOSE_CONFIG_REJECTED','compose-config',1),
    ('STAGING_DATA_ACCESS_REJECTED','storage-probe',41),
    ('STAGING_DATABASE_ACCESS_REJECTED','storage-probe',42),
    ('STAGING_BACKUPS_ACCESS_REJECTED','storage-probe',43),
    ('STAGING_ARTIFACT_REJECTED','artifact',None),
    ('STAGING_BASE_IMAGE_MISSING','base-image',None),
])
def test_new_verifier_evidence_survives_actions_client_without_private_output(code,step,status):
    evidence={'stagingFailure':code,'step':step,'auditId':'b'*32}
    if status is not None:evidence['exitCode']=status
    def run(*args,**kwargs):
        return SimpleNamespace(returncode=1,stdout='',stderr='private-token-and-server-output\n'+json.dumps(evidence))
    with pytest.raises(StagingAccessError) as caught:
        invoke({'version':1,'phase':'prepare','operation':'verify-offline'},host='fixture.example',port=22,
            private_key='private-key',known_hosts='public-pin',run=run)
    actual=caught.value.evidence
    assert actual['workerFailure']==code and actual['workerStep']==step and actual['workerAuditId']=='b'*32
    assert actual.get('workerExitCode')==status
    assert 'private-token-and-server-output' not in str(caught.value) and 'private-key' not in str(caught.value)
