"""Fail-closed operation admission and private state, independent of workflows."""
import re
import time

ROUTINE = frozenset({'health', 'prepare', 'install', 'update', 'restart', 'recover', 'smoke', 'history'})
SECURITY = frozenset({'activate', 'deactivate', 'host-promote'})
PUBLIC = frozenset({'status', 'challenge'})
OPERATIONS = ROUTINE | SECURITY | PUBLIC
MUTATIONS = frozenset({'prepare', 'install', 'update', 'restart', 'recover', 'host-promote'})
PREFIX = 'gamerhq-staging-automation-v1'
ERROR_CODES = frozenset(['ACCESS_REVOKED', 'AUTOMATION_NOT_ACTIVATED', 'AUTOMATION_REQUEST_REJECTED', 'DISK_BUDGET_REQUIRES_OWNER', 'DATABASE_BUDGET_REQUIRES_OWNER', 'BUILD_RATE_LIMIT', 'CHALLENGE_REJECTED', 'CLOCK_REGRESSION', 'CONCURRENT_OPERATION_REJECTED', 'CONTAINER_IDENTITY_REJECTED', 'CONTAINER_MOUNTS_REJECTED', 'CONTAINER_NETWORK_REJECTED', 'CONTAINER_PRIVILEGES_REJECTED', 'DEPLOYMENT_NOT_READY', 'GITHUB_POLICY_METADATA_UNAVAILABLE', 'HOST_NOT_HEALTHY', 'HOST_NOT_READY', 'HOST_OPERATION_FAILED', 'HTTP_REDIRECT_REJECTED', 'IMMUTABLE_RELEASE_CHANGED', 'INITIAL_BOOTSTRAP_NOT_VERIFIED', 'INITIAL_IMAGE_NOT_VERIFIED', 'LEGACY_ENVIRONMENT_NOT_PROTECTED', 'LEGACY_SOURCE_BINDING_REJECTED', 'MIGRATION_REQUIRES_OWNER', 'MUTATION_RATE_LIMIT', 'OFFLINE_PROBE_FAILED', 'OIDC_REJECTED', 'OIDC_REPLAY_REJECTED', 'OPERATION_NOT_ALLOWED', 'PACKAGE_UPDATE_REQUIRED', 'PAYLOAD_REJECTED', 'POLICY_REJECTED', 'PRIVILEGE_EXPANSION_REQUIRES_OWNER', 'PUBLIC_METADATA_LIMIT', 'PUBLISHER_REJECTED', 'RECOVERY_CIRCUIT_OPEN', 'RECOVERY_REQUIRES_OWNER', 'RELEASE_CHANNEL_REQUIRES_OWNER', 'RELEASE_DESCRIPTOR_REJECTED', 'REQUEST_LIMIT', 'REQUEST_RATE_LIMIT', 'REQUEST_REJECTED', 'RESOURCE_LIMIT_REQUIRES_OWNER', 'RESOURCE_POLICY_REJECTED', 'RESTART_COOLDOWN', 'RESTART_HEALTH_FAILED', 'RESTART_RATE_LIMIT', 'ROOT_EXECUTION_REJECTED', 'UNKNOWN_IMAGE_REQUIRES_OWNER', 'UPDATE_FAILED', 'VERSION_REQUIRES_OWNER'])


ERROR_CODES = ERROR_CODES | frozenset({'HOST_SOURCE_BUILD_FAILED', 'HOST_PROMOTION_NOT_INSTALLED'})


class Denied(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def request(value):
    if not isinstance(value, dict) or set(value) != {'version', 'phase', 'operation'}:
        raise Denied('REQUEST_REJECTED')
    if type(value['version']) is not int or value['version'] != 1 or value['phase'] != 'automation' or value['operation'] not in OPERATIONS:
        raise Denied('OPERATION_NOT_ALLOWED')
    return value['operation']


def validate_policy(value):
    if value.get('schemaVersion') != 1 or value.get('repository') != 'Duy-Phan96/GamerHQ':
        raise Denied('POLICY_REJECTED')
    for key in ('repositoryId', 'ownerId', 'bootstrapRun'):
        if not re.fullmatch('[1-9][0-9]{0,19}', str(value.get(key, ''))): raise Denied('POLICY_REJECTED')
    for key in ('routineWorkflowSha256', 'securityWorkflowSha256', 'publicKeySha256'):
        if not re.fullmatch('[0-9a-f]{64}', value.get(key, '')): raise Denied('POLICY_REJECTED')
    if 'hostPromotion' in value:
        from .host_promotion import COMMIT
        p = value['hostPromotion']
        if set(p) != {'commit', 'archiveSha256', 'workflowSha256'} or p['commit'] != COMMIT or any(not re.fullmatch('[0-9a-f]{64}', p[k]) for k in ('archiveSha256', 'workflowSha256')):
            raise Denied('POLICY_REJECTED')
    if value.get('limits') != {'mutationsPerHour': 12, 'buildsPerHour': 3, 'restartsPerHour': 2, 'restartCooldown': 120}:
        raise Denied('RESOURCE_POLICY_REJECTED')
    return value


def charge(state, operation, now=None):
    now = int(time.time() if now is None else now)
    if state.get('lastNow', 0) > now + 5: raise Denied('CLOCK_REGRESSION')
    state['lastNow'] = now
    events = [e for e in state.get('events', []) if 0 <= now - e['at'] < 3600]
    if len(events) >= 60: raise Denied('REQUEST_RATE_LIMIT')
    if operation in MUTATIONS and sum(e['op'] in MUTATIONS for e in events) >= 12: raise Denied('MUTATION_RATE_LIMIT')
    if operation in ('prepare', 'update', 'host-promote') and sum(e['op'] in ('prepare', 'update', 'host-promote') for e in events) >= 3: raise Denied('BUILD_RATE_LIMIT')
    if operation in ('restart', 'recover', 'host-promote'):
        restarts = [e for e in events if e['op'] in ('restart', 'recover', 'host-promote')]
        if len(restarts) >= 2: raise Denied('RESTART_RATE_LIMIT')
        if restarts and now - restarts[-1]['at'] < 120: raise Denied('RESTART_COOLDOWN')
        if now < state.get('circuitUntil', 0): raise Denied('RECOVERY_CIRCUIT_OPEN')
    state['events'] = events + [{'op': operation, 'at': now}]


def admit(operation, state):
    if operation in MUTATIONS and state.get('recoveryRequired'): raise Denied('RECOVERY_REQUIRES_OWNER')
    if operation == 'host-promote' and (not state.get('enabled') or not state.get('bootstrapVerified')):
        raise Denied('AUTOMATION_NOT_ACTIVATED')
    if operation in ROUTINE - {'health'} and not state.get('enabled'):
        raise Denied('AUTOMATION_NOT_ACTIVATED')
    if operation in {'install', 'update', 'restart', 'recover', 'smoke', 'history'} and not state.get('bootstrapVerified'):
        raise Denied('INITIAL_BOOTSTRAP_NOT_VERIFIED')


def release_allowed(candidate, current, profile):
    """Policy trusts published patch releases from the enrolled publisher only."""
    from hosts.gamerhq.skill_artifacts import version_parts
    from tools.staging_configuration import descriptor
    try: descriptor(candidate)
    except Exception: raise Denied('RELEASE_DESCRIPTOR_REJECTED') from None
    if candidate['repository'] != 'Duy-Phan96/gamerhq-skill-event-manager' or candidate['skillId'] != 'event-manager':
        raise Denied('PUBLISHER_REJECTED')
    old, new = version_parts(current['version']), version_parts(candidate['version'])
    if old[:2] != new[:2] or new[:2] != (0, 1) or new[2] < old[2]: raise Denied('VERSION_REQUIRES_OWNER')
    if (*new[:3], new[3] is None, int(new[3] or 0)) < (*old[:3], old[3] is None, int(old[3] or 0)): raise Denied('VERSION_REQUIRES_OWNER')
    if candidate.get('allowPrerelease') and not current.get('allowPrerelease'): raise Denied('RELEASE_CHANNEL_REQUIRES_OWNER')
    if candidate['version'] == current['version'] and candidate != current: raise Denied('IMMUTABLE_RELEASE_CHANGED')
    if set(candidate['capabilities']) - set(current['capabilities']) or set(candidate['capabilities']) - set(profile['approvedCapabilities']['event-manager']):
        raise Denied('PRIVILEGE_EXPANSION_REQUIRES_OWNER')
    if candidate['migrationMode'] not in ('none', 'backward-compatible'): raise Denied('MIGRATION_REQUIRES_OWNER')
