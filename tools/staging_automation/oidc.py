"""GitHub-signed short-lived workload identity; no JWT contents are logged."""
import base64
import hashlib
import json
import re
import time
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .policy import Denied, SECURITY, WEB

ISSUER = 'https://token.actions.githubusercontent.com'


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise Denied('HTTP_REDIRECT_REJECTED')


def read_url(url):
    with build_opener(NoRedirects()).open(Request(url, headers={'User-Agent': 'GamerHQ-Staging-Policy'}), timeout=20) as response:
        body = response.read(262145)
    if len(body) > 262144: raise Denied('PUBLIC_METADATA_LIMIT')
    return body


def github(policy, path, read=read_url):
    try: return json.loads(read('https://api.github.com/repos/' + policy['repository'] + path))
    except Denied: raise
    except Exception: raise Denied('GITHUB_POLICY_METADATA_UNAVAILABLE') from None


def owner_protected(environment, owner_id):
    return environment.get('can_admins_bypass') is False and any(
        r.get('type') == 'required_reviewers' and any(str(x.get('reviewer', {}).get('id')) == owner_id for x in r.get('reviewers', []))
        for r in environment.get('protection_rules', []))


def verify(token, policy, operation, challenge, *, read=read_url, now=None):
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes
    now = int(time.time() if now is None else now)
    def decode(value):
        if not re.fullmatch('[A-Za-z0-9_-]+', value): raise Denied('OIDC_REJECTED')
        return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    try:
        if not isinstance(token, str) or len(token) > 20000: raise ValueError()
        parts = token.split('.'); header = json.loads(decode(parts[0])); claims = json.loads(decode(parts[1]))
        if len(parts) != 3 or header.get('alg') != 'RS256' or header.get('typ') != 'JWT' or set(header) - {'alg', 'typ', 'kid', 'x5t', 'x5t#S256'}: raise ValueError()
        jwks = json.loads(read(ISSUER + '/.well-known/jwks'))
        matches = [k for k in jwks['keys'] if k['kid'] == header['kid'] and k['kty'] == 'RSA']
        if len(matches) != 1: raise ValueError()
        key = matches[0]
        numbers = rsa.RSAPublicNumbers(int.from_bytes(decode(key['e']), 'big'), int.from_bytes(decode(key['n']), 'big'))
        public = numbers.public_key()
        if public.key_size < 2048: raise ValueError()
        public.verify(decode(parts[2]), (parts[0] + '.' + parts[1]).encode(), padding.PKCS1v15(), hashes.SHA256())
        env = 'deployment-security' if operation in SECURITY else 'staging-automation'
        owner, repo = policy['repository'].split('/')
        subjects = {f'repo:{owner}/{repo}:environment:{env}', f'repo:{owner}@{policy["ownerId"]}/{repo}@{policy["repositoryId"]}:environment:{env}'}
        expected = {'iss': ISSUER, 'aud': 'gamerhq-staging-automation:' + policy['repositoryId'] + ':' + challenge,
            'repository': policy['repository'], 'repository_id': policy['repositoryId'], 'repository_owner_id': policy['ownerId'],
            'ref': 'refs/heads/main', 'ref_type': 'branch', 'runner_environment': 'github-hosted', 'environment': env}
        if any(claims.get(k) != v for k, v in expected.items()) or claims.get('sub') not in subjects: raise ValueError()
        if claims.get('event_name') != 'workflow_dispatch': raise ValueError()
        if any(type(claims.get(k)) is not int for k in ('iat', 'nbf', 'exp')): raise ValueError()
        if not now - 600 <= claims['iat'] <= now + 30 or claims['nbf'] > now + 30 or not now < claims['exp'] <= claims['iat'] + 600: raise ValueError()
        if not re.fullmatch('[A-Za-z0-9_-]{8,128}', claims.get('jti', '')): raise ValueError()
        if not re.fullmatch('[0-9a-f]{40}', claims.get('workflow_sha', '')) or not re.fullmatch('[1-9][0-9]{0,19}', claims.get('run_id', '')): raise ValueError()
        if operation == 'catalog-preview':
            filename = 'staging-catalog-preview.yml'
            digest = policy['catalogPreviewWorkflowSha256']
        elif operation in WEB:
            filename = 'staging-web.yml'
            digest = policy['webDeployment']['workflowSha256']
        elif operation == 'host-promote':
            filename = 'staging-host-promotion.yml'
            digest = policy['hostPromotion']['workflowSha256']
        else:
            filename = 'staging-automation-activate.yml' if operation in SECURITY else 'staging-automation.yml'
            digest = policy['securityWorkflowSha256' if operation in SECURITY else 'routineWorkflowSha256']
        if claims.get('workflow_ref') != policy['repository'] + '/.github/workflows/' + filename + '@refs/heads/main': raise ValueError()
        workflow = read('https://raw.githubusercontent.com/' + policy['repository'] + '/' + claims['workflow_sha'] + '/.github/workflows/' + filename)
        if hashlib.sha256(workflow).hexdigest() != digest: raise ValueError()
        if operation in SECURITY:
            if not owner_protected(github(policy, '/environments/deployment-security', read), policy['ownerId']): raise ValueError()
            reviews = github(policy, '/actions/runs/' + claims['run_id'] + '/approvals', read)
            if not any(r.get('state') == 'approved' and str(r.get('user', {}).get('id')) == policy['ownerId'] and
                any(e.get('name') == 'deployment-security' for e in r.get('environments', [])) for r in reviews): raise ValueError()
        return claims
    except Denied: raise
    except Exception: raise Denied('OIDC_REJECTED') from None
