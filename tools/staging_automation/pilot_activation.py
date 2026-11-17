"""Protected staging preparation for the exact tenant pilot. No live apply."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from .oidc import github
from .policy import Denied


def validate_pin(value):
    fields = {'commit': 40, 'archiveSha256': 64, 'workflowSha256': 64}
    if not isinstance(value, dict) or set(value) != {*fields, 'ciRun'}:
        raise Denied('PILOT_ACTIVATION_PIN_REJECTED')
    if any(not isinstance(value[key], str) or not re.fullmatch(r'[0-9a-f]{' + str(size) + '}', value[key])
           for key, size in fields.items()):
        raise Denied('PILOT_ACTIVATION_PIN_REJECTED')
    if not isinstance(value['ciRun'], str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value['ciRun']):
        raise Denied('PILOT_ACTIVATION_PIN_REJECTED')


def verified_run(policy, preview, *, read):
    run_id = preview.get('runId')
    digest = preview.get('backupReceiptSha256')
    if (not isinstance(run_id, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', run_id)
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise Denied('PILOT_CHECKPOINT_REJECTED')
    run = read(policy, '/actions/runs/' + run_id)
    if ((run.get('workflow_id'), run.get('head_branch'), run.get('event'), run.get('conclusion'))
            != (421593554, 'main', 'workflow_dispatch', 'success')):
        raise Denied('PILOT_CHECKPOINT_REJECTED')
    reviews = read(policy, '/actions/runs/' + run_id + '/approvals')
    if not any(row.get('state') == 'approved' and str(row.get('user', {}).get('id')) == policy['ownerId']
               and any(env.get('name') == 'deployment-security' for env in row.get('environments', ())) for row in reviews):
        raise Denied('PILOT_CHECKPOINT_REJECTED')
    # Older preview receipts lack a timestamp. Use the start of the protected
    # run as a conservative lower bound for their verification time.
    try:
        start = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))
        if start.tzinfo is None:
            raise ValueError
        return int(start.timestamp())
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Denied('PILOT_CHECKPOINT_REJECTED') from None


def prepare(backend, preview, approved_image, *, now, read=github):
    from hosts.gamerhq.skill_staging import verify_remote
    from tools.staging_tenant_rehearsal import approved_binding, rehearsal_binding, startup_override, verify_activation_checkpoint
    from .host_promotion import build_reviewed_overlay, require_baseline_skill, require_runtime_lifecycle, retained_state

    policy = backend.policy
    pin = policy['tenantPilotActivation']
    validate_pin(pin)
    baseline = policy['hostPromotion']['commit']
    profile = {**backend.profile, 'tenantPilot': policy['tenantPilot']}
    cohort = approved_binding(profile, policy)
    verified_at = verified_run(policy, preview, read=read)
    candidate_ci = read(policy, '/actions/runs/' + pin['ciRun'])
    if (candidate_ci.get('workflow_id'), candidate_ci.get('head_sha'), candidate_ci.get('conclusion')) != (361334574, pin['commit'], 'success'):
        raise Denied('OFFLINE_PROBE_FAILED')
    container = backend.inspect()
    if container['Image'] != approved_image or backend.health()['state'] != 'HOST_READY':
        raise Denied('PILOT_BASELINE_CHANGED')
    actual = rehearsal_binding(profile, policy, container)
    if actual['baselineHostCommit'] != baseline:
        raise Denied('PILOT_BASELINE_CHANGED')
    try:
        verify_activation_checkpoint(profile['backupRoot'], preview, policy['tenantPilot'], actual,
                                     verified_at=verified_at, now=now)
    except (ValueError, OSError) as error:
        code = str(error)
        raise Denied(code if code in {'PILOT_PREVIEW_EXPIRED', 'PILOT_BASELINE_CHANGED'} else 'PILOT_CHECKPOINT_REJECTED') from None
    verify_remote(profile, approved_pilot=cohort, read_only_artifacts=True)
    release = json.loads(Path(profile['releaseFile']).read_text())
    runtime = backend.skill()
    require_baseline_skill(release, runtime['version'])
    require_runtime_lifecycle(retained_state(profile['database'], profile['stagingGuildId']), runtime)

    def compatibility(git):
        for filename in ('requirements.txt', 'requirements.lock', 'requirements-skills.lock', 'pyproject.toml'):
            if git('rev-parse', baseline + ':' + filename) != git('rev-parse', pin['commit'] + ':' + filename):
                raise Denied('MIGRATION_REQUIRES_OWNER')

    candidate = build_reviewed_overlay(backend, approved_image, pin['commit'], pin['archiveSha256'], baseline, compatibility)
    launch = startup_override(profile, policy, candidate)
    canonical = json.dumps(launch, sort_keys=True, separators=(',', ':')).encode()
    # Both variants are checked with no mounts, no credentials and no network.
    from services.tenant_routing import PILOT_ENV
    binding = launch['services'][profile['service']]['environment'][PILOT_ENV]
    main = ('import os,sys; sys.path.insert(0,"/app"); from services.tenant_routing import pilot_routes; '
            'routes=pilot_routes(os.getenv(sys.argv[1]),primary=int(sys.argv[2]),database="/app/runtime/data/gamerhq.db",get_guild=lambda _:None); '
            'assert (routes is None) if len(sys.argv)==3 else routes.allowed==frozenset(map(int,sys.argv[3:])); print("PASS")')
    probe = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--user', '10001:10001',
             '--security-opt', 'no-new-privileges:true', '--memory', '128m', '--pids-limit', '32', '--entrypoint', 'python']
    argv = [candidate, '-I', '-B', '-c', main, PILOT_ENV, profile['stagingGuildId']]
    if backend.run([*probe, '--env', f'{PILOT_ENV}={binding}', *argv, *map(str, cohort)]) != 'PASS' or backend.run([*probe, *argv]) != 'PASS':
        raise Denied('OFFLINE_PROBE_FAILED')
    return {'state': 'PILOT_ACTIVATION_PREPARED', 'candidateCommit': pin['commit'], 'candidateImage': candidate,
            'startupOverrideSha256': hashlib.sha256(canonical).hexdigest(), 'previewRun': preview['runId'],
            'backupReceiptSha256': preview['backupReceiptSha256'], 'gatewayStarted': False, 'activationAuthorized': False}
