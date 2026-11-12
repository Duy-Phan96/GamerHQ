"""Inspect pinned Staging promotions and handle the separately authorized recovery release."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import syslog
import tempfile
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
RUN = 42664893488
RUN_STARTED = 1794245284
CONTROLLER = '4b15a48574f7c6ff3cc20f441afbc75160fd0bfb'
PROMOTION_WORKFLOW = 'e5cf82afabfb7e09f7835e42b38302968698433bd3d7e791060ac57235c51d10'
CATALOG_WORKFLOW = '771db3136163984413fa896483bb5a093bc70ec6d8b828f6dd9492768a7a3680'
CONTROLLER_FILES = {
    '__init__.py': 'ff54982103b29d90ebf83e0262b49dc07cc57504475c3f7b156cf743f1035ea5',
    'policy.py': 'cbe3d190113bcbdaf8252a9353215bf70a283c9f0828d92029f0db556c1c9bf2',
    'oidc.py': '8a286767bd0799d9f17619dfab3ae15c31200589511969205fbdb5a4c204ec3b',
    'backend.py': '97d81188707e8a97c22edb94b89e7bb4a09807ab8fe345d266d42b6e11de5855',
    'bridge.py': '15d54fc8c0f527cfce38f281e371cc892a3c717e7bb92748add8e32bb09f0f15',
    'host_promotion.py': '35202129e048eaa636939ba50199dbd046c6a3bf69bde9bc24d7a462b06fa3a6',
    'deployment.py': '0d4b602dc3d30f4ce40bbc3c6274119ef3412d57c873efbf59248dc2b3a286e1',
    'catalog_preview.py': 'af471d827c7c9dc1789fd8621bbaec2d43d03fb7d56edd51a8282a318fa93f83',
    'catalog_import.py': '9edaedc09dcc69a6e5779c26e1dda7769883da53eb9b179dd87689fe0d1abf09',
    'catalog_manifest.json': '92483e33bc6b8dce16adfccf1884f1584e4668f1f006190b4254abeba75cbb69',
}
TARGET = '89709625d9ea0c74c2b731436d1d13c9dcb453c4'
BASELINE = '29ecf575f3d865b0f32a9d82adf1b06d7969cdc9'
ARCHIVE = 'b4155457b84179aad5263da8d9dda63fd83751e9de29a0c3c34aafbecf964a2c'
LOCALIZED_PREFLIGHT = {
    'RUN': 43218555241,
    'RUN_STARTED': 1794486678,
    'CONTROLLER': '79584661eaf013ae60bb049ab1e4c58c9c0bcb98',
    'PROMOTION_WORKFLOW': '705ad33090c9ef47d04724521680ed71d782d1b4eb500021063bccc84c0b9595',
    'CATALOG_WORKFLOW': '771db3136163984413fa896483bb5a093bc70ec6d8b828f6dd9492768a7a3680',
    'TARGET': '77b3d36361bf31a66c6fc4bc6898815cd5e96467',
    'BASELINE': '71e1fcbb0cf3904365d1ed020a866b50755f58e4',
    'ARCHIVE': 'd52af3f62120b4f50e2b1bc6a7674238f2d6d094fc065e7f6eaad1e0b2cd5be4',
    'KNOWN_HOST_BACKUP': '5f35de2662a8402ab9df001f2de7b565',
    'CONTROLLER_FILES': {
        '__init__.py': 'ff54982103b29d90ebf83e0262b49dc07cc57504475c3f7b156cf743f1035ea5',
        'policy.py': 'cbe3d190113bcbdaf8252a9353215bf70a283c9f0828d92029f0db556c1c9bf2',
        'oidc.py': '8a286767bd0799d9f17619dfab3ae15c31200589511969205fbdb5a4c204ec3b',
        'backend.py': '97d81188707e8a97c22edb94b89e7bb4a09807ab8fe345d266d42b6e11de5855',
        'bridge.py': '15d54fc8c0f527cfce38f281e371cc892a3c717e7bb92748add8e32bb09f0f15',
        'host_promotion.py': 'd2d9a15edfb8dc312822a577879aaf6fb475aa65c96ef458ecb6f8eae3504386',
        # Enrollment installs hosts/gamerhq/skill_update_controller.py here.
        'deployment.py': '0d4b602dc3d30f4ce40bbc3c6274119ef3412d57c873efbf59248dc2b3a286e1',
        'catalog_preview.py': 'af471d827c7c9dc1789fd8621bbaec2d43d03fb7d56edd51a8282a318fa93f83',
        'catalog_import.py': '9edaedc09dcc69a6e5779c26e1dda7769883da53eb9b179dd87689fe0d1abf09',
        'catalog_manifest.json': '92483e33bc6b8dce16adfccf1884f1584e4668f1f006190b4254abeba75cbb69',
    },
}

MAX_BACKUP_BYTES = 256 * 1024 ** 2
MAX_MEMORY_RESTORE_BYTES = 32 * 1024 ** 2
KNOWN_SKILL_UPDATE = '1b01158008ff4839988a97911d31a1c9'
KNOWN_HOST_BACKUP = '8fbba0198bef4f71be26cd149260184f'
RELEASE_APPROVAL = 'RELEASE VERIFIED STAGING HOST RECOVERY HOLD'
SKILL_WHEEL_COMMIT = '41a7d968852453d815a52f2f03e4ccac7e93eff9'
SKILL_WHEEL_SHA256 = '84121ae4e48c151ec9060faec70e1d262b3c961629c8cdaa535afcba96788465'
STATES = {'STAGING', 'READY', 'FAILED', 'APPLYING', 'APPLIED', 'ROLLED_BACK', 'RECOVERY_REQUIRED'}


def fixed_file(path, *, uid=None, private=False, limit=None):
    info = path.lstat()
    if (path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (uid is not None and info.st_uid != uid)
            or (private and info.st_mode & 0o077) or (limit is not None and info.st_size > limit)):
        raise ValueError('FILE_SCOPE_REJECTED')
    return info


def config(path, uid, *, private=False):
    info = fixed_file(path, uid=uid, private=private, limit=65536)
    if info.st_mode & 0o022:
        raise ValueError('FILE_SCOPE_REJECTED')
    return json.loads(path.read_bytes())


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


class ControllerSourceMismatch(ValueError):
    def __init__(self, component, reason, expected, actual=None):
        super().__init__('CONTROLLER_SOURCE_MISMATCH')
        self.evidence = {'component': component, 'reason': reason, 'expectedSha256': expected}
        if actual is not None:
            self.evidence['actualSha256'] = actual


def verify_controller(automation):
    if (automation.get('controllerCommit') != CONTROLLER
            or automation.get('hostPromotion') != {
                'commit': TARGET, 'archiveSha256': ARCHIVE, 'workflowSha256': PROMOTION_WORKFLOW}
            or automation.get('catalogPreviewWorkflowSha256') != CATALOG_WORKFLOW):
        raise ValueError('CONTROLLER_BINDING_REJECTED')
    controller = ROOT / 'automation-controller'
    revision = controller / 'revisions' / CONTROLLER
    entry = controller / 'entry.py'
    expected = ("import sys\nsys.path[:0]=[" + repr(str(revision)) + ",'/opt/gamerhq-staging/deployment-worker']\n"
                "from staging_automation.bridge import entry\nentry()\n").encode()
    paths = {entry: hashlib.sha256(expected).hexdigest()}
    paths.update({revision / 'staging_automation' / name: value for name, value in CONTROLLER_FILES.items()})
    for path, expected_digest in paths.items():
        info = fixed_file(path, uid=0, limit=262144)
        if info.st_mode & 0o022:
            raise ControllerSourceMismatch(path.name, 'WRITABLE_SOURCE', expected_digest)
        actual_digest = digest(path)
        if actual_digest != expected_digest:
            raise ControllerSourceMismatch(path.name, 'CHECKSUM_MISMATCH', expected_digest, actual_digest)


def read_database(path, *, immutable=False):
    fixed_file(path, limit=MAX_BACKUP_BYTES)
    # A live read must not create WAL index files. If the sidecars are absent,
    # there is no live WAL to read; immutable mode never creates SQLite files.
    if not immutable:
        wal = path.with_name(path.name + '-wal')
        shm = path.with_name(path.name + '-shm')
        if wal.exists() or wal.is_symlink():
            fixed_file(wal)
            fixed_file(shm)
        else:
            if path.with_name(path.name + '-journal').exists():
                raise ValueError('DATABASE_READ_UNAVAILABLE')
            immutable = True
    uri = path.as_uri() + '?mode=ro' + ('&immutable=1' if immutable else '')
    return sqlite3.connect(uri, uri=True, timeout=3)


def integrity(conn):
    return conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]


def receipt_for_backup(root, ident, evidence, uid, *, guild_id=None):
    if not re.fullmatch('[0-9a-f]{32}', ident):
        raise ValueError('BACKUP_SCOPE_REJECTED')
    path = root / (ident + '.sqlite')
    saved = evidence.get('backup', {})
    verified = saved.get('verification', {})
    result = {'recorded': 'backup' in evidence, 'filePresent': path.exists(),
              'checksumMatches': None, 'integrityPass': None, 'restoreProbe': 'UNAVAILABLE',
              'restoreProbeRecorded': verified.get('restoreProbe') == 'PASS'}
    if not result['filePresent']:
        return {**result, 'inspection': 'FILE_MISSING'}
    try:
        info = fixed_file(path)
        result['mode'] = format(stat.S_IMODE(info.st_mode), '04o')
        if info.st_uid != uid:
            return {**result, 'inspection': 'OWNER_MISMATCH'}
        if info.st_mode & 0o077:
            return {**result, 'inspection': 'MODE_UNSAFE'}
        if not 0 < info.st_size <= MAX_BACKUP_BYTES:
            return {**result, 'inspection': 'SIZE_REJECTED'}
        actual = digest(path)
        if 'sha256' in saved:
            result['checksumMatches'] = actual == saved['sha256']
        if any(path.with_name(path.name + suffix).exists() or path.with_name(path.name + suffix).is_symlink()
               for suffix in ('-wal', '-shm', '-journal')):
            return {**result, 'inspection': 'SIDECARS_PRESENT'}
        with closing(read_database(path, immutable=True)) as conn:
            result['integrityPass'] = integrity(conn)
            if result['checksumMatches'] and result['integrityPass'] and guild_id is not None:
                result['guildLifecycle'] = lifecycle(conn, guild_id)
                if info.st_size > MAX_MEMORY_RESTORE_BYTES:
                    result['restoreProbe'] = 'DEFERRED_SIZE_LIMIT'
                else:
                    with closing(sqlite3.connect(':memory:')) as clone:
                        conn.backup(clone)
                        authority = clone.execute(
                            "SELECT guild_id,skill_id FROM skill_domain_authority WHERE domain='event-manager'"
                        ).fetchall()
                        same_schema = clone.execute('PRAGMA user_version').fetchone() == conn.execute('PRAGMA user_version').fetchone()
                        result['restoreProbe'] = ('PASS' if integrity(clone) and same_schema
                                                  and authority == [(guild_id, 'event-manager')] else 'FAILED')
        checked = fixed_file(path, uid=uid, private=True, limit=MAX_BACKUP_BYTES)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(checked) != identity(info):
            raise ValueError('BACKUP_CHANGED')
    except (OSError, ValueError, sqlite3.Error):
        result.pop('guildLifecycle', None)
        result.update(checksumMatches=None, integrityPass=None, restoreProbe='UNAVAILABLE', inspection='UNAVAILABLE')
    return result


def safe_commit(value):
    return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) else None


def lifecycle(conn, guild_id):
    skill = conn.execute(
        "SELECT installed,configured,enabled,version,updated_at FROM skill_guild_state "
        "WHERE guild_id=? AND skill_id='event-manager'", (guild_id,),
    ).fetchone()
    version = skill[3] if skill and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9.]+)?', str(skill[3])) else None
    return {'skillInstalled': bool(skill and skill[0]), 'skillConfigured': bool(skill and skill[1]),
            'skillEnabled': bool(skill and skill[2]), 'skillVersion': version,
            'skillLifecycleUpdatedAt': skill[4] if skill and type(skill[4]) is int else None}


def evidence_from_database(path, backup_root, guild_id, uid, *, approved_image=None):
    with closing(read_database(path)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        db_ok = integrity(conn)
        authority = conn.execute(
            "SELECT skill_id FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'",
            (guild_id,),
        ).fetchall()
        guild_lifecycle = lifecycle(conn, guild_id)
        rows = conn.execute(
            "SELECT id,state,created_at,updated_at,previous_image,evidence_json FROM skill_deployments "
            "WHERE skill_id='event-manager' AND (updated_at>=? OR state IN ('STAGING','READY','APPLYING','RECOVERY_REQUIRED')) "
            "ORDER BY updated_at DESC LIMIT 21", (RUN_STARTED,),
        ).fetchall()
        preceding = conn.execute(
            "SELECT id,evidence_json FROM skill_deployments WHERE skill_id='event-manager' "
            "AND state IN ('APPLIED','ROLLED_BACK') AND updated_at<? "
            "ORDER BY updated_at DESC LIMIT 1", (RUN_STARTED,),
        ).fetchone()
        known = conn.execute(
            "SELECT id,image,evidence_json FROM skill_deployments "
            "WHERE id=? AND skill_id='event-manager' AND state='APPLIED' AND version='0.1.2'",
            (KNOWN_SKILL_UPDATE,),
        ).fetchone()
        older = conn.execute(
            "SELECT id,evidence_json FROM skill_deployments WHERE id=? AND skill_id='event-manager' AND state='APPLIED'",
            (KNOWN_HOST_BACKUP,),
        ).fetchone()
    if len(rows) > 20:
        raise ValueError('DEPLOYMENT_HISTORY_LIMIT')
    attempts = []
    blockers = []
    for ident, state, created, updated, previous, raw in rows:
        evidence = json.loads(raw)
        target = evidence.get('hostSourceCommit')
        if not target:
            continue
        if state not in STATES:
            raise ValueError('DEPLOYMENT_STATE_REJECTED')
        if target == TARGET and created >= RUN_STARTED:
            attempts.append({'state': state, 'createdAt': created, 'updatedAt': updated,
                             'previousImageRecorded': bool(previous),
                             'offlineProbePassed': evidence.get('probe', {}).get('healthy') is True,
                             'backup': receipt_for_backup(backup_root, ident, evidence, uid)})
        elif state in {'APPLYING', 'RECOVERY_REQUIRED'}:
            blockers.append({'state': state, 'targetHostCommit': safe_commit(target), 'updatedAt': updated})
    known_release = json.loads(known[2]).get('release', {}) if known else {}
    known_matches = bool(known and approved_image and known[1] == approved_image
                         and known_release.get('reviewedCommit') == SKILL_WHEEL_COMMIT
                         and known_release.get('sha256') == SKILL_WHEEL_SHA256)
    return {'integrityPass': db_ok, 'eventManagerAuthority': authority == [('event-manager',)],
            'knownSkillUpdateMatchesApprovedImageAndArtifact': known_matches,
            'latestPreRunBackupMatchesKnownSkillUpdate': bool(preceding and preceding[0] == KNOWN_SKILL_UPDATE),
            **guild_lifecycle, 'attemptsSinceFailedRun': attempts, 'preexistingHostBlockers': blockers,
            'pendingDeploymentCount': sum(row[1] in {'STAGING', 'READY', 'APPLYING', 'RECOVERY_REQUIRED'} for row in rows),
            'latestPreRunBackup': (receipt_for_backup(backup_root, preceding[0], json.loads(preceding[1]), uid,
                                                       guild_id=guild_id) if preceding else None),
            'lastVerifiedHostBackup': (receipt_for_backup(backup_root, older[0], json.loads(older[1]), uid,
                                                          guild_id=guild_id)
                                       if older and (not preceding or older[0] != preceding[0]) else None)}


def container_status(account, socket, profile, state):
    info = socket.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != account.pw_uid or socket.resolve(strict=True) != socket:
        raise ValueError('ROOTLESS_SOCKET_SCOPE_REJECTED')
    if os.geteuid() != account.pw_uid:
        raise ValueError('STAGING_ACCOUNT_SCOPE_REJECTED')
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': account.pw_dir,
                   'DOCKER_HOST': 'unix://' + str(socket), 'DOCKER_CONFIG': str(CONTROL / 'docker-client'),
                   'XDG_RUNTIME_DIR': str(socket.parent)}

    def read(*args):
        result = subprocess.run(['docker', *args], env=environment, cwd=ROOT, capture_output=True, text=True,
                                check=True, timeout=10)
        if len(result.stdout) > 8192:
            raise ValueError('DOCKER_RESPONSE_REJECTED')
        return result.stdout.strip()

    security, daemon_id = read('info', '--format', '{{json .SecurityOptions}}|{{.ID}}').rsplit('|', 1)
    options = json.loads(security)
    if (not isinstance(options, list)
            or not any(str(option).split(',', 1)[0] == 'name=rootless' for option in options)
            or not daemon_id or daemon_id != profile.get('dockerDaemonId')):
        raise ValueError('ROOTLESS_SOCKET_SCOPE_REJECTED')
    container = 'gamerhq-staging-bot'
    if not read('ps', '--all', '--filter', 'name=^/' + container + '$', '--format', '{{.ID}}'):
        return {'present': False}
    fields = read('inspect', '--type', 'container', '--format',
                  '{{.Name}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}'
                  '|{{index .Config.Labels "com.docker.compose.project"}}'
                  '|{{index .Config.Labels "com.docker.compose.service"}}'
                  '|{{index .Config.Labels "org.opencontainers.image.revision"}}', container).split('|')
    if len(fields) != 7 or fields[0] != '/' + container or fields[4:6] != ['gamerhq-staging'] * 2:
        raise ValueError('CONTAINER_SCOPE_REJECTED')
    _, image, status, health, _, _, commit = fields
    approved = image == state.get('approvedImage')
    report = {'present': True, 'running': status == 'running', 'dockerHealthy': health == 'healthy',
              'hostCommit': safe_commit(commit), 'matchesApprovedImage': approved,
              'matchesProfileBaseImage': image == profile.get('baseImage')}
    if status != 'running' or not approved or commit != BASELINE:
        return report
    mount = read('inspect', '--type', 'container', '--format',
                 '{{range .Mounts}}{{if eq .Destination "/app/runtime/data"}}{{.Type}}|{{.Source}}{{end}}{{end}}',
                 container)
    if mount != 'bind|' + str(ROOT / 'data'):
        return {**report, 'runtimeInspection': 'MOUNT_SCOPE_REJECTED'}
    probe = """
import hashlib
import importlib.metadata
import json
import os
path = '/app/runtime/data/gamerhq.db'
if os.environ.get('GAMERHQ_DB_PATH') != path:
    raise SystemExit(1)
info = os.stat(path)
wheel = '/opt/skill-release/gamerhq_skill_event_manager-0.1.2-py3-none-any.whl'
digest = None
if os.path.isfile(wheel) and os.stat(wheel).st_size <= 32 * 1024 * 1024:
    with open(wheel, 'rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
print(json.dumps({'device': info.st_dev, 'inode': info.st_ino, 'wheelSha256': digest,
                  'version': importlib.metadata.version('gamerhq-skill-event-manager')}))
"""
    try:
        facts = json.loads(read('exec', '--user', '10001:10001', container, 'python', '-I', '-B', '-c', probe))
        host_db = fixed_file(ROOT / 'data/gamerhq.db')
        version = facts.get('version')
        valid_version = version if isinstance(version, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9.]+)?', version) else None
        return {**report, 'runtimeInspection': 'PASS', 'packageVersion': valid_version,
                'wheelMatchesReviewedArtifact': facts.get('wheelSha256') == SKILL_WHEEL_SHA256,
                'runtimeDatabaseMatchesProfile': (facts.get('device'), facts.get('inode'))
                == (host_db.st_dev, host_db.st_ino)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**report, 'runtimeInspection': 'UNAVAILABLE'}


def held_baseline_checks(report):
    container = report.get('container', {})
    database = report.get('database', {})
    backup = database.get('lastVerifiedHostBackup') or {}
    unsafe = database.get('latestPreRunBackup') or {}
    return {
        'reviewedController': report.get('controllerSourceVerified') is True,
        'recoveryHeld': report.get('recoveryRequired') is True and report.get('automationEnabled') is True,
        'baselineHostHealthy': (report.get('profileHostCommit') == BASELINE
                                and container.get('hostCommit') == BASELINE and container.get('running') is True
                                and container.get('dockerHealthy') is True and container.get('matchesApprovedImage') is True),
        'verifiedSkillAndDatabaseBinding': (container.get('runtimeInspection') == 'PASS'
                                            and container.get('runtimeDatabaseMatchesProfile') is True
                                            and container.get('packageVersion') == '0.1.2'
                                            and container.get('wheelMatchesReviewedArtifact') is True
                                            and database.get('knownSkillUpdateMatchesApprovedImageAndArtifact') is True),
        'guildLifecyclePreserved': (database.get('integrityPass') is True and database.get('eventManagerAuthority') is True
                                    and database.get('skillInstalled') is True and database.get('skillConfigured') is True
                                    and database.get('skillEnabled') is False and database.get('skillVersion') == '0.1.1'),
        'noCandidateAttemptOrHostBlocker': (database.get('attemptsSinceFailedRun') == []
                                            and database.get('preexistingHostBlockers') == []),
        'historicalBackupVerified': (backup.get('checksumMatches') is True and backup.get('integrityPass') is True
                                      and backup.get('restoreProbe') == 'PASS'),
        'unsafeBackupExcluded': (database.get('latestPreRunBackupMatchesKnownSkillUpdate') is True
                                 and unsafe.get('mode') == '0640' and unsafe.get('inspection') == 'MODE_UNSAFE'
                                 and unsafe.get('checksumMatches') is None and unsafe.get('restoreProbe') == 'UNAVAILABLE'),
    }


def audit_release(audit_id, phase, **details):
    syslog.openlog('gamerhq-staging-security', syslog.LOG_PID, syslog.LOG_AUTHPRIV)
    syslog.syslog(syslog.LOG_NOTICE, json.dumps({
        'operation': 'release-staging-host-recovery-hold', 'workflowRun': RUN,
        'controllerCommit': CONTROLLER, 'auditId': audit_id, 'state': phase, **details,
    }))


def private_backup_mode(root, uid):
    path = root / (KNOWN_SKILL_UPDATE + '.sqlite')
    info = fixed_file(path, uid=uid, limit=MAX_BACKUP_BYTES)
    if not 0 < info.st_size <= MAX_MEMORY_RESTORE_BYTES or any(
            path.with_name(path.name + suffix).exists() or path.with_name(path.name + suffix).is_symlink()
            for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('BACKUP_SCOPE_REJECTED')
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_uid, item.st_nlink)
        original = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(opened.st_mode) or identity(opened) != identity(info)
                or stat.S_IMODE(opened.st_mode) != original or original not in (0o640, 0o600)):
            raise ValueError('BACKUP_SCOPE_REJECTED')
        if original == 0o640:
            # O_PATH pins the inode without opening file contents. chmod follows
            # this process's own descriptor, never a replaceable backup pathname.
            os.chmod(f'/proc/self/fd/{fd}', 0o600)
        verified = fixed_file(path, uid=uid, private=True, limit=MAX_BACKUP_BYTES)
        if identity(verified) != identity(info) or stat.S_IMODE(verified.st_mode) != 0o600:
            raise ValueError('BACKUP_SCOPE_REJECTED')
        return format(original, '04o')
    finally:
        os.close(fd)


def release_checks(report, *, before_mode_change):
    checks = held_baseline_checks(report)
    unsafe_excluded = checks.pop('unsafeBackupExcluded')
    database = report.get('database', {})
    backup = database.get('latestPreRunBackup') or {}
    private_verified = (database.get('latestPreRunBackupMatchesKnownSkillUpdate') is True
                        and backup.get('mode') == '0600' and backup.get('checksumMatches') is True
                        and backup.get('integrityPass') is True and backup.get('restoreProbe') == 'PASS')
    checks['skillUpdateBackup'] = private_verified or (before_mode_change and unsafe_excluded)
    checks['noIncompleteDeployment'] = database.get('pendingDeploymentCount') == 0
    return checks


def release_state(path, state, uid):
    if state.get('recoveryRequired') is not True or config(path, uid, private=True) != state:
        raise ValueError('RECOVERY_STATE_CHANGED')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.staging-recovery-', delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump({**state, 'recoveryRequired': False}, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def release_hold(report, state_path, state, backup_root, uid, refresh):
    audit_id = uuid.uuid4().hex
    report.update(operation='release-staging-host-recovery-hold', auditId=audit_id,
                  ownerApprovalProvided=True, recoveryReleaseAuthorized=False, backupModeChanged=False)
    before = release_checks(report, before_mode_change=True)
    before['recoveryHeld'] = (before['recoveryHeld'] and state.get('recoveryRequired') is True
                              and state.get('bootstrapVerified') is True)
    report.update(checks=before, state='RECOVERY_HELD' if state.get('recoveryRequired') is True else 'REVIEW_REQUIRED')
    audit_release(audit_id, 'STARTED')
    if not all(before.values()):
        audit_release(audit_id, 'BASELINE_REJECTED')
        return report
    audit_release(audit_id, 'BACKUP_MODE_CHECK_STARTED')
    try:
        original_mode = private_backup_mode(backup_root, uid)
    except (OSError, ValueError):
        audit_release(audit_id, 'BACKUP_MODE_UNCONFIRMED')
        report.update(checks={}, backupModeChanged=None)
        return report
    report.update(backupModeBefore=original_mode, backupModeAfter='0600', backupModeChanged=original_mode != '0600')
    audit_release(audit_id, 'BACKUP_PRIVATE', previousMode=original_mode, mode='0600')
    try:
        refresh()
    except (OSError, ValueError):
        audit_release(audit_id, 'VERIFICATION_ERROR')
        report['checks'] = {}
        return report
    checks = release_checks(report, before_mode_change=False)
    report['checks'] = checks
    if not all(checks.values()):
        audit_release(audit_id, 'VERIFICATION_REJECTED')
        return report
    audit_release(audit_id, 'RELEASE_VERIFIED')
    release_state(state_path, state, uid)
    audit_release(audit_id, 'RELEASED')
    report.update(state='RECOVERY_HOLD_RELEASED', recoveryRequired=False, recoveryReleaseAuthorized=True)
    return report


class NoDiscordRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def discord_get(path, token):
    request = Request('https://discord.com/api/v10' + path, headers={
        'User-Agent': 'DiscordBot (https://github.com/Duy-Phan96/GamerHQ, staging-1)'}, method='GET')
    request.add_unredirected_header('Authorization', 'Bot ' + token)
    with build_opener(NoDiscordRedirect()).open(request, timeout=15) as response:
        body = response.read(262145)
    if len(body) > 262144:
        raise ValueError('DISCORD_RESPONSE_LIMIT')
    return json.loads(body)


def snowflake(value):
    value = str(value)
    return value if re.fullmatch('[1-9][0-9]{0,19}', value) else None


def event_resource_bindings(profile):
    guild = int(profile['stagingGuildId'])
    with closing(read_database(Path(profile['database']))) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        policy = conn.execute('SELECT value FROM settings WHERE key=?', (f'skill_resources:{guild}:event-manager',)).fetchone()
        rows = conn.execute("SELECT kind,state_json FROM skill_managed_resources WHERE guild_id=? "
                            "AND skill_id='event-manager' ORDER BY resource_key LIMIT 65", (guild,)).fetchall()
    if len(rows) > 64:
        raise ValueError('EVENT_BINDING_LIMIT')
    policy = json.loads(policy[0]) if policy else {}
    stored = {key: snowflake(policy.get(key)) for key in ('categoryId', 'voiceCategoryId')}
    bindings = []
    for kind, raw in rows:
        state = json.loads(raw)
        channel = snowflake(state.get('channelId'))
        if kind in {'text', 'voice'} and channel:
            bindings.append((channel, kind, state.get('marker')))
    return stored, bindings


def event_inventory(channels, guild, configured_categories, bindings):
    # Only disclose exact configured IDs, named test areas and recorded Skill resources.
    by_id = {}
    for row in channels:
        ident = snowflake(row['id'])
        if not ident or ident in by_id or str(row.get('guild_id', guild)) != guild:
            raise ValueError('CHANNEL_INVENTORY_SCOPE_REJECTED')
        by_id[ident] = row
    named = {ident for ident, row in by_id.items() if row.get('type') == 4
             and re.findall('[a-z0-9]+', str(row.get('name', '')).casefold()) == ['event', 'testing']}
    tracked = []
    parents = set()
    for ident, kind, marker in bindings:
        row = by_id.get(ident)
        entry = {'channelId': ident, 'storedKind': kind, 'presentInBotInventory': row is not None}
        if row is not None:
            parent = snowflake(row.get('parent_id'))
            entry.update(parentCategoryId=parent, typeMatches=row.get('type') == (0 if kind == 'text' else 2))
            if parent:
                parents.add(parent)
            if kind == 'text':
                entry['textMarkerMatches'] = bool(marker and row.get('topic') == marker)
        tracked.append(entry)
    test_areas = named | set(configured_categories)
    area_ids = test_areas | parents
    categories = [{'categoryId': ident, 'matchesEventTestingName': ident in named, 'parentOfStoredBinding': ident in parents}
                  for ident in sorted(area_ids) if by_id.get(ident, {}).get('type') == 4]
    children = [{'channelId': ident, 'parentCategoryId': snowflake(row.get('parent_id')), 'discordType': row['type']}
                for ident, row in sorted(by_id.items()) if snowflake(row.get('parent_id')) in test_areas]
    if len(children) > 128:
        raise ValueError('EVENT_INVENTORY_LIMIT')
    return {'categories': categories, 'storedChannelBindings': tracked, 'childrenOfReportedAreas': children}


def inventory_category(read, token, guild, category, bot, member, roles, *, bindings=None, configured_categories=()):
    """Return only the pinned ID's identity and effective visibility from the guild route."""
    try:
        channels = read(f'/guilds/{guild}/channels', token)
        if not isinstance(channels, list):
            raise ValueError('Unexpected channel inventory')
        detail = {'eventResources': event_inventory(channels, guild, configured_categories, bindings)} if bindings is not None else {}
        matches = [row for row in channels if str(row['id']) == category]
        if not matches:
            return {'state': 'ABSENT_FROM_BOT_INVENTORY', **detail}
        if len(matches) != 1 or matches[0].get('type') != 4 or str(matches[0].get('guild_id', guild)) != guild:
            return {'state': 'IDENTITY_REJECTED', **detail}

        # Match Discord's precedence for View Channel without reporting role IDs or overwrites.
        member_roles = set(map(str, member['roles'])) - {guild}
        role_ids = member_roles | {guild}
        permissions = 0
        for role in roles:
            if str(role['id']) in role_ids:
                permissions |= int(role['permissions'])
        if permissions & (1 << 3):
            return {'state': 'CATEGORY_FOUND', 'botViewChannel': True, **detail}
        view = 1 << 10
        overwrites = matches[0]['permission_overwrites']
        for targets, kind in (({guild}, 0), (member_roles, 0), ({bot}, 1)):
            allow = deny = 0
            for row in overwrites:
                if row['type'] == kind and str(row['id']) in targets:
                    allow |= int(row['allow'])
                    deny |= int(row['deny'])
            permissions = (permissions & ~deny) | allow
        return {'state': 'CATEGORY_FOUND', 'botViewChannel': bool(permissions & view), **detail}
    except HTTPError as error:
        return {'state': 'HTTP_REJECTED', 'httpStatus': error.code}
    except (OSError, AttributeError, TypeError, KeyError, ValueError):
        return {'state': 'LOOKUP_UNAVAILABLE'}


def discord_error_code(error):
    try:
        payload = json.loads(error.read(4097))
        code = payload.get('code')
    except (OSError, AttributeError, TypeError, ValueError):
        return None
    return code if type(code) is int and 0 < code < 1000000 else None


def discord_preflight(profile, token, *, read=discord_get, bindings=None):
    guild = str(profile['stagingGuildId'])
    bot = str(profile['stagingBotId'])
    categories = {step: str(profile[key]) for step, key in
                  (('text-category', 'categoryId'), ('voice-category', 'voiceCategoryId'))}
    if (guild != '1567098041641738331' or bot != '1567098530525351976'
            or str(profile['productionGuildId']) == guild or str(profile['productionBotId']) == bot
            or any(not re.fullmatch('[1-9][0-9]{0,19}', value) for value in categories.values())):
        raise ValueError('STAGING_BINDING_REJECTED')
    checks = [('bot-identity', '/users/@me', lambda row: str(row['id']) == bot),
              ('guild-membership', '/users/@me/guilds?limit=200',
               lambda rows: {str(row['id']) for row in rows} == {guild}),
              ('guild-identity', '/guilds/' + guild, lambda row: str(row['id']) == guild),
              ('bot-membership', f'/guilds/{guild}/members/{bot}', lambda row: isinstance(row.get('roles'), list)),
              ('guild-roles', f'/guilds/{guild}/roles', lambda rows: isinstance(rows, list))]
    for step, category in categories.items():
        checks.append((step, '/channels/' + category,
                       lambda row: str(row['guild_id']) == guild and row['type'] == 4))
    completed = []
    membership = {}
    for step, path, valid in checks:
        outcome = {'step': step, 'completed': completed}
        if step in categories:
            outcome['categoryId'] = categories[step]
        try:
            response = read(path, token)
            if not valid(response):
                return {**outcome, 'state': 'IDENTITY_REJECTED'}
            if step in {'bot-membership', 'guild-roles'}:
                membership[step] = response
        except HTTPError as error:
            code = discord_error_code(error)
            if code is not None:
                outcome['discordCode'] = code
            if error.code == 404 and step in categories:
                outcome['guildInventory'] = inventory_category(
                    read, token, guild, categories[step], bot, membership['bot-membership'], membership['guild-roles'],
                    bindings=bindings, configured_categories=categories.values())
            return {**outcome, 'state': 'HTTP_REJECTED', 'httpStatus': error.code}
        except (OSError, AttributeError, TypeError, KeyError, ValueError):
            return {**outcome, 'state': 'LOOKUP_UNAVAILABLE'}
        completed.append(step)
    result = {'state': 'LOOKUPS_PASS', 'completed': completed}
    if bindings is not None:
        result['guildInventory'] = inventory_category(
            read, token, guild, categories['text-category'], bot, membership['bot-membership'], membership['guild-roles'],
            bindings=bindings, configured_categories=categories.values())
    return result


def installed_discord_preflight(profile, uid):
    path = CONTROL / 'settings/bot.env'
    if profile.get('botEnvFile') != str(path):
        raise ValueError('STAGING_BINDING_REJECTED')
    fixed_file(path, uid=uid, private=True, limit=16384)
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, sep, value = line.partition('=')
            if not sep or key in values:
                raise ValueError('STAGING_BINDING_REJECTED')
            values[key] = value.strip().strip('"\'')
    token = values.get('DISCORD_TOKEN', '')
    if values.get('GUILD_ID') != str(profile['stagingGuildId']) or not re.fullmatch(r'[A-Za-z0-9_.-]{20,2048}', token):
        raise ValueError('STAGING_BINDING_REJECTED')
    configured = {key: snowflake(profile[key]) for key in ('categoryId', 'voiceCategoryId')}
    try:
        stored, bindings = event_resource_bindings(profile)
    except (OSError, AttributeError, KeyError, TypeError, ValueError, sqlite3.Error):
        result = discord_preflight(profile, token, bindings=[])
        return {**result, 'profileCategoryIds': configured, 'storedResourcePolicy': 'UNAVAILABLE'}
    result = discord_preflight(profile, token, bindings=bindings)
    return {**result, 'profileCategoryIds': configured, 'storedResourcePolicyCategoryIds': stored}


def collect(*, release=False, localized_preflight=False):
    if release and localized_preflight:
        raise ValueError('EXPLICIT_OWNER_RELEASE_REQUIRED')
    if os.geteuid() != 0:
        raise PermissionError('OWNER_ROOT_SESSION_REQUIRED')
    import pwd
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid = account.pw_uid
    if uid <= 0 or account.pw_dir != '/var/lib/gamerhq-staging-bridge':
        raise ValueError('STAGING_ACCOUNT_SCOPE_REJECTED')
    work = ROOT / 'worker'
    backup_root = ROOT / 'backups'
    db = ROOT / 'data/gamerhq.db'
    socket = Path(f'/run/user/{uid}/docker.sock')
    policy = config(CONTROL / 'policy.json', 0)
    automation_path = CONTROL / 'automation-policy.json'
    automation = config(automation_path, 0)
    verify_controller(automation)
    # Root verifies the two protected policies. All mutable files and the Docker
    # socket are read with the same unprivileged identity as the existing worker.
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(uid)
    profile = config(CONTROL / 'settings/profile.json', uid, private=True)
    expected = {'root': str(ROOT), 'workRoot': str(work), 'backupRoot': str(backup_root),
                'database': str(db), 'dockerHost': 'unix://' + str(socket), 'deploymentUid': uid,
                'dockerMode': 'rootless', 'environment': 'staging', 'container': 'gamerhq-staging-bot'}
    if (not isinstance(automation.get('hostPromotion'), dict)
            or any(profile.get(key) != value for key, value in expected.items())
            or any(policy.get(key) != expected[key] for key in ('root', 'deploymentUid', 'dockerHost'))
            or str(profile.get('stagingGuildId')) != '1567098041641738331'
            or str(profile.get('productionGuildId')) == str(profile.get('stagingGuildId'))
            or automation.get('controllerCommit') != CONTROLLER
            or automation.get('hostPromotion', {}).get('commit') != TARGET
            or automation.get('hostPromotion', {}).get('archiveSha256') != ARCHIVE):
        raise ValueError('STAGING_BINDING_REJECTED')
    for directory in (work, backup_root, db.parent):
        if directory.resolve(strict=True) != directory or not directory.is_dir():
            raise ValueError('STAGING_BINDING_REJECTED')
    lock = work / 'operation.lock'
    lock_info = fixed_file(lock, uid=uid, private=True)
    report = {'operation': 'verify-staging-promotion-recovery-hold', 'workflowRun': RUN,
              'controllerCommit': CONTROLLER, 'targetHostCommit': TARGET,
              'productionChanged': False, 'databaseChanged': False, 'containersChanged': False,
              'discordResourcesChanged': False, 'credentialFilesOpened': False}
    with os.fdopen(os.open(lock, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (lock_info.st_dev, lock_info.st_ino):
            raise ValueError('FILE_SCOPE_REJECTED')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {**report, 'operationLock': 'BUSY'}
        if config(automation_path, 0) != automation:
            raise ValueError('CONTROLLER_BINDING_REJECTED')
        verify_controller(automation)
        state_path = work / 'automation-state.json'
        state = config(state_path, uid, private=True)
        report.update(operationLock='AVAILABLE', controllerSourceVerified=True, recoveryRequired=state.get('recoveryRequired') is True,
                      automationEnabled=state.get('enabled') is True,
                      profileHostCommit=safe_commit(profile.get('applicationHostCommit')))
        def refresh():
            if config(automation_path, 0) != automation or config(state_path, uid, private=True) != state:
                raise ValueError('RECOVERY_STATE_CHANGED')
            verify_controller(automation)
            try:
                report['container'] = container_status(account, socket, profile, state)
            except (OSError, ValueError, subprocess.SubprocessError):
                report['container'] = {'inspection': 'UNAVAILABLE'}
            try:
                report['database'] = evidence_from_database(db, backup_root, int(profile['stagingGuildId']), uid,
                                                            approved_image=state.get('approvedImage'))
            except (OSError, ValueError, sqlite3.Error):
                report['database'] = {'inspection': 'UNAVAILABLE'}

        refresh()
        if localized_preflight:
            report.update(operation='verify-staging-promotion-preflight', state='PREFLIGHT_COLLECTED')
            report.pop('credentialFilesOpened')
            try:
                report['discord'] = installed_discord_preflight(profile, uid)
            except (OSError, ValueError, KeyError):
                report['discord'] = {'state': 'LOCAL_CONFIGURATION_REJECTED'}
            return report
        if release:
            return release_hold(report, state_path, state, backup_root, uid, refresh)
        checks = held_baseline_checks(report)
        report.update(checks=checks, state='RECOVERY_HOLD_VERIFIED' if all(checks.values()) else 'REVIEW_REQUIRED',
                      recoveryReleaseAuthorized=False)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-verified-recovery', action='store_true')
    parser.add_argument('--localized-reuse-preflight', action='store_true')
    parser.add_argument('--approval')
    args = parser.parse_args()
    try:
        if (args.release_verified_recovery and args.approval != RELEASE_APPROVAL
                or not args.release_verified_recovery and args.approval is not None):
            raise PermissionError('EXPLICIT_OWNER_RELEASE_REQUIRED')
        if args.localized_reuse_preflight:
            globals().update(LOCALIZED_PREFLIGHT)
        print(json.dumps(collect(release=args.release_verified_recovery,
                                 localized_preflight=args.localized_reuse_preflight), indent=2))
    except ControllerSourceMismatch as error:
        raise SystemExit(json.dumps({'diagnosticError': str(error), 'controllerCommit': CONTROLLER,
                                     **error.evidence, 'productionChanged': False})) from None
    except Exception as error:
        reason = str(error)
        known = {'OWNER_ROOT_SESSION_REQUIRED', 'STAGING_ACCOUNT_SCOPE_REJECTED',
                 'STAGING_BINDING_REJECTED', 'FILE_SCOPE_REJECTED',
                 'CONTROLLER_BINDING_REJECTED', 'CONTROLLER_SOURCE_MISMATCH',
                 'BACKUP_SCOPE_REJECTED', 'RECOVERY_STATE_CHANGED', 'EXPLICIT_OWNER_RELEASE_REQUIRED'}
        code = reason if reason in known else 'DIAGNOSTIC_UNAVAILABLE'
        raise SystemExit(json.dumps({'diagnosticError': code, 'productionChanged': False})) from None
