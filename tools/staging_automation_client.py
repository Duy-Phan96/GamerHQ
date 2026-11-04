"""Strict SSH transport plus short-lived GitHub OIDC; never prints tokens."""
import argparse
import base64
import json
import os
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from tools.staging_ssh_client import invoke, StagingAccessError
from tools.staging_automation.policy import request, PREFIX, Denied
from tools.staging_automation.oidc import NoRedirects

PUBLIC_ERRORS = frozenset('AUTOMATION_NOT_ACTIVATED INITIAL_BOOTSTRAP_NOT_VERIFIED LEGACY_ENVIRONMENT_NOT_PROTECTED INITIAL_IMAGE_NOT_VERIFIED HOST_NOT_READY UNKNOWN_IMAGE_REQUIRES_OWNER RECOVERY_REQUIRES_OWNER RESTART_RATE_LIMIT RESTART_COOLDOWN RECOVERY_CIRCUIT_OPEN MUTATION_RATE_LIMIT BUILD_RATE_LIMIT REQUEST_RATE_LIMIT OIDC_REJECTED OIDC_REPLAY_REJECTED CHALLENGE_REJECTED CLOCK_REGRESSION CONCURRENT_OPERATION_REJECTED OPERATION_NOT_ALLOWED RESOURCE_LIMIT_REQUIRES_OWNER PUBLISHER_REJECTED VERSION_REQUIRES_OWNER IMMUTABLE_RELEASE_CHANGED PRIVILEGE_EXPANSION_REQUIRES_OWNER MIGRATION_REQUIRES_OWNER RELEASE_DESCRIPTOR_REJECTED OFFLINE_PROBE_FAILED RESTART_HEALTH_FAILED PACKAGE_UPDATE_REQUIRED HOST_OPERATION_FAILED ACCESS_REVOKED PAYLOAD_REJECTED AUTOMATION_OPERATION_FAILED'.split())


def failure(request, code, authenticated, stderr):
    public = 'AUTOMATION_REQUEST_REJECTED'
    if len(stderr.encode()) <= 65536:
        for line in reversed(stderr.splitlines()):
            try: value = json.loads(line)
            except ValueError: continue
            if isinstance(value, dict) and set(value) == {'automationError'} and value['automationError'] in PUBLIC_ERRORS:
                public = value['automationError']; break
    error = StagingAccessError('STAGING_AUTOMATION_REJECTED', request, exit_code=code, authenticated=authenticated)
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
    parser.add_argument('operation', choices=['status', 'health', 'prepare', 'install', 'update', 'restart', 'recover', 'smoke', 'history', 'activate', 'deactivate', 'reconcile'])
    args = parser.parse_args()
    if args.operation == 'status': result = exchange('status')
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
    except Exception: raise SystemExit('{"automationError":"STAGING_AUTOMATION_FAILED"}') from None
