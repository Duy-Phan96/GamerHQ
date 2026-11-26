"""Entry behind the separate forced-command key and existing systemd sandbox."""
import json
import hashlib
import os
from pathlib import Path
import sys
import time
import uuid

from .policy import Denied, request, validate_policy, charge, admit, PUBLIC, WEB
from .oidc import verify


def assert_active_epoch_image(backend, state):
    """Bind routine health/acceptance to the new terminal journal or attested baseline."""
    from .pilot_cutover import journal_path, digest
    from tools.staging_dispatch import protected_file
    generation = backend.policy['tenantPilotGeneration']
    image, commit = generation['baselineImage'], generation['baselineCommit']
    path = journal_path(backend.profile)
    if os.path.lexists(path):
        protected_file(path, private=True, uid=os.geteuid())
        record = json.loads(path.read_bytes())
        prepared = state.get('tenantPilotActivationPrepared')
        if not isinstance(prepared, dict):
            raise Denied('RECOVERY_REQUIRES_OWNER')
        if record.get('binding') != {'generation': generation['id'], 'baselineImage': image,
                'candidateImage': prepared.get('candidateImage'), 'fallbackImage': image,
                'preparedSha256': digest(prepared)}:
            raise Denied('RECOVERY_REQUIRES_OWNER')
        if record.get('phase') == 'PILOT_ACTIVE':
            image, commit = prepared['candidateImage'], backend.policy['tenantPilotActivation']['commit']
        elif record.get('phase') not in {'PILOT_ROLLED_BACK', 'PILOT_STOP_CANCELLED'}:
            raise Denied('RECOVERY_REQUIRES_OWNER')
    current = backend.inspect()
    if current['Image'] != image or current['Config']['Labels'].get('org.opencontainers.image.revision') != commit:
        raise Denied('RECOVERY_REQUIRES_OWNER')


def execute(operation, payload, policy, state, backend, save, *, verifier=verify, now=None):
    now = int(time.time() if now is None else now)
    charge(state, operation, now); save(state)
    active_upgrade = policy.get('tenantPilotGeneration', {}).get('id') == 'active-pilot-rc4-v3'
    cutover = 'tenantPilotCutover' in policy
    path = Path(backend.profile['workRoot']) / 'tenant-pilot-cutover.json' if cutover else None
    if cutover and backend.profile.get('pilotGeneration'):
        from .pilot_cutover import journal_path
        path = journal_path(backend.profile)
    journal = cutover and os.path.lexists(path)
    if operation == 'status':
        preparation = {}
        if 'tenantPilotActivation' in policy:
            from .pilot_activation import preparation_state
            preparation['tenantPilotPreparationState'] = preparation_state(
                state.get('tenantPilotActivationPrepared'), policy['tenantPilotActivation'], now)
        if cutover:
            preparation.update(tenantPilotCutoverEnrolled=True, tenantPilotCutoverJournal=journal)
            if journal:
                from .pilot_cutover import journal_status
                preparation['tenantPilotJournalStatus'] = journal_status(backend.profile)
        if payload is not None: raise Denied('PAYLOAD_REJECTED')
        pilot = policy.get('tenantPilot')
        pilot_digest = hashlib.sha256(json.dumps(pilot, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if pilot else None
        return {'state': 'ACTIVE' if state.get('enabled') else 'NOT_ACTIVATED', 'controllerCommit': policy['controllerCommit'], 'legacyWorkerCommit': policy['legacyWorkerCommit'], 'publicKeySha256': policy['publicKeySha256'], 'bootstrapVerified': bool(state.get('bootstrapVerified')), 'limits': policy['limits'], 'recoveryRequired': bool(state.get('recoveryRequired')), 'hostPromotionSupported': 'hostPromotion' in policy,
                'tenantPilotBindingSha256': pilot_digest,
                'tenantPilotActivationPreparationEnrolled': 'tenantPilotActivation' in policy,
                **preparation,
                'hostPromotionCommit': policy.get('hostPromotion', {}).get('commit'),
                'tenantPilotGeneration': policy.get('tenantPilotGeneration', {}).get('id'),
                'tenantAcceptanceEnrolled': 'tenantAcceptance' in policy,
                'gamesAuditEnrolled': 'gamesAudit' in policy,
                'webArtifactId': policy.get('webDeployment', {}).get('artifactId'),
                'webRunId': policy.get('webDeployment', {}).get('runId'),
                'webCommit': policy.get('webDeployment', {}).get('commit'),
                'webArchiveSha256': policy.get('webDeployment', {}).get('archiveSha256')}
    if operation == 'challenge':
        if not isinstance(payload, dict) or set(payload) != {'operation'} or payload['operation'] in PUBLIC:
            raise Denied('PAYLOAD_REJECTED')
        request({'version': 1, 'phase': 'automation', 'operation': payload['operation']})
        if 'tenantPilotGeneration' in policy and payload['operation'] not in {
                'health', 'smoke', 'tenant-pilot-preview', 'tenant-pilot-activation-prepare', 'tenant-pilot-cutover', 'tenant-acceptance', 'games-audit'}:
            raise Denied('OPERATION_NOT_ALLOWED')
        if payload['operation'] == 'catalog-preview' and 'catalogPreviewWorkflowSha256' not in policy:
            raise Denied('CATALOG_PREVIEW_NOT_ENROLLED')
        if payload['operation'] == 'tenant-pilot-preview' and 'tenantPilot' not in policy:
            raise Denied('OPERATION_NOT_ALLOWED')
        if payload['operation'] == 'tenant-pilot-activation-prepare' and 'tenantPilotActivation' not in policy:
            raise Denied('OPERATION_NOT_ALLOWED')
        if payload['operation'] == 'tenant-pilot-cutover' and not cutover:
            raise Denied('OPERATION_NOT_ALLOWED')
        if payload['operation'] == 'games-audit' and 'gamesAudit' not in policy:
            raise Denied('GAMES_AUDIT_NOT_ENROLLED')
        if payload['operation'] == 'tenant-acceptance' and 'tenantAcceptance' not in policy:
            raise Denied('ACCEPTANCE_POLICY_REJECTED')
        if journal and payload['operation'] not in ('health', 'smoke', 'tenant-pilot-cutover', 'tenant-acceptance', 'games-audit'):
            raise Denied('OPERATION_NOT_ALLOWED')
        if payload['operation'] in WEB and 'webDeployment' not in policy:
            raise Denied('WEB_NOT_ENROLLED')
        nonce = uuid.uuid4().hex
        state['challenge'] = {'nonce': nonce, 'operation': payload['operation'], 'expires': now + 300}; save(state)
        return {'challenge': nonce, 'audience': 'gamerhq-staging-automation:' + policy['repositoryId'] + ':' + nonce}
    extra = {'release'} if operation == 'prepare' else {'artifactUrl'} if operation == 'web-deploy' else set()
    if not isinstance(payload, dict) or set(payload) - ({'token', 'challenge'} | extra) or not {'token', 'challenge'} <= set(payload):
        raise Denied('PAYLOAD_REJECTED')
    challenge = state.get('challenge', {})
    if challenge.get('nonce') != payload['challenge'] or challenge.get('operation') != operation or challenge.get('expires', 0) <= now: raise Denied('CHALLENGE_REJECTED')
    claims = verifier(payload['token'], policy, operation, payload['challenge'], now=now)
    if 'tenantPilotGeneration' in policy and operation not in {
            'health', 'smoke', 'tenant-pilot-preview', 'tenant-pilot-activation-prepare', 'tenant-pilot-cutover', 'tenant-acceptance', 'games-audit'}:
        raise Denied('OPERATION_NOT_ALLOWED')
    seen = {k: exp for k, exp in state.get('usedTokens', {}).items() if exp > now}
    if claims['jti'] in seen or len(seen) >= 256: raise Denied('OIDC_REPLAY_REJECTED')
    seen[claims['jti']] = claims['exp']; state['usedTokens'] = seen; state.pop('challenge', None); save(state)
    if journal and operation not in ('health', 'smoke', 'tenant-pilot-cutover', 'tenant-acceptance', 'games-audit'):
        raise Denied('OPERATION_NOT_ALLOWED')
    if operation == 'activate':
        image = backend.activate(state.get('approvedImage') if state.get('bootstrapVerified') else None)
        state.update(enabled=True, bootstrapVerified=True, approvedImage=image, activationRun=claims['run_id']); save(state)
        return {'state': 'ACTIVATED'}
    if operation == 'deactivate':
        state['enabled'] = False; save(state); return {'state': 'DEACTIVATED'}
    if operation == 'tenant-pilot-cutover' and journal:
        # A protected retry can recover only the already journaled transaction.
        admit(operation, {**state, 'recoveryRequired': False})
    else:
        admit(operation, state)
    if state.get('enabled'): backend.policy_integrity()
    try:
        if active_upgrade and operation in {'health', 'smoke', 'tenant-acceptance', 'games-audit'}:
            assert_active_epoch_image(backend, state)
        if operation == 'games-audit':
            from .games_audit import run
            return run(backend)
        if operation == 'tenant-acceptance':
            from .tenant_acceptance import run
            return run(backend)
        if operation == 'catalog-preview':
            from .catalog_preview import preview
            return preview(backend.profile)
        if operation == 'tenant-pilot-preview':
            if 'tenantPilot' not in policy:
                raise Denied('OPERATION_NOT_ALLOWED')
            state.pop('tenantPilotPreview', None)
            state.pop('tenantPilotActivationPrepared', None)
            save(state)
            result = backend.verify_tenant_pilot(expected_image=state['approvedImage'])
            if result.get('state') == 'PILOT_CORE_PERMISSIONS_REJECTED':
                return result
            state['tenantPilotPreview'] = {
                key: result[key] for key in ('backupBatch', 'backupReceiptSha256', 'baselineHostCommit',
                                             'baselineImage', 'startupOverrideSha256')
            }
            state['tenantPilotPreview']['runId'] = claims['run_id']
            save(state)
            return result
        if operation == 'tenant-pilot-activation-prepare':
            if 'tenantPilotActivation' not in policy:
                raise Denied('PILOT_CHECKPOINT_REJECTED')
            state.pop('tenantPilotActivationPrepared', None)
            state.pop('tenantPilotPreview', None)
            save(state)
            result = backend.prepare_tenant_pilot_activation(state['approvedImage'])
            if result.get('state') == 'PILOT_ACTIVATION_PREPARED':
                state['tenantPilotActivationPrepared'] = {**result, 'runId': claims['run_id']}
            save(state)
            return result
        if operation == 'tenant-pilot-cutover':
            prepared = state.get('tenantPilotActivationPrepared')
            if not cutover or not isinstance(prepared, dict):
                raise Denied('PILOT_CHECKPOINT_REJECTED')
            recovery_only = state.get('recoveryRequired') is True
            # The transaction's baseline remains immutable even after success.
            baseline_image = policy['tenantPilotGeneration']['baselineImage'] if active_upgrade else state['approvedImage']
            result = backend.cutover_tenant_pilot(baseline_image, prepared, claims['run_id'],
                                                  recovery_only=recovery_only)
            if active_upgrade and not recovery_only and result.get('state') == 'PILOT_ACTIVE' and result.get('activationAuthorized') is True:
                # The exact journaled cutover has verified its candidate. Preserve
                # baseline in policy, record active image separately from preparation.
                state['approvedImage'] = prepared['candidateImage']
                save(state)
            if recovery_only:
                if (result.get('state') != 'PILOT_ROLLED_BACK' or result.get('recoveryVerified') is not True
                        or result.get('activationAuthorized') is not False):
                    raise Denied('RECOVERY_REQUIRES_OWNER')
                state['recoveryRequired'] = False
                save(state)
                return {**result, 'recoveryRequired': False}
            return result
        if operation in WEB:
            from .web import WebDeployment
            web = WebDeployment(backend, policy['webDeployment'], state, save)
            if operation == 'web-health':
                return web.health()
            if operation == 'web-rollback':
                return web.rollback()
            return web.deploy(payload.get('artifactUrl'))
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
    policy_bytes = (CONTROL / 'automation-policy.json').read_bytes()
    policy = validate_policy(json.loads(policy_bytes))
    protected_file(PROFILE, private=True, uid=os.geteuid())
    protected_file(CONTROL / 'policy.json')
    profile_bytes = PROFILE.read_bytes()
    profile = json.loads(profile_bytes); validate_profile(profile)
    validate_binding(profile, json.loads((CONTROL / 'policy.json').read_text()))
    from .pilot_generation import effective_profile
    profile = effective_profile(profile, policy)
    if profile['hostSourceCommit'] != policy['legacyWorkerCommit']: raise Denied('LEGACY_SOURCE_BINDING_REJECTED')
    values = environment(); os.environ.clear(); os.environ.update(values)
    verify_rootless(profile)
    root = Path(profile['workRoot'])
    with (root / 'operation.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Denied('CONCURRENT_OPERATION_REJECTED') from None
        if (CONTROL / 'automation-policy.json').read_bytes() != policy_bytes or PROFILE.read_bytes() != profile_bytes:
            raise Denied('POLICY_REJECTED')
        transition = CONTROL / 'pilot-generation-transition.json'
        if transition.exists():
            protected_file(transition)
            if json.loads(transition.read_bytes()).get('phase') != 'COMMITTED':
                raise Denied('RECOVERY_REQUIRES_OWNER')
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
            backend = HostBackend(profile, policy)
            backend.operation_lock = lock.fileno()
            result = execute(operation, envelope['configuration'], policy, state, backend, lambda value: write_record(state_file, value))
        except Exception:
            audit('automation-' + operation, 'FAILED', audit_id=audit_id); raise
        audit('automation-' + operation, 'PASS', audit_id=audit_id)
        print(json.dumps({'result': 'PASS', 'operation': operation, 'auditId': audit_id, 'productionChanged': False, **result}))


def entry():
    try: main()
    except Denied as error: raise SystemExit(json.dumps({'automationError': error.code})) from None
    except Exception: raise SystemExit('{"automationError":"AUTOMATION_REQUEST_REJECTED"}') from None
