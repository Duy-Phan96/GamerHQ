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
    fixed_file(path)
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


def receipt_for_backup(root, ident, evidence, uid):
    if not re.fullmatch('[0-9a-f]{32}', ident):
        raise ValueError('BACKUP_SCOPE_REJECTED')
    path = root / (ident + '.sqlite')
    saved = evidence.get('backup', {})
    verified = saved.get('verification', {})
    result = {'recorded': 'backup' in evidence, 'filePresent': path.exists(),
              'checksumMatches': None, 'integrityPass': None,
              'restoreProbeRecorded': verified.get('restoreProbe') == 'PASS'}
    if not result['filePresent']:
        return result
    try:
        info = fixed_file(path, uid=uid, private=True, limit=MAX_BACKUP_BYTES)
        if any(path.with_name(path.name + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('BACKUP_SCOPE_REJECTED')
        actual = digest(path)
        if 'sha256' in saved:
            result['checksumMatches'] = actual == saved['sha256']
        with closing(read_database(path, immutable=True)) as conn:
            result['integrityPass'] = integrity(conn)
        checked = fixed_file(path, uid=uid, private=True, limit=MAX_BACKUP_BYTES)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(checked) != identity(info):
            raise ValueError('BACKUP_CHANGED')
    except (OSError, ValueError, sqlite3.Error):
        result.update(checksumMatches=None, integrityPass=None, inspection='UNAVAILABLE')
    return result


def safe_commit(value):
    return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) else None


def evidence_from_database(path, backup_root, guild_id, uid):
    with closing(read_database(path)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        db_ok = integrity(conn)
        authority = conn.execute(
            "SELECT skill_id FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'",
            (guild_id,),
        ).fetchall()
        skill = conn.execute(
            "SELECT installed,configured,enabled,version FROM skill_guild_state WHERE guild_id=? AND skill_id='event-manager'",
            (guild_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT id,state,created_at,updated_at,previous_image,evidence_json FROM skill_deployments "
            "WHERE skill_id='event-manager' AND (updated_at>=? OR state IN ('APPLYING','RECOVERY_REQUIRED')) "
            "ORDER BY updated_at DESC LIMIT 21", (RUN_STARTED,),
        ).fetchall()
        preceding = conn.execute(
            "SELECT id,evidence_json FROM skill_deployments WHERE skill_id='event-manager' "
            "AND state IN ('APPLIED','ROLLED_BACK') AND updated_at<? "
            "AND json_type(evidence_json,'$.backup.sha256')='text' ORDER BY updated_at DESC LIMIT 1",
            (RUN_STARTED,),
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
    version = skill[3] if skill and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9.]+)?', str(skill[3])) else None
    return {'integrityPass': db_ok, 'eventManagerAuthority': authority == [('event-manager',)],
            'skillInstalled': bool(skill and skill[0]), 'skillConfigured': bool(skill and skill[1]),
            'skillEnabled': bool(skill and skill[2]), 'skillVersion': version,
            'attemptsSinceFailedRun': attempts, 'preexistingHostBlockers': blockers,
            'latestPreRunBackup': (receipt_for_backup(backup_root, preceding[0], json.loads(preceding[1]), uid)
                                   if preceding else None)}


def container_status(account, socket, profile, state):
    info = socket.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != account.pw_uid or socket.resolve(strict=True) != socket:
        raise ValueError('ROOTLESS_SOCKET_SCOPE_REJECTED')
    command = ['runuser', '-u', account.pw_name, '--', 'env', '-i',
               'PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=' + account.pw_dir,
               'DOCKER_HOST=unix://' + str(socket), 'DOCKER_CONFIG=' + str(CONTROL / 'docker-client'), 'docker']
    def read(*args):
        result = subprocess.run([*command, *args], capture_output=True, text=True, check=True, timeout=10)
        if len(result.stdout) > 8192:
            raise ValueError('DOCKER_RESPONSE_REJECTED')
        return result.stdout.strip()

    options = json.loads(read('info', '--format', '{{json .SecurityOptions}}'))
    if not isinstance(options, list) or 'name=rootless' not in options:
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
    return {'present': True, 'running': status == 'running', 'dockerHealthy': health == 'healthy',
            'hostCommit': safe_commit(commit), 'matchesApprovedImage': image == state.get('approvedImage'),
            'matchesProfileBaseImage': image == profile.get('baseImage')}


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
    fixed_file(lock, uid=uid, private=True)
    report = {'operation': 'diagnose-staging-host-promotion', 'workflowRun': RUN,
              'controllerCommit': CONTROLLER, 'targetHostCommit': TARGET,
              'productionChanged': False, 'databaseChanged': False, 'containersChanged': False,
              'discordResourcesChanged': False, 'credentialFilesOpened': False}
    with lock.open('rb') as stream:
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
            report['database'] = evidence_from_database(db, backup_root, int(profile['stagingGuildId']), uid)
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
