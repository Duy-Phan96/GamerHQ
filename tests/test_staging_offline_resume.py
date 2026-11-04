import pytest

from tools.staging_offline_prepare import prepare_offline,response_document

def fake_worker(fail=None,mutation=None):
    calls=[]
    def call(phase,operation,**kwargs):
        calls.append((phase,operation,kwargs.get('approval','')))
        if operation==fail:raise RuntimeError('worker failed')
        if operation=='health':value={'result':'PASS','status':'READY_OFFLINE','gatewayChecked':False,'productionChanged':False}
        else:value={'result':'PASS','operation':operation,'workerEnabled':False,'productionChanged':False,'gatewayMayStart':False}
        if operation=='inspect':value['inspection']={'schemaVersion':1,'verificationParentsWritable':True,
            'storage':{key:{'present':True} for key in ('data','database','backups')}}
        if mutation:mutation(operation,value)
        return value
    return call,calls

def test_resume_existing_storage_never_initializes_installs_or_authorizes_gateway():
    call,calls=fake_worker()
    for _ in range(2):
        result=prepare_offline(call)
        assert result['state']=='OFFLINE_READY' and result['databaseInitializationPerformed'] is False
        assert result['workerReplacementPerformed'] is False and result['gatewayStarted'] is False
    expected=[('configure','health',''),('prepare','inspect',''),('prepare','build-base','BUILD ISOLATED STAGING BASE'),
        ('prepare','fetch',''),('prepare','verify-offline',''),('prepare','build','')]
    assert calls==expected+expected

@pytest.mark.parametrize('operation',['build-base','fetch','verify-offline','build'])
def test_failed_phase_stops_before_any_following_phase(operation):
    call,calls=fake_worker(fail=operation)
    with pytest.raises(RuntimeError):prepare_offline(call)
    assert calls[-1][1]==operation

@pytest.mark.parametrize('mode',['live','missing-contract','readonly-proof','missing-database'])
def test_read_only_preflight_rejects_incompatible_worker_without_mutation(mode):
    def mutation(operation,value):
        if mode=='live' and operation=='health':value.update(status='HOST_READY',gatewayChecked=True)
        if operation=='inspect':
            if mode=='missing-contract':value.pop('inspection')
            if mode=='readonly-proof':value['inspection']['verificationParentsWritable']=False
            if mode=='missing-database':value['inspection']['storage']['database']['present']=False
    call,calls=fake_worker(mutation=mutation)
    with pytest.raises(ValueError):prepare_offline(call)
    assert len(calls)<=2

def test_base_response_accepts_existing_multiple_document_protocol():
    assert response_document('{"reviewCandidateImage":"sha256:fixture"}\n{"result":"PASS"}\n')=={'result':'PASS'}
    for body in ('','[]','private output\n{"result":"PASS"}'):
        with pytest.raises(ValueError):response_document(body)

def test_step_that_reports_live_or_changed_production_is_never_accepted():
    def mutation(operation,value):
        if operation=='fetch':value['gatewayMayStart']=True
    call,calls=fake_worker(mutation=mutation)
    with pytest.raises(ValueError):prepare_offline(call)
    assert calls[-1][1]=='fetch'
