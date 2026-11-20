"""Scope interpretation only; synthetic receipts are not live acceptance."""
import copy

import pytest

from services.tenant_acceptance import compare_passes
from tools.tenant_acceptance_scope import assess, FIELDS


MISSING = ('staff', 'support-tickets', 'bot-log', 'games-log', 'mod-commands',
           'mod-log', 'server-log', 'staff-chat', 'staff-suggestions', 'ticket-logs')


def seal(row):
    point = copy.deepcopy({key: row[key] for key in FIELDS})
    row['firstPass'] = point
    row['comparison'] = compare_passes(point, point)
    row['previewFingerprints'] = row['comparison']['semanticFingerprints']
    row['previewStable'] = True


@pytest.fixture
def receipt():
    rows = []
    for guild in ('1', '2'):
        partial = guild == '2'
        row = {'guildId': guild, 'status': 'FAIL' if partial else 'UNVERIFIED',
               'inventory': {'channels': [{'id': guild+'11'}], 'roles': [{'id': guild+'01'}]},
               'preview': {'rows': [{'key': 'managed_category:'+guild+':'+key, 'decision': 'create'} for key in MISSING] if partial else [],
                           'ownership': [], 'foreignLifecycleRows': 0,
                           'counts': {'Already configured': 52 if partial else 55, 'Will create': 10 if partial else 0,
                                      'Will reuse': 0, 'Needs review': 0, 'Blocked': 0},
                           'journal': {'state': 'remaining' if partial else 'complete', 'remaining': 10 if partial else 0,
                                       'pendingCreationCheckpoints': [], 'audit': []}},
               'skills': [{'id': 'event-manager', 'state': {'installed': not partial, 'configured': not partial, 'enabled': False},
                           'runtime': {'status': 'UNVERIFIED', 'reason': 'HOST_API_RUNTIME_FLAG_UNAVAILABLE'}}],
               'lifecycleMismatches': [], 'foreignMappings': [], 'unresolvedMappings': [],
               'templateAcceptance': {'selected': True, 'status': 'FAIL' if partial else 'PASS'}}
        seal(row)
        rows.append(row)
    return {'schemaVersion': 1, 'state': 'TENANT_ACCEPTANCE_COLLECTED', 'status': 'FAIL', 'guilds': rows,
            **dict.fromkeys(('mutationsAttempted', 'databaseChanged', 'discordResourcesChanged', 'gatewayRestarted', 'productionChanged'), False),
            'isolationGuards': {'status': 'PASS', 'mode': 'IN_MEMORY_DISPATCH_WITH_VERIFIED_LIVE_BINDINGS',
                                'liveMutatorsInvoked': False,
                                'paths': dict.fromkeys(('command', 'component', 'modal', 'scheduler', 'skillLifecycle'), True)},
            'hostCrossTenantReadDenials': [{'authorizedGuild': a, 'requestedGuild': b, 'status': 'PASS', 'httpStatus': 403}
                                          for a, b in (('1', '2'), ('2', '1'))]}


def test_partial_template_is_visible_without_failing_independent_platform_scope(receipt):
    before = copy.deepcopy(receipt)
    result = assess(receipt, guild_ids=(1, 2))
    assert result['platformSafety'] == 'PASS' and result['originalReceiptStatus'] == 'FAIL'
    primary, secondary = result['guilds']
    assert primary['templateCompletion']['status'] == 'COMPLETE'
    assert secondary['templateCompletion']['status'] == 'INCOMPLETE'
    assert secondary['templateCompletion']['counts']['Will create'] == 10
    assert len(secondary['templateCompletion']['missingCoreKeys']) == 10
    assert secondary['templateCompletion']['remaining'] == 10
    assert secondary['templateCompletion']['requiresSeparateOwnerConfirmation'] is True
    assert result['authorizesMergeOrDeployment'] is False
    assert receipt == before


def test_no_template_selected_is_a_valid_platform_customer(receipt):
    secondary = receipt['guilds'][1]
    secondary['templateAcceptance'] = {'selected': False, 'status': 'UNVERIFIED'}
    secondary['preview']['journal'] = {'state': 'ABSENT', 'remaining': None, 'pendingCreationCheckpoints': []}
    seal(secondary)
    result = assess(receipt, guild_ids=(1, 2))
    assert result['platformSafety'] == 'PASS'
    assert result['guilds'][1]['templateCompletion']['status'] == 'NOT_SELECTED'


@pytest.mark.parametrize('field', ['foreignMappings', 'unresolvedMappings', 'lifecycleMismatches'])
def test_partial_template_never_excuses_resource_or_lifecycle_violations(receipt, field):
    receipt['guilds'][1][field] = ['conflict']
    assert assess(receipt, guild_ids=(1, 2))['platformSafety'] == 'FAIL'


def test_real_preview_change_is_not_reclassified_as_optional_completion(receipt):
    row = receipt['guilds'][1]
    row['preview']['rows'][0]['decision'] = 'review'
    assert assess(receipt, guild_ids=(1, 2))['platformSafety'] == 'FAIL'


def test_unknown_or_incomplete_collection_cannot_pass_platform_scope(receipt):
    del receipt['guilds'][1]['comparison']
    result = assess(receipt, guild_ids=(1, 2))
    assert result['platformSafety'] == 'UNVERIFIED'
    assert result['guilds'][1]['checks']['twoIndependentCompletePasses'] == 'UNVERIFIED'


@pytest.mark.parametrize('failure', ['http', 'guard', 'write', 'cohort', 'enable', 'foreign_lifecycle'])
def test_platform_boundaries_remain_mandatory(receipt, failure):
    if failure == 'http': receipt['hostCrossTenantReadDenials'][0]['httpStatus'] = 200
    elif failure == 'guard': receipt['isolationGuards']['paths']['modal'] = False
    elif failure == 'write': receipt['mutationsAttempted'] = True
    elif failure == 'cohort': receipt['guilds'][1]['guildId'] = '3'
    elif failure == 'enable':
        receipt['guilds'][1]['skills'][0]['state']['enabled'] = True
        seal(receipt['guilds'][1])
    elif failure == 'foreign_lifecycle':
        receipt['guilds'][1]['preview']['foreignLifecycleRows'] = 1
        seal(receipt['guilds'][1])
    assert assess(receipt, guild_ids=(1, 2))['platformSafety'] == 'FAIL'


def test_unknown_coverage_stays_explicit_and_does_not_authorize_live_mutations(receipt):
    result = assess(receipt, guild_ids=(1, 2))
    assert result['platformSafety'] == 'PASS'
    assert all(item['status'] == 'UNVERIFIED' for item in result['separateCoverage'])
    assert {item['criterion'] for item in result['separateCoverage']} >= {
        'commandRegistry', 'roleMembershipAudit', 'liveMutationPathTests', 'liveSkillRuntimeFlag',
        'freshBackupRestore', 'freshOSFileOwnershipAttestation', 'publicEnrollment', 'productionReadiness'}


def test_pending_creation_checkpoint_never_claims_template_complete(receipt):
    row = receipt['guilds'][0]
    row['preview']['journal']['pendingCreationCheckpoints'] = ['pending-role']
    seal(row)
    result = assess(receipt, guild_ids=(1, 2))
    assert result['guilds'][0]['templateCompletion']['status'] == 'INCOMPLETE'
    assert result['guilds'][0]['templateCompletion']['pendingCreationCheckpoints'] == 1


def test_missing_core_entry_message_remains_an_explicit_template_obligation(receipt):
    row=receipt['guilds'][0]
    row['preview']['counts']['Will create']=1
    row['preview']['rows']=[{'key':'role_message:1:language','kind':'message','status':'MISSING','decision':'MISSING'}]
    seal(row)
    result=assess(receipt,guild_ids=(1,2))
    assert result['platformSafety']=='PASS'
    assert result['guilds'][0]['templateCompletion']['status']=='INCOMPLETE'
    assert result['guilds'][0]['templateCompletion']['missingCoreKeys']==['role_message:1:language']
