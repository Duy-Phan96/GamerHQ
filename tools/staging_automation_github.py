"""One-time administrative handoff, never run with routine automation credentials.

Receives a public matching enrollment status receipt. GitHub administration is
required to protect the legacy environment; no SSH or secret values are read.
"""
import argparse
import json
import os
from urllib.request import Request, urlopen


def finalize(api, receipt, expected_commit, expected_key):
    if receipt.get('result') != 'PASS' or receipt.get('operation') != 'status' or receipt.get('state') != 'NOT_ACTIVATED' or receipt.get('controllerCommit') != expected_commit or receipt.get('publicKeySha256') != expected_key:
        raise ValueError('MATCHING_INACTIVE_ENROLLMENT_RECEIPT_REQUIRED')
    first = api('/actions/runs/41850674216')
    if first.get('conclusion') != 'success' or first.get('head_sha') != '6a444d2051568335a7abd13d872edddde16aae9c':
        raise ValueError('WAIT_FOR_ORIGINAL_OWNER_APPROVED_BOOTSTRAP')
    reviews = api('/actions/runs/41850674216/approvals')
    if not any(r.get('state') == 'approved' and r.get('user', {}).get('id') == 272528289 and any(e.get('name') == 'deployment-security' for e in r.get('environments', [])) for r in reviews):
        raise ValueError('ORIGINAL_OWNER_REVIEW_REQUIRED')
    environment = api('/environments/staging')
    if environment.get('can_admins_bypass') is not False or environment.get('deployment_branch_policy') != {'protected_branches': False, 'custom_branch_policies': True}:
        raise ValueError('LEGACY_ENVIRONMENT_POLICY_DRIFT')
    if any(r.get('type') not in ('required_reviewers', 'branch_policy') for r in environment.get('protection_rules', [])):
        raise ValueError('PRESERVE_ADDITIONAL_OWNER_PROTECTION')
    api('/environments/staging', 'PUT', {'can_admins_bypass': False, 'prevent_self_review': False,
        'reviewers': [{'type': 'User', 'id': 272528289}], 'deployment_branch_policy': environment['deployment_branch_policy']})
    protected = api('/environments/staging')
    from tools.staging_automation.oidc import owner_protected
    if not owner_protected(protected, '272528289'): raise ValueError('LEGACY_PROTECTION_NOT_APPLIED')
    variables = {v['name']: v['value'] for v in api('/actions/variables')['variables']}
    if variables.get('GAMERHQ_STAGING_UPDATES_ENABLED') != 'false' or variables.get('GAMERHQ_DEPLOYMENT_WORKER_ENABLED', 'false') != 'false':
        raise ValueError('DEPLOYMENT_GATE_DRIFT')
    name = 'GAMERHQ_STAGING_AUTOMATION_ENROLLED'
    api('/actions/variables/' + name, 'PATCH', {'name': name, 'value': 'true'})
    return {'state': 'READY_FOR_OWNER_ACTIVATION', 'legacyEnvironmentOwnerProtected': True,
        'productionChanged': False, 'activationRequired': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--receipt', required=True); parser.add_argument('--commit', required=True); parser.add_argument('--key-sha256', required=True)
    args = parser.parse_args()
    def api(path, method='GET', body=None):
        headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'}
        req = Request('https://api.github.com/repos/Duy-Phan96/GamerHQ' + path, method=method, headers=headers, data=json.dumps(body).encode() if body is not None else None)
        with urlopen(req, timeout=30) as r:
            value = r.read(); return json.loads(value) if value else None
    try:
        from pathlib import Path
        print(json.dumps(finalize(api, json.loads(Path(args.receipt).read_text()), args.commit, args.key_sha256)))
    except Exception: raise SystemExit('Administrative staging handoff is not ready; no activation was attempted.') from None
