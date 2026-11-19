"""Real installer/Git/filesystem transactions on a disposable root-owned Linux fixture.

Only the external systemd/Discord/Docker baseline attestation is substituted.
Immutable Git reads, archives, ownership checks, historical SQLite verification,
operation locking, fsynced writes and interrupted/resumed enrollment are real.
Never accesses the VPS or the runner's installed controller/storage.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from tools import enroll_staging_pilot_generation as enrollment
from tools import enroll_staging_tenant_preview as previous
from tools.staging_automation import pilot_generation as generation


pytestmark = pytest.mark.skipif(
    os.environ.get('GAMERHQ_PILOT_ENROLLMENT_E2E') != 'true',
    reason='Explicit disposable Linux root fixture job only')


def git(folder, *args):
    return subprocess.run(['git', '-c', 'safe.directory=' + str(folder), '-C', str(folder), *args],
                          check=True, capture_output=True, timeout=180).stdout


def digest(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture(scope='module')
def source_mirror():
    assert os.name == 'posix' and os.geteuid() == 0
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='gamerhq-enrollment-source-') as temporary:
        mirror = Path(temporary) / 'mirror.git'
        # Use the existing full checkout; no dependency on public Git availability.
        subprocess.run(['git', '-c', 'safe.directory=' + str(repository), 'clone', '--quiet',
                        '--bare', '--no-hardlinks', str(repository), str(mirror)],
                       check=True, capture_output=True, timeout=180)
        main = git(repository, 'rev-parse', 'refs/remotes/origin/main').strip().decode()
        git(mirror, 'update-ref', 'refs/heads/main', main)
        source = git(repository, 'rev-parse', 'HEAD').strip().decode()
        # The installer self-verification must exercise exactly the checked-out blob.
        assert git(mirror, 'show', source + ':tools/enroll_staging_pilot_generation.py') == Path(enrollment.__file__).read_bytes()
        yield mirror, source


@pytest.fixture
def installed(source_mirror, monkeypatch):
    import pwd
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    mirror, source = source_mirror
    native_run = subprocess.run
    native_replace = enrollment.replace
    old_path = list(sys.path)
    old_modules = {key: value for key, value in sys.modules.items()
                   if key == 'staging_automation' or key.startswith('staging_automation.')}
    for key in old_modules:
        del sys.modules[key]
    with tempfile.TemporaryDirectory(prefix='gamerhq-enrollment-vps-') as temporary:
        base = Path(temporary)
        os.chown(base, 0, gid)
        base.chmod(0o750)
        root, control = base / 'root', base / 'control'

        def folder(path, owner=0, mode=0o750):
            if not path.exists():
                if path.parent != base and not path.parent.exists():
                    folder(path.parent)
                path.mkdir()
            os.chown(path, owner, gid)
            path.chmod(mode)
            return path

        def write(path, body, owner=0, mode=0o640):
            if not path.parent.exists():
                folder(path.parent)
            path.write_bytes(body)
            os.chown(path, owner, gid)
            path.chmod(mode)
            return path

        folder(root)
        folder(control)
        folder(root / 'worker', uid)
        write(root / 'worker/operation.lock', b'', uid, 0o600)
        old = folder(root / 'automation-controller/revisions' / enrollment.PREVIOUS)
        folder(old / 'staging_automation')
        names = git(mirror, 'ls-tree', '--name-only', enrollment.LEGACY_CONTROLLER_BASE,
                    'tools/staging_automation/').decode().splitlines()
        names = {Path(name).name for name in names if name.endswith('.py')} | set(previous.SOURCE_PATHS)
        overlays = previous.OVERLAY | {'pilot_activation.py', 'pilot_cutover.py', 'recovery_api_repair.py'}
        for name in names | overlays:
            revision = enrollment.PREVIOUS if name in overlays else enrollment.LEGACY_CONTROLLER_BASE
            relative = previous.SOURCE_PATHS.get(name, 'tools/staging_automation/' + name)
            write(old / 'staging_automation' / name, git(mirror, 'show', revision + ':' + relative))
        worker = folder(root / 'deployment-worker')
        git(worker, 'init', '--quiet')
        git(worker, 'fetch', '--quiet', '--depth=1', mirror.as_uri(), previous.WORKER)
        git(worker, 'checkout', '--quiet', '--detach', 'FETCH_HEAD')

        databases = folder(root / 'fixture-databases', uid, 0o700)
        live = []
        for index, guild in enumerate(generation.COHORT['guildIds']):
            path = databases / (guild + '.sqlite')
            with sqlite3.connect(path) as database:
                if index:
                    database.execute('CREATE TABLE gamerhq_tenant_identity(guild_id INTEGER,ready INTEGER)')
                    database.execute('INSERT INTO gamerhq_tenant_identity VALUES (?,1)', (int(guild),))
                else:
                    database.execute('CREATE TABLE skill_domain_authority(guild_id TEXT,domain TEXT,skill_id TEXT)')
                    database.execute('INSERT INTO skill_domain_authority VALUES (?,?,?)', (guild, 'event-manager', 'event-manager'))
                    database.execute('CREATE TABLE skill_guild_state(guild_id TEXT,skill_id TEXT,installed INTEGER,configured INTEGER,enabled INTEGER,version TEXT)')
                    database.execute('INSERT INTO skill_guild_state VALUES (?,?,1,1,0,?)', (guild, 'event-manager', '0.1.1'))
            os.chown(path, uid, gid)
            path.chmod(0o600)
            live.append(path)
        backups = folder(root / 'backups', uid, 0o700)

        def backup(batch, quiesced=False):
            destination = folder(backups / batch, uid, 0o700)
            rows = []
            for path in live:
                target = destination / path.name
                with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                    with sqlite3.connect(target) as snapshot:
                        db.backup(snapshot)
                    with sqlite3.connect(':memory:') as restored:
                        db.backup(restored)
                        assert restored.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
                os.chown(target, uid, gid)
                target.chmod(0o600)
                rows.append({'guildId': path.stem, 'backupSha256': digest(target.read_bytes()), 'restore': 'PASS'})
            receipt = generation.encode({'tenants': rows, 'rollbackRehearsal': 'PRIMARY_ONLY_PASS',
                                         'binding': {'baselineHostCommit': previous.HOST,
                                                     'baselineImage': previous.BASELINE_IMAGE,
                                                     'startupOverrideSha256': 'a' * 64,
                                                     'quiesced': quiesced}})
            write(destination / 'verified.json', receipt, uid, 0o600)
            return {'backupBatch': batch, 'backupReceiptSha256': digest(receipt),
                    'startupOverrideSha256': 'a' * 64, 'quiesced': quiesced}

        checkpoint = backup('tenant-pilot-3bnrqu5l', True)
        preview = backup('tenant-pilot-vs39zdvz')
        prepared = {'runId': '44282392511', 'preview': preview, 'fallbackRollbackRehearsal': 'PASS'}
        state = {'enabled': True, 'bootstrapVerified': True, 'recoveryRequired': False,
                 'approvedImage': previous.BASELINE_IMAGE, 'tenantPilotActivationPrepared': prepared}
        incident = {'phase': 'PILOT_ROLLED_BACK', 'failedAt': 'VERIFYING', 'activationRun': '44283427505',
                    'checkpoint': checkpoint, 'recoveryApiRepair': {'image': generation.BASELINE_IMAGE,
                                                                   'commit': generation.BASELINE_COMMIT},
                    'binding': {'preparedSha256': digest(json.dumps(prepared, sort_keys=True, separators=(',', ':')).encode()),
                                'baselineImage': previous.BASELINE_IMAGE,
                                'candidateImage': 'sha256:c9c6996a646b93412dc7931cf98b20256387b9d95aec36d359009aff82cf7320',
                                'fallbackImage': previous.API_REPAIR_PIN['parentImage']}}
        profile = {'environment': 'staging', 'stagingBotId': generation.COHORT['applicationId'],
                   'stagingGuildId': generation.COHORT['guildIds'][0], 'productionBotId': '999999999999999999',
                   'productionGuildId': '999999999999999998', 'applicationHostCommit': previous.HOST,
                   'hostSourceCommit': previous.WORKER, 'baseImage': previous.BASELINE_IMAGE,
                   'backupRoot': str(backups), 'database': str(live[0])}
        policy = {'controllerCommit': enrollment.PREVIOUS, 'tenantPilot': generation.COHORT,
                  'primaryRecoveryApiRepair': previous.API_REPAIR_PIN,
                  'hostPromotion': {'commit': previous.HOST},
                  'tenantPilotCutover': {'workflowSha256': previous.CUTOVER_DIGEST}}
        paths = {'entry': write(root / 'automation-controller/entry.py', previous.renderer(old, recovery_completion=True)),
                 'profile': write(control / 'settings/profile.json', generation.encode(profile), uid, 0o600),
                 'state': write(root / 'worker/automation-state.json', generation.encode(state), uid, 0o600),
                 'policy': write(control / 'automation-policy.json', generation.encode(policy))}
        incident_path = write(root / 'worker/tenant-pilot-cutover.json', generation.encode(incident), uid, 0o600)
        sentinel = write(base / 'production-sentinel', b'never touched')
        preserved = {path: (path.read_bytes(), path.stat().st_mode, path.stat().st_uid, path.stat().st_gid,
                            path.stat().st_mtime_ns) for path in [*live, incident_path, sentinel]}
        calls = []

        def run(command, *args, **kwargs):
            if command[0] != '/usr/sbin/runuser':
                return native_run(command, *args, **kwargs)
            # Only substitute the external live attestation. Never run a bot.
            assert command[1:4] == ['-u', account.pw_name, '--']
            assert '--property=NoNewPrivileges=yes' in command
            assert '--property=ProtectSystem=strict' in command
            assert '--property=ProtectHome=yes' in command
            assert '--property=RestrictSUIDSGID=yes' in command
            assert command[-2] == enrollment.PROBE
            packet = json.loads(kwargs['input'])
            assert packet['image'] == generation.BASELINE_IMAGE
            assert packet['policy']['tenantPilot'] == generation.COHORT
            assert packet['profile']['applicationHostCommit'] == generation.BASELINE_COMMIT
            generation.validate_generation(packet['policy'])
            calls.append(packet)
            fresh = backup('tenant-pilot-fixture' + str(len(calls)).zfill(1))
            proof = {'state': 'GENERATION_BASELINE_VERIFIED', 'hostCommit': generation.BASELINE_COMMIT,
                     **fresh, 'bothTenantRestores': 'PASS', 'primaryOnlyVerified': True, 'gatewayChanged': False}
            return subprocess.CompletedProcess(command, 0, json.dumps(proof), '')

        monkeypatch.setattr(enrollment, 'ROOT', root)
        monkeypatch.setattr(enrollment, 'CONTROL', control)
        monkeypatch.setattr(enrollment, 'REPOSITORY', mirror.as_uri())
        monkeypatch.setattr(subprocess, 'run', run)
        enrollment._stage_trace.clear()
        fixture = SimpleNamespace(source=source, root=root, control=control, paths=paths, calls=calls,
                                  preserved=preserved, uid=uid, gid=gid, native_replace=native_replace,
                                  marker=control / 'pilot-generation-transition.json')
        try:
            yield fixture
            for path, before in preserved.items():
                assert (path.read_bytes(), path.stat().st_mode, path.stat().st_uid, path.stat().st_gid,
                        path.stat().st_mtime_ns) == before
        finally:
            sys.path[:] = old_path
            for key in list(sys.modules):
                if key == 'staging_automation' or key.startswith('staging_automation.'):
                    del sys.modules[key]
            sys.modules.update(old_modules)


class PowerLoss(BaseException):
    pass


@pytest.mark.parametrize('interrupt_after', [None, 'entry', 'profile', 'state', 'policy'])
def test_real_installer_complete_interrupted_resumed_and_idempotent(installed, monkeypatch, interrupt_after):
    fixture = installed
    if interrupt_after:
        def interrupt(path, *args, **kwargs):
            fixture.native_replace(path, *args, **kwargs)
            if path == fixture.paths[interrupt_after]:
                raise PowerLoss()
        monkeypatch.setattr(enrollment, 'replace', interrupt)
        with pytest.raises(PowerLoss):
            enrollment.enroll(fixture.source, enrollment.APPROVAL, require_absent=True)
        assert json.loads(fixture.marker.read_bytes())['phase'] == 'PREPARED'
        gate = subprocess.run([sys.executable, '-I', '-S', '-B', str(fixture.paths['entry'])],
                              capture_output=True, text=True)
        assert gate.returncode != 0 and 'PILOT_GENERATION_HELD' in gate.stderr
        monkeypatch.setattr(enrollment, 'replace', fixture.native_replace)
        with pytest.raises(ValueError, match='EXISTING_TRANSITION_REQUIRES_REVIEW'):
            enrollment.enroll(fixture.source, enrollment.APPROVAL, require_absent=True)
    result = enrollment.enroll(fixture.source, enrollment.APPROVAL, require_absent=not interrupt_after)
    assert result['state'] == 'RECOVERED_PRIMARY_GENERATION_ENROLLED'
    assert result['initialTransitionPhase'] == ('PREPARED' if interrupt_after else 'ABSENT')
    assert result['activationAuthorized'] is False and result['gatewayChanged'] is False
    assert json.loads(fixture.marker.read_bytes())['phase'] == 'COMMITTED'
    assert fixture.marker.stat().st_uid == 0 and fixture.marker.stat().st_mode & 0o777 == 0o640
    state = json.loads(fixture.paths['state'].read_bytes())
    assert state['recoveryRequired'] is False and state['approvedImage'] == generation.BASELINE_IMAGE
    for name, path in fixture.paths.items():
        assert path.stat().st_uid == (fixture.uid if name in {'state', 'profile'} else 0)
        assert path.stat().st_gid == fixture.gid
        assert path.stat().st_mode & 0o777 == (0o600 if name in {'state', 'profile'} else 0o640)
    snapshot = {path: path.read_bytes() for path in [*fixture.paths.values(), fixture.marker]}
    repeated = enrollment.enroll(fixture.source, enrollment.APPROVAL)
    assert repeated['alreadyEnrolled'] is True and repeated['initialTransitionPhase'] == 'COMMITTED'
    assert all(path.read_bytes() == body for path, body in snapshot.items())


def test_missing_historical_fetch_fails_before_metadata_writes(installed, monkeypatch):
    fixture = installed
    original = enrollment.fetch_revision
    before = {path: path.read_bytes() for path in fixture.paths.values()}
    def reject(git_call, revision):
        if revision == enrollment.LEGACY_CONTROLLER_BASE:
            raise subprocess.CalledProcessError(128, ['git', 'fetch'])
        return original(git_call, revision)
    monkeypatch.setattr(enrollment, 'fetch_revision', reject)
    with pytest.raises(subprocess.CalledProcessError):
        enrollment.enroll(fixture.source, enrollment.APPROVAL, require_absent=True)
    assert not fixture.marker.exists() and not fixture.calls
    assert all(path.read_bytes() == body for path, body in before.items())
    monkeypatch.setattr(enrollment, 'fetch_revision', original)
    assert enrollment.enroll(fixture.source, enrollment.APPROVAL, require_absent=True)['state'] == 'RECOVERED_PRIMARY_GENERATION_ENROLLED'


def test_unknown_partial_state_is_never_overwritten(installed, monkeypatch):
    fixture = installed
    def interrupt(path, *args, **kwargs):
        fixture.native_replace(path, *args, **kwargs)
        if path == fixture.paths['entry']:
            raise PowerLoss()
    monkeypatch.setattr(enrollment, 'replace', interrupt)
    with pytest.raises(PowerLoss):
        enrollment.enroll(fixture.source, enrollment.APPROVAL)
    monkeypatch.setattr(enrollment, 'replace', fixture.native_replace)
    changed = fixture.paths['profile'].read_bytes() + b' '
    fixture.paths['profile'].write_bytes(changed)
    marker = fixture.marker.read_bytes()
    with pytest.raises(ValueError, match='GENERATION_STATE_CHANGED'):
        enrollment.enroll(fixture.source, enrollment.APPROVAL)
    assert fixture.paths['profile'].read_bytes() == changed
    assert fixture.marker.read_bytes() == marker


def test_fresh_only_rechecks_marker_under_operation_lock(installed, monkeypatch):
    fixture = installed
    native_fetch = enrollment.fetch_sources
    before = {path: path.read_bytes() for path in fixture.paths.values()}
    def fetch(*args):
        native_fetch(*args)
        fixture.native_replace(fixture.marker, b'protected concurrent transition', 0, fixture.gid, 0o640)
    monkeypatch.setattr(enrollment, 'fetch_sources', fetch)
    with pytest.raises(ValueError, match='EXISTING_TRANSITION_REQUIRES_REVIEW'):
        enrollment.enroll(fixture.source, enrollment.APPROVAL, require_absent=True)
    assert enrollment._stage_trace[-1] == 'OPERATION_LOCK'
    assert fixture.marker.read_bytes() == b'protected concurrent transition'
    assert not fixture.calls
    assert all(path.read_bytes() == body for path, body in before.items())


def test_modified_installed_controller_still_fails_content_verification(installed):
    fixture = installed
    member = fixture.root / 'automation-controller/revisions' / enrollment.PREVIOUS / 'staging_automation/__init__.py'
    member.write_bytes(member.read_bytes() + b'\n# unreviewed fixture change\n')
    with pytest.raises(ValueError, match='CONTROLLER_SOURCE_MISMATCH'):
        enrollment.enroll(fixture.source, enrollment.APPROVAL, require_absent=True)
    assert not fixture.marker.exists() and not fixture.calls
