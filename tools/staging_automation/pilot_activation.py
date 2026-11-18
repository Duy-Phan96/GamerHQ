"""Protected staging preparation for the exact tenant pilot. No live apply."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from .oidc import github
from .policy import Denied

PREVIEW_MAX_AGE = 3600  # Same lifetime as live Staging verification.


def preparation_state(evidence, pin, now):
    """Compute expiry from the live verification time, never the run completion time."""
    if not isinstance(evidence, dict) or evidence.get('state') != 'PILOT_ACTIVATION_PREPARED':
        return 'MISSING'
    preview = evidence.get('preview')
    if (not isinstance(preview, dict) or preview.get('state') != 'PILOT_PREVIEW_VERIFIED'
            or not isinstance(pin, dict) or evidence.get('candidateCommit') != pin.get('commit')
            or evidence.get('activationAuthorized') is not False or evidence.get('gatewayStarted') is not False
            or evidence.get('containerRollbackRehearsal') != 'PASS'):
        return 'REJECTED'
    verified = preview.get('verifiedAt')
    if type(verified) is not int or now < verified:
        return 'REJECTED'
    return 'FRESH' if now - verified <= PREVIEW_MAX_AGE else 'EXPIRED'


def validate_pin(value):
    fields = {'commit': 40, 'archiveSha256': 64, 'workflowSha256': 64}
    if not isinstance(value, dict) or set(value) != {*fields, 'ciRun'}:
        raise Denied('PILOT_ACTIVATION_PIN_REJECTED')
    if any(not isinstance(value[key], str) or not re.fullmatch(r'[0-9a-f]{' + str(size) + '}', value[key])
           for key, size in fields.items()):
        raise Denied('PILOT_ACTIVATION_PIN_REJECTED')
    if not isinstance(value['ciRun'], str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value['ciRun']):
        raise Denied('PILOT_ACTIVATION_PIN_REJECTED')


def prepare_session(backend, approved_image):
    """Called under the protected operation lock after owner-approved OIDC."""
    preview = backend._verify_tenant_pilot(expected_image=approved_image)
    if preview.get('state') != 'PILOT_PREVIEW_VERIFIED':
        return preview
    return prepare(backend, preview, approved_image, now=int(time.time()))


def rehearse_container_rollback(backend, baseline, candidate, fallback=None):
    """Run the real Compose switch and rollback against an isolated service."""
    from hosts.gamerhq.skill_deployment_docker import DockerSkillDeployment

    with tempfile.TemporaryDirectory(dir=backend.profile['workRoot'], prefix='pilotrehearsal') as directory:
        root = Path(directory)
        name = root.name
        compose = root / 'compose.json'
        compose.write_text(json.dumps({'name': name, 'services': {'probe': {
            'image': baseline, 'container_name': name, 'network_mode': 'none', 'read_only': True,
            'user': '10001:10001', 'restart': 'no', 'cap_drop': ['ALL'],
            'security_opt': ['no-new-privileges:true'], 'pids_limit': 32, 'mem_limit': '128m',
            'entrypoint': ['python', '-I', '-B', '-c'], 'command': ['import time; time.sleep(300)'],
        }}}))
        docker = DockerSkillDeployment(base_image=baseline, sdk_wheel=root / 'unused.whl',
                                       sdk_version='', sdk_sha256='', compose_file=compose,
                                       service='probe', container=name, work_root=root, run=backend.run)
        try:
            docker.activate(baseline)
            if docker.current_image() != baseline:
                raise Denied('OFFLINE_PROBE_FAILED')
            docker.stop()
            docker.activate(candidate)
            if docker.current_image() != candidate:
                raise Denied('OFFLINE_PROBE_FAILED')
            docker.stop()
            target = fallback or baseline
            docker.activate(target)
            if docker.current_image() != target:
                raise Denied('OFFLINE_PROBE_FAILED')
        finally:
            backend.run(['docker', 'compose', '--file', str(compose), 'down', '--timeout', '5'])


def prepare(backend, preview, approved_image, *, now, read=github):
    from hosts.gamerhq.skill_staging import verify_remote
    from tools.staging_tenant_rehearsal import approved_binding, rehearsal_binding, startup_override, verify_activation_checkpoint
    from .host_promotion import build_reviewed_overlay, require_baseline_skill, require_runtime_lifecycle, retained_state

    started = time.monotonic()
    policy = backend.policy
    pin = policy['tenantPilotActivation']
    validate_pin(pin)
    baseline = policy['hostPromotion']['commit']
    profile = {**backend.profile, 'tenantPilot': policy['tenantPilot']}
    cohort = approved_binding(profile, policy)
    if preview.get('state') != 'PILOT_PREVIEW_VERIFIED' or preview.get('gatewayStarted') is not False:
        raise Denied('PILOT_CHECKPOINT_REJECTED')
    verified_at = preview.get('verifiedAt')
    candidate_ci = read(policy, '/actions/runs/' + pin['ciRun'])
    if (candidate_ci.get('workflow_id'), candidate_ci.get('head_sha'), candidate_ci.get('conclusion')) != (361334574, pin['commit'], 'success'):
        raise Denied('OFFLINE_PROBE_FAILED')
    container = backend.inspect()
    if container['Image'] != approved_image or backend.health()['state'] != 'HOST_READY':
        raise Denied('PILOT_BASELINE_CHANGED')
    actual = rehearsal_binding(profile, policy, container)
    if actual['baselineHostCommit'] != baseline:
        raise Denied('PILOT_BASELINE_CHANGED')
    def recheck(checked_at):
        try:
            verify_activation_checkpoint(profile['backupRoot'], preview, policy['tenantPilot'], actual,
                                         verified_at=verified_at, now=checked_at)
        except (ValueError, OSError) as error:
            code = str(error)
            raise Denied(code if code in {'PILOT_PREVIEW_EXPIRED', 'PILOT_BASELINE_CHANGED'} else 'PILOT_CHECKPOINT_REJECTED') from None

    recheck(now)
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
    rehearse_container_rollback(backend, approved_image, candidate)
    fallback_evidence = {}
    if 'tenantPilotCutover' in policy:
        pin = policy['tenantPilotCutover']['fallback']
        ci = read(policy, '/actions/runs/' + pin['ciRun'])
        if (ci.get('workflow_id'), ci.get('head_sha'), ci.get('conclusion')) != (361334574, pin['commit'], 'success'):
            raise Denied('OFFLINE_PROBE_FAILED')
        fallback = build_reviewed_overlay(backend, approved_image, pin['commit'], pin['archiveSha256'], baseline, compatibility)
        fallback_override = startup_override(profile, policy, fallback, primary_only=True)
        value = fallback_override['services'][profile['service']]['environment'][PILOT_ENV]
        probe_primary = main.replace('print("PASS")', 'assert len(routes.membership)==2; print("PASS")')
        if backend.run([*probe, '--env', f'{PILOT_ENV}={value}', fallback, '-I', '-B', '-c', probe_primary,
                        PILOT_ENV, profile['stagingGuildId'], profile['stagingGuildId']]) != 'PASS':
            raise Denied('OFFLINE_PROBE_FAILED')
        from .pilot_cutover import digest
        rehearse_container_rollback(backend, approved_image, candidate, fallback)
        fallback_evidence = {'fallbackImage': fallback, 'fallbackCommit': pin['commit'],
                             'fallbackStartupOverrideSha256': digest(fallback_override), 'fallbackRollbackRehearsal': 'PASS'}
    if backend.inspect()['Id'] != container['Id'] or backend.health()['state'] != 'HOST_READY':
        raise Denied('PILOT_BASELINE_CHANGED')
    recheck(now + int(time.monotonic() - started))
    return {'state': 'PILOT_ACTIVATION_PREPARED', 'candidateCommit': policy['tenantPilotActivation']['commit'], 'candidateImage': candidate,
            'startupOverrideSha256': hashlib.sha256(canonical).hexdigest(), 'containerRollbackRehearsal': 'PASS',
            'preview': preview,
            'backupReceiptSha256': preview['backupReceiptSha256'], 'gatewayStarted': False, 'activationAuthorized': False,
            **fallback_evidence}
