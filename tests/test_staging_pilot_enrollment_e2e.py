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


@pytest.fixture
def acceptance_installed(source_mirror, monkeypatch, tmp_path):
    import pwd
    from tools import enroll_staging_tenant_acceptance as installer
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid,gid = account.pw_uid,account.pw_gid
    mirror,source = source_mirror
    # This historical acceptance installer permits reporting-source revisions,
    # not replacing its enrolled shared client. Rehearse with that immutable
    # client, even when the current PR adds a different scoped operation to it.
    # The synthetic source exists only in this mirror; live pins are untouched.
    workflow = installer.WORKFLOW
    body = git(mirror, 'show', source + ':' + workflow)
    import re
    client = re.search(rb'          ref: ([0-9a-f]{40})\n', body).group(1).decode()
    client_path = 'tools/staging_automation_client.py'
    historical_client = {path: git(mirror, 'show', client + ':' + path) for path in
                         (client_path, 'tools/staging_automation/tenant_acceptance.py', 'tools/tenant_acceptance_report.py')}
    with tempfile.TemporaryDirectory(prefix='acceptance-registration-') as index_dir:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(index_dir) / 'index'))
        def staged(*args, input=None):
            return subprocess.run(['git', '-c', 'safe.directory=' + str(mirror),
                                   '-c', 'user.name=Disposable rehearsal',
                                   '-c', 'user.email=rehearsal@example.invalid',
                                   '-C', str(mirror), *args], input=input, env=env,
                                  capture_output=True, check=True, timeout=180).stdout.strip()
        staged('read-tree', source)
        for path, body in historical_client.items():
            blob = staged('hash-object', '-w', '--stdin', input=body).decode()
            staged('update-index', '--add', '--cacheinfo', '100644', blob, path)
        tree = staged('write-tree').decode()
        source = staged('commit-tree', tree, '-p', source,
                        input=b'Disposable historical acceptance client fixture\n').decode()
    saved_refs = {}
    for ref in ('refs/heads/main','refs/heads/develop'):
        try: saved_refs[ref] = git(mirror,'rev-parse','--verify',ref).strip().decode()
        except subprocess.CalledProcessError: saved_refs[ref] = None
    for ref in saved_refs: git(mirror,'update-ref',ref,source)
    root,control = tmp_path/'root',tmp_path/'control'
    def folder(path,owner=0):
        path.mkdir(parents=True,exist_ok=True)
        os.chown(path,owner,gid);path.chmod(0o750)
        return path
    def put(path,body,owner=0,mode=0o640):
        folder(path.parent, uid if path.parent==root/'worker' else 0)
        path.write_bytes(body);os.chown(path,owner,gid);path.chmod(mode)
        return path
    folder(root);folder(control)
    worker=folder(root/'deployment-worker')
    git(worker,'init','--quiet')
    git(worker,'fetch','--quiet','--depth=1',mirror.as_uri(),previous.WORKER)
    git(worker,'checkout','--quiet','--detach','FETCH_HEAD')
    previous_path=folder(root/'automation-controller/revisions'/installer.PREVIOUS)
    folder(previous_path.parent);folder(previous_path.parent.parent)
    folder(previous_path/'staging_automation')
    overlays=previous.OVERLAY|{'pilot_activation.py','pilot_cutover.py','recovery_api_repair.py','pilot_generation.py'}
    names={Path(name).name for name in git(mirror,'ls-tree','--name-only',installer.LEGACY,'tools/staging_automation/').decode().splitlines() if name.endswith('.py')}|set(previous.SOURCE_PATHS)|overlays
    for name in names:
        pin=installer.PREVIOUS if name in overlays else installer.LEGACY
        relative=previous.SOURCE_PATHS.get(name,'tools/staging_automation/'+name)
        put(previous_path/'staging_automation'/name,git(mirror,'show',pin+':'+relative))
    prepared={'candidateImage':'sha256:'+'c'*64,'fallbackImage':generation.BASELINE_IMAGE}
    state={'enabled':True,'bootstrapVerified':True,'recoveryRequired':False,
           'approvedImage':generation.BASELINE_IMAGE,'tenantPilotActivationPrepared':prepared}
    journal={'phase':'PILOT_ACTIVE','binding':{'preparedSha256':digest(json.dumps(prepared,sort_keys=True,separators=(',',':')).encode()),
             'baselineImage':state['approvedImage'],'candidateImage':prepared['candidateImage'],
             'fallbackImage':prepared['fallbackImage'],'generation':'recovered-primary-v2'}}
    policy={'controllerCommit':installer.PREVIOUS,'legacyWorkerCommit':previous.WORKER,'tenantPilotGeneration':generation.GENERATION,'tenantPilot':generation.COHORT,
            'tenantPilotActivation':{'commit':'1899c6f8cc498fefa0510d0e3f70453f6f1aa082'}}
    profile={'environment':'staging','hostSourceCommit':previous.WORKER,'stagingBotId':generation.COHORT['applicationId'],'stagingGuildId':generation.COHORT['guildIds'][0],
             'productionBotId':'888888888888888888','productionGuildId':'888888888888888889'}
    folder(root/'worker',uid)
    put(root/'worker/operation.lock',b'',uid,0o600)
    incident=put(root/'worker/tenant-pilot-cutover-v2.json',generation.encode(journal),uid,0o600)
    statefile=put(root/'worker/automation-state.json',generation.encode(state),uid,0o600)
    marker=put(control/'pilot-generation-transition.json',generation.encode({'source':installer.PREVIOUS,'phase':'COMMITTED'}))
    profilefile=put(control/'settings/profile.json',generation.encode(profile),uid,0o600)
    entry=put(root/'automation-controller/entry.py',enrollment.gate(previous_path))
    policyfile=put(control/'automation-policy.json',generation.encode(policy))
    primary=put(root/'fixture-primary.sqlite',b'preserve-primary-fixture',uid,0o600)
    secondary=put(root/'fixture-secondary.sqlite',b'preserve-secondary-fixture',uid,0o600)
    preserved={path:(path.read_bytes(),path.stat().st_mode,path.stat().st_uid,path.stat().st_mtime_ns) for path in (incident,statefile,marker,profilefile,primary,secondary)}
    native_run=subprocess.run
    def run(command,*args,**kwargs):
        if command[0]!='/usr/sbin/runuser':return native_run(command,*args,**kwargs)
        assert '--property=ProtectSystem=strict' in command and '--property=NoNewPrivileges=yes' in command
        packet=json.loads(kwargs['input'])
        assert packet['image']==prepared['candidateImage']
        return subprocess.CompletedProcess(command,0,'{"state":"ACTIVE_PILOT_VERIFIED"}','')
    monkeypatch.setattr(subprocess,'run',run)
    monkeypatch.setattr(installer,'ROOT',root);monkeypatch.setattr(installer,'CONTROL',control)
    monkeypatch.setattr(installer,'REPOSITORY',mirror.as_uri())
    old_path=list(sys.path)
    old_modules={key:value for key,value in sys.modules.items() if key=='staging_automation' or key.startswith('staging_automation.')}
    for key in old_modules:del sys.modules[key]
    try:
        yield SimpleNamespace(installer=installer,source=source,entry=entry,policy=policyfile,
                              marker=control/'tenant-acceptance-transition.json',root=root)
        for path,before in preserved.items():
            assert (path.read_bytes(),path.stat().st_mode,path.stat().st_uid,path.stat().st_mtime_ns)==before
    finally:
        for ref,pin in saved_refs.items():
            if pin is None:git(mirror,'update-ref','-d',ref)
            else:git(mirror,'update-ref',ref,pin)
        sys.path[:]=old_path
        for key in list(sys.modules):
            if key=='staging_automation' or key.startswith('staging_automation.'):del sys.modules[key]
        sys.modules.update(old_modules)


@pytest.mark.parametrize('boundary',[None,'PREPARED','entry','policy','COMMITTED'])
def test_readonly_acceptance_enrollment_and_interrupted_resume_preserve_active_pilot(acceptance_installed,boundary):
    f=acceptance_installed
    def interrupt(phase):
        if phase==boundary:raise PowerLoss()
    if boundary:
        with pytest.raises(PowerLoss):f.installer.enroll(f.source,f.installer.APPROVAL,checkpoint=interrupt)
        assert json.loads(f.marker.read_bytes())['phase']==('COMMITTED' if boundary=='COMMITTED' else 'PREPARED')
    result=f.installer.enroll(f.source,f.installer.APPROVAL)
    assert result['state']=='TENANT_ACCEPTANCE_ENROLLED'
    assert result['gatewayChanged'] is False and result['databaseChanged'] is False
    assert json.loads(f.marker.read_bytes())['phase']=='COMMITTED'
    policy=json.loads(f.policy.read_bytes())
    assert policy['controllerCommit']==f.source and 'tenantAcceptance' in policy
    assert f.installer.enroll(f.source,f.installer.APPROVAL)==result


@pytest.mark.parametrize('boundary',[None,'PREPARED','entry','policy','COMMITTED'])
def test_acceptance_source_revision_preserves_original_enrollment_and_resumes(acceptance_installed,source_mirror,tmp_path,monkeypatch,boundary):
    import types
    f=acceptance_installed
    mirror,_=source_mirror
    # Replay each actual immutable installer in order, not a fabricated gate.
    for index,(_,pin) in enumerate(f.installer.ANCESTRY):
        oldfile=tmp_path/f'ancestor-{index}.py'
        oldfile.write_bytes(git(mirror,'show',pin+':tools/enroll_staging_tenant_acceptance.py'))
        old=types.ModuleType(f'ancestor_{index}');old.__file__=str(oldfile)
        exec(compile(oldfile.read_bytes(),str(oldfile),'exec'),old.__dict__)
        monkeypatch.setattr(old,'ROOT',f.root);monkeypatch.setattr(old,'CONTROL',f.policy.parent)
        monkeypatch.setattr(old,'REPOSITORY',mirror.as_uri())
        if index:
            old.enroll(pin,old.REVISION_APPROVAL,revise=True)
        else:
            old.enroll(pin,old.APPROVAL)
    original=f.marker.read_bytes()
    parents={f.policy.parent/name:(f.policy.parent/name).read_bytes() for name,_ in f.installer.ANCESTRY}
    before_policy=json.loads(f.policy.read_bytes())
    prior=f.root/'automation-controller/revisions'/f.installer.REVISION_PREVIOUS
    prior_modules={p.name:p.read_bytes() for p in (prior/'staging_automation').iterdir()}
    def interrupt(stage):
        if stage==boundary:raise PowerLoss()
    if boundary:
        with pytest.raises(PowerLoss):
            f.installer.enroll(f.source,f.installer.REVISION_APPROVAL,revise=True,checkpoint=interrupt)
        if boundary in ('entry','policy'):
            held=subprocess.run([sys.executable,'-B',str(f.entry)],capture_output=True,text=True,timeout=20)
            assert held.returncode!=0 and 'ACCEPTANCE_ENROLLMENT_HELD' in held.stderr
    result=f.installer.enroll(f.source,f.installer.REVISION_APPROVAL,revise=True)
    assert result['state']=='TENANT_ACCEPTANCE_SOURCE_REVISED'
    assert f.marker.read_bytes()==original
    assert all(path.read_bytes()==body for path,body in parents.items())
    assert json.loads((f.policy.parent/f.installer.SOURCE_TRANSITION).read_bytes())['phase']=='COMMITTED'
    after=json.loads(f.policy.read_bytes())
    assert {k:v for k,v in after.items() if k not in {'controllerCommit','tenantAcceptance'}}=={k:v for k,v in before_policy.items() if k not in {'controllerCommit','tenantAcceptance'}}
    assert after['tenantAcceptance']['workflowSha256']==before_policy['tenantAcceptance']['workflowSha256']
    current=f.root/'automation-controller/revisions'/f.source
    assert {p.name:p.read_bytes() for p in (current/'staging_automation').iterdir()}==prior_modules
    assert f.installer.enroll(f.source,f.installer.REVISION_APPROVAL,revise=True)==result

@pytest.fixture
def active_upgrade_installed(source_mirror, monkeypatch, tmp_path):
    import pwd
    from tools import enroll_staging_tenant_acceptance as acceptance
    from test_staging_pilot_generation import active_documents
    mirror, source = source_mirror
    old_main = git(mirror,'rev-parse','refs/heads/main').strip().decode()
    git(mirror,'update-ref','refs/heads/main',source)
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid,gid = account.pw_uid,account.pw_gid
    root,control=tmp_path/'root',tmp_path/'control'
    def folder(path, owner=0):
        path.mkdir(parents=True,exist_ok=True);os.chown(path,owner,gid);path.chmod(0o750)
        return path
    def put(path,body,owner=0,mode=0o640):
        folder(path.parent, uid if path.parent==root/'worker' else 0)
        path.write_bytes(body);os.chown(path,owner,gid);path.chmod(mode)
        return path
    folder(root);folder(control)
    worker=folder(root/'deployment-worker')
    git(worker,'init','--quiet')
    git(worker,'fetch','--quiet','--depth=1',mirror.as_uri(),previous.WORKER)
    git(worker,'checkout','--quiet','--detach','FETCH_HEAD')
    previous_path=folder(root/'automation-controller/revisions'/enrollment.ACTIVE_PREVIOUS)
    folder(previous_path.parent);folder(previous_path.parent.parent)
    folder(previous_path/'staging_automation')
    overlays=previous.OVERLAY|{'pilot_activation.py','pilot_cutover.py','recovery_api_repair.py','pilot_generation.py'}
    names={Path(name).name for name in git(mirror,'ls-tree','--name-only',enrollment.LEGACY_CONTROLLER_BASE,'tools/staging_automation/').decode().splitlines() if name.endswith('.py')}|set(previous.SOURCE_PATHS)|overlays|{'tenant_acceptance.py'}
    for name in names:
        pin=(enrollment.ACTIVE_PREVIOUS if name in {'bridge.py','policy.py','oidc.py','tenant_acceptance.py'}
             else enrollment.ACTIVE_GENERATION_SOURCE if name in overlays else enrollment.LEGACY_CONTROLLER_BASE)
        relative=previous.SOURCE_PATHS.get(name,'tools/staging_automation/'+name)
        put(previous_path/'staging_automation'/name,git(mirror,'show',pin+':'+relative))
    policy,profile,state,journal=active_documents()
    policy.update(controllerCommit=enrollment.ACTIVE_PREVIOUS,legacyWorkerCommit=previous.WORKER)
    profile['hostSourceCommit']=previous.WORKER
    collector={}
    for name in acceptance.FILES:
        body=git(mirror,'show',enrollment.ACTIVE_PREVIOUS+':'+name)
        put(previous_path/'pilot-source'/name,body)
        collector[name]=digest(body)
    policy['tenantAcceptance']={'sourceSha256':collector,'workflowSha256':'a'*64}
    folder(root/'worker',uid)
    put(root/'worker/operation.lock',b'',uid,0o600)
    history=[put(control/name,generation.encode({'source':pin,'phase':'COMMITTED'})) for name,pin in
             (('pilot-generation-transition.json',enrollment.ACTIVE_GENERATION_SOURCE),*acceptance.ANCESTRY,
              (acceptance.SOURCE_TRANSITION,enrollment.ACTIVE_PREVIOUS))]
    journalfile=put(root/'worker/tenant-pilot-cutover-v2.json',generation.encode(journal),uid,0o600)
    statefile=put(root/'worker/automation-state.json',generation.encode(state),uid,0o600)
    profilefile=put(control/'settings/profile.json',generation.encode(profile),uid,0o600)
    monkeypatch.setattr(acceptance,'ROOT',root);monkeypatch.setattr(acceptance,'CONTROL',control)
    entry=put(root/'automation-controller/entry.py',acceptance.launcher(previous_path,revise=True))
    policyfile=put(control/'automation-policy.json',generation.encode(policy))
    primary=put(root/'fixture-primary.sqlite',b'primary-not-modified',uid,0o600)
    secondary=put(root/'fixture-secondary.sqlite',b'secondary-not-modified',uid,0o600)
    preserved={path:path.read_bytes() for path in [*history,journalfile,primary,secondary]}
    native_run=subprocess.run
    def run(command,*args,**kwargs):
        if command[0]!='/usr/sbin/runuser':return native_run(command,*args,**kwargs)
        packet=json.loads(kwargs['input'])
        assert '--property=ProtectSystem=strict' in command and '--property=NoNewPrivileges=yes' in command
        assert packet['profile']['pilotGeneration']==generation.ACTIVE_GENERATION['id']
        assert packet['profile']['baseImage']==generation.ACTIVE_BASELINE_IMAGE
        return subprocess.CompletedProcess(command,0,json.dumps({'state':'ACTIVE_PILOT_VERIFIED','admissionPreserved':True,
                                                                'hostCommit':generation.ACTIVE_BASELINE_COMMIT}),'')
    monkeypatch.setattr(subprocess,'run',run)
    monkeypatch.setattr(enrollment,'ROOT',root);monkeypatch.setattr(enrollment,'CONTROL',control)
    monkeypatch.setattr(enrollment,'REPOSITORY',mirror.as_uri())
    old_path=list(sys.path)
    old_modules={key:value for key,value in sys.modules.items() if key=='staging_automation' or key.startswith('staging_automation.')}
    for key in old_modules:del sys.modules[key]
    try:
        yield SimpleNamespace(source=source,mirror=mirror,root=root,entry=entry,policy=policyfile,profile=profilefile,state=statefile,
                              marker=control/enrollment.ACTIVE_TRANSITION)
        assert all(path.read_bytes()==body for path,body in preserved.items())
    finally:
        git(mirror,'update-ref','refs/heads/main',old_main)
        sys.path[:]=old_path
        for key in list(sys.modules):
            if key=='staging_automation' or key.startswith('staging_automation.'):del sys.modules[key]
        sys.modules.update(old_modules)


@pytest.mark.parametrize('boundary',[None,'PREPARED','entry','profile','state','policy','COMMITTED'])
def test_active_rc4_enrollment_real_filesystem_and_retry(active_upgrade_installed,boundary):
    f=active_upgrade_installed
    def interrupt(phase):
        if phase==boundary:raise PowerLoss()
    if boundary:
        with pytest.raises(PowerLoss):
            enrollment.enroll_active(f.source,enrollment.ACTIVE_APPROVAL,checkpoint=interrupt)
        assert json.loads(f.marker.read_bytes())['phase']==('COMMITTED' if boundary=='COMMITTED' else 'PREPARED')
    receipt=enrollment.enroll_active(f.source,enrollment.ACTIVE_APPROVAL)
    assert receipt['state']=='RC4_ACTIVE_GENERATION_ENROLLED' and receipt['activationAuthorized'] is False
    assert json.loads(f.marker.read_bytes())['phase']=='COMMITTED'
    assert json.loads(f.policy.read_bytes())['tenantPilotGeneration']==generation.ACTIVE_GENERATION
    assert json.loads(f.state.read_bytes())['approvedImage']==generation.ACTIVE_BASELINE_IMAGE
    assert json.loads(f.profile.read_bytes())['baseImage']==generation.ACTIVE_BASELINE_IMAGE
    assert enrollment.enroll_active(f.source,enrollment.ACTIVE_APPROVAL)['alreadyEnrolled'] is True


@pytest.mark.parametrize('boundary',[None,'entry','policy'])
def test_single_layer_repair_preserves_real_enrollment_and_resumes(active_upgrade_installed, monkeypatch, boundary):
    from tools import repair_staging_rc4_overlay as repair
    f = active_upgrade_installed
    # Reconstruct the exact previously enrolled revision, not the repair candidate.
    receipt = enrollment.enroll_active(repair.PREVIOUS, enrollment.ACTIVE_APPROVAL)
    assert receipt['state'] == 'RC4_ACTIVE_GENERATION_ENROLLED'
    preserved = {path: path.read_bytes() for path in (f.marker, f.profile, f.state)}
    old_policy = json.loads(f.policy.read_bytes())
    monkeypatch.setattr(repair, 'ROOT', f.root)
    monkeypatch.setattr(repair, 'CONTROL', f.marker.parent)
    monkeypatch.setattr(repair, 'REPOSITORY', f.mirror.as_uri())
    native = repair.switch
    if boundary:
        def interrupt(plan, read, write):
            def step(name, body):
                write(name, body)
                if name == boundary:
                    raise PowerLoss()
            native(plan, read, step)
        monkeypatch.setattr(repair, 'switch', interrupt)
        with pytest.raises(PowerLoss):
            repair.repair(f.source, repair.APPROVAL)
        marker = f.marker.parent / repair.MARKER
        assert json.loads(marker.read_bytes())['phase'] == 'PREPARED'
        monkeypatch.setattr(repair, 'switch', native)
    receipt = repair.repair(f.source, repair.APPROVAL)
    assert receipt['state'] == 'RC4_OVERLAY_REPAIR_INSTALLED'
    assert receipt['activationAuthorized'] is False
    assert json.loads(f.policy.read_bytes()) == {**old_policy, 'controllerCommit': f.source}
    assert all(path.read_bytes() == body for path, body in preserved.items())
    revision = f.root / 'automation-controller/revisions' / f.source
    previous_revision = revision.parent / repair.PREVIOUS
    builder = 'pilot-source/' + repair.BUILDER
    assert (revision / builder).read_bytes() == git(f.mirror,'show',f.source+':'+repair.BUILDER)
    assert (previous_revision / builder).read_bytes() == git(f.mirror,'show',repair.PREVIOUS+':'+repair.BUILDER)
    assert repair.repair(f.source, repair.APPROVAL)['alreadyInstalled'] is True
