"""Reviewed policy generation following the completed primary-only recovery.

This module does not start containers, write SQLite or contact Discord.
Enrollment calls the transition under the existing protected operation lock.
"""
import hashlib
import json
import re

from .policy import Denied

BASELINE_COMMIT = '8f450533ec0c9589b6fa375e34bd951f91dff419'
BASELINE_ARCHIVE = 'b84ed81d4371112634200021f24b43f436ac8113c4ecf6d273cd95daeca573e8'
BASELINE_IMAGE = 'sha256:d308a43d80737c26b92a3aea2ef9d822f7aa2987fd701a56e354df722672818b'
GENERATION = {'id': 'recovered-primary-v2', 'baselineCommit': BASELINE_COMMIT,
              'baselineImage': BASELINE_IMAGE, 'baselineAdmission': 'PRIMARY_ONLY',
              'incidentActivationRun': '44283427505'}
COHORT = {'applicationId': '1567098530525351976',
          'guildIds': ['1567098041641738331', '1571908030532948129']}


# Fixed next epoch; never accept a caller-selected baseline, image or release.
ACTIVE_BASELINE_COMMIT = '1899c6f8cc498fefa0510d0e3f70453f6f1aa082'
ACTIVE_BASELINE_ARCHIVE = 'b09ae812bf58fb79bf3b7df21a055d234ec7e4e8dbd5dc84b5cb9dc2bedfa23d'
ACTIVE_BASELINE_IMAGE = 'sha256:f50cc0611c474d38b77cbec0a983043d278c892c628dbaad3ced3c74bb311def'
ACTIVE_BASELINE_CI = '44504256668'
RC4_PIN = {'commit': 'bc090665e6ffd24f16cc26b5f67f666c8e3f98a5',
           'archiveSha256': '57e413bcabd60f8f7f4d0a5d8b36636b0e1c062c3809875a11d2e9264d83ba04',
           'ciRun': '45817521196'}
ACTIVE_GENERATION = {'id': 'active-pilot-rc4-v3', 'baselineCommit': ACTIVE_BASELINE_COMMIT,
                     'baselineImage': ACTIVE_BASELINE_IMAGE, 'baselineAdmission': 'PILOT_ACTIVE',
                     'previousGeneration': GENERATION['id']}


def is_active_upgrade(policy):
    return policy.get('tenantPilotGeneration') == ACTIVE_GENERATION


def validate_generation(policy):
    if is_active_upgrade(policy):
        promotion = policy.get('hostPromotion', {})
        cutover = policy.get('tenantPilotCutover', {})
        activation = policy.get('tenantPilotActivation', {})
        if (policy.get('tenantPilot') != COHORT
                or promotion.get('commit') != ACTIVE_BASELINE_COMMIT
                or promotion.get('archiveSha256') != ACTIVE_BASELINE_ARCHIVE
                or cutover.get('fallback') != {'commit': ACTIVE_BASELINE_COMMIT,
                                               'archiveSha256': ACTIVE_BASELINE_ARCHIVE,
                                               'ciRun': ACTIVE_BASELINE_CI}
                or any(activation.get(key) != value for key, value in RC4_PIN.items())
                or 'primaryRecoveryApiRepair' in policy):
            raise Denied('PILOT_CHECKPOINT_REJECTED')
        return
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
    return {**profile, 'pilotGeneration': policy['tenantPilotGeneration']['id']}


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


def active_upgrade_documents(policy, profile, state, journal, *, source, preparation_digest, cutover_digest):
    """Pure document construction; the installer must attest files/live identity under the lock.

    Preserve the historical inputs for the existing sealed transition. No live
    operation or owner authorization is performed here.
    """
    validate_generation(policy)
    effective_profile(profile, policy)
    prepared = state.get('tenantPilotActivationPrepared')
    pin = policy.get('tenantPilotActivation', {})
    expected = {'baselineImage': BASELINE_IMAGE, 'candidateImage': ACTIVE_BASELINE_IMAGE,
                'fallbackImage': BASELINE_IMAGE, 'generation': GENERATION['id']}
    if (policy.get('tenantPilotGeneration') != GENERATION
            or not isinstance(prepared, dict) or not isinstance(journal, dict)
            or state.get('pilotGeneration') != GENERATION['id']
            or state.get('enabled') is not True or state.get('bootstrapVerified') is not True
            or state.get('recoveryRequired') is not False or state.get('approvedImage') != BASELINE_IMAGE
            or profile.get('baseImage') != BASELINE_IMAGE or profile.get('applicationHostCommit') != BASELINE_COMMIT
            or pin.get('commit') != ACTIVE_BASELINE_COMMIT or pin.get('archiveSha256') != ACTIVE_BASELINE_ARCHIVE
            or pin.get('ciRun') != ACTIVE_BASELINE_CI
            or prepared.get('candidateCommit') != ACTIVE_BASELINE_COMMIT
            or prepared.get('candidateImage') != ACTIVE_BASELINE_IMAGE
            or prepared.get('fallbackImage') != BASELINE_IMAGE
            or prepared.get('generation') != GENERATION['id']
            or prepared.get('state') != 'PILOT_ACTIVATION_PREPARED'
            or prepared.get('activationAuthorized') is not False or prepared.get('gatewayStarted') is not False
            or prepared.get('containerRollbackRehearsal') != 'PASS' or prepared.get('fallbackRollbackRehearsal') != 'PASS'
            or journal.get('phase') != 'PILOT_ACTIVE' or not isinstance(journal.get('checkpoint'), dict)
            or journal.get('binding') != {**expected, 'preparedSha256': hashlib.sha256(
                json.dumps(prepared, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
            or not re.fullmatch(r'[0-9a-f]{64}', journal.get('baselineContainer', ''))
            or journal.get('activationRun') != '44581943871'
            or not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{' + str(size) + '}', value)
                       for value, size in ((source, 40), (preparation_digest, 64), (cutover_digest, 64)))):
        raise Denied('PILOT_CHECKPOINT_REJECTED')
    new_policy = {**policy, 'controllerCommit': source, 'tenantPilotGeneration': ACTIVE_GENERATION,
                  'hostPromotion': {**policy['hostPromotion'], 'commit': ACTIVE_BASELINE_COMMIT,
                                    'archiveSha256': ACTIVE_BASELINE_ARCHIVE},
                  'tenantPilotActivation': {**RC4_PIN, 'workflowSha256': preparation_digest},
                  'tenantPilotCutover': {'workflowSha256': cutover_digest,
                                        'fallback': {'commit': ACTIVE_BASELINE_COMMIT,
                                                     'archiveSha256': ACTIVE_BASELINE_ARCHIVE,
                                                     'ciRun': ACTIVE_BASELINE_CI}}}
    # Retain usedTokens and rate/recovery state. Only discard the now-stale
    # preparation/challenge; original documents belong in sealed history.
    new_state = {key: value for key, value in state.items()
                 if key not in {'tenantPilotPreview', 'tenantPilotActivationPrepared', 'challenge'}}
    new_state.update(approvedImage=ACTIVE_BASELINE_IMAGE, pilotGeneration=ACTIVE_GENERATION['id'])
    new_profile = {**profile, 'applicationHostCommit': ACTIVE_BASELINE_COMMIT, 'baseImage': ACTIVE_BASELINE_IMAGE}
    validate_generation(new_policy)
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
