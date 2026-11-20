"""Offline scope assessment of an already authenticated, redacted receipt.

Companion to the immutable tenant_acceptance_report client, not a new collector
or deployment gate. Does not authenticate its input, access SQL/Discord/config,
rewrite the original result, or authorize setup, enrollment, rollout or merge.
Verify the protected run/artifact provenance before using this interpretation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


FIELDS = ('inventory', 'preview', 'skills', 'lifecycleMismatches')
PATHS = {'command', 'component', 'modal', 'scheduler', 'skillLifecycle'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def aggregate(values):
    return 'FAIL' if 'FAIL' in values else 'UNVERIFIED' if 'UNVERIFIED' in values else 'PASS'


def empty_list(value):
    return 'UNVERIFIED' if not isinstance(value, list) else 'FAIL' if value else 'PASS'


def stable(row):
    try:
        comparison = row['comparison']
        if comparison['scope'] != 'CURRENT_STATE_V2_AUDIT_DIAGNOSTIC_OWNERSHIP_MULTISET':
            return 'UNVERIFIED'
        first, second = row['firstPass'], {key: row[key] for key in FIELDS}
        if comparison['rawFingerprints'] != [digest(first), digest(second)]:
            return 'FAIL'
        semantic = comparison['semanticFingerprints']
        components = comparison['components']
        if (len(semantic) != 2 or set(components) != set(FIELDS)
                or any(len(pair) != 2 for pair in components.values())
                or any(not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)
                       for pair in (semantic,*components.values()) for value in pair)):
            return 'UNVERIFIED'
        return 'PASS' if (row['previewStable'] is True and semantic[0] == semantic[1]
                          and row['previewFingerprints'] == semantic
                          and all(pair[0] == pair[1] for pair in components.values())) else 'FAIL'
    except (KeyError, TypeError, ValueError):
        return 'UNVERIFIED'


def template(row):
    plan = row.get('preview', {})
    counts, journal = plan.get('counts', {}), plan.get('journal', {})
    names = ('Already configured', 'Will create', 'Will reuse', 'Needs review', 'Blocked')
    valid = all(type(counts.get(key)) is int and counts[key] >= 0 for key in names)
    selected = row.get('templateAcceptance', {}).get('selected')
    if not valid or type(selected) is not bool:
        status = 'UNVERIFIED'
    elif not selected:
        status = 'NOT_SELECTED' if journal.get('state') == 'ABSENT' else 'UNVERIFIED'
    elif (sum(counts[key] for key in names[1:]) or journal.get('pendingCreationCheckpoints')
          or journal.get('remaining') != 0 or journal.get('state') != 'complete'):
        status = 'INCOMPLETE'
    else:
        status = 'COMPLETE'
    return {'status': status, 'originalStatus': row.get('templateAcceptance', {}).get('status', 'UNVERIFIED'),
            'counts': {key: counts[key] for key in names if key in counts},
            'journalState': journal.get('state', 'UNVERIFIED'), 'remaining': journal.get('remaining'),
            'pendingCreationCheckpoints': len(journal.get('pendingCreationCheckpoints', [])),
            'missingCoreKeys': sorted(item['key'] for item in plan.get('rows', [])
                if (item.get('decision') == 'create' or item.get('kind') == 'message' and item.get('status') == 'MISSING')
                and re.fullmatch(r'[a-zA-Z0-9:_-]{1,120}', item.get('key', ''))),
            'requiresSeparateOwnerConfirmation': status == 'INCOMPLETE'}


def assess(receipt, *, guild_ids):
    expected = tuple(str(value) for value in guild_ids)
    if len(expected) != 2 or len(set(expected)) != 2 or any(not value.isdecimal() for value in expected):
        raise ValueError('EXACT_TWO_GUILD_COHORT_REQUIRED')
    rows = receipt.get('guilds', [])
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        rows=[]
    actual = [row.get('guildId') for row in rows]
    checks = {'receiptComplete': 'PASS' if receipt.get('schemaVersion') == 1 and receipt.get('state') == 'TENANT_ACCEPTANCE_COLLECTED' else 'UNVERIFIED',
              'exactCohort': 'PASS' if len(actual)==2 and set(actual)==set(expected) else 'FAIL'}
    flags = ('mutationsAttempted', 'databaseChanged', 'discordResourcesChanged', 'gatewayRestarted', 'productionChanged')
    checks['readOnly'] = aggregate(['PASS' if receipt.get(key) is False else 'FAIL' if receipt.get(key) is True else 'UNVERIFIED' for key in flags])
    guards = receipt.get('isolationGuards', {})
    paths = guards.get('paths', {})
    checks['ingressGuardFixturesWithLiveBindings'] = ('UNVERIFIED' if set(paths) != PATHS else
        'PASS' if guards.get('status') == 'PASS' and all(value is True for value in paths.values())
        and guards.get('liveMutatorsInvoked') is False and guards.get('mode') == 'IN_MEMORY_DISPATCH_WITH_VERIFIED_LIVE_BINDINGS' else 'FAIL')
    denials = receipt.get('hostCrossTenantReadDenials', [])
    pairs = [(item.get('authorizedGuild'), item.get('requestedGuild')) for item in denials]
    checks['liveCrossTenantHostAPI'] = ('UNVERIFIED' if not denials else 'PASS' if len(pairs) == 2
        and set(pairs) == {(expected[0], expected[1]), (expected[1], expected[0])}
        and all(item.get('status') == 'PASS' and item.get('httpStatus') == 403 for item in denials) else 'FAIL')
    guilds = []
    for row in rows:
        plan = row.get('preview', {})
        local = {'twoIndependentCompletePasses': stable(row),
                 'noForeignResourceMappings': empty_list(row.get('foreignMappings')),
                 'noUnresolvedStoredMappings': empty_list(row.get('unresolvedMappings')),
                 'skillLifecycleAgreesWithStorage': empty_list(row.get('lifecycleMismatches')),
                 'noForeignLifecycleRows': 'UNVERIFIED' if type(plan.get('foreignLifecycleRows')) is not int else 'PASS' if plan['foreignLifecycleRows']==0 else 'FAIL'}
        skills = row.get('skills')
        if not isinstance(skills, list):
            local['validSkillLifecycle'] = 'UNVERIFIED'
        else:
            states = [item.get('state', {}) for item in skills]
            local['validSkillLifecycle'] = aggregate(['UNVERIFIED' if any(type(state.get(key)) is not bool for key in ('installed','configured','enabled')) else
                'FAIL' if state['enabled'] and not (state['installed'] and state['configured']) or state['configured'] and not state['installed'] else 'PASS' for state in states])
        guilds.append({'guildId': row.get('guildId'), 'platformSafety': aggregate(local.values()),
                       'checks': local, 'templateCompletion': template(row)})
    checks['guildPlatformChecks'] = aggregate([row['platformSafety'] for row in guilds]) if guilds else 'UNVERIFIED'
    gaps = [{'criterion': key, 'status': receipt.get(key, {}).get('status', 'UNVERIFIED'),
             'reason': receipt.get(key, {}).get('reason', 'NOT_COLLECTED')}
            for key in ('commandRegistry', 'roleMembershipAudit', 'liveMutationPathTests')]
    gaps.extend({'criterion': 'liveSkillRuntimeFlag', 'guildId': row.get('guildId'), 'skillId': item.get('id'),
                 'status': item.get('runtime', {}).get('status', 'UNVERIFIED'),
                 'reason': item.get('runtime', {}).get('reason', 'NOT_COLLECTED')}
                for row in rows for item in (row.get('skills') or []) if item.get('runtime', {}).get('status') != 'PASS')
    gaps.extend({'criterion': key, 'status': 'UNVERIFIED', 'reason': 'NOT_ATTESTED_BY_THIS_COLLECTION'}
                for key in ('freshBackupRestore', 'freshOSFileOwnershipAttestation', 'publicEnrollment', 'productionReadiness'))
    return {'scope': 'READ_ONLY_EXACT_TWO_GUILD_PLATFORM_SAFETY', 'inputFingerprint': digest(receipt),
            'originalReceiptStatus': receipt.get('status', 'UNVERIFIED'), 'platformSafety': aggregate(checks.values()),
            'checks': checks, 'guilds': guilds, 'separateCoverage': gaps,
            'authorizesMergeOrDeployment': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--guild-id', action='append', required=True)
    args = parser.parse_args()
    raw = args.receipt.read_bytes()
    if len(raw) > 262144:
        raise ValueError('ACCEPTANCE_RESPONSE_REJECTED')
    print(json.dumps(assess(json.loads(raw), guild_ids=args.guild_id), indent=2))


if __name__ == '__main__':
    main()
