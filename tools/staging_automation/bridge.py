"""Entry behind the separate forced-command key and existing systemd sandbox."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

from .policy import Denied, request, validate_policy, charge, admit, PUBLIC
from .oidc import verify


def execute(operation, payload, policy, state, backend, save, *, verifier=verify, now=None):
    now = int(time.time() if now is None else now)
    charge(state, operation, now); save(state)
    if operation == 'status':
        if payload is not None: raise Denied('PAYLOAD_REJECTED')
        return {'state': 'ACTIVE' if state.get('enabled') else 'NOT_ACTIVATED', 'controllerCommit': policy['controllerCommit'], 'legacyWorkerCommit': policy['legacyWorkerCommit'], 'publicKeySha256': policy['publicKeySha256'], 'bootstrapVerified': bool(state.get('bootstrapVerified')), 'limits': policy['limits'], 'recoveryRequired': bool(state.get('recoveryRequired')), 'hostPromotionSupported': 'hostPromotion' in policy,
                'hostPromotionCommit': policy.get('hostPromotion', {}).get('commit')}
    if operation == 'challenge':
        if not isinstance(payload, dict) or set(payload) != {'operation'} or payload['operation'] in PUBLIC:
            raise Denied('PAYLOAD_REJECTED')
        request({'version': 1, 'phase': 'automation', 'operation': payload['operation']})
        nonce = uuid.uuid4().hex
        state['challenge'] = {'nonce': nonce, 'operation': payload['operation'], 'expires': now + 300}; save(state)
        return {'challenge': nonce, 'audience': 'gamerhq-staging-automation:' + policy['repositoryId'] + ':' + nonce}
    if not isinstance(payload, dict) or set(payload) - {'token', 'challenge', 'release'} or not {'token', 'challenge'} <= set(payload): raise Denied('PAYLOAD_REJECTED')
    challenge = state.get('challenge', {})
    if challenge.get('nonce') != payload['challenge'] or challenge.get('operation') != operation or challenge.get('expires', 0) <= now: raise Denied('CHALLENGE_REJECTED')
    claims = verifier(payload['token'], policy, operation, payload['challenge'], now=now)
    seen = {k: exp for k, exp in state.get('usedTokens', {}).items() if exp > now}
    if claims['jti'] in seen or len(seen) >= 256: raise Denied('OIDC_REPLAY_REJECTED')
    seen[claims['jti']] = claims['exp']; state['usedTokens'] = seen; state.pop('challenge', None); save(state)
    if 'release' in payload and operation != 'prepare': raise Denied('PAYLOAD_REJECTED')
    if operation == 'activate':
        image = backend.activate(state.get('approvedImage') if state.get('bootstrapVerified') else None)
        state.update(enabled=True, bootstrapVerified=True, approvedImage=image, activationRun=claims['run_id']); save(state)
        return {'state': 'ACTIVATED'}
    if operation == 'deactivate':
        state['enabled'] = False; save(state); return {'state': 'DEACTIVATED'}
    admit(operation, state)
    if state.get('enabled'): backend.policy_integrity()
    try:
        if operation == 'health': return backend.health()
        if operation == 'prepare': return backend.prepare(payload.get('release'))
        if operation == 'install': return backend.install()
        if operation == 'update':
            result = backend.update()
            if result.get('image'): state['approvedImage'] = result.pop('image'); save(state)
            return result
        if operation == 'host-promote':
            from .host_promotion import promote
            result = promote(backend, expected_image=state['approvedImage'])
            state['approvedImage'] = result.pop('image'); save(state)
            return result
        if operation in ('restart', 'recover'): return backend.restart(state['approvedImage'], recover=operation == 'recover')
        if operation == 'history': return backend.history()
        if operation == 'smoke': return backend.smoke()
        raise Denied('OPERATION_NOT_ALLOWED')
    except Denied as error:
        if error.code == 'RECOVERY_REQUIRES_OWNER': state['recoveryRequired'] = True
        if operation in ('restart', 'recover'): state['circuitUntil'] = now + 3600
        save(state); raise
    except Exception as error:
        if operation in ('restart', 'recover'): state['circuitUntil'] = now + 3600
        save(state)
        from tools.staging_errors import decode_evidence
        evidence = decode_evidence(str(error))
        raise Denied('HOST_' + evidence['stagingFailure'] if evidence else 'AUTOMATION_OPERATION_FAILED') from None


def main():
    import fcntl
    from tools.staging_dispatch import protected_file, PROFILE, CONTROL, environment
    from tools.staging_configuration import validate_binding, audit
    from tools.skill_staging import write_record
    from hosts.gamerhq.skill_staging import validate_profile
    from hosts.gamerhq.staging_rootless import verify_rootless
    from .backend import HostBackend
    if os.geteuid() == 0: raise Denied('ROOT_EXECUTION_REJECTED')
    protected_file(CONTROL / 'automation-policy.json')
    policy = validate_policy(json.loads((CONTROL / 'automation-policy.json').read_text()))
    protected_file(PROFILE, private=True, uid=os.geteuid())
    protected_file(CONTROL / 'policy.json')
    profile = json.loads(PROFILE.read_text()); validate_profile(profile)
    validate_binding(profile, json.loads((CONTROL / 'policy.json').read_text()))
    if profile['hostSourceCommit'] != policy['legacyWorkerCommit']: raise Denied('LEGACY_SOURCE_BINDING_REJECTED')
    values = environment(); os.environ.clear(); os.environ.update(values)
    verify_rootless(profile)
    root = Path(profile['workRoot'])
    with (root / 'operation.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Denied('CONCURRENT_OPERATION_REJECTED') from None
        if (root / 'access-revoked').exists(): raise Denied('ACCESS_REVOKED')
        body = sys.stdin.buffer.read(32769)
        if len(body) > 32768: raise Denied('REQUEST_LIMIT')
        envelope = json.loads(body)
        if set(envelope) != {'request', 'configuration'}: raise Denied('PAYLOAD_REJECTED')
        operation = request(envelope['request'])
        state_file = root / 'automation-state.json'
        if state_file.exists(): protected_file(state_file, private=True, uid=os.geteuid())
        state = json.loads(state_file.read_text()) if state_file.exists() else {'enabled': False}
        audit_id = uuid.uuid4().hex
        audit('automation-' + operation, 'STARTED', audit_id=audit_id)
        try:
            result = execute(operation, envelope['configuration'], policy, state, HostBackend(profile, policy), lambda value: write_record(state_file, value))
        except Exception:
            audit('automation-' + operation, 'FAILED', audit_id=audit_id); raise
        audit('automation-' + operation, 'PASS', audit_id=audit_id)
        print(json.dumps({'result': 'PASS', 'operation': operation, 'auditId': audit_id, 'productionChanged': False, **result}))


def entry():
    try: main()
    except Denied as error: raise SystemExit(json.dumps({'automationError': error.code})) from None
    except Exception: raise SystemExit('{"automationError":"AUTOMATION_REQUEST_REJECTED"}') from None
