"""Fail-closed operation admission and private state, independent of workflows."""
import re
import time

ROUTINE = frozenset({'health', 'prepare', 'install', 'update', 'restart', 'recover', 'smoke', 'history'})
SECURITY = frozenset({'activate', 'deactivate'})
PUBLIC = frozenset({'status', 'challenge'})
OPERATIONS = ROUTINE | SECURITY | PUBLIC
MUTATIONS = frozenset({'prepare', 'install', 'update', 'restart', 'recover'})
PREFIX = 'gamerhq-staging-automation-v1'


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
    if operation in ('prepare', 'update') and sum(e['op'] in ('prepare', 'update') for e in events) >= 3: raise Denied('BUILD_RATE_LIMIT')
    if operation in ('restart', 'recover'):
        restarts = [e for e in events if e['op'] in ('restart', 'recover')]
        if len(restarts) >= 2: raise Denied('RESTART_RATE_LIMIT')
        if restarts and now - restarts[-1]['at'] < 120: raise Denied('RESTART_COOLDOWN')
        if now < state.get('circuitUntil', 0): raise Denied('RECOVERY_CIRCUIT_OPEN')
    state['events'] = events + [{'op': operation, 'at': now}]


def admit(operation, state):
    if operation in MUTATIONS and state.get('recoveryRequired'): raise Denied('RECOVERY_REQUIRES_OWNER')
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
    if candidate['version'] == current['version'] and candidate != current: raise Denied('IMMUTABLE_RELEASE_CHANGED')
    if set(candidate['capabilities']) - set(current['capabilities']) or set(candidate['capabilities']) - set(profile['approvedCapabilities']['event-manager']):
        raise Denied('PRIVILEGE_EXPANSION_REQUIRES_OWNER')
    if candidate['migrationMode'] not in ('none', 'backward-compatible'): raise Denied('MIGRATION_REQUIRES_OWNER')
