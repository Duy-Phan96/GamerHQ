import base64
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes

from tools.staging_automation.policy import Denied, request, charge, admit, validate_policy, release_allowed
from tools.staging_automation.oidc import verify, ISSUER
from tools.staging_automation.bridge import execute
from tools.staging_automation.backend import HostBackend
from tools.staging_automation_client import perform, failure


@pytest.fixture(scope='module')
def signing_key(): return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(value): return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


@pytest.mark.parametrize('mutation', ['newline-prefix', 'newline-suffix', 'tab', 'options', 'space-prefix'])
def test_enrollment_rejects_authorized_keys_line_and_option_injection(mutation):
    from tools.enroll_staging_automation import public_key_bytes
    canonical = 'ssh-ed25519 ' + base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + b'a' * 32).decode()
    assert len(public_key_bytes(canonical)) == 51
    variants = {'newline-prefix': '\n' + canonical, 'newline-suffix': canonical + '\n', 'tab': canonical.replace(' ', '\t'),
        'options': 'command="shell" ' + canonical, 'space-prefix': ' ' + canonical}
    with pytest.raises(ValueError): public_key_bytes(variants[mutation])


@pytest.fixture
def trust(signing_key):
    workflow = b'reviewed workflow bytes'
    policy = {'schemaVersion': 1, 'repository': 'Duy-Phan96/GamerHQ', 'repositoryId': '1375800851', 'ownerId': '272528289',
        'controllerCommit': 'a' * 40, 'legacyWorkerCommit': 'b' * 40, 'bootstrapRun': '41850674216',
        'routineWorkflowSha256': hashlib.sha256(workflow).hexdigest(), 'securityWorkflowSha256': hashlib.sha256(workflow).hexdigest(),
        'publicKeySha256': 'c' * 64, 'limits': {'mutationsPerHour': 12, 'buildsPerHour': 3, 'restartsPerHour': 2, 'restartCooldown': 120}}
    numbers = signing_key.public_key().public_numbers()
    jwk = {'kid': 'test-key', 'kty': 'RSA', 'n': b64(numbers.n.to_bytes(256, 'big')), 'e': b64(numbers.e.to_bytes(3, 'big'))}
    claims = {'iss': ISSUER, 'aud': 'gamerhq-staging-automation:1375800851:' + 'd' * 32,
        'repository': policy['repository'], 'repository_id': policy['repositoryId'], 'repository_owner_id': policy['ownerId'],
        'sub': 'repo:Duy-Phan96@272528289/GamerHQ@1375800851:environment:staging-automation',
        'environment': 'staging-automation', 'ref': 'refs/heads/main', 'ref_type': 'branch', 'runner_environment': 'github-hosted',
        'event_name': 'workflow_dispatch', 'workflow_ref': policy['repository'] + '/.github/workflows/staging-automation.yml@refs/heads/main',
        'workflow_sha': 'e' * 40, 'run_id': '12345', 'iat': 1000, 'nbf': 1000, 'exp': 1300, 'jti': 'synthetic-job-123'}
    def token(value=claims, header=None):
        parts = [b64(json.dumps(header or {'alg': 'RS256', 'typ': 'JWT', 'kid': 'test-key'}).encode()), b64(json.dumps(value).encode())]
        parts.append(b64(signing_key.sign('.'.join(parts).encode(), padding.PKCS1v15(), hashes.SHA256())))
        return '.'.join(parts)
    reviews = [{'state': 'approved', 'user': {'id': 272528289}, 'environments': [{'name': 'deployment-security'}]}]
    environment = {'can_admins_bypass': False, 'protection_rules': [{'type': 'required_reviewers', 'reviewers': [{'reviewer': {'id': 272528289}}]}]}
    def read(url):
        if url == ISSUER + '/.well-known/jwks': return json.dumps({'keys': [jwk]}).encode()
        if url.startswith('https://raw.githubusercontent.com/'): return workflow
        if url.endswith('/approvals'): return json.dumps(reviews).encode()
        if url.endswith('/environments/deployment-security'): return json.dumps(environment).encode()
        raise AssertionError('Unexpected public endpoint')
    return policy, claims, token, read, reviews, environment


def test_signed_routine_identity_and_immutable_subject(trust):
    policy, claims, token, read, _, _ = trust
    assert validate_policy(policy) is policy
    assert verify(token(), policy, 'health', 'd' * 32, read=read, now=1001)['run_id'] == '12345'
    claims['sub'] = 'repo:Duy-Phan96/GamerHQ:environment:staging-automation'
    assert verify(token(), policy, 'health', 'd' * 32, read=read, now=1001)


@pytest.mark.parametrize('key,value', [('iss', 'https://invalid'), ('aud', 'wrong'), ('repository_id', '1'), ('repository_owner_id', '1'),
    ('environment', 'production'), ('sub', 'repo:other:environment:staging-automation'), ('ref', 'refs/heads/develop'),
    ('runner_environment', 'self-hosted'), ('workflow_ref', 'unreviewed'), ('event_name', 'pull_request'), ('exp', 999), ('iat', 2000), ('nbf', 2000), ('jti', ''), ('workflow_sha', 'main')])
def test_signed_but_out_of_scope_identity_is_rejected(trust, key, value):
    p, c, token, read, _, _ = trust; c[key] = value
    with pytest.raises(Denied): verify(token(), p, 'health', 'd' * 32, read=read, now=1001)


@pytest.mark.parametrize('header', [{'alg': 'HS256', 'typ': 'JWT', 'kid': 'test-key'}, {'alg': 'none', 'typ': 'JWT', 'kid': 'test-key'},
    {'alg': 'RS256', 'typ': 'JWT', 'kid': 'unknown'}, {'alg': 'RS256', 'typ': 'JWT', 'kid': 'test-key', 'jku': 'https://invalid'}])
def test_algorithm_confusion_and_untrusted_key_locations_are_rejected(trust, header):
    p, _, token, read, _, _ = trust
    with pytest.raises(Denied): verify(token(header=header), p, 'health', 'd' * 32, read=read, now=1001)


def test_corrupted_signature_wrong_challenge_and_workflow_digest_fail_closed(trust):
    p, _, token, read, _, _ = trust
    parts = token().split('.'); parts[2] = b64(b'x' * 256)
    with pytest.raises(Denied): verify('.'.join(parts), p, 'health', 'd' * 32, read=read, now=1001)
    with pytest.raises(Denied): verify(token(), p, 'health', 'f' * 32, read=read, now=1001)
    p['routineWorkflowSha256'] = '0' * 64
    with pytest.raises(Denied): verify(token(), p, 'health', 'd' * 32, read=read, now=1001)


def security_claims(claims):
    claims['environment'] = 'deployment-security'
    claims['sub'] = 'repo:Duy-Phan96@272528289/GamerHQ@1375800851:environment:deployment-security'
    claims['workflow_ref'] = 'Duy-Phan96/GamerHQ/.github/workflows/staging-automation-activate.yml@refs/heads/main'


def test_activation_needs_actual_owner_review_not_only_an_environment_name(trust):
    p, c, token, read, reviews, environment = trust
    with pytest.raises(Denied): verify(token(), p, 'activate', 'd' * 32, read=read, now=1001)
    security_claims(c)
    assert verify(token(), p, 'activate', 'd' * 32, read=read, now=1001)
    reviews[0]['user']['id'] = 1
    with pytest.raises(Denied): verify(token(), p, 'activate', 'd' * 32, read=read, now=1001)
    reviews[0]['user']['id'] = 272528289; environment['can_admins_bypass'] = True
    with pytest.raises(Denied): verify(token(), p, 'activate', 'd' * 32, read=read, now=1001)


@pytest.mark.parametrize('operation', ['bootstrap', 'initial', 'credentials', 'release', 'revoke', 'shell', 'docker', 'apply', 'production'])
def test_legacy_security_and_arbitrary_commands_are_not_in_routine_protocol(operation):
    with pytest.raises(Denied): request({'version': 1, 'phase': 'automation', 'operation': operation})


def test_rate_limits_count_failures_and_clock_regression():
    state = {}; charge(state, 'restart', 1000)
    with pytest.raises(Denied, match='COOLDOWN'): charge(state, 'recover', 1100)
    charge(state, 'restart', 1200)
    with pytest.raises(Denied, match='RESTART_RATE_LIMIT'): charge(state, 'restart', 1400)
    with pytest.raises(Denied, match='CLOCK_REGRESSION'): charge(state, 'health', 900)
    state = {}
    for t in range(3): charge(state, 'update', 1000 + t)
    with pytest.raises(Denied, match='BUILD_RATE_LIMIT'): charge(state, 'prepare', 1010)


class FakeHost:
    def __init__(self): self.calls = []; self.fail = False
    def policy_integrity(self): self.calls.append('policy-integrity')
    def activate(self, old=None):
        self.calls.append('activate')
        if self.fail: raise Denied('INITIAL_BOOTSTRAP_NOT_VERIFIED')
        return 'sha256:' + 'a' * 64
    def health(self): self.calls.append('health'); return {'state': 'HOST_READY'}
    def restart(self, image, recover=False):
        self.calls.append('restart')
        if self.fail: raise Denied('RESTART_HEALTH_FAILED')
        return {'state': 'RESTARTED'}
    def install(self): self.calls.append('install'); return {'state': 'INSTALLED'}


def invoke_fixture(state, host, policy, operation, at=1000, jti=None):
    saved = []
    save = lambda value: saved.append(copy.deepcopy(value))
    nonce = execute('challenge', {'operation': operation}, policy, state, host, save, now=at)['challenge']
    verifier = lambda *args, **kwargs: {'jti': jti or nonce, 'exp': at + 300, 'run_id': '12345'}
    return execute(operation, {'token': 'not-logged', 'challenge': nonce}, policy, state, host, save, verifier=verifier, now=at + 1)


def test_policy_cannot_arm_before_verified_initial_bootstrap_and_routine_cannot_start_first(trust):
    p = trust[0]; host = FakeHost(); state = {'enabled': False}
    for op in ('install', 'update', 'restart', 'recover'):
        with pytest.raises(Denied, match='NOT_ACTIVATED'): invoke_fixture(state, host, p, op)
    host.fail = True
    with pytest.raises(Denied, match='INITIAL_BOOTSTRAP'): invoke_fixture(state, host, p, 'activate')
    assert not state['enabled'] and 'restart' not in host.calls
    host.fail = False; invoke_fixture(state, host, p, 'activate', at=1200)
    assert state['enabled'] and state['bootstrapVerified']
    assert invoke_fixture(state, host, p, 'restart', at=1400)['state'] == 'RESTARTED'


def test_replay_and_recovery_failure_do_not_retry_or_reset_safeguards(trust):
    p = trust[0]; host = FakeHost(); state = {'enabled': True, 'bootstrapVerified': True, 'approvedImage': 'sha256:' + 'a' * 64}
    invoke_fixture(state, host, p, 'health', jti='same-token')
    with pytest.raises(Denied, match='REPLAY'): invoke_fixture(state, host, p, 'health', jti='same-token')
    host.fail = True
    with pytest.raises(Denied): invoke_fixture(state, host, p, 'recover', at=1200)
    assert state['circuitUntil'] == 4801
    with pytest.raises(Denied): invoke_fixture(state, host, p, 'recover', at=1400)
    assert host.calls.count('restart') == 1


def test_client_binds_fresh_challenge_to_token_and_never_echoes_secrets():
    calls = []
    def call(op, payload):
        calls.append((op, payload))
        return {'challenge': 'nonce', 'audience': 'bound'} if op == 'challenge' else {'state': 'SMOKE_PASS'}
    assert perform('smoke', call=call, get_token=lambda aud: 'private-token')['state'] == 'SMOKE_PASS'
    assert calls == [('challenge', {'operation': 'smoke'}), ('smoke', {'challenge': 'nonce', 'token': 'private-token'})]
    error = failure({'phase': 'automation', 'operation': 'smoke'}, 1, True, 'private-token\n{"automationError":"HOST_NOT_READY"}')
    assert 'private-token' not in str(error) and error.evidence['automationFailure'] == 'HOST_NOT_READY'
    error = failure({'phase': 'automation', 'operation': 'status'}, 255, False, 'Permission denied (publickey). private-key')
    assert error.evidence['stagingError'] == 'SSH_AUTHENTICATION_REJECTED' and 'private-key' not in str(error)


def container():
    return {'Name': '/gamerhq-staging-bot', 'Image': 'sha256:' + 'a' * 64,
        'Config': {'User': '10001:10001', 'Labels': {'com.docker.compose.project': 'gamerhq-staging', 'com.docker.compose.service': 'gamerhq-staging'}},
        'HostConfig': {'Privileged': False, 'CapAdd': None, 'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'Memory': 536870912, 'NanoCpus': 1000000000, 'PidsLimit': 128, 'RestartPolicy': {'Name': 'no'}},
        'NetworkSettings': {'Networks': {'gamerhq-staging-private': {}}},
        'Mounts': [{'Type': 'bind', 'Source': '/opt/gamerhq-staging/data', 'Destination': '/app/runtime/data'}, {'Type': 'bind', 'Source': '/opt/gamerhq-staging/backups', 'Destination': '/app/backups'}]}


@pytest.mark.parametrize('mutation', ['privileged', 'unlimited-memory', 'cpu', 'pids', 'root', 'production-mount', 'shared-network', 'restart-policy', 'cap-add'])
def test_container_privilege_mount_and_resource_drift_requires_owner(mutation):
    c = container(); h = c['HostConfig']
    if mutation == 'privileged': h['Privileged'] = True
    if mutation == 'unlimited-memory': h['Memory'] = 0
    if mutation == 'cpu': h['NanoCpus'] *= 2
    if mutation == 'pids': h['PidsLimit'] *= 2
    if mutation == 'root': c['Config']['User'] = '0:0'
    if mutation == 'production-mount': c['Mounts'][0]['Source'] = '/opt/gamerhq/data'
    if mutation == 'shared-network': c['NetworkSettings']['Networks'] = {'host': {}}
    if mutation == 'restart-policy': h['RestartPolicy']['Name'] = 'always'
    if mutation == 'cap-add': h['CapAdd'] = ['SYS_ADMIN']
    backend = HostBackend({'container': 'gamerhq-staging-bot', 'database': '/opt/gamerhq-staging/data/gamerhq.db', 'backupRoot': '/opt/gamerhq-staging/backups'}, {})
    backend.run = Mock(return_value=json.dumps([c]))
    with pytest.raises(Denied): backend.inspect()
    assert backend.run.call_count == 1


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / 'hosts/gamerhq/skill_artifacts.py').exists(), reason='Host publisher adapter is on platform branch')
def test_release_policy_refuses_privilege_and_migration_expansion():
    base = {'repository': 'Duy-Phan96/gamerhq-skill-event-manager', 'distribution': 'gamerhq-skill-event-manager', 'version': '0.1.0', 'tag': 'v0.1.0', 'reviewedCommit': 'a' * 40, 'sha256': 'b' * 64, 'sourceReviewed': True, 'skillId': 'event-manager', 'runtimeApi': '1', 'capabilities': ['storage.skill'], 'migrationMode': 'backward-compatible'}
    profile = {'approvedCapabilities': {'event-manager': ['storage.skill', 'scheduler.jobs']}}
    patch_release = {**base, 'version': '0.1.1', 'tag': 'v0.1.1'}
    release_allowed(patch_release, base, profile)
    for candidate in ({**patch_release, 'capabilities': ['storage.skill', 'scheduler.jobs']}, {**patch_release, 'migrationMode': 'destructive'}, {**patch_release, 'version': '0.2.0', 'tag': 'v0.2.0'}, {**base, 'sha256': 'c' * 64}):
        with pytest.raises(Denied): release_allowed(candidate, base, profile)


def test_administrative_handoff_cannot_change_pending_bootstrap_or_use_fake_enrollment():
    from tools.staging_automation_github import finalize
    mutations = []
    def api(path, method='GET', body=None):
        if method != 'GET': mutations.append((path, body)); return {}
        if path.startswith('/actions/runs/'): return {'conclusion': None, 'head_sha': '6a444d2051568335a7abd13d872edddde16aae9c'}
        raise AssertionError(path)
    receipt = {'result': 'PASS', 'operation': 'status', 'state': 'NOT_ACTIVATED', 'controllerCommit': 'a' * 40, 'publicKeySha256': 'b' * 64}
    with pytest.raises(ValueError, match='WAIT_FOR_ORIGINAL'): finalize(api, receipt, 'a' * 40, 'b' * 64)
    assert not mutations
    with pytest.raises(ValueError, match='MATCHING_INACTIVE'): finalize(api, {**receipt, 'controllerCommit': 'c' * 40}, 'a' * 40, 'b' * 64)
    assert not mutations


def test_activation_requires_original_success_and_owner_protected_legacy_environment(monkeypatch):
    from tools.staging_automation import backend as module
    policy = {'repository': 'Duy-Phan96/GamerHQ', 'ownerId': '272528289', 'bootstrapRun': '41850674216', 'bootstrapCommit': 'a' * 40}
    host = HostBackend({}, policy); host.fresh_verify = Mock()
    monkeypatch.setattr(module, 'github', lambda *args: {'conclusion': None})
    with pytest.raises(Denied, match='INITIAL_BOOTSTRAP'): host.activate()
    host.fresh_verify.assert_not_called()

    def github(p, path):
        if path.endswith('/approvals'): return [{'state': 'approved', 'user': {'id': 272528289}, 'environments': [{'name': 'deployment-security'}]}]
        if path.endswith('/environments/staging'): return {'can_admins_bypass': False, 'protection_rules': []}
        return {'conclusion': 'success', 'head_sha': 'a' * 40, 'head_branch': 'main', 'event': 'workflow_dispatch'}
    monkeypatch.setattr(module, 'github', github)
    with pytest.raises(Denied, match='LEGACY_ENVIRONMENT'): host.activate()
    host.fresh_verify.assert_not_called()


def test_storage_budget_fails_before_build_or_backup_and_never_cleans_data(tmp_path, monkeypatch):
    from tools.staging_automation import backend as module
    import sqlite3
    from types import SimpleNamespace
    path = tmp_path / 'gamerhq.db'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE existing(value TEXT)')
    before = path.read_bytes()
    host = HostBackend({'workRoot': str(tmp_path), 'database': str(path)}, {})
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda unused: SimpleNamespace(free=0))
    with pytest.raises(Denied, match='DISK_BUDGET'): host.storage_budget()
    assert path.read_bytes() == before
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda unused: SimpleNamespace(free=2147483648))
    host.storage_budget()
    assert path.read_bytes() == before
