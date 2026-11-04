import ast
import base64
from pathlib import Path

import pytest

from tools.staging_ssh_router import LEGACY, AUTOMATION, key_digest, routes, select
from tools.repair_staging_ssh_routing import ORIGINAL, REPAIRED, verify_effective


def key(char):
    return 'ssh-ed25519 ' + base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + char * 32).decode()


@pytest.fixture
def identities():
    old, new = key(b'a'), key(b'b')
    authorized = f'restrict,command="{LEGACY}" {old}\nrestrict,command="{AUTOMATION}" {new}\n'
    digest = key_digest(*new.split())
    return old, new, authorized, digest


def test_actual_authenticated_key_controls_route_not_client_prefix(identities):
    old, new, authorized, digest = identities; allowed = routes(authorized, digest)
    assert select('publickey ' + new + '\n', 'gamerhq-staging-automation-v1 e30=', allowed) == AUTOMATION
    assert select('publickey ' + old + '\n', 'gamerhq-staging-v1 e30=', allowed) == LEGACY
    with pytest.raises(ValueError): select('publickey ' + new, 'gamerhq-staging-v1 e30=', allowed)
    with pytest.raises(ValueError): select('publickey ' + old, 'gamerhq-staging-automation-v1 e30=', allowed)


@pytest.mark.parametrize('auth', ['', 'password', 'publickey ssh-rsa invalid', 'publickey {}\npublickey {}', 'publickey {} extra', 'publickey {}-cert-v01@openssh.com'])
def test_missing_ambiguous_or_unsupported_auth_identity_is_denied(identities, auth):
    old, new, authorized, digest = identities
    with pytest.raises(ValueError): select(auth.format(new, old), 'gamerhq-staging-automation-v1 e30=', routes(authorized, digest))


@pytest.mark.parametrize('command', ['sh', 'gamerhq-staging-automation-v1 e30=; id', 'gamerhq-staging-automation-v1\te30=', 'gamerhq-staging-automation-v1 ' + 'a' * 6000])
def test_no_shell_or_command_injection(identities, command):
    _, new, authorized, digest = identities
    with pytest.raises(ValueError): select('publickey ' + new, command, routes(authorized, digest))


def test_unforced_duplicate_or_reassigned_key_requires_owner_review(identities):
    old, new, authorized, digest = identities
    for changed in [authorized + new, authorized + authorized, authorized.replace(AUTOMATION, LEGACY), authorized.replace('restrict,', '')]:
        with pytest.raises(ValueError): routes(changed, digest)


def test_repair_retains_all_account_restrictions_and_rejects_auth_env_spoofing():
    assert b'ForceCommand ' in REPAIRED and b'ExposeAuthInfo yes' in REPAIRED
    assert REPAIRED.replace(b'    ExposeAuthInfo yes\n', b'').replace(b'gamerhq-staging-ssh-router', b'gamerhq-staging-entry') == ORIGINAL
    policy = '\n'.join(['authenticationmethods publickey', 'passwordauthentication no', 'kbdinteractiveauthentication no',
        'disableforwarding yes', 'permittty no', 'permittunnel no', 'permituserrc no', 'permituserenvironment no',
        'forcecommand /usr/local/libexec/gamerhq-staging-ssh-router', 'exposeauthinfo yes'])
    verify_effective(policy, True)
    for changed in [policy.replace('disableforwarding yes', 'disableforwarding no'), policy + '\nacceptenv SSH_*', policy + '\nsetenv SSH_USER_AUTH=/tmp/forged']:
        with pytest.raises(ValueError): verify_effective(changed, True)


def test_router_and_installer_support_almalinux_system_python_39():
    root = Path(__file__).resolve().parents[1]
    for name in ['staging_ssh_router.py', 'repair_staging_ssh_routing.py', 'repair_staging_automation_access.py']:
        ast.parse((root / 'tools' / name).read_text(), feature_version=(3, 9))


@pytest.mark.skipif(__import__('os').name != 'posix' or __import__('os').getuid() != 0, reason='POSIX root-owned directory fixture')
def test_restrictive_owner_umask_is_restored_only_on_reviewed_code_directories(tmp_path):
    import os
    from tools.repair_staging_automation_access import restore_directories
    first, second = tmp_path / 'controller', tmp_path / 'package'
    previous = os.umask(0o077)
    try:
        first.mkdir(mode=0o750); second.mkdir(mode=0o750)
    finally: os.umask(previous)
    assert restore_directories((first, second), os.getgid()) == [0o700, 0o700]
    assert first.stat().st_mode & 0o777 == 0o750
    assert second.stat().st_mode & 0o777 == 0o750
    first.chmod(0o770)
    with pytest.raises(ValueError): restore_directories((first, second), os.getgid())
