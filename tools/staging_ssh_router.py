#!/usr/bin/python3 -I
"""Account ForceCommand: bind protocol to OpenSSH's authenticated public key.

Python 3.9 compatible; no payload/credential files, shell or mutable imports.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat

LEGACY = '/usr/local/libexec/gamerhq-staging-entry'
AUTOMATION = '/usr/local/libexec/gamerhq-staging-automation-entry'


def read_file(path, uid, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & (0o077 if private else 0o022) or info.st_size > 32768:
            raise ValueError('SSH_ROUTING_FILE_REJECTED')
        value = os.read(fd, 32769)
        if len(value) > 32768: raise ValueError('SSH_ROUTING_FILE_REJECTED')
        return value.decode('ascii')
    finally: os.close(fd)


def key_digest(kind, encoded):
    raw = base64.b64decode(encoded, validate=True)
    if kind != 'ssh-ed25519' or len(raw) != 51 or raw[:19] != b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':
        raise ValueError('SSH_ROUTING_KEY_REJECTED')
    return hashlib.sha256(raw).hexdigest()


def routes(authorized_keys, automation_digest):
    result = {}
    for line in authorized_keys.splitlines():
        if not line or line.startswith('#'): continue
        match = re.fullmatch(r'restrict,command="(' + re.escape(LEGACY) + '|' + re.escape(AUTOMATION) + r')" (ssh-ed25519) ([A-Za-z0-9+/=]+)(?: [^\r\n]*)?', line)
        if not match: raise ValueError('SSH_ROUTING_KEYS_POLICY_REJECTED')
        entry, kind, encoded = match.groups(); digest = key_digest(kind, encoded)
        if digest in result or (entry == AUTOMATION) != (digest == automation_digest):
            raise ValueError('SSH_ROUTING_KEYS_POLICY_REJECTED')
        result[digest] = entry
    if AUTOMATION not in result.values() or LEGACY not in result.values():
        raise ValueError('SSH_ROUTING_KEYS_POLICY_REJECTED')
    return result


def select(auth_info, command, allowed):
    # AuthenticationMethods publickey produces one method/key line. Extra
    # methods, certificates and ambiguous identities fail closed.
    lines = auth_info.splitlines()
    if len(lines) != 1: raise ValueError('SSH_ROUTING_AUTH_INFO_REJECTED')
    fields = lines[0].split()
    if len(fields) != 3 or fields[0] != 'publickey': raise ValueError('SSH_ROUTING_AUTH_INFO_REJECTED')
    entry = allowed.get(key_digest(fields[1], fields[2]))
    prefix = 'gamerhq-staging-automation-v1' if entry == AUTOMATION else 'gamerhq-staging-v1'
    if entry not in (LEGACY, AUTOMATION) or len(command) > 6000 or command.count(' ') != 1 or command.split(' ', 1)[0] != prefix:
        raise ValueError('SSH_ROUTING_PROTOCOL_REJECTED')
    return entry


def main():
    import pwd
    account = pwd.getpwnam('gamerhq-staging-bridge')
    if os.geteuid() != account.pw_uid or os.geteuid() == 0: raise ValueError('SSH_ROUTING_IDENTITY_REJECTED')
    path = os.environ.get('SSH_USER_AUTH', '')
    if not re.fullmatch(r'/tmp/sshauth\.[A-Za-z0-9]+', path): raise ValueError('SSH_ROUTING_AUTH_INFO_REJECTED')
    policy = json.loads(read_file('/etc/gamerhq-staging/automation-policy.json', 0))
    allowed = routes(read_file(str(Path(account.pw_dir) / '.ssh/authorized_keys'), 0), policy['publicKeySha256'])
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    entry = select(read_file(path, account.pw_uid, private=True), command, allowed)
    # No client-supplied PATH, Python settings or auth-info path reaches children.
    os.execve(entry, [entry], {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': account.pw_dir, 'SSH_ORIGINAL_COMMAND': command})


if __name__ == '__main__':
    try: main()
    except Exception: raise SystemExit('{"automationError":"SSH_AUTHENTICATED_ROUTE_REJECTED"}') from None
