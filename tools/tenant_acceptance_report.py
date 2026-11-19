"""Render the redacted receipt; no credentials, network, SQL or resource writes."""
import argparse
import json
from pathlib import Path


def summary(receipt):
    lines = ['# Tenant read-only acceptance', '', '**Result: ' + receipt['status'] + '**',
             '', 'Runtime flags and live mutation-path tests marked UNVERIFIED are not acceptance passes.',
             '', '| Guild | Result | Preview stable | Configured | Create | Review | Blocked | Staff verified |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in receipt.get('guilds', []):
        plan = row.get('preview', {})
        counts = plan.get('counts', {})
        lines.append('| ' + ' | '.join(str(value) for value in (row['guildId'], row['status'], row.get('previewStable', 'UNVERIFIED'),
            counts.get('Already configured', '?'), counts.get('Will create', '?'), counts.get('Needs review', '?'),
            counts.get('Blocked', '?'), plan.get('staff', {}).get('verified', 'UNVERIFIED'))) + ' |')
        for skill in row.get('skills', []):
            state = skill['state']
            lines.extend(('', f"Guild {row['guildId']} / {skill['id']}: package {skill['version']}, installed={state['installed']}, configured={state['configured']}, enabled={state['enabled']}, runtime={skill['runtime']['status']}"))
        journal = plan.get('journal', {})
        lines.extend(('', f"Guild {row['guildId']} setup journal: {journal.get('state', 'UNVERIFIED')}; unresolved checkpoints: {len(journal.get('pendingCreationCheckpoints', []))}."))
        for resource in plan.get('rows', []):
            if resource['reason'] != 'NONE':
                lines.append(f"- `{resource['key']}`: {resource['reason']} ({resource['status']}); saved ID {resource['savedId']}, current ID {resource['resourceId']}.")
    lines.extend(('', 'See the structured receipt artifact for inventory, permission masks, ownership mappings, both fingerprints and per-guild lifecycle states.',
                  'No templates or Skills are applied by this operation. Collection failures do not count as acceptance passes.'))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--summary', required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = json.loads(args.receipt.read_bytes())
    except (OSError, ValueError):
        receipt = {'schemaVersion':1,'state':'TENANT_ACCEPTANCE_UNAVAILABLE','status':'UNVERIFIED',
                   'reason':'NO_VERIFIED_RECEIPT','guilds':[]}
        args.receipt.write_text(json.dumps(receipt),encoding='utf-8')
    args.summary.write_text(summary(receipt), encoding='utf-8')


if __name__ == '__main__':
    main()
