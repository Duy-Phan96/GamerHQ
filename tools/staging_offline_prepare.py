"""Resume bounded offline preparation without initialization or live operations."""
import json
import os
import sys

from .staging_ssh_client import invoke,StagingAccessError

def response_document(body):
    """Existing build-base emits an image result followed by its PASS record."""
    values=[json.loads(line) for line in body.splitlines() if line.strip()]
    if not values or any(not isinstance(value,dict) for value in values):
        raise ValueError('INVALID_OFFLINE_RESPONSE')
    return values[-1]

def prepare_offline(call,progress=None):
    completed=[]
    def record(operation):
        completed.append(operation)
        if progress:progress({'offlineStep':operation,'state':'PASS','gatewayStarted':False})
    health=call('configure','health')
    if health.get('result')!='PASS' or health.get('status')!='READY_OFFLINE' or health.get('gatewayChecked') is not False or health.get('productionChanged') is not False:
        raise ValueError('OFFLINE_HOST_REQUIRED')
    record('health')
    inspected=call('prepare','inspect')
    detail=inspected.get('inspection',{})
    if inspected.get('result')!='PASS' or inspected.get('operation')!='inspect' or inspected.get('workerEnabled') is not False or inspected.get('productionChanged') is not False or inspected.get('gatewayMayStart') is not False:
        raise ValueError('OFFLINE_WORKER_REQUIRED')
    if detail.get('schemaVersion')!=1 or detail.get('verificationParentsWritable') is not True:
        raise ValueError('WRITABLE_PROOF_CONTRACT_REQUIRED')
    storage=detail.get('storage',{})
    if not all(storage.get(key,{}).get('present') is True for key in ('data','database','backups')):
        raise ValueError('EXISTING_STAGING_STORAGE_REQUIRED')
    record('inspect')
    for operation,approval in (('build-base','BUILD ISOLATED STAGING BASE'),('fetch',''),('verify-offline',''),('build','')):
        value=call('prepare',operation,approval=approval)
        if value.get('result')!='PASS' or value.get('operation')!=operation or value.get('workerEnabled') is not False or value.get('productionChanged') is not False or value.get('gatewayMayStart') is not False:
            raise ValueError('OFFLINE_STEP_NOT_VERIFIED')
        record(operation)
    return {'state':'OFFLINE_READY','completed':completed,'gatewayStarted':False,'productionChanged':False,
        'databaseInitializationPerformed':False,'workerReplacementPerformed':False}

def main():
    def call(phase,operation,*,approval=''):
        output=invoke({'version':1,'phase':phase,'operation':operation,'approval':approval,'deploymentId':''},
            host=os.environ.get('GAMERHQ_STAGING_SSH_HOST',''),port=int(os.environ.get('GAMERHQ_STAGING_SSH_PORT','22')),
            private_key=os.environ.get('GAMERHQ_STAGING_SSH_KEY',''),known_hosts=os.environ.get('GAMERHQ_STAGING_SSH_KNOWN_HOSTS',''))
        return response_document(output)
    print(json.dumps(prepare_offline(call,lambda value:print(json.dumps(value),flush=True))))

if __name__=='__main__':
    try:main()
    except StagingAccessError as error:
        print(str(error),file=sys.stderr);raise SystemExit(1) from None
    except ValueError as error:
        allowed={'OFFLINE_HOST_REQUIRED','OFFLINE_WORKER_REQUIRED','WRITABLE_PROOF_CONTRACT_REQUIRED',
            'EXISTING_STAGING_STORAGE_REQUIRED','OFFLINE_STEP_NOT_VERIFIED','INVALID_OFFLINE_RESPONSE'}
        code=str(error) if str(error) in allowed else 'OFFLINE_RESPONSE_INVALID'
        raise SystemExit(json.dumps({'stagingError':code})) from None
    except Exception:
        raise SystemExit('{"stagingError":"OFFLINE_PREPARATION_NOT_VERIFIED"}') from None
