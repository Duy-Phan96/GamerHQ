"""Owner-only revision of the enrolled RC4 builder after the mount-option limit.

No new generation, application pin, workflow, SSH, database, or Docker change.
The original generation marker and all its ancestors remain immutable.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import types

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
PREVIOUS = 'aac15b971fa6f7a5f7d68e928a69a3f8f37a31da'
APPROVAL = 'REPAIR RC4 SINGLE LAYER OVERLAY'
MARKER = 'pilot-rc4-overlay-repair-transition.json'
BUILDER = 'tools/staging_automation/host_promotion.py'
BASELINE_IMAGE = 'sha256:f50cc0611c474d38b77cbec0a983043d278c892c628dbaad3ced3c74bb311def'
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
ALLOWED = {BUILDER, 'tests/test_staging_host_promotion.py',
           'tools/repair_staging_rc4_overlay.py', 'tests/test_staging_rc4_overlay_repair.py',
           'tests/test_staging_pilot_enrollment_e2e.py',
           '.github/workflows/staging-automation-regression.yml'}


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def make_plan(source, old_policy, old_entry, new_entry):
    policy = json.loads(old_policy)
    if policy.get('controllerCommit') != PREVIOUS or not re.fullmatch('[0-9a-f]{40}', source) or source == PREVIOUS:
        raise ValueError('CONTROLLER_IDENTITY_REJECTED')
    return {'files': [
        {'name': 'entry', 'before': old_entry.hex(), 'after': new_entry.hex()},
        {'name': 'policy', 'before': old_policy.hex(), 'after': encode({**policy, 'controllerCommit': source}).hex()}]}


def switch(plan, read, write):
    # Check every file before switching the entry; unknown bytes are never overwritten.
    for item in plan['files']:
        if read(item['name']) not in (bytes.fromhex(item['before']), bytes.fromhex(item['after'])):
            raise ValueError('CONTROLLER_BYTES_CHANGED')
    for item in plan['files']:
        after = bytes.fromhex(item['after'])
        if read(item['name']) != after:
            write(item['name'], after)
    if any(read(item['name']) != bytes.fromhex(item['after']) for item in plan['files']):
        raise ValueError('CONTROLLER_SWITCH_REJECTED')


def copy_revision(previous, target, helper, gid, old_builder, new_builder):
    """Copy trusted root-owned installed bytes, replace only the invoked builder."""
    helper.directory(previous)
    helper.directory(target, gid)
    expected_files = set()
    for folder, dirs, files in os.walk(previous, followlinks=False):
        folder = Path(folder)
        helper.directory(folder)
        relative = folder.relative_to(previous)
        destination = target / relative
        helper.directory(destination, gid)
        for name in dirs:
            helper.directory(folder / name)
        for name in files:
            path = relative / name
            body = helper.checked(folder / name)
            if path.as_posix() == 'pilot-source/' + BUILDER:
                if body != old_builder:
                    raise ValueError('INSTALLED_BUILDER_CHANGED')
                body = new_builder
            expected_files.add(path)
            output = target / path
            if os.path.lexists(output):
                if helper.checked(output) != body:
                    raise ValueError('REVISION_BYTES_CHANGED')
            else:
                helper.atomic(output, body, gid)
    if Path('pilot-source/' + BUILDER) not in expected_files:
        raise ValueError('INSTALLED_BUILDER_MISSING')
    observed = set()
    for folder, dirs, files in os.walk(target, followlinks=False):
        folder = Path(folder)
        helper.directory(folder)
        for name in dirs:
            helper.directory(folder / name)
        observed.update((folder / name).relative_to(target) for name in files)
    if observed != expected_files:
        raise ValueError('UNEXPECTED_REVISION_FILES')


def repair(source, approval):
    if (os.geteuid() != 0 or approval != APPROVAL or not re.fullmatch('[0-9a-f]{40}', source)
            or source == PREVIOUS):
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    with tempfile.TemporaryDirectory(prefix='gamerhq-rc4-overlay-repair-') as scratch:
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*args):
            return subprocess.run(['git', '-C', scratch, '-c', 'gc.auto=0', *args], env=env,
                                  capture_output=True, check=True, timeout=180).stdout
        git('init', '--quiet')
        for revision in (PREVIOUS, source):
            git('fetch', '--quiet', '--depth=1', REPOSITORY, revision)
            if git('rev-parse', 'FETCH_HEAD').decode().strip() != revision:
                raise ValueError('SOURCE_REJECTED')
        if git('show', source + ':tools/repair_staging_rc4_overlay.py') != Path(__file__).read_bytes():
            raise ValueError('INSTALLER_SOURCE_MISMATCH')
        changed = set(git('diff', '--name-only', PREVIOUS, source, '--').decode().splitlines())
        if BUILDER not in changed or not changed <= ALLOWED:
            raise ValueError('UNREVIEWED_SOURCE_CHANGE')
        def load(path, name):
            module = types.ModuleType(name)
            module.__file__ = str(Path(scratch) / (name + '.py'))
            exec(compile(git('show', PREVIOUS + ':' + path), module.__file__, 'exec'), module.__dict__)
            return module
        helper = load('tools/enroll_staging_tenant_preview.py', 'reviewed_helper')
        old = load('tools/enroll_staging_pilot_generation.py', 'reviewed_enrollment')
        old.ROOT, old.CONTROL = ROOT, CONTROL
        revisions = ROOT / 'automation-controller/revisions'
        previous, target = revisions / PREVIOUS, revisions / source
        worker = ROOT / 'worker'
        for folder in (ROOT, CONTROL, revisions.parent, revisions, previous):
            helper.directory(folder)
        info = worker.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise ValueError('WORKER_DIRECTORY_REJECTED')
        with os.fdopen(os.open(worker / 'operation.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('OPERATION_LOCK_REJECTED')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            old_marker = helper.checked(CONTROL / old.ACTIVE_TRANSITION)
            ancestor = json.loads(old_marker)
            if ancestor.get('source') != PREVIOUS or ancestor.get('phase') != 'COMMITTED':
                raise ValueError('ENROLLED_GENERATION_REQUIRED')
            history = ancestor['ancestorSha256']
            for name, sha in history.items():
                if Path(name).name != name or digest(helper.checked(CONTROL / name)) != sha:
                    raise ValueError('ANCESTOR_CHANGED')
            prior = {item['name']: bytes.fromhex(item['after']) for item in ancestor['plan']['files']}
            # The committed plan records the exact launcher bytes, including the
            # original ancestor iteration order; do not reconstruct from sorted JSON.
            old_entry = prior['entry']
            if helper.checked(CONTROL / 'settings/profile.json', uid, private=True) != prior['profile']:
                raise ValueError('GENERATION_BINDING_CHANGED')
            history = {**history, old.ACTIVE_TRANSITION: digest(old_marker)}
            old.ACTIVE_TRANSITION = MARKER
            new_entry = old.active_gate(target, history)
            plan = make_plan(source, prior['policy'], old_entry, new_entry)
            marker = CONTROL / MARKER
            record = {'source': source, 'phase': 'PREPARED', 'ancestorSha256': history, 'plan': plan}
            paths = {'entry': revisions.parent / 'entry.py', 'policy': CONTROL / 'automation-policy.json'}
            def read(name):
                return helper.checked(paths[name])
            def write(name, body):
                old.replace(paths[name], body, 0, gid, 0o640)
            if os.path.lexists(marker):
                current = json.loads(helper.checked(marker))
                if current not in (record, {**record, 'phase': 'COMMITTED'}):
                    raise ValueError('REPAIR_TRANSACTION_CHANGED')
                if current['phase'] == 'COMMITTED':
                    if any(read(item['name']) != bytes.fromhex(item['after']) for item in plan['files']):
                        raise ValueError('CONTROLLER_BYTES_CHANGED')
                    return {'state': 'RC4_OVERLAY_REPAIR_INSTALLED', 'controllerCommit': source,
                            'alreadyInstalled': True, 'activationAuthorized': False, 'productionChanged': False}
            state = json.loads(helper.checked(worker / 'automation-state.json', uid, private=True))
            if (state.get('pilotGeneration') != 'active-pilot-rc4-v3' or state.get('approvedImage') != BASELINE_IMAGE
                    or state.get('enabled') is not True or state.get('bootstrapVerified') is not True
                    or state.get('recoveryRequired') is not False or state.get('tenantPilotActivationPrepared') is not None
                    or os.path.lexists(worker / 'tenant-pilot-cutover-rc4-v3.json') or os.path.lexists(worker / 'access-revoked')):
                raise ValueError('ACTIVE_BASELINE_REQUIRED')
            if any(read(item['name']) not in (bytes.fromhex(item['before']), bytes.fromhex(item['after'])) for item in plan['files']):
                raise ValueError('CONTROLLER_BYTES_CHANGED')
            copy_revision(previous, target, helper, gid, git('show', PREVIOUS + ':' + BUILDER), git('show', source + ':' + BUILDER))
            selinux = Path('/sys/fs/selinux/enforce').exists()
            if selinux:
                subprocess.run(['/usr/sbin/restorecon', '-RF', str(target)], check=True, capture_output=True, timeout=45)
            old.replace(marker, encode(record), 0, gid, 0o640)
            if selinux:
                subprocess.run(['/usr/sbin/restorecon', '-F', str(marker)], check=True, capture_output=True, timeout=45)
            switch(plan, read, write)
            if any(digest(helper.checked(CONTROL / name)) != sha for name, sha in history.items()):
                raise ValueError('ANCESTOR_CHANGED')
            old.replace(marker, encode({**record, 'phase': 'COMMITTED'}), 0, gid, 0o640)
    return {'state': 'RC4_OVERLAY_REPAIR_INSTALLED', 'controllerCommit': source,
            'generation': 'active-pilot-rc4-v3', 'singleFilesystemLayer': True,
            'activationAuthorized': False, 'gatewayChanged': False, 'databaseChanged': False,
            'productionChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--approval', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(repair(args.commit, args.approval)))
    except Exception as error:
        code = error.args[0] if error.args else None
        # Never print command output, arbitrary paths, or configuration.
        print(json.dumps({'state': 'RC4_OVERLAY_REPAIR_REJECTED', 'exceptionType': type(error).__name__,
                          'reason': code if isinstance(code, str) and re.fullmatch('[A-Z_]{3,64}', code) else 'REVIEW_REQUIRED',
                          'activationAuthorized': False, 'productionChanged': False}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
