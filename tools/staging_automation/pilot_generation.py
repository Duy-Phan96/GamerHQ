"""Reviewed policy generation following the completed primary-only recovery.

This module does not start containers, write SQLite or contact Discord.
Enrollment calls the transition under the existing protected operation lock.
"""
import hashlib
import json

from .policy import Denied

BASELINE_COMMIT = '8f450533ec0c9589b6fa375e34bd951f91dff419'
BASELINE_ARCHIVE = 'b84ed81d4371112634200021f24b43f436ac8113c4ecf6d273cd95daeca573e8'
BASELINE_IMAGE = 'sha256:d308a43d80737c26b92a3aea2ef9d822f7aa2987fd701a56e354df722672818b'
GENERATION = {'id': 'recovered-primary-v2', 'baselineCommit': BASELINE_COMMIT,
              'baselineImage': BASELINE_IMAGE, 'baselineAdmission': 'PRIMARY_ONLY',
              'incidentActivationRun': '44283427505'}
COHORT = {'applicationId': '1567098530525351976',
          'guildIds': ['1567098041641738331', '1571908030532948129']}


def validate_generation(policy):
    if (policy.get('tenantPilotGeneration') != GENERATION
            or policy.get('tenantPilot') != COHORT
            or policy.get('hostPromotion', {}).get('commit') != BASELINE_COMMIT
            or policy.get('hostPromotion', {}).get('archiveSha256') != BASELINE_ARCHIVE
            or policy.get('tenantPilotCutover', {}).get('fallback', {}).get('commit') != BASELINE_COMMIT
            or policy['tenantPilotCutover']['fallback'].get('archiveSha256') != BASELINE_ARCHIVE
            or policy.get('tenantPilotActivation', {}).get('commit') == BASELINE_COMMIT
            or 'primaryRecoveryApiRepair' in policy):
        raise Denied('PILOT_CHECKPOINT_REJECTED')


def effective_profile(profile, policy):
    if 'tenantPilotGeneration' not in policy:
        return profile
    validate_generation(policy)
    if (profile.get('environment') != 'staging' or profile.get('stagingBotId') != COHORT['applicationId']
            or profile.get('stagingGuildId') != COHORT['guildIds'][0]
            or profile.get('productionGuildId') in COHORT['guildIds']
            or profile.get('productionBotId') == COHORT['applicationId']):
        raise Denied('PILOT_CHECKPOINT_REJECTED')
    return {**profile, 'pilotGeneration': GENERATION['id']}


def generation_documents(policy, profile, state, incident, *, source, candidate, cutover_digest, preparation_digest):
    """Build the atomic transition only from the verified completed incident."""
    repair = incident.get('recoveryApiRepair', {})
    if (state.get('recoveryRequired') is not False or state.get('enabled') is not True
            or state.get('bootstrapVerified') is not True or incident.get('phase') != 'PILOT_ROLLED_BACK'
            or incident.get('activationRun') != GENERATION['incidentActivationRun']
            or repair.get('image') != BASELINE_IMAGE or repair.get('commit') != BASELINE_COMMIT
            or policy.get('tenantPilot') != COHORT
            or profile.get('stagingBotId') != COHORT['applicationId']
            or profile.get('stagingGuildId') != COHORT['guildIds'][0]
            or profile.get('productionGuildId') in COHORT['guildIds']
            or profile.get('productionBotId') == COHORT['applicationId']):
        raise Denied('RECOVERY_REQUIRES_OWNER')
    new_policy = {**policy, 'controllerCommit': source, 'tenantPilotGeneration': GENERATION,
                  'hostPromotion': {**policy['hostPromotion'], 'commit': BASELINE_COMMIT,
                                    'archiveSha256': BASELINE_ARCHIVE},
                  'tenantPilotActivation': {**candidate, 'workflowSha256': preparation_digest},
                  'tenantPilotCutover': {'workflowSha256': cutover_digest,
                                        'fallback': {'commit': BASELINE_COMMIT, 'archiveSha256': BASELINE_ARCHIVE,
                                                     'ciRun': candidate['baselineCiRun']}}}
    new_policy['tenantPilotActivation'].pop('baselineCiRun')
    new_policy.pop('primaryRecoveryApiRepair', None)
    validate_generation(new_policy)
    new_profile = {**profile, 'applicationHostCommit': BASELINE_COMMIT, 'baseImage': BASELINE_IMAGE}
    new_state = {key: value for key, value in state.items()
                 if key not in {'tenantPilotPreview', 'tenantPilotActivationPrepared', 'challenge', 'usedTokens'}}
    new_state.update(approvedImage=BASELINE_IMAGE, pilotGeneration=GENERATION['id'])
    return new_policy, new_profile, new_state


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def transition(plan, read, write, *, checkpoint=lambda _: None):
    """Resume a sealed root-owned transition; never overwrite unknown content.

    `read` rejects symlinks, wrong ownership/mode and oversized files. `write`
    atomically fsyncs with the existing owner/mode and SELinux label. The plan
    is immutable and persisted before any writes. The launcher fails closed
    while its marker is PREPARED. Callers re-prove live health on every retry.
    """
    for item in plan['files']:
        current = read(item['name'])
        before, after = bytes.fromhex(item['before']), bytes.fromhex(item['after'])
        if current not in (before, after):
            raise Denied('IMMUTABLE_RELEASE_CHANGED')
    for item in plan['files']:
        after = bytes.fromhex(item['after'])
        if read(item['name']) != after:
            write(item['name'], after)
        checkpoint(item['name'])
    if any(read(item['name']) != bytes.fromhex(item['after']) for item in plan['files']):
        raise Denied('RECOVERY_REQUIRES_OWNER')
    return hashlib.sha256(encode(plan)).hexdigest()
