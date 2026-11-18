"""Owner-run read-only receipt for the exact enrolled two-guild cutover."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time


ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
CONTROLLER = 'a2d49804e94a1914b84711d1ffb6cb4e74dca2f3'
WORKER = '92db47042d58055cb0debdfd83f11f8508e19809'
WORKFLOW = 'f982aa0d7d2d76b4a9e8fe27e1a998535aa03fe61e27d9765eccf4348c7e9011'
ACTIVATION_RUN = '44283427505'
PREPARATION_RUN = '44282392511'
COHORT = {'applicationId': '1567098530525351976', 'guildIds': ['1567098041641738331', '1571908030532948129']}
HOSTS = {'baseline': '40c88fada10f7990ef3d2cddb81f30b6381fe73e',
         'candidate': '12e0053f3af479235f7281b6f67637e564b962fc',
         'fallback': 'ca6bc4ac5604987dcab9b5310b7af2b9c53cf652'}
IMAGES = {'baseline': 'sha256:1fac8871a93d79180eae0c11ace3735ace453714425516dbfe72df262a50caf8',
          'candidate': 'sha256:c9c6996a646b93412dc7931cf98b20256387b9d95aec36d359009aff82cf7320',
          'fallback': 'sha256:78e394acafa55e9bbf9810530b69beaa2ef9d1f3fccfa7c736669f85db0d0e02'}
PHASES = {'STOPPING', 'CHECKPOINTED', 'STARTING', 'VERIFYING', 'ROLLING_BACK',
          'PILOT_ACTIVE', 'PILOT_ROLLED_BACK', 'PILOT_STOP_CANCELLED'}
FIELDS = (
    '{"id":{{json .Id}},"image":{{json .Image}},'
    '"host":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"user":{{json .Config.User}},"running":{{json .State.Running}},'
    '"status":{{json .State.Status}},"exitCode":{{json .State.ExitCode}},'
    '"health":{{with .State.Health}}{{json .Status}}{{else}}"none"{{end}},'
    '"startedAt":{{json .State.StartedAt}},"finishedAt":{{json .State.FinishedAt}},'
    '"oomKilled":{{json .State.OOMKilled}},'
    '"healthProbeExitCodes":[{{with .State.Health}}{{range $i,$row := .Log}}{{if $i}},{{end}}{{json $row.ExitCode}}{{end}}{{end}}],'
    '"processLabel":{{json .ProcessLabel}},"networks":{{json .NetworkSettings.Networks}},'
    '"mounts":{{json .Mounts}}}'
)


def protected(path, uid, mode=None, *, limit=262144):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
                or (mode is None and info.st_mode & 0o022) or not 0 < info.st_size <= limit):
            raise ValueError('PROTECTED_FILE_REJECTED')
        return stream.read(limit + 1)


def verify_journal(state, record):
    proof = state['tenantPilotActivationPrepared']
    prepared = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expected = {key + 'Image': value for key, value in IMAGES.items()}
    expected['preparedSha256'] = prepared
    if (record.get('binding') != expected or record.get('activationRun') != ACTIVATION_RUN
            or record.get('phase') not in PHASES or proof.get('runId') != PREPARATION_RUN
            or proof.get('fallbackRollbackRehearsal') != 'PASS'
            or not re.fullmatch(r'[a-f0-9]{64}', record.get('baselineContainer', ''))):
        raise ValueError('JOURNAL_BINDING_REJECTED')
    failure = record.get('failure')
    return {'phase': record['phase'], 'failedAt': record.get('failedAt') if record.get('failedAt') in PHASES else None,
            'failure': failure if isinstance(failure, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{2,80}', failure) else None}


def inspect_database(path, *, secondary=False, sealed=False):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return {'state': 'UNSAFE_PATH'}
        mode = stat.S_IMODE(info.st_mode)
        result = {'uid': info.st_uid, 'gid': info.st_gid, 'mode': oct(mode)[2:].zfill(4)}
        if secondary and not sealed and mode != 0o600:
            return {**result, 'state': 'MODE_UNSAFE'}
        # immutable opens cannot create SQLite sidecars. Refuse when a sidecar
        # could contain live or uncheckpointed data.
        if any(os.path.lexists(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal')):
            return {**result, 'state': 'SIDECAR_PRESENT'}
        uri = path.as_uri() + '?mode=ro&immutable=1'
        with closing(sqlite3.connect(uri, uri=True)) as database:
            check = database.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            if not check:
                return {**result, 'state': 'INTEGRITY_FAILED'}
            if secondary:
                identity = database.execute('SELECT guild_id, ready FROM gamerhq_tenant_identity').fetchall()
                if identity != [(int(COHORT['guildIds'][1]), 1)]:
                    return {**result, 'state': 'GUILD_IDENTITY_REJECTED'}
            else:
                authority = database.execute(
                    "SELECT skill_id FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'",
                    (COHORT['guildIds'][0],)).fetchone()
                result['eventManagerAuthority'] = authority == ('event-manager',)
                lifecycle = database.execute(
                    "SELECT installed,configured,enabled,version FROM skill_guild_state WHERE guild_id=? AND skill_id='event-manager'",
                    (COHORT['guildIds'][0],)).fetchone()
                if lifecycle:
                    result['eventManagerLifecycle'] = {
                        'installed': lifecycle[0] == 1, 'configured': lifecycle[1] == 1,
                        'enabled': lifecycle[2] == 1,
                        'storedVersion': lifecycle[3] if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', str(lifecycle[3])) else None}
        return {**result, 'state': 'INTEGRITY_PASS'}
    except FileNotFoundError:
        return {'state': 'ABSENT'}
    except (OSError, sqlite3.Error):
        return {'state': 'READ_REJECTED'}


def inspect_backup(backup_root, uid, evidence):
    if not evidence:
        return {'state': 'NOT_RECORDED'}
    try:
        batch = evidence['backupBatch']
        if not re.fullmatch(r'tenant-pilot-[a-z0-9_]{8}', batch):
            raise ValueError
        folder = backup_root / batch
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError
        manifest = protected(folder / 'verified.json', uid, 0o600, limit=16384)
        if hashlib.sha256(manifest).hexdigest() != evidence['backupReceiptSha256']:
            raise ValueError
        receipt = json.loads(manifest)
        rows = receipt['tenants']
        binding = receipt.get('binding', {})
        if (binding.get('baselineHostCommit') != HOSTS['baseline']
                or binding.get('baselineImage') != IMAGES['baseline']
                or binding.get('startupOverrideSha256') != evidence.get('startupOverrideSha256')
                or bool(binding.get('quiesced')) != bool(evidence.get('quiesced'))
                or len(rows) != 2 or {str(row['guildId']) for row in rows} != set(COHORT['guildIds'])
                or receipt.get('rollbackRehearsal') != 'PRIMARY_ONLY_PASS'):
            raise ValueError
        results = {}
        for row in rows:
            guild = str(row['guildId'])
            secondary = guild == COHORT['guildIds'][1]
            path = folder / (guild + '.sqlite')
            data = protected(path, uid, 0o600, limit=(16 if secondary else 256) * 1024**2)
            integrity = inspect_database(path, secondary=secondary, sealed=True)['state']
            results[guild] = {'checksumPass': hashlib.sha256(data).hexdigest() == row['backupSha256'],
                              'integrity': integrity, 'recordedRestorePass': row.get('restore') == 'PASS'}
        return {'state': 'VERIFIED' if all(r['checksumPass'] and r['integrity'] == 'INTEGRITY_PASS'
                                          and r['recordedRestorePass'] for r in results.values()) else 'REJECTED',
                'batch': batch, 'tenants': results}
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'REJECTED'}


def summarize_startup(log):
    """Only publish known exception types, reviewed source locations and fixed reason codes."""
    known = {
        'GuildAdmissionError', 'GuildCorePermissionsError', 'PermissionError', 'OperationalError',
        'IntegrityError', 'Forbidden', 'HTTPException', 'LoginFailure', 'PrivilegedIntentsRequired',
        'ExtensionFailed', 'RuntimeError', 'ValueError', 'AttributeError', 'TypeError', 'ModuleNotFoundError',
        'ConnectionClosed', 'ClientConnectorError', 'ConfigurationError', 'IndexError', 'KeyError', 'TimeoutError',
    }
    reasons = {
        'Guild runtime is still starting.': 'ADMISSION_STILL_STARTING',
        'Guild operation has expired.': 'TENANT_SCOPE_EXPIRED',
        'Guild has no verified admission.': 'GUILD_ADMISSION_MISSING',
        'Guild membership could not be verified.': 'GUILD_MEMBERSHIP_UNVERIFIED',
        'Discord memberships differ from the approved guilds.': 'COHORT_REJECTED',
        'Discord requests require a verified guild scope.': 'DISCORD_SCOPE_REQUIRED',
        'Discord request has no verifiable guild resource.': 'DISCORD_RESOURCE_SCOPE_REQUIRED',
        'Pilot guild operations require a verified tenant scope.': 'RUNTIME_SCOPE_REQUIRED',
        'Pilot startup requires the current database schema.': 'DATABASE_SCHEMA_REJECTED',
        'Pilot startup requires an intact database.': 'DATABASE_INTEGRITY_REJECTED',
        'unable to open database file': 'SQLITE_OPEN_REJECTED',
        'attempt to write a readonly database': 'SQLITE_WRITE_REJECTED',
        'Guild pilot startup failed.': 'PILOT_STARTUP_FAILED',
        'Guild pilot paused pending exact membership and storage verification.': 'PILOT_ADMISSION_PAUSED',
        'Session ID:': 'GATEWAY_SESSION_OBSERVED',
        'Internal Web API listening on': 'WEB_LISTENER_OBSERVED',
    }
    frames, exceptions, signals = [], [], []
    for line in log.splitlines():
        match = re.search(r'File "/app/((?:[a-zA-Z0-9_]+/)*[a-zA-Z0-9_]+\.py)", line (\d{1,5}), in ([a-zA-Z0-9_<>]+)', line)
        if match:
            frames.append({'file': match[1], 'line': int(match[2]), 'function': match[3]})
        match = re.search(r'(?:^|\s)(?:[a-zA-Z0-9_.]+\.)?([A-Z][a-zA-Z0-9_]+):', line)
        if match and match[1] in known:
            exceptions.append(match[1])
        signals.extend(code for phrase, code in reasons.items() if phrase in line and code not in signals)
    return {'state': 'COLLECTED', 'frames': frames[-30:], 'exceptions': exceptions[-12:], 'signals': signals}


def collect():
    import fcntl
    import pwd

    if os.geteuid() != 0:
        raise ValueError('OWNER_SESSION_REQUIRED')
    worker = ROOT / 'worker'
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid = account.pw_uid
    info = worker.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise ValueError('WORKER_PATH_REJECTED')
    with os.fdopen(os.open(worker / 'operation.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as lock:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('OPERATION_LOCK_REJECTED')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        policy = json.loads(protected(CONTROL / 'automation-policy.json', 0))
        state = json.loads(protected(worker / 'automation-state.json', uid, 0o600))
        profile = json.loads(protected(CONTROL / 'settings/profile.json', uid, 0o600))
        if (policy.get('controllerCommit') != CONTROLLER or policy.get('legacyWorkerCommit') != WORKER
                or policy.get('tenantPilot') != COHORT or policy.get('tenantPilotCutover', {}).get('workflowSha256') != WORKFLOW
                or profile.get('environment') != 'staging' or profile.get('container') != 'gamerhq-staging-bot'
                or profile.get('stagingBotId') != COHORT['applicationId']
                or profile.get('stagingGuildId') != COHORT['guildIds'][0]
                or state.get('approvedImage') != IMAGES['baseline']
                or profile.get('database') != str(ROOT / 'data/gamerhq.db')
                or profile.get('backupRoot') != str(ROOT / 'backups')
                or any(path.resolve(strict=True) != path for path in (ROOT / 'data', ROOT / 'backups'))):
            raise ValueError('STAGING_IDENTITY_REJECTED')
        recorded = json.loads(protected(worker / 'tenant-pilot-cutover.json', uid, 0o600))
        journal = verify_journal(state, recorded)
        env = ['PATH=/usr/local/bin:/usr/bin:/bin', f'HOME={account.pw_dir}',
               f'XDG_RUNTIME_DIR=/run/user/{uid}', f'DOCKER_HOST=unix:///run/user/{uid}/docker.sock',
               'DOCKER_CONFIG=/etc/gamerhq-staging/docker-client']

        def docker(*args, logs=False):
            result = subprocess.run(['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i',
                                     *env, 'docker', *args], capture_output=True, text=True, errors='replace',
                                    timeout=35, check=True)
            content = result.stdout + ('\n' + result.stderr if logs else '')
            if len(content) > (262144 if logs else 32768):
                raise ValueError('OUTPUT_LIMIT')
            return content.strip()

        ids = docker('ps', '-aq', '--filter', 'name=^/gamerhq-staging-bot$').splitlines()
        container = {'state': 'ABSENT'}
        quiesced = not ids
        if ids:
            observed = json.loads(docker('inspect', '--format', FIELDS, 'gamerhq-staging-bot'))
            kind = next((key for key in IMAGES if IMAGES[key] == observed['image']), None)
            if (kind is None or observed['host'] != HOSTS[kind] or observed['project'] != 'gamerhq-staging'
                    or observed['service'] != 'gamerhq-staging' or observed['user'] != '10001:10001'):
                raise ValueError('CONTAINER_IDENTITY_REJECTED')
            mounts = {(m['Source'], m['Destination']) for m in observed['mounts'] if m['Type'] != 'tmpfs'}
            if mounts != {(str(ROOT / 'data'), '/app/runtime/data'), (str(ROOT / 'backups'), '/app/backups')}:
                raise ValueError('CONTAINER_MOUNTS_REJECTED')
            prefix = 'GAMERHQ_STAGING_TENANT_PILOT='
            template = '{{range .Config.Env}}{{if eq (printf "%.' + str(len(prefix)) + 's" .) "' + prefix + '"}}{{println .}}{{end}}{{end}}'
            binding = docker('inspect', '--format', template, 'gamerhq-staging-bot')
            mode = 'LEGACY_PRIMARY' if not binding else 'REJECTED'
            if binding.startswith(prefix):
                value = json.loads(binding[len(prefix):])
                if ((value.get('applicationId'), value.get('guildIds')) ==
                        (COHORT['applicationId'], COHORT['guildIds'])):
                    mode = 'PRIMARY_ONLY' if value.get('primaryOnly') is True else 'TWO_GUILD'
            level = (observed['processLabel'] or '').split(':', 3)[-1]
            container = {'state': observed['status'], 'kind': kind, 'hostCommit': observed['host'],
                         'image': observed['image'], 'running': observed['running'], 'dockerHealth': observed['health'],
                         'exitCode': observed['exitCode'], 'matchesJournalBaseline': observed['id'] == recorded['baselineContainer'],
                         'configuredAdmission': mode, 'attachedNetworkPresent': 'gamerhq-staging-private' in observed['networks'],
                         'selinuxLevelAccepted': not level or bool(re.fullmatch(r's0:c[0-9]+,c[0-9]+', level)),
                         **{key: observed[key] for key in ('startedAt', 'finishedAt', 'oomKilled', 'healthProbeExitCodes')}}
            try:
                startup = summarize_startup(docker('logs', '--tail', '160', observed['id'], logs=True))
            except (subprocess.SubprocessError, ValueError):
                startup = {'state': 'UNAVAILABLE'}
            try:
                raw = docker('events', '--since', observed['startedAt'], '--until', str(int(time.time())),
                             '--filter', 'container=' + observed['id'], '--format', '{{json .}}')
                events = []
                for line in raw.splitlines():
                    event = json.loads(line)
                    action = event.get('Action')
                    if action in ('start', 'kill', 'stop', 'die', 'oom', 'health_status: unhealthy', 'health_status: healthy'):
                        attrs = event.get('Actor', {}).get('Attributes', {})
                        events.append({'action': action, 'time': event.get('time'),
                                       **{key: attrs[key] for key in ('signal', 'exitCode')
                                          if re.fullmatch(r'[0-9]{1,3}', str(attrs.get(key, '')))}})
                startup['events'] = events[-20:]
            except (subprocess.SubprocessError, ValueError, TypeError):
                startup['eventHistory'] = 'UNAVAILABLE'
            container['startupEvidence'] = startup
            if observed['running']:
                try:
                    version = docker('exec', '--user', '10001:10001', observed['id'], 'python', '-I', '-B', '-c',
                                     "from importlib.metadata import version; print(version('gamerhq-skill-event-manager'))")
                except subprocess.SubprocessError:
                    version = ''
                container['eventManagerPackage'] = version if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) else None
            quiesced = observed['status'] == 'exited' and observed['running'] is False
        primary = Path(profile['database'])
        secondary = primary.parent / 'tenants' / (COHORT['guildIds'][1] + '.sqlite')
        data = ({'primary': inspect_database(primary), 'secondary': inspect_database(secondary, secondary=True)}
                if quiesced and not secondary.parent.is_symlink() else {'state': 'LIVE_OR_UNSAFE_PATH'})
        return {'state': 'CUTOVER_DIAGNOSTICS', 'activationRun': ACTIVATION_RUN, 'controllerCommit': CONTROLLER,
                'operationLockAcquired': True, 'recoveryRequired': state.get('recoveryRequired'),
                'approvedCohort': COHORT, 'journal': journal, 'container': container, 'database': data,
                'preparationBackup': inspect_backup(ROOT / 'backups', uid, state['tenantPilotActivationPrepared']['preview']),
                'cutoverBackup': inspect_backup(ROOT / 'backups', uid, recorded.get('checkpoint')),
                'productionChanged': False, 'databaseChanged': False, 'containersChanged': False}


if __name__ == '__main__':
    try:
        print(json.dumps(collect(), sort_keys=True))
    except BlockingIOError:
        raise SystemExit('{"diagnosticError":"OPERATION_LOCK_BUSY","productionChanged":false}') from None
    except subprocess.SubprocessError:
        raise SystemExit('{"diagnosticError":"ROOTLESS_INSPECTION_REJECTED","productionChanged":false}') from None
    except (OSError, ValueError, TypeError, KeyError) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch(r'[A-Z_]{8,80}', str(error)) else 'DIAGNOSTIC_REJECTED'
        raise SystemExit(json.dumps({'diagnosticError': code, 'productionChanged': False})) from None
