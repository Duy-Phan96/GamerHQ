"""Strict SSH transport plus short-lived GitHub OIDC; never prints tokens."""
import argparse
import base64
import json
import os
import sys
import hashlib
from pathlib import Path
import uuid
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from tools.staging_ssh_client import invoke, StagingAccessError, failure_code
from tools.staging_automation.policy import request, PREFIX, Denied
from tools.staging_automation.oidc import NoRedirects

from tools.staging_automation.policy import ERROR_CODES
from tools.staging_errors import CODES
PUBLIC_ERRORS = ERROR_CODES | {'HOST_' + code for code in CODES} | {'SSH_AUTHENTICATED_ROUTE_REJECTED'}


def launch_failure(stderr):
    """Categorize known public launcher paths; never return raw remote text."""
    if len(stderr.encode()) > 65536: return None
    paths = {
        '/usr/local/libexec/gamerhq-staging-ssh-router': 'SSH_ROUTER',
        '/usr/local/libexec/gamerhq-staging-automation-entry': 'SSH_AUTOMATION_ENTRY',
        '/opt/gamerhq-staging/automation-controller/entry.py': 'AUTOMATION_CONTROLLER',
    }
    for line in stderr.splitlines():
        for path, phase in paths.items():
            if "can't open file '" + path + "'" in line:
                if 'No such file or directory' in line: return phase + '_NOT_FOUND'
                if 'Permission denied' in line: return phase + '_NOT_READABLE'
        if 'Failed to connect to bus' in line: return 'SYSTEMD_USER_BUS_UNAVAILABLE'
    return None


def failure(request, code, authenticated, stderr, *, stdout=''):
    # systemd releases may forward launcher errors on either stream. Parse only
    # bounded allowlisted facts, and never retain either raw stream in errors.
    evidence = stderr + '\n' + stdout
    public = launch_failure(evidence) or 'AUTOMATION_REQUEST_REJECTED'
    if len(evidence.encode()) <= 65536:
        for line in reversed(evidence.splitlines()):
            try: value = json.loads(line)
            except ValueError: continue
            if isinstance(value, dict) and set(value) == {'automationError'} and value['automationError'] in PUBLIC_ERRORS:
                public = value['automationError']; break
    error = StagingAccessError(failure_code(code, stderr), request, exit_code=code, authenticated=authenticated)
    error.evidence['automationFailure'] = public
    RuntimeError.__init__(error, json.dumps(error.evidence))
    return error


def encode(value):
    request(value)
    return PREFIX + ' ' + base64.b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()


def exchange(operation, payload=None):
    body = invoke({'version': 1, 'phase': 'automation', 'operation': operation},
        host=os.environ.get('GAMERHQ_STAGING_SSH_HOST', ''), port=int(os.environ.get('GAMERHQ_STAGING_SSH_PORT', '22')),
        private_key=os.environ.get('GAMERHQ_STAGING_AUTOMATION_SSH_KEY', ''), known_hosts=os.environ.get('GAMERHQ_STAGING_SSH_KNOWN_HOSTS', ''),
        payload=payload, encoder=encode, failure_factory=failure)
    result = json.loads(body)
    if not isinstance(result, dict) or result.get('result') != 'PASS' or result.get('operation') != operation or result.get('productionChanged') is not False: raise Denied('RESPONSE_REJECTED')
    return result


def token(audience):
    url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL', '')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.actions.githubusercontent.com') or parsed.username or parsed.password: raise Denied('OIDC_ENDPOINT_REJECTED')
    req = Request(url + ('&' if parsed.query else '?') + urlencode({'audience': audience}))
    req.add_unredirected_header('Authorization', 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN'])
    with build_opener(NoRedirects()).open(req, timeout=20) as response: body = response.read(32769)
    if len(body) > 32768: raise Denied('OIDC_RESPONSE_LIMIT')
    value = json.loads(body)['value']
    if not isinstance(value, str) or len(value) > 20000: raise Denied('OIDC_RESPONSE_REJECTED')
    return value


def perform(operation, release=None, *, call=exchange, get_token=token):
    challenge = call('challenge', {'operation': operation})
    payload = {'challenge': challenge['challenge'], 'token': get_token(challenge['audience'])}
    if release is not None: payload['release'] = release
    return call(operation, payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['status', 'health', 'prepare', 'install', 'update', 'restart', 'recover', 'smoke', 'history', 'activate', 'deactivate', 'reconcile', 'identity-check'])
    args = parser.parse_args()
    if args.operation == 'identity-check':
        from tools.staging_automation.oidc import verify
        nonce = uuid.uuid4().hex
        policy = {'repository': 'Duy-Phan96/GamerHQ', 'repositoryId': '1375800851', 'ownerId': '272528289',
            'routineWorkflowSha256': hashlib.sha256(Path('.github/workflows/staging-automation.yml').read_bytes()).hexdigest()}
        verify(token('gamerhq-staging-automation:1375800851:' + nonce), policy, 'health', nonce)
        result = {'state': 'WORKLOAD_IDENTITY_VERIFIED', 'vpsAccess': False, 'gatewayStarted': False, 'productionChanged': False}
    elif args.operation == 'status': result = exchange('status')
    elif args.operation == 'reconcile':
        status = exchange('status')
        if status.get('state') != 'ACTIVE': result = status
        else:
            try:
                health = perform('health')
                if health.get('state') != 'HOST_READY': raise Denied('HOST_NOT_READY')
            except StagingAccessError: perform('recover')
            result = perform('smoke')
    else:
        release = json.loads(os.environ['GAMERHQ_STAGING_RELEASE']) if args.operation == 'prepare' and os.environ.get('GAMERHQ_STAGING_RELEASE') else None
        result = perform(args.operation, release)
    print(json.dumps(result))


if __name__ == '__main__':
    try: main()
    except StagingAccessError as error: raise SystemExit(str(error)) from None
    except Denied as error:
        raise SystemExit(json.dumps({'automationError': error.code if error.code in ERROR_CODES else 'CLIENT_REQUEST_REJECTED'})) from None
    except Exception: raise SystemExit('{"automationError":"STAGING_AUTOMATION_FAILED"}') from None
