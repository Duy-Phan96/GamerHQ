"""Owner-only, read-only receipt for the failed staging Host promotion."""
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

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
RUN = 42664893488
RUN_STARTED = 1794245284
CONTROLLER = '0931e16e85db6024b3142aacc072f7721ec28e2d'
TARGET = '89709625d9ea0c74c2b731436d1d13c9dcb453c4'
BASELINE = '29ecf575f3d865b0f32a9d82adf1b06d7969cdc9'
ARCHIVE = 'b4155457b84179aad5263da8d9dda63fd83751e9de29a0c3c34aafbecf964a2c'
MAX_BACKUP_BYTES = 256 * 1024 ** 2
MAX_MEMORY_RESTORE_BYTES = 32 * 1024 ** 2
KNOWN_SKILL_UPDATE = '1b01158008ff4839988a97911d31a1c9'
KNOWN_HOST_BACKUP = '8fbba0198bef4f71be26cd149260184f'
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
            "WHERE skill_id='event-manager' AND (updated_at>=? OR state IN ('APPLYING','RECOVERY_REQUIRED')) "
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
            **guild_lifecycle, 'attemptsSinceFailedRun': attempts, 'preexistingHostBlockers': blockers,
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
import importlib.metadata
import json
import os
path = '/app/runtime/data/gamerhq.db'
if os.environ.get('GAMERHQ_DB_PATH') != path:
    raise SystemExit(1)
info = os.stat(path)
print(json.dumps({'device': info.st_dev, 'inode': info.st_ino,
                  'version': importlib.metadata.version('gamerhq-skill-event-manager')}))
"""
    try:
        facts = json.loads(read('exec', '--user', '10001:10001', container, 'python', '-I', '-B', '-c', probe))
        host_db = fixed_file(ROOT / 'data/gamerhq.db')
        version = facts.get('version')
        valid_version = version if isinstance(version, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9.]+)?', version) else None
        return {**report, 'runtimeInspection': 'PASS', 'packageVersion': valid_version,
                'runtimeDatabaseMatchesProfile': (facts.get('device'), facts.get('inode'))
                == (host_db.st_dev, host_db.st_ino)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**report, 'runtimeInspection': 'UNAVAILABLE'}


def collect():
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
    automation = config(CONTROL / 'automation-policy.json', 0)
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
    report = {'operation': 'diagnose-staging-host-promotion', 'workflowRun': RUN,
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
        state = config(work / 'automation-state.json', uid, private=True)
        report.update(operationLock='AVAILABLE', recoveryRequired=state.get('recoveryRequired') is True,
                      automationEnabled=state.get('enabled') is True,
                      profileHostCommit=safe_commit(profile.get('applicationHostCommit')))
        try:
            report['container'] = container_status(account, socket, profile, state)
        except (OSError, ValueError, subprocess.SubprocessError):
            report['container'] = {'inspection': 'UNAVAILABLE'}
        try:
            report['database'] = evidence_from_database(db, backup_root, int(profile['stagingGuildId']), uid,
                                                        approved_image=state.get('approvedImage'))
        except (OSError, ValueError, sqlite3.Error):
            report['database'] = {'inspection': 'UNAVAILABLE'}
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(collect(), indent=2))
    except Exception as error:
        reason = str(error)
        known = {'OWNER_ROOT_SESSION_REQUIRED', 'STAGING_ACCOUNT_SCOPE_REJECTED',
                 'STAGING_BINDING_REJECTED', 'FILE_SCOPE_REJECTED'}
        code = reason if reason in known else 'DIAGNOSTIC_UNAVAILABLE'
        raise SystemExit(json.dumps({'diagnosticError': code, 'productionChanged': False})) from None
