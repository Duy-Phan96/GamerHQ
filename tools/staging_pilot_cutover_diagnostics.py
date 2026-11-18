"""Owner-run read-only receipt for the exact enrolled two-guild cutover."""
from contextlib import closing
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
import tempfile
import time


ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
CONTROLLERS = {'a2d49804e94a1914b84711d1ffb6cb4e74dca2f3', 'af276099e34d0c7fcbc94397c8fc39f9885b147d'}
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
API_DIAGNOSTIC_APPROVAL = 'RUN ISOLATED STAGING HOST API DIAGNOSTIC'
CONFIRMATION_DIAGNOSTIC_APPROVAL = 'REHEARSE STAGING WATCHDOG IN THE CONTROLLER SANDBOX'
CONFIRMATION_CONTROLLER = 'af276099e34d0c7fcbc94397c8fc39f9885b147d'
CONFIRMATION_SOURCE = {
    'tools/staging_automation/backend.py': 'e78f0f815e30768466a842ae2f512694e76c39d216ed7ed1023f1cba98ed2658',
    'tools/staging_health.py': '6b2f9f943a92f8c5cd89565cd3842027051773e381cc12a23d97acb52f06176f',
    'tools/skill_staging.py': '93955b6914540162199c5de847573b96d5eaa503407ded4c4a7829004df5f280',
    'tools/staging_dispatch.py': 'b0b0d7d371e2de0e30f296a4080535173e6b835237ab1211cce3fe5fb3216c0c',
    'hosts/gamerhq/staging_rootless.py': 'd1ddccdb70698f6836c741aff483b1b7d4a1560ac098492ae19b1890d7f9a6d2',
    'tools/staging_configuration.py': '7dcfd68412420c70b16d0fedcd87f9895ffac8d74a710f8ac77fe1e35b5ab671',
}
API_PATH = '/api/v1/servers/' + COHORT['guildIds'][0] + '/skills/event-manager'
MAX_PROBE_DB = 32 * 1024**2
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
    '"portBindings":{{json .HostConfig.PortBindings}},'
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
            'failure': failure if isinstance(failure, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{2,80}', failure) else None,
            **{key: record[key] for key in ('failedCheck', 'rollbackFailedCheck')
               if record.get(key) in {'POST_START_ATTESTATION', 'HOST_API_HEALTH', 'CHECKPOINT_INTEGRITY',
                                      'PRIMARY_DATA_RETENTION', 'EVENT_MANAGER_LIFECYCLE', 'SECONDARY_DATA_INTEGRITY'}}}


def health_audit(uid):
    """Use only the existing allowlisted health/journal events from the failed attempt."""
    try:
        result = subprocess.run(['/usr/bin/journalctl', '--no-pager', '--output=json',
                                 '--since=2026-11-18 12:02:00 UTC', '--until=2026-11-18 12:04:00 UTC',
                                 'SYSLOG_IDENTIFIER=gamerhq-staging', f'_UID={uid}'],
                                capture_output=True, text=True, errors='replace', timeout=20, check=True)
        if len(result.stdout) > 262144:
            return {'state': 'OUTPUT_LIMIT'}
        events = []
        for line in result.stdout.splitlines():
            row = json.loads(line)
            message = json.loads(row['MESSAGE'])
            if (message.get('operation') in ('health', 'automation-tenant-pilot-cutover')
                    and message.get('state') in ('HOST_READY', 'READY_OFFLINE', 'STARTED', 'FAILED', 'PASS')):
                events.append({'operation': message['operation'], 'state': message['state'],
                               'utcEpoch': int(row['__REALTIME_TIMESTAMP']) // 1000000})
        return {'state': 'COLLECTED', 'events': events[-20:]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        return {'state': 'UNAVAILABLE'}


def health_credential_matches(profile, uid, container_setting):
    """Compare only the API credential across the protected configuration and stopped container."""
    key = 'GAMERHQ_STAGING_WEB_API_SECRET'
    expected_bot_file = CONTROL / 'settings/bot.env'
    if (profile.get('healthSecretEnv') != key or profile.get('botEnvFile') != str(expected_bot_file)
            or (CONTROL / 'settings').resolve(strict=True) != CONTROL / 'settings'):
        return {'state': 'PROTECTED_BINDING_REJECTED'}

    def read_selected(path, name):
        key_prefix = (name + '=').encode()
        matches = [line[len(key_prefix):].strip().strip(bytes((34, 39))) for line in protected(path, uid, 0o600).splitlines()
                   if line.startswith(key_prefix)]
        return matches[0] if len(matches) == 1 and len(matches[0]) >= 32 else b''

    try:
        worker = read_selected(CONTROL / 'settings/worker.env', key)
        bot = read_selected(expected_bot_file, 'GAMERHQ_WEB_API_SECRET')
        lines = container_setting.splitlines()
        prefix = 'GAMERHQ_WEB_API_SECRET='
        value = lines[0][len(prefix):].encode() if len(lines) == 1 and lines[0].startswith(prefix) else b''
        return {'state': 'COLLECTED', 'workerCredentialPresent': bool(worker), 'botFileCredentialPresent': bool(bot),
                'containerCredentialPresent': len(value) >= 32,
                'workerMatchesBotFile': bool(worker and bot and hmac.compare_digest(worker, bot)),
                'workerMatchesContainer': bool(worker and value and hmac.compare_digest(worker, value))}
    except (OSError, ValueError):
        return {'state': 'CREDENTIAL_MATCH_UNAVAILABLE'}


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
        'Internal Web API listening on 0.0.0.0:8080': 'WEB_EXPECTED_CONTAINER_BIND',
        'Internal Web API listening on 127.0.0.1:8080': 'WEB_LOOPBACK_ONLY_CONTAINER_BIND',
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


def collect(*, api_probe=False, confirmation_probe=False):
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
        if (policy.get('controllerCommit') not in CONTROLLERS or policy.get('legacyWorkerCommit') != WORKER
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
               'DOCKER_CONFIG=/etc/gamerhq-staging/docker-client',
               f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus']

        def as_worker(args, **kwargs):
            return subprocess.run(['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i',
                                   *env, *args], capture_output=True, text=True, errors='replace',
                                  timeout=35, check=True, **kwargs)

        def docker(*args, logs=False):
            result = as_worker(['docker', *args])
            content = result.stdout + ('\n' + result.stderr if logs else '')
            if len(content) > (262144 if logs else 32768):
                raise ValueError('OUTPUT_LIMIT')
            return content.strip()

        if api_probe:
            if (state.get('recoveryRequired') is not True or journal['phase'] != 'ROLLING_BACK'
                    or journal.get('rollbackFailedCheck') != 'HOST_API_HEALTH'
                    or os.path.lexists(worker / 'access-revoked')
                    or profile.get('healthUrl') != 'http://127.0.0.1:18080'):
                raise ValueError('HELD_API_RECOVERY_REQUIRED')
            if confirmation_probe:
                verify_confirmation_source(policy['controllerCommit'])
            return run_api_probe(docker, as_worker, profile, policy['controllerCommit'], recorded, uid,
                                 confirmation=confirmation_probe)

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
            secret_prefix = 'GAMERHQ_WEB_API_SECRET='
            secret_selector = ('{{range .Config.Env}}{{if eq (printf "%.' + str(len(secret_prefix)) +
                               's" .) "' + secret_prefix + '"}}{{println .}}{{end}}{{end}}')
            credentials = health_credential_matches(profile, uid, docker('inspect', '--format', secret_selector,
                                                                       'gamerhq-staging-bot'))
            api_env = set(docker('inspect', '--format',
                                 '{{range .Config.Env}}{{if or (eq . "GAMERHQ_WEB_API_ENABLED=true") '
                                 '(eq . "GAMERHQ_WEB_API_HOST=0.0.0.0") (eq . "GAMERHQ_WEB_API_PORT=8080")}}'
                                 '{{println .}}{{end}}{{end}}', 'gamerhq-staging-bot').splitlines())
            mode = 'LEGACY_PRIMARY' if not binding else 'REJECTED'
            if binding.startswith(prefix):
                value = json.loads(binding[len(prefix):])
                if ((value.get('applicationId'), value.get('guildIds')) ==
                        (COHORT['applicationId'], COHORT['guildIds'])):
                    mode = 'PRIMARY_ONLY' if value.get('primaryOnly') is True else 'TWO_GUILD'
            level = (observed['processLabel'] or '').split(':', 3)[-1]
            expected_port = {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]}
            container = {'state': observed['status'], 'kind': kind, 'hostCommit': observed['host'],
                         'image': observed['image'], 'running': observed['running'], 'dockerHealth': observed['health'],
                         'exitCode': observed['exitCode'], 'matchesJournalBaseline': observed['id'] == recorded['baselineContainer'],
                         'configuredAdmission': mode, 'attachedNetworkPresent': 'gamerhq-staging-private' in observed['networks'],
                         'healthPortBindingExpected': observed['portBindings'] == expected_port,
                         'healthPortPublishedPorts': len(observed['portBindings'] or {}),
                         'healthCredentialBinding': credentials,
                         'healthListenerEnvironmentExpected': api_env == {
                             'GAMERHQ_WEB_API_ENABLED=true', 'GAMERHQ_WEB_API_HOST=0.0.0.0', 'GAMERHQ_WEB_API_PORT=8080'},
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
        return {'state': 'CUTOVER_DIAGNOSTICS', 'activationRun': ACTIVATION_RUN, 'controllerCommit': policy['controllerCommit'],
                'operationLockAcquired': True, 'recoveryRequired': state.get('recoveryRequired'),
                'approvedCohort': COHORT, 'journal': journal, 'container': container, 'database': data,
                'protectedHealthUrlExpected': profile.get('healthUrl') == 'http://127.0.0.1:18080',
                'lastRecoveryAudit': health_audit(uid),
                'preparationBackup': inspect_backup(ROOT / 'backups', uid, state['tenantPilotActivationPrepared']['preview']),
                'cutoverBackup': inspect_backup(ROOT / 'backups', uid, recorded.get('checkpoint')),
                'productionChanged': False, 'databaseChanged': False, 'containersChanged': False}


API_CLIENT = """
import json, os, socket, sys
from urllib.error import HTTPError, URLError
sys.path.insert(0, sys.argv[1])
from tools.skill_staging import host_request
packet=json.load(sys.stdin)
profile={'healthUrl':sys.argv[2], 'healthSecretEnv':'GAMERHQ_DIAGNOSTIC_SECRET',
         'stagingGuildId':packet['guild']}
os.environ['GAMERHQ_DIAGNOSTIC_SECRET']=packet['secret']
try:
    value=host_request(profile,packet['path'])
except HTTPError as error:
    result={'state':'HTTP_REJECTED','httpStatus':error.code}
except (URLError,OSError) as error:
    cause=error.reason if isinstance(error,URLError) else error
    result={'state': 'CONNECTION_REFUSED' if isinstance(cause,ConnectionRefusedError)
            else 'TIMEOUT' if isinstance(cause,(TimeoutError,socket.timeout)) else 'TRANSPORT_REJECTED'}
except (ValueError,TypeError,KeyError):
    result={'state':'RESPONSE_REJECTED'}
else:
    lifecycle=value.get('state') if isinstance(value,dict) else None
    if not isinstance(lifecycle,dict):
        result={'state':'RESPONSE_REJECTED'}
    else:
        result={'state':'RESPONSE_COLLECTED','httpStatus':200,
                'versionMatches':value.get('version')=='0.1.2',
                'capabilitiesPass':value.get('missingCapabilities')==[],
                'installed':lifecycle.get('installed') is True,
                'configured':lifecycle.get('configured') is True,
                'enabled':lifecycle.get('enabled') is True,
                'healthy':lifecycle.get('healthy') is True}
print(json.dumps(result))
"""

CONFIRMATION_CLIENT = """
import json, os, re, socket, subprocess, sys, time
from urllib.error import HTTPError, URLError
sys.path.insert(0,sys.argv[1])
from hosts.gamerhq import staging_rootless
from tools import skill_staging, staging_configuration, staging_health
from tools.staging_automation.backend import HostBackend
from tools.staging_automation.policy import Denied
from tools.staging_dispatch import PROFILE, environment
profile=json.loads(PROFILE.read_bytes())
packet=json.load(sys.stdin)
name=packet['container']
assert re.fullmatch('gamerhq-staging-api-probe-[0-9a-f]{16}', name)
assert profile['healthUrl']=='http://127.0.0.1:18080' and profile['workerEnabled'] is True
values=environment()
os.environ.clear()
os.environ.update(values)
os.environ[profile['healthSecretEnv']]=packet['secret']
native_run=subprocess.run
native_verify=staging_rootless.verify_rootless
native_request=skill_staging.host_request
native_opener=skill_staging.build_opener
native_audit=staging_configuration.audit
backend=HostBackend(profile,{})
native_inspect=backend.inspect
evidence={}
def verify(*args,**kwargs):
    evidence['stage']='ROOTLESS_DAEMON'
    return native_verify(*args,**kwargs)
def run(args,*rest,**kwargs):
    if args==['docker','inspect','--format','{{.State.Health.Status}}','gamerhq-staging-bot']:
        evidence['stage']='DOCKER_HEARTBEAT'
        answer=native_run([*args[:-1],name],*rest,**kwargs)
        health=answer.stdout.strip()
        evidence['dockerHealth']=health if health in ('healthy','starting','unhealthy') else 'UNAVAILABLE'
        return answer
    return native_run(args,*rest,**kwargs)
def request(*args,**kwargs):
    evidence['stage']='HOST_API_TRANSPORT'
    try:
        value=native_request(*args,**kwargs)
    except HTTPError as error:
        evidence.update(reason='HEALTH_HTTP_REJECTED', httpStatus=error.code)
        raise
    except (URLError,OSError) as error:
        cause=error.reason if isinstance(error,URLError) else error
        evidence['reason']=('HEALTH_CONNECTION_REFUSED' if isinstance(cause,ConnectionRefusedError)
                           else 'HEALTH_TIMEOUT' if isinstance(cause,(TimeoutError,socket.timeout))
                           else 'HEALTH_TRANSPORT_REJECTED')
        raise
    except (ValueError,TypeError,KeyError):
        evidence['reason']='HEALTH_RESPONSE_REJECTED'
        raise
    evidence['stage']='SKILL_HEALTH_VALIDATION'
    if isinstance(value,dict):
        state=value.get('state')
        evidence['versionMatches']=value.get('version')=='0.1.2'
        evidence['capabilitiesPass']=value.get('missingCapabilities')==[]
        if isinstance(state,dict):
            evidence['enabled']=state.get('enabled') is True
            evidence['healthy']=state.get('healthy') is True
    return value
def build_opener(*args,**kwargs):
    opener=native_opener(*args,**kwargs)
    native_open=opener.open
    def open_response(*args,**kwargs):
        response=native_open(*args,**kwargs)
        evidence['httpStatus']=response.status
        return response
    opener.open=open_response
    return opener
def audit(operation,state,**kwargs):
    evidence['stage']='HEALTH_AUDIT'
    return native_audit('tenant-pilot-api-rehearsal',state,**kwargs)
def inspect():
    evidence['stage']='CONTAINER_INSPECTION'
    # Verify the held fallback's actual deployment contract. Only its stopped
    # runtime state is replaced with the disposable server's observed state.
    held=native_inspect()
    fixture=json.loads(native_run(['docker','inspect','--format','{{json .State}}',name],
                                 capture_output=True,text=True,timeout=30,check=True).stdout)
    return {**held,'State':fixture}
backend.inspect=inspect
staging_rootless.verify_rootless=verify
staging_health.subprocess.run=run
skill_staging.host_request=request
skill_staging.build_opener=build_opener
staging_configuration.audit=audit
samples=[]
for idx in range(2):
    evidence={}
    started=time.monotonic()
    try:
        outcome=backend.health()
        evidence['reason']='HOST_READY' if outcome.get('state')=='HOST_READY' else 'HOST_NOT_READY'
    except Denied as error:
        evidence.setdefault('reason', error.code if error.code in ('HOST_NOT_HEALTHY',
            'CONTAINER_IDENTITY_REJECTED','CONTAINER_PRIVILEGES_REJECTED','RESOURCE_LIMIT_REQUIRES_OWNER',
            'CONTAINER_NETWORK_REJECTED','CONTAINER_MOUNTS_REJECTED') else 'HEALTH_BACKEND_REJECTED')
    except Exception:
        evidence.setdefault('reason', {'ROOTLESS_DAEMON':'HEALTH_DAEMON_REJECTED',
                                      'CONTAINER_INSPECTION':'HEALTH_INSPECTION_REJECTED',
                                      'DOCKER_HEARTBEAT':'HEALTH_GATEWAY_HEARTBEAT_REJECTED',
                                      'SKILL_HEALTH_VALIDATION':'HEALTH_SKILL_REJECTED',
                                      'HEALTH_AUDIT':'HEALTH_AUDIT_REJECTED'}.get(
                                          evidence.get('stage'),'HEALTH_OTHER_REJECTED'))
    evidence['elapsedMs']=int((time.monotonic()-started)*1000)
    samples.append(evidence)
    if idx==0:
        time.sleep(1)
print(json.dumps({'samples':samples}))
"""

API_SERVER = """
import asyncio, json, os, socket, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
sys.path.insert(0,'/app')

def no_external_connect(event,args):
    if event=='socket.connect':
        sock,address=args
        if sock.family not in (socket.AF_INET, socket.AF_INET6) or address[0] not in ('127.0.0.1','::1'):
            raise PermissionError('Probe may connect only to its own loopback address.')
sys.addaudithook(no_external_connect)
os.environ.clear()
os.environ['PYTHON_DOTENV_DISABLED']='1'
packet=json.loads(sys.stdin.buffer.readline())
assert os.geteuid()==os.getegid()==10001 and not packet.get('discordToken')
from hosts.gamerhq.skill_packages import load_external_skill_packages
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from hosts.gamerhq.web_api import GamerHQWebApiServer
from services.tenant_routing import pilot_routes
from tools.skill_staging import host_request
from database import db
primary=int(packet['guild'])
pin=packet['binding']
with tempfile.TemporaryDirectory(dir='/tmp') as root:
    database=Path(root)/'gamerhq.db'
    data=sys.stdin.buffer.read(32*1024**2+1)
    assert 0 < len(data) <= 32*1024**2
    database.write_bytes(data)
    db.DB_PATH=database
    guild=SimpleNamespace(id=primary, me=SimpleNamespace(id=int(pin['applicationId'])))
    get_guild=lambda ident: guild if ident==primary else None
    routes=pilot_routes(json.dumps(pin),primary=primary,database=database,get_guild=get_guild)
    routes.verify({'id':pin['applicationId'],'bot':True},[{'id':g} for g in pin['guildIds']])
    routes.accepting=True
    bot=SimpleNamespace(guild_routes=routes,get_guild=get_guild)
    runtime=GamerHQSkillRuntime(bot)
    assert load_external_skill_packages(runtime,('event-manager',)).loaded==('event-manager',)
    async def probe():
        server=GamerHQWebApiServer(bot=bot,skill_runtime=runtime,shared_secret=packet['secret'],host='0.0.0.0')
        await server.start()
        try:
            os.environ['GAMERHQ_DIAGNOSTIC_SECRET']=packet['secret']
            profile={'healthUrl':'http://127.0.0.1:8080','healthSecretEnv':'GAMERHQ_DIAGNOSTIC_SECRET','stagingGuildId':packet['guild']}
            try:
                value=await asyncio.to_thread(host_request,profile,packet['path'])
            except HTTPError as error:
                result={'state':'HTTP_REJECTED','httpStatus':error.code}
            except Exception:
                result={'state':'LOCAL_REQUEST_FAILED'}
            else:
                state=value.get('state',{}) if isinstance(value,dict) else {}
                result={'state':'RESPONSE_COLLECTED','httpStatus':200,
                        'versionMatches':value.get('version')=='0.1.2',
                        'capabilitiesPass':value.get('missingCapabilities')==[],
                        'installed':state.get('installed') is True,
                        'configured':state.get('configured') is True,
                        'enabled':state.get('enabled') is True,
                        'healthy':state.get('healthy') is True}
            print('GAMERHQ_API_PROBE '+json.dumps(result),flush=True)
            await asyncio.sleep(55)
        finally:
            await server.close()
    asyncio.run(probe())
"""


def api_receipt(raw):
    value = json.loads(raw)
    states = {'HTTP_REJECTED', 'CONNECTION_REFUSED', 'TIMEOUT', 'TRANSPORT_REJECTED',
              'RESPONSE_REJECTED', 'RESPONSE_COLLECTED', 'LOCAL_REQUEST_FAILED'}
    if not isinstance(value, dict) or value.get('state') not in states:
        raise ValueError('API_PROBE_RECEIPT_REJECTED')
    result = {'state': value['state']}
    status = value.get('httpStatus')
    if type(status) is int and 100 <= status <= 599:
        result['httpStatus'] = status
    for key in ('versionMatches', 'capabilitiesPass', 'installed', 'configured', 'enabled', 'healthy'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    return result


def confirmation_receipt(raw):
    value = json.loads(raw)
    samples = value.get('samples') if isinstance(value, dict) else None
    if not isinstance(samples, list) or len(samples) != 2:
        raise ValueError('CONFIRMATION_RECEIPT_REJECTED')
    public = []
    for row in samples:
        if not isinstance(row, dict) or row.get('reason') not in (
                'HOST_READY', 'HOST_NOT_READY', 'HEALTH_HTTP_REJECTED', 'HEALTH_CONNECTION_REFUSED',
                'HEALTH_TIMEOUT', 'HEALTH_TRANSPORT_REJECTED', 'HEALTH_RESPONSE_REJECTED',
                'HEALTH_DAEMON_REJECTED', 'HEALTH_GATEWAY_HEARTBEAT_REJECTED',
                'HEALTH_SKILL_REJECTED', 'HEALTH_AUDIT_REJECTED', 'HEALTH_OTHER_REJECTED',
                'HEALTH_INSPECTION_REJECTED', 'HEALTH_BACKEND_REJECTED', 'HOST_NOT_HEALTHY',
                'CONTAINER_IDENTITY_REJECTED', 'CONTAINER_PRIVILEGES_REJECTED', 'RESOURCE_LIMIT_REQUIRES_OWNER',
                'CONTAINER_NETWORK_REJECTED', 'CONTAINER_MOUNTS_REJECTED'):
            raise ValueError('CONFIRMATION_RECEIPT_REJECTED')
        result = {'reason': row['reason']}
        if row.get('stage') in ('ROOTLESS_DAEMON', 'CONTAINER_INSPECTION', 'DOCKER_HEARTBEAT', 'HOST_API_TRANSPORT',
                                'SKILL_HEALTH_VALIDATION', 'HEALTH_AUDIT'):
            result['stage'] = row['stage']
        if row.get('dockerHealth') in ('healthy', 'starting', 'unhealthy', 'UNAVAILABLE'):
            result['dockerHealth'] = row['dockerHealth']
        status = row.get('httpStatus')
        if type(status) is int and 100 <= status <= 599:
            result['httpStatus'] = status
        elapsed = row.get('elapsedMs')
        if type(elapsed) is int and 0 <= elapsed <= 35000:
            result['elapsedMs'] = elapsed
        for key in ('versionMatches', 'capabilitiesPass', 'enabled', 'healthy'):
            if type(row.get(key)) is bool:
                result[key] = row[key]
        public.append(result)
    return {'state': 'WATCHDOG_SAMPLES_COLLECTED', 'samples': public}


def verify_confirmation_source(controller):
    if controller != CONFIRMATION_CONTROLLER:
        raise ValueError('CONFIRMATION_SOURCE_MISMATCH')
    bundle = ROOT / 'automation-controller/revisions' / controller / 'pilot-source'
    for relative, expected in CONFIRMATION_SOURCE.items():
        path = bundle / relative
        if path.resolve(strict=True) != path or hashlib.sha256(protected(path, 0)).hexdigest() != expected:
            raise ValueError('CONFIRMATION_SOURCE_MISMATCH')


def run_api_probe(docker, as_worker, profile, controller, journal, uid, *, confirmation=False):
    """Run only the exact image's API in a disposable container over Staging loopback."""
    current = json.loads(docker('inspect', '--format', FIELDS, 'gamerhq-staging-bot'))
    if (current['running'] is not False or current['status'] != 'exited' or current['image'] != IMAGES['fallback']
            or current['host'] != HOSTS['fallback'] or current['project'] != 'gamerhq-staging'):
        raise ValueError('STOPPED_PINNED_FALLBACK_REQUIRED')
    daemon = json.loads(docker('info', '--format', '{{if .ID}}{{json .}}{{end}}'))
    if (not profile.get('dockerDaemonId') or daemon.get('ID') != profile.get('dockerDaemonId')
            or not any('rootless' in opt for opt in daemon.get('SecurityOptions') or [])
            or daemon.get('CgroupDriver') != 'systemd' or str(daemon.get('CgroupVersion')) != '2'):
        raise ValueError('ROOTLESS_DAEMON_REJECTED')
    if profile.get('workRoot') != str(ROOT / 'worker'):
        raise ValueError('STAGING_PATH_REJECTED')
    evidence = journal.get('checkpoint')
    if inspect_backup(ROOT / 'backups', uid, evidence)['state'] != 'VERIFIED':
        raise ValueError('CUTOVER_BACKUP_REJECTED')
    primary = COHORT['guildIds'][0]
    path = ROOT / 'backups' / evidence['backupBatch'] / (primary + '.sqlite')
    data = protected(path, uid, 0o600, limit=MAX_PROBE_DB)
    secret = secrets.token_urlsafe(40)
    client_packet = {'guild': primary, 'path': API_PATH, 'secret': secret}
    packet = {**client_packet, 'binding': {**COHORT, 'productionBotId': profile['productionBotId'],
                                           'productionGuildId': profile['productionGuildId'], 'primaryOnly': True}}
    name = 'gamerhq-staging-api-probe-' + secrets.token_hex(8)
    bundle = ROOT / 'automation-controller/revisions' / controller / 'pilot-source'
    worker_python = str(ROOT / 'deployment-worker/.venv/bin/python')
    args = [
        'create', '--name', name, '--interactive', '--pull', 'never', '--network', 'gamerhq-staging-private',
        '--publish', '127.0.0.1:18080:8080', '--read-only', '--restart', 'no',
        '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=48m,mode=1777', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--user', '10001:10001', '--memory', '256m', '--pids-limit', '32',
        '--cpus', '0.5', '--log-driver', 'json-file', '--log-opt', 'max-size=64k', '--log-opt', 'max-file=1',
    ]
    if confirmation:
        args += ['--health-cmd', """python -I -c "import socket; socket.create_connection(('127.0.0.1',8080),2).close()" """.strip(),
                 '--health-interval', '1s', '--health-timeout', '3s', '--health-retries', '30',
                 '--health-start-period', '30s']
    else:
        args += ['--no-healthcheck']
    args += ['--entrypoint', 'python', IMAGES['fallback'], '-I', '-B', '-c', API_SERVER]
    container = docker(*args)
    if not re.fullmatch(r'[0-9a-f]{64}', container) or container == current['id']:
        raise ValueError('DISPOSABLE_ID_REJECTED')
    try:
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as startup_errors:
            source.write(json.dumps(packet).encode() + b'\n')
            source.write(data)
            source.seek(0)
            import pwd
            account = pwd.getpwuid(uid)
            env = ['PATH=/usr/local/bin:/usr/bin:/bin', f'HOME={account.pw_dir}',
                   f'XDG_RUNTIME_DIR=/run/user/{uid}', f'DOCKER_HOST=unix:///run/user/{uid}/docker.sock',
                   'DOCKER_CONFIG=/etc/gamerhq-staging/docker-client']
            job = subprocess.Popen(['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env', '-i',
                                    *env, 'docker', 'start', '--attach', '--interactive', container],
                                   stdin=source, stdout=subprocess.DEVNULL, stderr=startup_errors)
            try:
                local = {'state': 'PROBE_STARTUP_UNVERIFIED'}
                for _ in range(30):
                    try:
                        logs = docker('logs', '--tail', '30', container, logs=True)
                    except subprocess.SubprocessError:
                        logs = ''
                    signals = [line for line in logs.splitlines() if line.startswith('GAMERHQ_API_PROBE ')]
                    if signals:
                        local = api_receipt(signals[-1].split(' ', 1)[1])
                        break
                    if job.poll() is not None:
                        break
                    time.sleep(1)
                external = {'state': 'NOT_ATTEMPTED'}
                if local['state'] == 'PROBE_STARTUP_UNVERIFIED':
                    startup_errors.seek(0)
                    errors = startup_errors.read(16384).lower()
                    if b'address already in use' in errors or b'port is already allocated' in errors:
                        local = {'state': 'LOOPBACK_PORT_OCCUPIED'}
                elif confirmation:
                    for _ in range(12):
                        if docker('inspect', '--format', '{{.State.Health.Status}}', container) == 'healthy':
                            break
                        time.sleep(1)
                    reply = as_worker(['systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet',
                                       '--property=RuntimeMaxSec=34s',
                                       worker_python, '-I', '-B', '-c', CONFIRMATION_CLIENT, str(bundle)],
                                      input=json.dumps({'container': name, 'secret': secret}))
                    external = confirmation_receipt(reply.stdout)
                else:
                    reply = as_worker([worker_python, '-I', '-B', '-c', API_CLIENT, str(bundle),
                                       'http://127.0.0.1:18080'], input=json.dumps(client_packet))
                    external = api_receipt(reply.stdout)
                return {'state': 'HOST_API_CONFIRMATION_REHEARSAL' if confirmation else 'HOST_API_DIAGNOSTIC',
                        'fallbackImage': IMAGES['fallback'],
                        'path': API_PATH, 'local': local, 'publishedLoopback': external,
                        'gatewayStarted': False, 'productionChanged': False, 'liveDatabaseChanged': False,
                        'recoveryLatchChanged': False}
            finally:
                if job.poll() is None:
                    job.terminate()
                    job.wait(timeout=10)
    finally:
        docker('rm', '--force', container)
        after = json.loads(docker('inspect', '--format', FIELDS, 'gamerhq-staging-bot'))
        if after['id'] != current['id'] or after['running'] is not False:
            raise ValueError('LIVE_FALLBACK_STATE_CHANGED_EXTERNALLY')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--api-probe', action='store_true')
    mode.add_argument('--confirmation-probe', action='store_true')
    parser.add_argument('--approval')
    args = parser.parse_args()
    try:
        if ((args.api_probe and args.approval != API_DIAGNOSTIC_APPROVAL)
                or (args.confirmation_probe and args.approval != CONFIRMATION_DIAGNOSTIC_APPROVAL)):
            raise ValueError('OWNER_DIAGNOSTIC_APPROVAL_REQUIRED')
        print(json.dumps(collect(api_probe=args.api_probe or args.confirmation_probe,
                                 confirmation_probe=args.confirmation_probe), sort_keys=True))
    except BlockingIOError:
        raise SystemExit('{"diagnosticError":"OPERATION_LOCK_BUSY","productionChanged":false}') from None
    except subprocess.SubprocessError:
        raise SystemExit('{"diagnosticError":"ROOTLESS_INSPECTION_REJECTED","productionChanged":false}') from None
    except (OSError, ValueError, TypeError, KeyError) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch(r'[A-Z_]{8,80}', str(error)) else 'DIAGNOSTIC_REJECTED'
        raise SystemExit(json.dumps({'diagnosticError': code, 'productionChanged': False})) from None
