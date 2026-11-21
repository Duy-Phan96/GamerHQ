"""Offline comparison of owner-private Games receipts; stdout is aggregate only."""
import argparse
from collections import Counter
import json
from pathlib import Path

from tools.game_catalog_import import name_key

STATES = {'READY', 'NOT_SET_UP', 'HIDDEN', 'SETUP_NEEDED', 'REVIEW', 'UNVERIFIED'}


def validate(receipt, environment):
    if (not isinstance(receipt, dict) or receipt.get('schemaVersion') != 1
            or receipt.get('state') != 'GAMES_AUDIT_COLLECTED' or receipt.get('environment') != environment
            or any(type(receipt.get(key)) is not int or receipt[key] <= 0 for key in ('guildId', 'applicationId'))
            or receipt.get('databaseIntegrity') != 'PASS'
            or any(receipt.get(key) is not False for key in ('databaseChanged', 'discordResourcesChanged', 'gatewayRestarted', 'mutationsAttempted'))
            or receipt.get('inventory') != {'state': 'VERIFIED'}
            or not isinstance(receipt.get('games'), list)):
        raise ValueError('VERIFIED_GAMES_RECEIPT_REQUIRED')
    for row in receipt['games']:
        if (not isinstance(row, dict) or row.get('status') not in STATES
                or not isinstance(row.get('name'), str) or not name_key(row['name'])
                or row.get('logicalKey') != name_key(row['name'])
                or not isinstance(row.get('aliases'), list) or any(not isinstance(v, str) for v in row['aliases'])
                or any(type(row.get(k)) is not bool for k in ('active', 'selectable', 'operationPending'))):
            raise ValueError('GAMES_RECEIPT_SCHEMA_REJECTED')
    return receipt['games']


def compare(production, staging):
    """Alias-overlap components, never Discord IDs or environment-local catalog IDs."""
    unavailable = [env for env, value in (('production', production), ('staging', staging))
                   if isinstance(value, dict) and value.get('schemaVersion') == 1
                   and value.get('state') == 'GAMES_AUDIT_UNAVAILABLE'
                   and all(value.get(key) is False for key in ('databaseChanged', 'discordResourcesChanged', 'gatewayRestarted'))]
    if unavailable:
        known = []
        for env, value in (('production', production), ('staging', staging)):
            if env not in unavailable:
                known.extend({'logicalKey': row['logicalKey'], 'disposition': 'UNVERIFIED',
                              'observedEnvironment': env, 'observed': row} for row in validate(value, env))
        return {'schemaVersion': 1, 'state': 'GAMES_PARITY_COMPARED',
                'coverage': {env: 'UNVERIFIED' if env in unavailable else 'VERIFIED' for env in ('production', 'staging')},
                'counts': {'UNVERIFIED': len(known)}, 'games': known, 'liveCorrectionAuthorized': False}
    p, s = validate(production, 'production'), validate(staging, 'staging')
    if production['guildId'] == staging['guildId']:
        raise ValueError('ENVIRONMENT_ISOLATION_REJECTED')
    vertices = [('production', row) for row in p] + [('staging', row) for row in s]
    keys = [{name_key(v) for v in [row['name'], *row['aliases']]} - {''} for _, row in vertices]
    unseen = set(range(len(vertices)))
    rows = []
    while unseen:
        group = {min(unseen)}
        while True:
            terms = set().union(*(keys[i] for i in group))
            added = {i for i in unseen if terms & keys[i]} - group
            if not added:
                break
            group |= added
        unseen -= group
        sides = {env: [vertices[i][1] for i in sorted(group) if vertices[i][0] == env]
                 for env in ('production', 'staging')}
        collision = (any(len(v) > 1 for v in sides.values())
                     or any(vertices[i][1].get('logicalIdentityConflict') is True for i in group))
        disposition = 'REVIEW' if collision else 'MATCHED' if all(sides.values()) else 'PRODUCTION_ONLY' if sides['production'] else 'STAGING_ONLY'
        fields = ('active', 'selectable', 'status', 'roleVerified', 'roleHierarchySafe',
                  'canonicalChannelVerified', 'operationPending', 'effectiveAccess')
        differences = ([key for key in fields if sides['production'][0].get(key) != sides['staging'][0].get(key)]
                       if disposition == 'MATCHED' else [])
        rows.append({'logicalKey': min(vertices[i][1]['logicalKey'] for i in group),
                     'disposition': disposition, 'differentFields': differences,
                     'production': sides['production'], 'staging': sides['staging'],
                     'channelIntent': 'UNVERIFIED_OWNER_LAUNCH_SELECTION_REQUIRED'})
    return {'schemaVersion': 1, 'state': 'GAMES_PARITY_COMPARED', 'coverage': {'production': 'VERIFIED', 'staging': 'VERIFIED'},
            'counts': dict(Counter(row['disposition'] for row in rows)),
            'games': sorted(rows, key=lambda row: row['logicalKey']),
            'productionCategoryState': production['gamesCategory']['state'],
            'stagingCategoryState': staging['gamesCategory']['state'],
            'categoryAccessDiffers': production['gamesCategory'].get('effectiveAccess') != staging['gamesCategory'].get('effectiveAccess'),
            'liveCorrectionAuthorized': False}


def summary(receipt):
    """Safe for public CI logs/issue summaries: never names, IDs, ACLs or reasons."""
    if receipt.get('state') == 'GAMES_PARITY_COMPARED':
        if (not isinstance(receipt.get('counts'), dict)
                or set(receipt['counts']) - {'MATCHED', 'REVIEW', 'UNVERIFIED', 'PRODUCTION_ONLY', 'STAGING_ONLY'}
                or any(type(v) is not int or v < 0 for v in receipt['counts'].values())):
            raise ValueError('PUBLIC_SUMMARY_REJECTED')
        coverage = receipt.get('coverage')
        if not isinstance(coverage, dict) or set(coverage) != {'production', 'staging'} or any(v not in {'VERIFIED', 'UNVERIFIED'} for v in coverage.values()):
            raise ValueError('PUBLIC_SUMMARY_REJECTED')
        return {'state': 'GAMES_PARITY_COMPARED', 'coverage': coverage,
                'status': 'UNVERIFIED' if 'UNVERIFIED' in coverage.values() else 'COMPARED_NOT_ACCEPTANCE',
                'counts': receipt['counts'], 'liveCorrectionAuthorized': False}
    if receipt.get('state') == 'GAMES_AUDIT_COLLECTED':
        rows = validate(receipt, receipt.get('environment'))
        if receipt.get('environment') not in {'production', 'staging'}:
            raise ValueError('ENVIRONMENT_ISOLATION_REJECTED')
        return {'state': 'GAMES_AUDIT_COLLECTED', 'environment': receipt['environment'],
                'counts': dict(Counter(row['status'] for row in rows)),
                'channelIntentVerified': False, 'databaseChanged': False, 'discordResourcesChanged': False}
    return {'state': 'GAMES_AUDIT_UNAVAILABLE', 'status': 'UNVERIFIED'}


def private_write(path, value):
    import os
    # Never overwrite a pre-existing, symlinked or unexpectedly public receipt.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('production', type=Path)
    parser.add_argument('staging', type=Path)
    parser.add_argument('--private-output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.production.read_text()), json.loads(args.staging.read_text()))
    private_write(args.private_output, result)
    print(json.dumps(summary(result)))


if __name__ == '__main__':
    main()
