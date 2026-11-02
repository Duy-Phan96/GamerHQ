"""Verified one-time rootless staging bootstrap; never modifies production Docker.

The operator downloads this file at an immutable commit and verifies its SHA-256.
Root is required only for creating the new identity, ACLs and its user-manager
sandbox. Automated deployment then runs without sudo or production Docker access.
"""
import argparse
import base64
import hashlib
import fnmatch
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path('/opt/gamerhq-staging')
CONFIG = Path('/etc/gamerhq-staging')
WORKER = ROOT / 'deployment-worker'
ACCOUNT = 'gamerhq-staging-bridge'
HOME = Path('/var/lib/gamerhq-staging-bridge')
ENTRY = Path('/usr/local/libexec/gamerhq-staging-entry')
STATE = Path('/var/lib/gamerhq-staging-bootstrap')
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
BLOCKED = ('/opt/gamerhq', '/etc/gamerhq', '/run/docker.sock', '/var/lib/docker', '/root')


def run(args, **kwargs):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=1200, **kwargs).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError('Setup command ' + Path(args[0]).name + ' failed (exit ' + str(error.returncode) + '); private stderr was withheld') from None


def public_key(text):
    parts = text.strip().split()
    if len(parts) not in (2, 3) or parts[0] != 'ssh-ed25519':
        raise ValueError('Supply one dedicated Ed25519 public key without authorized_keys options.')
    data = base64.b64decode(parts[1], validate=True)
    if len(data) != 51 or data[:19] != b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':
        raise ValueError('Invalid Ed25519 public key.')
    return 'ssh-ed25519 ' + parts[1]


def write_new(path, content, mode=0o600):
    with path.open('x', encoding='utf-8', newline='\n') as output:
        output.write(content)
    path.chmod(mode)


def install_ssh_restriction(path, body):
    created = not path.exists()
    if created:
        write_new(path, body, 0o644)
    elif path.read_text() != body:
        raise PermissionError('Existing SSH staging policy differs; it was not overwritten')
    try:
        run(['sshd', '-t'])
        effective = run(['sshd', '-T', '-C', 'user=' + ACCOUNT + ',host=localhost,addr=127.0.0.1'])
        if not all(value in effective.splitlines() for value in ('authenticationmethods publickey', 'passwordauthentication no',
                'kbdinteractiveauthentication no', 'disableforwarding yes', 'permittty no', 'forcecommand ' + str(ENTRY))):
            raise RuntimeError('The per-account SSH restrictions did not take effect; global SSH policy was not weakened')
    except Exception:
        if created:
            path.unlink()  # only our just-created file; never an existing owner policy
        raise


def record(value):
    with tempfile.NamedTemporaryFile(mode='w', dir=STATE, prefix='state-', delete=False) as output:
        json.dump(value, output, indent=2)
        temporary = Path(output.name)
    temporary.chmod(0o600); temporary.replace(STATE / 'state.json')


def package_plan(output):
    """Never remove/upgrade installed packages or install a rootful daemon."""
    forbidden = {'docker-ce', 'docker-ce-cli', 'docker.io', 'containerd', 'containerd.io', 'runc', 'openssh-server', 'systemd', 'libc6'}
    for line in output.splitlines():
        if line.startswith('Remv ') or line.startswith('Inst ') and (' [' in line or line.split()[1].split(':')[0] in forbidden):
            raise PermissionError('Prerequisite installation would change existing infrastructure; no packages were installed.')


def distribution(path=Path('/etc/os-release')):
    values = {}
    for line in path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value.strip().strip('\"\'')
    names = {values.get('ID', ''), *values.get('ID_LIKE', '').split()}
    family = 'rhel' if names & {'rhel', 'almalinux', 'rocky', 'centos', 'fedora'} else 'debian' if names & {'debian', 'ubuntu'} else 'unknown'
    return {'id': values.get('ID', 'unknown'), 'version': values.get('VERSION_ID', ''), 'family': family}


def python_runtime():
    """Test the installed interpreter, venv, pip and required stdlib, not RPM/DEB names."""
    for name in ('python3.12', 'python3.14', 'python3.13'):
        python = shutil.which(name)
        if not python:
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='gamerhq-venv-check-') as directory:
                run([python, '-m', 'venv', directory])
                executable = str(Path(directory) / 'bin/python')
                run([executable, '-I', '-c', 'import sys, ssl, sqlite3; assert sys.version_info >= (3,12)'])
                run([executable, '-I', '-m', 'pip', '--version'])
            return python
        except RuntimeError:
            continue
    return None


def dbus_provider(roots=(Path('/usr/lib/systemd/user'), Path('/lib/systemd/user'))):
    # RHEL uses dbus-broker; Debian commonly uses dbus-daemon. Follow unit aliases.
    socket = any((root / 'dbus.socket').is_file() for root in roots)
    provider = any((root / name).is_file() for root in roots for name in ('dbus.service', 'dbus-broker.service'))
    return socket and provider and bool(shutil.which('dbus-broker-launch') or shutil.which('dbus-daemon'))


def selinux_status():
    enforce = Path('/sys/fs/selinux/enforce')
    if not enforce.exists():
        return 'Disabled'
    if not shutil.which('getenforce') or not shutil.which('selinuxenabled'):
        raise RuntimeError('Active SELinux requires getenforce and selinuxenabled (RHEL: libselinux-utils); policy was not changed')
    status = run(['getenforce'])
    if status not in ('Enforcing', 'Permissive') or status != ('Enforcing' if enforce.read_text().strip() == '1' else 'Permissive'):
        raise RuntimeError('SELinux runtime state could not be verified; policy was not changed')
    return status


def prerequisites(install=False):
    distro = distribution()
    required = {'git': 'git', 'setfacl': 'acl', 'newuidmap': 'uidmap', 'newgidmap': 'uidmap',
        'rootlesskit': 'rootlesskit',
        'slirp4netns': 'slirp4netns'}
    missing = sorted({package for command, package in required.items() if not shutil.which(command)})
    if not dbus_provider():
        missing.append('dbus-user-session')
    python = python_runtime()
    if python is None:
        missing += ['python3.12', 'python3.12-venv']
    if missing and install and distro['family'] == 'debian' and shutil.which('apt-get'):
        args = ['apt-get', '--no-upgrade', '--no-install-recommends', 'install', *sorted(set(missing))]
        try:
            plan = run([args[0], '--simulate', *args[1:]])
        except RuntimeError:
            raise RuntimeError('Required prerequisites are unavailable from configured package sources: ' + ', '.join(sorted(set(missing))) + '; no existing packages or repositories were changed') from None
        package_plan(plan)
        run(['apt-get', '-y', *args[1:]], env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l'})
        return prerequisites(False)
    if missing:
        if distro['family'] == 'rhel':
            hints = {'uidmap': 'shadow-utils (newuidmap/newgidmap)', 'dbus-user-session': 'dbus-broker or dbus-daemon user socket/service',
                'python3.12-venv': 'working Python 3.12+ venv with pip (no separate RHEL venv package)'}
            missing = [hints.get(value, value) for value in missing]
        raise RuntimeError('Missing runtime capabilities on ' + distro['id'] + ' ' + distro['version'] + ': ' + ', '.join(sorted(set(missing))) + '. RHEL packages are never installed automatically; no production Docker fallback is permitted.')
    for command in ('docker', 'dockerd', 'systemctl', 'systemd-run', 'loginctl', 'runuser', 'useradd', 'getent', 'ssh-keygen', 'sshd'):
        if not shutil.which(command):
            raise RuntimeError('Existing infrastructure prerequisite missing: ' + command)
    if Path('/proc/1/comm').read_text().strip() != 'systemd' or not Path('/sys/fs/cgroup/cgroup.controllers').exists():
        raise RuntimeError('Rootless staging requires systemd and cgroup v2; no global kernel/SSH policy will be weakened.')
    for path in ('/proc/sys/user/max_user_namespaces', '/proc/sys/kernel/unprivileged_userns_clone'):
        if Path(path).exists() and int(Path(path).read_text()) <= 0:
            raise RuntimeError('User namespaces are disabled by host policy: ' + path + '; no global sysctl was changed')
    selinux_status()
    ssh = run(['sshd', '-T']).splitlines()
    if 'permituserenvironment no' not in ssh or any(fnmatch.fnmatch(name, pattern) for line in ssh if line.startswith('acceptenv ')
            for pattern in line.split()[1:] for name in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'ENV', 'BASH_ENV')):
        raise PermissionError('Existing SSH environment policy permits loader/shell injection; owner policy review is required before setup')
    return python


def preflight(commit, key):
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise PermissionError('Run this verified bootstrap in the existing owner-controlled root SSH session.')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('The exact immutable Host commit is required.')
    key = public_key(key)
    if STATE.exists():
        path = STATE / 'state.json'
        if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
            raise PermissionError('The existing bootstrap state is not protected.')
        state = json.loads(path.read_text())
        if state.get('commit') != commit or state.get('publicKey') != key or state.get('schemaVersion') != 2:
            raise PermissionError('Existing bootstrap state belongs to another source/key; review rather than overwrite it.')
        return state
    for path in (ROOT, CONFIG, HOME, ENTRY, Path('/etc/gamerhq/staging'), Path('/etc/sudoers.d/gamerhq-staging-bridge'),
            Path('/usr/local/libexec/gamerhq-staging-dispatch'), Path('/etc/ssh/sshd_config.d/90-gamerhq-staging.conf')):
        if path.exists() or path.is_symlink():
            raise FileExistsError('Unknown/legacy staging target already exists: ' + str(path) + '. No existing target was overwritten.')
    account = subprocess.run(['getent', 'passwd', ACCOUNT], capture_output=True)
    if account.returncode != 2:
        raise PermissionError('The deployment account already exists or account lookup failed.')
    return {'schemaVersion': 2, 'commit': commit, 'publicKey': key, 'phase': 'PREFLIGHT', 'workerEnabled': False, 'gatewayStarted': False}


def prepare(commit, key, *, apply=False, approval='', install_prerequisites=False):
    state = preflight(commit, key)
    if not apply:
        state = {**state, 'python': prerequisites(False), 'productionChanged': False}
        return state
    if approval != 'PREPARE ISOLATED STAGING':
        raise PermissionError('Explicit one-time isolated staging preparation approval is required.')
    python = prerequisites(install_prerequisites)
    if sys.version_info < (3, 12):
        os.execv(python, [python, str(Path(__file__).resolve()), *sys.argv[1:]])
    os.umask(0o077)
    selinux = selinux_status()
    if not STATE.exists():
        STATE.mkdir(mode=0o700)
        record(state)
    if state['phase'] == 'PREFLIGHT':
        # A root-protected state marker makes interrupted setup retryable. Unknown
        # pre-existing installations never enter this branch.
        ROOT.mkdir(mode=0o700, exist_ok=True)
        clean = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'PIP_CONFIG_FILE': '/dev/null'}
        if not WORKER.exists():
            run(['git', 'clone', '--no-checkout', REPOSITORY, str(WORKER)], env=clean)
        run(['git', '-C', str(WORKER), 'checkout', '--detach', commit], env=clean)
        if run(['git', '-C', str(WORKER), 'rev-parse', 'HEAD'], env=clean) != commit:
            raise ValueError('Worker source does not match the approved immutable commit.')
        if (WORKER / 'tools/prepare_staging_access.py').read_bytes() != Path(__file__).read_bytes():
            raise ValueError('Downloaded installer differs from the exact worker source; refusing execution.')
        run([python, '-m', 'venv', str(WORKER / '.venv')], env=clean)
        run([str(WORKER / '.venv/bin/python'), '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple', '--disable-pip-version-check', '-r', str(WORKER / 'requirements.lock')], env=clean)
        run([str(WORKER / '.venv/bin/python'), '-m', 'pip', 'check'], env=clean)
        account = subprocess.run(['getent', 'passwd', ACCOUNT], capture_output=True)
        if account.returncode == 2:
            run(['useradd', '--user-group', '--no-create-home', '--password', '*', '--home-dir', str(HOME), '--shell', '/bin/sh', ACCOUNT])
        import pwd
        identity = pwd.getpwnam(ACCOUNT)
        if identity.pw_uid <= 0 or identity.pw_dir != str(HOME):
            raise PermissionError('The new deployment identity does not match its fixed home.')
        if set(os.getgrouplist(ACCOUNT, identity.pw_gid)) != {identity.pw_gid}:
            raise PermissionError('The deployment identity must not inherit supplementary production/admin groups.')
        state.update(uid=identity.pw_uid, gid=identity.pw_gid, phase='IDENTITY')
        record(state)
    uid, gid = state['uid'], state['gid']
    if state['phase'] == 'IDENTITY':
        subuids = []
        for path in ('/etc/subuid', '/etc/subgid'):
            entries = [line.split(':') for line in Path(path).read_text().splitlines() if line.startswith(ACCOUNT + ':')]
            if len(entries) != 1 or int(entries[0][2]) < 65536 or int(entries[0][1]) <= 10001:
                raise RuntimeError('The new account needs non-overlapping subordinate UID/GID ranges of at least 65536; no existing ranges were changed.')
            subuids.append(int(entries[0][1]))
        HOME.mkdir(mode=0o755, exist_ok=True); HOME.chmod(0o755)
        for name in ('.config', '.local', '.cache', '.docker'):
            path = HOME / name; path.mkdir(mode=0o700, exist_ok=True); os.chown(path, uid, gid)
        ROOT.chmod(0o750); os.chown(ROOT, 0, gid)
        for name in ('data', 'backups', 'artifacts', 'worker'):
            path = ROOT / name; path.mkdir(mode=0o700, exist_ok=True); os.chown(path, uid, gid)
        for name in ('data', 'backups'):
            # Rootless container UID 10001 maps to subordinate start + 10000.
            acl = f'u:{uid}:rwx,u:{subuids[0]+10000}:rwx,d:u::rwx,d:u:{uid}:rwx,d:u:{subuids[0]+10000}:rwx,d:g::---,d:m::rwx,d:o::---'
            run(['setfacl', '-m', acl, str(ROOT / name)])
        CONFIG.mkdir(mode=0o750, exist_ok=True); os.chown(CONFIG, 0, gid); CONFIG.chmod(0o750)
        settings = CONFIG / 'settings'; settings.mkdir(mode=0o700, exist_ok=True); os.chown(settings, uid, gid)
        client = CONFIG / 'docker-client'; client.mkdir(mode=0o750, exist_ok=True); os.chown(client, 0, gid); client.chmod(0o750)
        if not (client / 'config.json').exists(): write_new(client / 'config.json', '{"currentContext":"default"}\n', 0o640)
        if not (CONFIG / 'gitconfig').exists(): write_new(CONFIG / 'gitconfig', '[safe]\n\tdirectory = ' + str(WORKER) + '\n', 0o640)
        for path in (client / 'config.json', CONFIG / 'gitconfig'): os.chown(path, 0, gid)
        # Protect only this newly cloned worker. Venv interpreter symlinks are
        # intentional; source-controlled symlinks are forbidden before execution.
        for path in [WORKER, *WORKER.rglob('*')]:
            if path.is_symlink():
                if not path.is_relative_to(WORKER / '.venv'):
                    raise ValueError('Worker sources cannot contain symbolic links.')
                os.lchown(path, 0, gid)
            else:
                os.chown(path, 0, gid)
                path.chmod(0o750 if path.is_dir() or path.stat().st_mode & 0o100 else 0o640)
        dropin = Path(f'/etc/systemd/system/user@{uid}.service.d')
        dropin.mkdir(mode=0o755, exist_ok=True)
        if not (dropin / 'gamerhq-staging.conf').exists():
            if subprocess.run(['systemctl', 'is-active', '--quiet', f'user@{uid}.service']).returncode == 0:
                raise PermissionError('An existing user manager already uses the new UID; refusing to change another session')
            writable = [f'/run/user/{uid}', *[str(HOME / name) for name in ('.config', '.local', '.cache', '.docker')],
                *[str(ROOT / name) for name in ('data', 'backups', 'artifacts', 'worker')], str(CONFIG / 'settings')]
            write_new(dropin / 'gamerhq-staging.conf', '[Service]\nDelegate=cpu cpuset io memory pids\nMemoryMax=1G\nCPUQuota=50%\nTasksMax=2048\n'
                'Environment=HOME=' + str(HOME) + '\nEnvironment=XDG_RUNTIME_DIR=/run/user/' + str(uid) + '\n'
                'Environment=XDG_CONFIG_HOME=' + str(HOME / '.config') + '\nEnvironment=XDG_DATA_HOME=' + str(HOME / '.local/share') + '\n'
                'Environment=XDG_CACHE_HOME=' + str(HOME / '.cache') + '\n'
                # dbus.socket startup uses the manager's private transport. A
                # pre-set session-bus address can deadlock socket activation.
                'UnsetEnvironment=DBUS_SESSION_BUS_ADDRESS\n'
                'ProtectSystem=strict\nPrivateTmp=yes\nReadWritePaths=' + ' '.join('-' + p for p in writable) + '\nInaccessiblePaths=' + ' '.join('-' + p for p in BLOCKED) + '\n', 0o644)
        run(['systemctl', 'daemon-reload'])  # no production service restart
        run(['loginctl', 'enable-linger', ACCOUNT])
        run(['systemctl', 'start', f'user@{uid}.service'])
        state['phase'] = 'DAEMON'; record(state)
    values = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(HOME), 'XDG_RUNTIME_DIR': f'/run/user/{uid}',
        'XDG_CONFIG_HOME': str(HOME / '.config'), 'XDG_DATA_HOME': str(HOME / '.local/share'), 'XDG_CACHE_HOME': str(HOME / '.cache'),
        'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{uid}/bus'}
    def user(*args):
        return run(['runuser', '-u', ACCOUNT, '--', 'env', '-i', *[k + '=' + v for k,v in values.items()], *args])
    if state['phase'] == 'DAEMON':
        # Check the actual new user's bus/manager, not the owner's root session.
        user('systemctl', '--user', 'start', 'dbus.socket')
        user('systemctl', '--user', 'is-active', 'dbus.socket')
        user('systemctl', '--user', 'show-environment')
        # The real sandboxed daemon below exercises RootlessKit, UID/GID maps,
        # networking and storage together. A bare RootlessKit transient unit
        # lacks the launcher's copy-up mounts and is not a valid daemon probe.
        # Use the exact vendored upstream launcher with the existing daemon binary.
        # No Docker CE package repository or rootful daemon replacement is needed.
        vendor = WORKER / 'deploy/staging/rootless'
        upstream = json.loads((vendor / 'UPSTREAM.json').read_text())
        launcher = vendor / 'dockerd-rootless.sh'
        if hashlib.sha256(launcher.read_bytes()).hexdigest() != upstream['sha256']['dockerd-rootless.sh']:
            raise ValueError('Pinned upstream rootless launcher checksum differs')
        for path in (HOME / '.config/systemd', HOME / '.config/systemd/user'):
            path.mkdir(mode=0o700, exist_ok=True); os.chown(path, uid, gid)
        unit = HOME / '.config/systemd/user/docker.service'
        if not unit.exists():
            write_new(unit, '[Unit]\nDescription=Isolated rootless GamerHQ staging Docker\nRequires=dbus.socket\n'
                '[Service]\nEnvironment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
                'Environment=HOME=' + str(HOME) + '\nEnvironment=XDG_RUNTIME_DIR=/run/user/' + str(uid) + '\n'
                'Environment=XDG_DATA_HOME=' + str(HOME / '.local/share') + '\nEnvironment=XDG_CONFIG_HOME=' + str(HOME / '.config') + '\n'
                'Environment=XDG_CACHE_HOME=' + str(HOME / '.cache') + '\n'
                'Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(uid) + '/bus\n'
                'Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_DISABLE_HOST_LOOPBACK=true\nExecStart=' + str(launcher) + '\n'
                'Type=notify\nNotifyAccess=all\nDelegate=yes\nKillMode=mixed\nTimeoutStartSec=90\nTimeoutStopSec=45\n'
                'Restart=on-failure\nRestartSec=2\nMemoryMax=1G\nCPUQuota=50%\nTasksMax=1024\n'
                '[Install]\nWantedBy=default.target\n', 0o644)
        user('systemctl', '--user', 'daemon-reload')
        user('systemctl', '--user', 'enable', '--now', 'docker.service')
        values.update(DOCKER_HOST=f'unix:///run/user/{uid}/docker.sock', DOCKER_CONFIG=str(CONFIG / 'docker-client'))
        info = {}
        for _ in range(20):
            try:
                info = json.loads(user('docker', 'info', '--format', '{{if .ID}}{{json .}}{{end}}'))
                if info.get('ID') and info.get('SecurityOptions'):
                    break
            except (RuntimeError, json.JSONDecodeError):
                pass
            time.sleep(1)
        if not any('rootless' in item for item in info.get('SecurityOptions') or []) or info.get('CgroupDriver') != 'systemd' or str(info.get('CgroupVersion')) != '2':
            raise RuntimeError('Rootless daemon/resource delegation did not verify; no rootful fallback is permitted.')
        if info.get('Driver') not in ('overlay2', 'fuse-overlayfs', 'btrfs', 'vfs', 'overlayfs'):
            raise RuntimeError('Rootless storage driver could not be verified')
        if not Path(info.get('DockerRootDir', '/')).is_relative_to(HOME):
            raise RuntimeError('Rootless storage is outside the dedicated staging home')
        if selinux_status() != selinux:
            raise RuntimeError('SELinux state changed during setup; staging acceptance refused')
        state['capabilities'] = {'distribution': distribution(), 'python': python, 'venv': True,
            'userBus': True, 'userNamespace': True, 'cgroupVersion': '2', 'storageDriver': info['Driver'],
            'selinux': selinux, 'selinuxPolicyChanged': False}
        profile = json.loads((WORKER / 'deploy/staging/profile.example.json').read_text())
        profile.update(configurationRoot=str(CONFIG), deploymentUid=uid, deploymentHome=str(HOME), dockerMode='rootless',
            dockerHost=values['DOCKER_HOST'], dockerDaemonId=info['ID'], hostSourceCommit=commit, workerEnabled=False)
        for name in ('composeFile', 'botEnvFile', 'releaseFile', 'sdkReleaseFile'):
            profile[name] = str(CONFIG / 'settings' / Path(profile[name]).name)
        compose = json.loads((WORKER / 'deploy/staging/compose.json').read_text())
        compose['services']['gamerhq-staging']['env_file'][0]['path'] = profile['botEnvFile']
        generated = {
            Path(profile['composeFile']): json.dumps(compose, indent=2) + '\n',
            CONFIG / 'settings/profile.json': json.dumps(profile, indent=2) + '\n',
            Path(profile['releaseFile']): (WORKER / 'deploy/staging/event-manager.release.example.json').read_text(),
            Path(profile['sdkReleaseFile']): (WORKER / 'deploy/staging/sdk.release.example.json').read_text(),
        }
        secret_file = STATE / 'staging-api-secret'
        if not secret_file.exists(): write_new(secret_file, secrets.token_urlsafe(40))
        api_secret = secret_file.read_text()
        generated[CONFIG / 'settings/worker.env'] = 'GAMERHQ_SKILL_RELEASE_TOKEN=\nGAMERHQ_STAGING_WEB_API_SECRET=' + api_secret + '\n'
        generated[Path(profile['botEnvFile'])] = 'DISCORD_TOKEN=\nGUILD_ID=0\nGAMERHQ_WEB_API_SECRET=' + api_secret + '\n'
        for path, content in generated.items():
            if not path.exists(): write_new(path, content)
            os.chown(path, uid, gid)
        policy = {k: profile[k] for k in ('root', 'configurationRoot', 'deploymentUid', 'deploymentHome', 'dockerHost', 'hostSourceCommit')}
        policy['schemaVersion'] = 2
        if not (CONFIG / 'policy.json').exists(): write_new(CONFIG / 'policy.json', json.dumps(policy, indent=2) + '\n', 0o640)
        os.chown(CONFIG / 'policy.json', 0, gid)
        state['phase'] = 'ACCESS'; record(state)
    if state['phase'] == 'ACCESS':
        if not ENTRY.parent.exists(): ENTRY.parent.mkdir(mode=0o755, parents=True); ENTRY.parent.chmod(0o755)
        entry = '''#!/usr/bin/python3 -I
import base64, json, os, pwd, subprocess, sys, uuid
try:
    account = pwd.getpwnam("gamerhq-staging-bridge")
    if os.geteuid() != account.pw_uid or os.geteuid() == 0:
        raise PermissionError()
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if len(command) > 6000 or command.count(" ") != 1: raise ValueError()
    prefix, encoded = command.split(" ")
    if prefix != "gamerhq-staging-v1": raise ValueError()
    request = base64.b64decode(encoded, validate=True)
    if len(request) > 4096: raise ValueError()
    request = json.loads(request)
    body = sys.stdin.buffer.read(16385)
    if len(body) > 16384: raise ValueError()
    envelope = json.dumps({"request": request, "configuration": json.loads(body) if body else None})
    uid = account.pw_uid
    environment = {"PATH":"/usr/local/bin:/usr/bin:/bin", "HOME":account.pw_dir,
        "XDG_RUNTIME_DIR":f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS":f"unix:path=/run/user/{uid}/bus"}
    result = subprocess.run(["systemd-run", "--user", "--pipe", "--wait", "--collect", "--quiet",
        "--unit=gamerhq-staging-operation-" + uuid.uuid4().hex,
        "/opt/gamerhq-staging/deployment-worker/.venv/bin/python", "-I", "-B",
        "/opt/gamerhq-staging/deployment-worker/tools/staging_dispatch.py"],
        input=envelope, text=True, env=environment, timeout=2200)
    sys.exit(result.returncode)
except Exception:
    sys.exit("Unsupported or unavailable isolated staging request.")
'''
        if not ENTRY.exists(): write_new(ENTRY, entry, 0o755)
        ssh = HOME / '.ssh'; ssh.mkdir(mode=0o755, exist_ok=True); ssh.chmod(0o755)
        if not (ssh / 'authorized_keys').exists():
            write_new(ssh / 'authorized_keys', 'restrict,command="' + str(ENTRY) + '" ' + state['publicKey'] + '\n', 0o644)
        restriction = Path('/etc/ssh/sshd_config.d/90-gamerhq-staging.conf')
        if not restriction.parent.is_dir():
            raise RuntimeError('The SSH daemon needs a reviewed per-account drop-in include; global SSH policy was not changed')
        install_ssh_restriction(restriction, 'Match User ' + ACCOUNT + '\n    AuthenticationMethods publickey\n    PasswordAuthentication no\n    KbdInteractiveAuthentication no\n    DisableForwarding yes\n    PermitTTY no\n    PermitTunnel no\n    PermitUserRC no\n    ForceCommand ' + str(ENTRY) + '\nMatch all\n')
        service = next((name for name in ('ssh.service', 'sshd.service') if subprocess.run(['systemctl', 'is-active', '--quiet', name]).returncode == 0), None)
        if not service:
            raise RuntimeError('No existing SSH service was found for a safe configuration reload')
        run(['systemctl', 'reload', service])  # existing sessions stay connected
        units = HOME / '.config/systemd/user'
        for name, body in {
            'gamerhq-staging-health.service': '[Unit]\nDescription=Read-only GamerHQ staging health\n[Service]\nType=oneshot\nExecStart=' + str(WORKER / '.venv/bin/python') + ' -I -B ' + str(WORKER / 'tools/staging_health.py') + '\n',
            'gamerhq-staging-health.timer': '[Unit]\nDescription=Read-only GamerHQ staging watchdog\n[Timer]\nOnBootSec=2min\nOnUnitActiveSec=5min\n[Install]\nWantedBy=timers.target\n',
        }.items():
            if not (units / name).exists(): write_new(units / name, body, 0o644)
        user('systemctl', '--user', 'daemon-reload')
        user('systemctl', '--user', 'enable', '--now', 'gamerhq-staging-health.timer')
        state['phase'] = 'COMPLETE'; record(state)
    return {**state, 'productionChanged': False, 'sudoGranted': False, 'dockerGroupGranted': False,
        'configurationPath': str(CONFIG / 'settings'), 'remainingConfiguration': 'Use the protected staging configuration workflow; no routine VPS commands are needed.'}


def receipt():
    """Public endpoint/host-key metadata only. Never inspect private SSH keys."""
    connection = os.environ.get('SSH_CONNECTION', '').split()
    key_path = Path('/etc/ssh/ssh_host_ed25519_key.pub')
    if len(connection) != 4 or not key_path.is_file():
        return {'endpointDiscovery': 'Owner must supply the actual reachable SSH endpoint and independently pinned public host key.'}
    host, port = connection[2], int(connection[3])
    key = public_key(' '.join(key_path.read_text().split()[:2]))
    return {'GAMERHQ_STAGING_SSH_HOST': host, 'GAMERHQ_STAGING_SSH_PORT': str(port),
        'GAMERHQ_STAGING_SSH_KNOWN_HOSTS': (host if port == 22 else '[' + host + ']:' + str(port)) + ' ' + key}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    keys = parser.add_mutually_exclusive_group(required=True)
    keys.add_argument('--ssh-public-key')
    keys.add_argument('--public-key-file', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approval', default='')
    parser.add_argument('--install-prerequisites', action='store_true')
    args = parser.parse_args()
    key = args.ssh_public_key or args.public_key_file.read_text()
    result = prepare(args.commit, key, apply=args.apply, approval=args.approval, install_prerequisites=args.install_prerequisites)
    print(json.dumps({**result, 'publicEndpointReceipt': receipt()}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # All setup errors are our bounded prerequisite/path/policy messages or
        # sanitized subprocess failures. Never print command stderr or secrets.
        message = str(error) if isinstance(error, (ValueError, PermissionError, FileExistsError, RuntimeError)) else type(error).__name__
        frame = traceback.extract_tb(error.__traceback__)[-1]
        location = Path(frame.filename).name + ':' + str(frame.lineno) + ' (' + frame.name + ')'
        raise SystemExit('Rootless staging bootstrap stopped at ' + location + ': ' + message + '. Production Docker and the bot were not restarted or reconfigured. Any SSH restrictions target only the new staging identity. Re-run the same verified command to resume known setup state.') from None
