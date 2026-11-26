import copy
import json
import ast
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.staging_automation import pilot_generation as generation
from tools.staging_automation.policy import Denied


def documents():
    from tools import enroll_staging_tenant_preview as old
    incident = {'phase': 'PILOT_ROLLED_BACK', 'activationRun': '44283427505',
                'recoveryApiRepair': {'image': generation.BASELINE_IMAGE, 'commit': generation.BASELINE_COMMIT}}
    policy = {'hostPromotion': {'workflowSha256': 'f' * 64}, 'tenantPilot': generation.COHORT,
              'primaryRecoveryApiRepair': old.API_REPAIR_PIN}
    profile = {'environment': 'staging', 'stagingBotId': generation.COHORT['applicationId'],
               'stagingGuildId': generation.COHORT['guildIds'][0], 'productionBotId': '1', 'productionGuildId': '2'}
    state = {'enabled': True, 'bootstrapVerified': True, 'recoveryRequired': False,
             'tenantPilotActivationPrepared': {'expired': True}, 'tenantPilotPreview': {}, 'challenge': {'old': True}}
    candidate = {'commit': 'c' * 40, 'archiveSha256': 'd' * 64, 'ciRun': '123', 'baselineCiRun': '124'}
    return policy, profile, state, incident, candidate


def migrate(packet):
    policy, profile, state, incident, candidate = packet
    return generation.generation_documents(policy, profile, state, incident, source='e' * 40,
                                           candidate=candidate, cutover_digest='a' * 64, preparation_digest='b' * 64)


def test_generation_adopts_only_verified_recovered_baseline_and_keeps_incident():
    packet = documents()
    before = copy.deepcopy(packet)
    policy, profile, state = migrate(packet)
    assert packet == before
    assert policy['tenantPilotGeneration'] == generation.GENERATION
    assert policy['tenantPilotCutover']['fallback']['commit'] == generation.BASELINE_COMMIT
    assert 'primaryRecoveryApiRepair' not in policy
    assert profile['baseImage'] == state['approvedImage'] == generation.BASELINE_IMAGE
    assert not {'tenantPilotActivationPrepared', 'tenantPilotPreview', 'challenge'} & state.keys()
    assert state['recoveryRequired'] is False


@pytest.mark.parametrize(('index', 'key', 'value'), [
    (2, 'recoveryRequired', True), (2, 'enabled', False), (3, 'phase', 'ROLLING_BACK'),
    (3, 'activationRun', '123'), (3, 'recoveryApiRepair', {}),
    (0, 'tenantPilot', {'applicationId': '99', 'guildIds': ['1', '2']}),
    (1, 'productionGuildId', generation.COHORT['guildIds'][1]),
])
def test_generation_rejects_unverified_or_cross_environment_adoption(index, key, value):
    packet = documents()
    packet[index][key] = value
    with pytest.raises(Denied):
        migrate(packet)


@pytest.mark.parametrize('boundary', range(4))
def test_generation_transition_resumes_after_each_interrupted_write(boundary):
    files = {name: b'old-' + name.encode() for name in ['state', 'profile', 'policy', 'entry']}
    plan = {'files': [{'name': name, 'before': value.hex(), 'after': (b'new-' + name.encode()).hex()}
                      for name, value in files.items()]}
    class PowerLoss(BaseException):
        pass
    seen = []
    def interrupt(name):
        seen.append(name)
        if len(seen) == boundary + 1:
            raise PowerLoss()
    with pytest.raises(PowerLoss):
        generation.transition(plan, files.get, files.__setitem__, checkpoint=interrupt)
    proof = generation.transition(plan, files.get, files.__setitem__)
    assert len(proof) == 64
    assert all(value == b'new-' + name.encode() for name, value in files.items())
    assert generation.transition(plan, files.get, files.__setitem__) == proof


def test_generation_transition_never_overwrites_unknown_state_even_after_interruption():
    files = {'state': b'old', 'policy': b'unknown'}
    plan = {'files': [{'name': name, 'before': b'old'.hex(), 'after': b'new'.hex()} for name in files]}
    with pytest.raises(Denied, match='IMMUTABLE_RELEASE_CHANGED'):
        generation.transition(plan, files.get, files.__setitem__)
    assert files == {'state': b'old', 'policy': b'unknown'}


def test_generation_namespaces_the_new_journal_and_preserves_incident_bytes(tmp_path):
    from tools.staging_automation.pilot_cutover import journal_path
    incident = tmp_path / 'tenant-pilot-cutover.json'
    incident.write_bytes(b'original terminal incident')
    new = journal_path({'workRoot': tmp_path, 'pilotGeneration': generation.GENERATION['id']})
    assert new.name == 'tenant-pilot-cutover-v2.json' and not new.exists()
    assert incident.read_bytes() == b'original terminal incident'
    with pytest.raises(Denied):
        journal_path({'workRoot': tmp_path, 'pilotGeneration': '../../bad'})


def test_recovered_baseline_binding_requires_primary_only_startup(tmp_path):
    from tools.staging_tenant_rehearsal import baseline_override, rehearsal_binding
    from services.tenant_routing import PILOT_ENV
    policy, profile, _ = migrate(documents())
    profile.update(service='gamerhq-staging', database=str(tmp_path / 'primary.sqlite'), tenantPilot=policy['tenantPilot'])
    override = baseline_override(profile, policy, generation.BASELINE_IMAGE)
    value = override['services'][profile['service']]['environment'][PILOT_ENV]
    assert json.loads(value)['primaryOnly'] is True
    container = {'Image': generation.BASELINE_IMAGE, 'Config': {'Env': [PILOT_ENV + '=' + value],
                 'Labels': {'org.opencontainers.image.revision': generation.BASELINE_COMMIT}}}
    assert rehearsal_binding(profile, policy, container)['baselineImage'] == generation.BASELINE_IMAGE
    container['Config']['Env'] = []
    with pytest.raises(PermissionError):
        rehearsal_binding(profile, policy, container)


def test_only_reviewed_baseline_can_share_the_fallback_image(tmp_path):
    from tools.staging_automation.pilot_cutover import PilotCutover, digest
    from tools.staging_tenant_rehearsal import startup_override
    policy, profile, _ = migrate(documents())
    profile.update(service='gamerhq-staging', database=str(tmp_path / 'primary.sqlite'), workRoot=tmp_path)
    effective = {**profile, 'tenantPilot': policy['tenantPilot']}
    candidate = 'sha256:' + 'c' * 64
    prepared = {'generation': generation.GENERATION['id'], 'candidateImage': candidate, 'fallbackImage': generation.BASELINE_IMAGE,
                'startupOverrideSha256': digest(startup_override(effective, policy, candidate)),
                'fallbackStartupOverrideSha256': digest(startup_override(effective, policy, generation.BASELINE_IMAGE, primary_only=True))}
    backend = SimpleNamespace(profile=profile, policy=policy)
    transaction = PilotCutover(backend, prepared, generation.BASELINE_IMAGE, '123')
    assert transaction.fallback == transaction.baseline != transaction.candidate
    assert transaction.binding['generation'] == generation.GENERATION['id']
    for key in ['candidateImage', 'fallbackImage']:
        altered = {**prepared, key: 'sha256:' + 'd' * 64 if key == 'fallbackImage' else generation.BASELINE_IMAGE}
        with pytest.raises(Denied):
            PilotCutover(backend, altered, generation.BASELINE_IMAGE, '123')


def test_generation_registration_manifest_matches_exact_workflow_and_client():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'deploy/staging/tenant-pilot-generation.json').read_bytes())
    assert manifest['baseline']['commit'] == generation.BASELINE_COMMIT
    assert manifest['baseline']['image'] == generation.BASELINE_IMAGE
    assert manifest['candidate']['commit'] != generation.BASELINE_COMMIT
    import hashlib
    for name, pin in manifest['workflows'].items():
        body = (root / '.github/workflows' / name).read_bytes()
        assert hashlib.sha256(body).hexdigest() == pin['sha256']
        assert ('ref: ' + pin['client'] + '\n').encode() in body
        assert b'environment: deployment-security' in body
        assert b'cancel-in-progress: false' in body
        assert b'GAMERHQ_STAGING_PILOT_GENERATION: recovered-primary-v2' in body
    ast.parse((root / 'tools/enroll_staging_pilot_generation.py').read_text(), feature_version=(3, 9))


@pytest.mark.parametrize('phase', [None, 'PREPARED', 'WRONG_SOURCE', 'COMMITTED'])
def test_generation_entry_blocks_interrupted_or_wrong_source_transition(tmp_path, monkeypatch, phase):
    from tools import enroll_staging_pilot_generation as installer
    monkeypatch.setattr(installer, 'CONTROL', tmp_path)
    target = tmp_path / 'selected-controller'
    (target / 'staging_automation').mkdir(parents=True)
    (target / 'staging_automation/__init__.py').touch()
    (target / 'staging_automation/bridge.py').write_text('def entry(): print("ENTERED_VERIFIED_CONTROLLER")\n')
    if phase:
        (tmp_path / 'pilot-generation-transition.json').write_text(json.dumps({
            'phase': 'COMMITTED' if phase == 'WRONG_SOURCE' else phase,
            'source': 'wrong-controller' if phase == 'WRONG_SOURCE' else target.name}))
    process = subprocess.run([sys.executable, '-I', '-c', installer.gate(target).decode()],
                             text=True, capture_output=True, timeout=10)
    assert process.returncode == (0 if phase == 'COMMITTED' else 1)
    assert ('ENTERED_VERIFIED_CONTROLLER' in process.stdout) == (phase == 'COMMITTED')


@pytest.mark.skipif(os.name != 'posix', reason='Real owner/mode/fsync contract is POSIX-only')
@pytest.mark.parametrize('boundary', range(4))
def test_real_filesystem_transition_failure_retry_preserves_tenants_and_incident(tmp_path, boundary):
    from tools.enroll_staging_pilot_generation import replace
    names = ['entry', 'profile', 'state', 'policy']
    files = {name: tmp_path / name for name in names}
    incident, primary, secondary = [tmp_path / name for name in ['incident', 'primary.sqlite', 'secondary.sqlite']]
    for path in [*files.values(), incident, primary, secondary]:
        path.write_bytes(b'original-' + path.name.encode())
    retained = {path: path.read_bytes() for path in [incident, primary, secondary]}
    plan = {'files': [{'name': name, 'before': path.read_bytes().hex(), 'after': (b'new-' + name.encode()).hex()}
                      for name, path in files.items()]}
    class PowerLoss(BaseException):
        pass
    seen = []
    def checkpoint(name):
        seen.append(name)
        if len(seen) == boundary + 1:
            raise PowerLoss()
    def write(name, content):
        replace(files[name], content, os.geteuid(), os.getegid(), 0o600)
    read = lambda name: files[name].read_bytes()
    with pytest.raises(PowerLoss):
        generation.transition(plan, read, write, checkpoint=checkpoint)
    generation.transition(plan, read, write)
    assert all(path.read_bytes() == data for path, data in retained.items())
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files.values())


def test_generation_never_permits_routine_single_database_updates(tmp_path):
    from tools.staging_automation.bridge import execute
    policy, _, state = migrate(documents())
    backend = SimpleNamespace(profile={'workRoot': tmp_path})
    for operation in ['update', 'host-promote', 'restart', 'install']:
        with pytest.raises(Denied, match='OPERATION_NOT_ALLOWED'):
            execute('challenge', {'operation': operation}, policy, state, backend, lambda _: None, now=1)


@pytest.mark.parametrize('accepted', [False, True])
def test_enrollment_cli_reports_rejected_preflight_as_failure(monkeypatch, capsys, accepted):
    from tools import enroll_staging_pilot_generation as installer
    state = 'RECOVERED_PRIMARY_GENERATION_ENROLLED' if accepted else 'GENERATION_PREFLIGHT_REJECTED'
    monkeypatch.setattr(sys, 'argv', ['installer', '--commit', 'a' * 40, '--approval', installer.APPROVAL])
    monkeypatch.setattr(installer, 'enroll', lambda *args: {'state': state, 'activationAuthorized': False})
    if accepted:
        installer.main()
    else:
        with pytest.raises(SystemExit) as error:
            installer.main()
        assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)['state'] == state


def test_root_policy_builder_needs_only_system_python_standard_library(tmp_path):
    import shutil
    module = tmp_path / 'staging_automation'
    module.mkdir()
    for name in ['__init__.py', 'policy.py', 'pilot_generation.py']:
        shutil.copyfile(Path('tools/staging_automation') / name, module / name)
    script = '''
import json,sys
sys.path.insert(0,sys.argv[1])
from staging_automation.pilot_generation import generation_documents
p=json.load(sys.stdin)
result=generation_documents(*p[:4],source='e'*40,candidate=p[4],cutover_digest='a'*64,preparation_digest='b'*64)
assert result[2]['recoveryRequired'] is False
assert not any(name.split('.')[0] in {'hosts','database','discord','cryptography'} for name in sys.modules)
print('SYSTEM_PYTHON_POLICY_BUILDER_PASS')
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(tmp_path)],
                            input=json.dumps(documents()), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'SYSTEM_PYTHON_POLICY_BUILDER_PASS' in result.stdout


@pytest.mark.parametrize('failure', [None, 'stopped', 'denied'])
def test_live_storage_preflight_never_rebinds_or_relabels_existing_data(failure):
    from unittest.mock import Mock
    from tools.staging_automation.backend import verify_live_storage
    container = {'Id': 'a' * 64, 'State': {'Running': failure != 'stopped', 'Health': {'Status': 'healthy'}},
                 'ProcessLabel': 'synthetic-accepted-mcs'}
    run = Mock(side_effect=Denied('HOST_OPERATION_FAILED') if failure == 'denied' else None)
    before = copy.deepcopy(container)
    if failure:
        with pytest.raises(Denied):
            verify_live_storage(SimpleNamespace(run=run), container)
    else:
        verify_live_storage(SimpleNamespace(run=run), container)
    assert container == before
    if failure == 'stopped':
        run.assert_not_called()
    else:
        command = run.call_args.args[0]
        assert command[:5] == ['docker', 'exec', '--user', '10001:10001', container['Id']]
        assert command[-3:] == ['/app/runtime/data', '/app/runtime/data/gamerhq.db', '/app/backups']
        assert '--volume' not in command and '--mount' not in command


def active_documents():
    from tools.staging_automation.pilot_cutover import digest
    packet = documents()
    packet[4].update(commit=generation.ACTIVE_BASELINE_COMMIT, archiveSha256=generation.ACTIVE_BASELINE_ARCHIVE,
                     ciRun=generation.ACTIVE_BASELINE_CI)
    policy, profile, state = migrate(packet)
    prepared = {'generation': generation.GENERATION['id'], 'candidateCommit': generation.ACTIVE_BASELINE_COMMIT,
                'candidateImage': generation.ACTIVE_BASELINE_IMAGE, 'fallbackImage': generation.BASELINE_IMAGE,
                'state': 'PILOT_ACTIVATION_PREPARED', 'activationAuthorized': False, 'gatewayStarted': False,
                'containerRollbackRehearsal': 'PASS', 'fallbackRollbackRehearsal': 'PASS'}
    state.update(tenantPilotActivationPrepared=prepared, usedTokens={'old-token': 999}, challenge={'old': True})
    journal = {'phase': 'PILOT_ACTIVE', 'activationRun': '44581943871', 'baselineContainer': 'd' * 64, 'checkpoint': {},
               'binding': {'preparedSha256': digest(prepared), 'generation': generation.GENERATION['id'],
                           'baselineImage': generation.BASELINE_IMAGE, 'fallbackImage': generation.BASELINE_IMAGE,
                           'candidateImage': generation.ACTIVE_BASELINE_IMAGE}}
    return policy, profile, state, journal


def upgrade(packet):
    return generation.active_upgrade_documents(*packet, source='a' * 40,
                                               preparation_digest='b' * 64, cutover_digest='c' * 64)


def test_active_epoch_uses_accepted_image_and_preserves_replay_history():
    packet = active_documents()
    before = copy.deepcopy(packet)
    policy, profile, state = upgrade(packet)
    assert packet == before
    assert generation.is_active_upgrade(policy)
    assert policy['tenantPilotActivation']['commit'] == generation.RC4_PIN['commit']
    assert profile['baseImage'] == state['approvedImage'] == generation.ACTIVE_BASELINE_IMAGE
    assert state['usedTokens'] == before[2]['usedTokens']
    assert 'tenantPilotActivationPrepared' not in state and 'challenge' not in state
    assert generation.effective_profile(profile, policy)['pilotGeneration'] == generation.ACTIVE_GENERATION['id']


@pytest.mark.parametrize(('index', 'key', 'value'), [
    (2, 'recoveryRequired', True), (2, 'approvedImage', generation.ACTIVE_BASELINE_IMAGE),
    (3, 'phase', 'PILOT_ROLLED_BACK'), (3, 'binding', {}), (3, 'checkpoint', None),
    (1, 'productionGuildId', generation.COHORT['guildIds'][1]),
])
def test_active_epoch_rejects_unverified_old_transaction(index, key, value):
    packet = active_documents()
    packet[index][key] = value
    with pytest.raises(Denied):
        upgrade(packet)


def test_active_epoch_uses_distinct_journal_and_dual_admission(tmp_path):
    from tools.staging_automation.pilot_cutover import PilotCutover, digest, journal_path
    from tools.staging_tenant_rehearsal import baseline_override, startup_override, rehearsal_binding
    from services.tenant_routing import PILOT_ENV
    policy, profile, _ = upgrade(active_documents())
    profile.update(service='gamerhq-staging', database=str(tmp_path / 'primary.sqlite'), workRoot=tmp_path,
                   tenantPilot=policy['tenantPilot'])
    historical = tmp_path / 'tenant-pilot-cutover-v2.json'
    historical.write_bytes(b'accepted historical journal')
    candidate = 'sha256:' + 'c' * 64
    fallback = generation.ACTIVE_BASELINE_IMAGE
    baseline = baseline_override(profile, policy, fallback)
    admission = baseline['services'][profile['service']]['environment'][PILOT_ENV]
    assert not json.loads(admission).get('primaryOnly', False)
    container = {'Image': fallback, 'Config': {'Env': [PILOT_ENV + '=' + admission],
                 'Labels': {'org.opencontainers.image.revision': generation.ACTIVE_BASELINE_COMMIT}}}
    assert rehearsal_binding(profile, policy, container)['baselineAdmission'] == 'PILOT_ACTIVE'
    prepared = {'generation': generation.ACTIVE_GENERATION['id'], 'candidateImage': candidate, 'fallbackImage': fallback,
                'startupOverrideSha256': digest(startup_override(profile, policy, candidate)),
                'fallbackStartupOverrideSha256': digest(baseline)}
    tx = PilotCutover(SimpleNamespace(profile=profile, policy=policy), prepared, fallback, '123')
    assert tx.fallback_override == baseline
    assert tx.path == journal_path(generation.effective_profile(profile, policy))
    assert tx.path != historical and not tx.path.exists()
    assert historical.read_bytes() == b'accepted historical journal'
    bad = {**prepared, 'fallbackStartupOverrideSha256': digest(startup_override(profile, policy, fallback, primary_only=True))}
    with pytest.raises(Denied):
        PilotCutover(SimpleNamespace(profile=profile, policy=policy), bad, fallback, '123')


def test_active_epoch_never_falls_back_after_unverified_candidate_writes():
    from tools.staging_automation.pilot_cutover import PilotCutover
    tx = object.__new__(PilotCutover)
    tx.active_upgrade = True
    tx.baseline = tx.fallback = generation.ACTIVE_BASELINE_IMAGE
    tx.candidate = 'sha256:' + 'c' * 64
    tx.inspect = lambda: {'Image': tx.candidate, 'State': {'Running': True}}
    actions = []
    tx.compose = lambda *args, **kw: actions.append(args)
    def save(record, phase, **kw):
        actions.append((phase, kw))
        return {**record, 'phase': phase, **kw}
    tx.save = save
    def incompatible(record):
        raise Denied('RECOVERY_REQUIRES_OWNER')
    tx.prove_rollback_compatibility = incompatible
    tx.attest = lambda: pytest.fail('must not start rollback without compatibility proof')
    with pytest.raises(Denied, match='RECOVERY_REQUIRES_OWNER'):
        tx.rollback({'phase': 'VERIFYING', 'checkpoint': {}})
    assert actions == [('stop', '--timeout', '30'), ('ROLLING_BACK', {'candidateMayHaveStarted': True})]


def test_active_rollback_compares_complete_logical_content_of_both_tenants(tmp_path):
    import sqlite3
    from tools.staging_tenant_rehearsal import require_unchanged_dual_checkpoint
    before, after = tmp_path / 'before', tmp_path / 'after'
    before.mkdir(); after.mkdir()
    for folder in (before, after):
        for guild in (1, 2):
            with sqlite3.connect(folder / f'{guild}.sqlite') as conn:
                conn.execute('CREATE TABLE records (value TEXT)')
                conn.execute('INSERT INTO records VALUES (?)', ('original',))
                conn.execute('CREATE TABLE skill_deployments (state TEXT)')
                conn.execute("INSERT INTO skill_deployments VALUES ('retained')")
    require_unchanged_dual_checkpoint(before, after, (1, 2))
    with sqlite3.connect(after / '2.sqlite') as conn:
        conn.execute("UPDATE skill_deployments SET state='changed'")
    with pytest.raises(Denied, match='RECOVERY_REQUIRES_OWNER'):
        require_unchanged_dual_checkpoint(before, after, (1, 2))

@pytest.mark.parametrize('phase,mutated,expected', [
    ('PREPARED', False, False), ('COMMITTED', False, True), ('COMMITTED', True, False),
])
def test_active_gate_requires_committed_epoch_and_unchanged_ancestors(tmp_path, monkeypatch, phase, mutated, expected):
    import hashlib
    from tools import enroll_staging_pilot_generation as installer
    monkeypatch.setattr(installer, 'CONTROL', tmp_path)
    target = tmp_path / 'selected-controller'
    (target / 'staging_automation').mkdir(parents=True)
    (target / 'staging_automation/__init__.py').touch()
    (target / 'staging_automation/bridge.py').write_text('def entry(): print("ENTERED_VERIFIED_CONTROLLER")\n')
    parent = tmp_path / 'historical.json'
    parent.write_bytes(b'old')
    history = {parent.name: hashlib.sha256(parent.read_bytes()).hexdigest()}
    (tmp_path / installer.ACTIVE_TRANSITION).write_text(json.dumps({'phase':phase,'source':target.name}))
    if mutated:
        parent.write_bytes(b'changed')
    result = subprocess.run([sys.executable, '-O', '-I', '-c', installer.active_gate(target,history).decode()],
                            capture_output=True,text=True,timeout=10)
    assert (result.returncode == 0) is expected
    assert ('ENTERED_VERIFIED_CONTROLLER' in result.stdout) is expected
