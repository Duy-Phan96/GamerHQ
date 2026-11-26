"""Durable single-container Staging cutover, called under the operation lock."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from contextlib import contextmanager

from .policy import Denied


TERMINAL = {'PILOT_ACTIVE', 'PILOT_ROLLED_BACK', 'PILOT_STOP_CANCELLED'}
PENDING = {'STOPPING', 'CHECKPOINTED', 'STARTING', 'VERIFYING', 'ROLLING_BACK'}
CONFIRMATION_CHECKS = {'POST_START_ATTESTATION', 'HOST_API_HEALTH', 'CHECKPOINT_INTEGRITY',
                       'PRIMARY_DATA_RETENTION', 'EVENT_MANAGER_LIFECYCLE', 'SECONDARY_DATA_INTEGRITY'}


class ConfirmationFailure(Denied):
    def __init__(self, stage, error):
        super().__init__(error.code if isinstance(error, Denied) else 'AUTOMATION_OPERATION_FAILED')
        self.stage = stage
        self.detail = {}
        if stage == 'HOST_API_HEALTH':
            from .backend import HealthDockerDenied
            from tools.staging_health import HEALTH_FAILURE_REASONS, HostHealthFailure
            self.detail['reason'] = 'HEALTH_UNCLASSIFIED_FAILURE'
            if isinstance(error, (HostHealthFailure, HealthDockerDenied)) and error.reason in HEALTH_FAILURE_REASONS:
                self.detail['reason'] = error.reason
                if error.reason == 'HEALTH_HTTP_REJECTED' and type(error.status) is int and 300 <= error.status <= 599:
                    self.detail['httpStatus'] = error.status


@contextmanager
def confirmation_step(stage):
    try:
        yield
    except Exception as error:
        raise ConfirmationFailure(stage, error) from None


def journal_path(profile):
    generation = profile.get('pilotGeneration')
    names = {None: 'tenant-pilot-cutover.json',
             'recovered-primary-v2': 'tenant-pilot-cutover-v2.json',
             'active-pilot-rc4-v3': 'tenant-pilot-cutover-rc4-v3.json'}
    if generation not in names:
        raise Denied('PILOT_CHECKPOINT_REJECTED')
    return Path(profile['workRoot']) / names[generation]


def journal_status(profile):
    from tools.staging_dispatch import protected_file
    from tools.staging_health import HEALTH_FAILURE_REASONS

    path = journal_path(profile)
    try:
        protected_file(path, private=True, uid=os.geteuid())
        with path.open('rb') as stream:
            record = json.loads(stream.read(16384))
        phase = record.get('phase')
        details = record.get('rollbackHealthFailure')
        public = {}
        reason = details.get('reason') if isinstance(details, dict) else None
        if isinstance(reason, str) and reason in HEALTH_FAILURE_REASONS:
            public['reason'] = details['reason']
            status = details.get('httpStatus')
            if reason == 'HEALTH_HTTP_REJECTED' and type(status) is int and 300 <= status <= 599:
                public['httpStatus'] = status
        return {'phase': phase if phase in PENDING | TERMINAL else 'UNKNOWN',
                **{key: record[key] for key in ('failedCheck', 'rollbackFailedCheck')
                   if record.get(key) in CONFIRMATION_CHECKS},
                **({'rollbackHealthFailure': public} if public else {})}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'phase': 'UNAVAILABLE'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class PilotCutover:
    def __init__(self, backend, prepared, approved_image, run_id):
        from hosts.gamerhq.skill_deployment_docker import IMAGE
        from tools.staging_tenant_rehearsal import approved_binding, startup_override

        self.backend = backend
        self.policy = backend.policy
        from .pilot_generation import is_active_upgrade
        self.active_upgrade = is_active_upgrade(self.policy)
        self.profile = {**backend.profile, 'tenantPilot': self.policy['tenantPilot']}
        generation = self.policy.get('tenantPilotGeneration')
        if generation is not None:
            from .pilot_generation import validate_generation
            validate_generation(self.policy)
            self.profile['pilotGeneration'] = generation['id']
            if prepared.get('generation') != generation['id']:
                raise Denied('PILOT_CHECKPOINT_REJECTED')
        self.cohort = approved_binding(self.profile, self.policy)
        self.prepared = prepared
        self.run_id = run_id
        self.path = journal_path(self.profile)
        self.baseline = approved_image
        self.candidate = prepared.get('candidateImage')
        self.fallback = prepared.get('fallbackImage')
        if (not all(isinstance(v, str) and IMAGE.fullmatch(v) for v in (self.baseline, self.candidate, self.fallback))
                or (generation is None and len({self.baseline, self.candidate, self.fallback}) != 3)
                or (generation is not None and (self.baseline != generation['baselineImage']
                    or self.fallback != self.baseline or self.candidate == self.baseline))):
            raise Denied('PILOT_CHECKPOINT_REJECTED')
        self.candidate_override = startup_override(self.profile, self.policy, self.candidate)
        self.fallback_override = startup_override(self.profile, self.policy, self.fallback, primary_only=not self.active_upgrade)
        if (prepared.get('startupOverrideSha256') != digest(self.candidate_override)
                or prepared.get('fallbackStartupOverrideSha256') != digest(self.fallback_override)):
            raise Denied('PILOT_CHECKPOINT_REJECTED')
        self.binding = {
            'preparedSha256': digest(prepared), 'baselineImage': self.baseline,
            'candidateImage': self.candidate, 'fallbackImage': self.fallback,
        }
        if generation is not None:
            self.binding['generation'] = generation['id']

    def load(self):
        from tools.staging_dispatch import protected_file

        if not os.path.lexists(self.path):
            return None
        protected_file(self.path, private=True, uid=os.geteuid())
        record = json.loads(self.path.read_bytes())
        if (record.get('binding') != self.binding or record.get('phase') not in PENDING | TERMINAL
                or not re.fullmatch(r'[0-9a-f]{64}', record.get('baselineContainer', ''))):
            raise Denied('RECOVERY_REQUIRES_OWNER')
        return record

    def save(self, record, phase, **evidence):
        from tools.skill_staging import write_record

        value = {**record, 'phase': phase, **evidence}
        write_record(self.path, value)
        if os.name == 'posix':
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return value

    def inspect(self):
        present = self.backend.run(['docker', 'ps', '-aq', '--filter', 'name=^/gamerhq-staging-bot$'])
        return self.backend.inspect() if present else None

    def compose(self, *args, override=None):
        from tools.skill_staging import write_record

        os.environ['GAMERHQ_STAGING_IMAGE'] = self.baseline
        cmd = ['docker', 'compose', '--file', self.profile['composeFile'], '--profile', 'live-acceptance']
        if override:
            path = self.path.with_name('tenant-pilot-compose-rc4-v3.json' if getattr(self, 'active_upgrade', False) else 'tenant-pilot-compose.json')
            write_record(path, override)
            cmd.extend(['--file', str(path)])
        self.backend.run([*cmd, *args, self.profile['service']], timeout=90,
                         pass_fds=(self.backend.operation_lock,))

    def attest(self, *, running_container=None):
        from hosts.gamerhq.skill_staging import verify_remote
        from .host_promotion import source_commit

        pin = self.policy['tenantPilotActivation']
        fallback = self.policy['tenantPilotCutover']['fallback']
        expected_fallback = getattr(self, 'recovery_api_repair', {}).get('commit', fallback['commit'])
        if (self.prepared.get('candidateCommit') != pin['commit']
                or self.prepared.get('fallbackCommit') != fallback['commit']
                or self.prepared.get('containerRollbackRehearsal') != 'PASS'
                or self.prepared.get('fallbackRollbackRehearsal') != 'PASS'
                or source_commit(self.backend, self.candidate) != pin['commit']
                or source_commit(self.backend, self.fallback) != expected_fallback):
            raise Denied('PILOT_CHECKPOINT_REJECTED')
        if running_container:
            # A second :Z bind would relabel the live service's private data.
            # Inspect already verified the mounts. Check access from their
            # owning container and keep the remaining attestation read-only.
            self.backend.run(['docker', 'exec', '--user', '10001:10001', running_container, 'python', '-I', '-B', '-c',
                              'import os,sys; sys.exit(not all(os.access(p,os.R_OK|os.W_OK) for p in sys.argv[1:]))',
                              '/app/runtime/data', '/app/runtime/data/gamerhq.db', '/app/backups'])
        # Before launch, the existing Host binding and sealed dual checkpoint
        # prove storage access. Do not relabel either mount ahead of a restart.
        return verify_remote(self.profile, approved_pilot=self.cohort, read_only_artifacts=True, probe_storage=False)

    def checkpoint(self, record):
        from tools.staging_tenant_rehearsal import retain_quiesced, baseline_override

        captured = retain_quiesced(self.backend, record['baselineContainer'], self.baseline)
        binding = {'baselineHostCommit': self.policy['hostPromotion']['commit'], 'baselineImage': self.baseline,
                   'startupOverrideSha256': digest(baseline_override(self.profile, self.policy, self.baseline)),
                   'quiesced': True}
        if getattr(self, 'active_upgrade', False):
            binding['baselineAdmission'] = 'PILOT_ACTIVE'
        return {**captured, **binding, 'capturedAt': int(time.time())}

    def verify_checkpoint(self, record, *, recovery=False):
        from tools.staging_tenant_rehearsal import baseline_override, verify_activation_checkpoint

        checkpoint = record['checkpoint']
        stamp = checkpoint['capturedAt']
        binding = {'baselineHostCommit': self.policy['hostPromotion']['commit'], 'baselineImage': self.baseline,
                   'startupOverrideSha256': digest(baseline_override(self.profile, self.policy, self.baseline)),
                   'quiesced': True}
        if getattr(self, 'active_upgrade', False):
            binding['baselineAdmission'] = 'PILOT_ACTIVE'
        verify_activation_checkpoint(self.profile['backupRoot'], checkpoint, self.policy['tenantPilot'], binding,
                                     verified_at=stamp, now=stamp if recovery else int(time.time()))
        return Path(self.profile['backupRoot']) / checkpoint['backupBatch']

    def confirm(self, image, override, record, *, recovery=False):
        from services.tenant_routing import PILOT_ENV
        from tools.staging_tenant_rehearsal import capture_container_tenant
        from .host_promotion import (application_fingerprint, require_baseline_skill, require_retained,
                                     require_runtime_lifecycle, retained_state)

        expected_binding = override['services'][self.profile['service']]['environment'][PILOT_ENV]
        for _ in range(60):
            container = self.backend.inspect()
            values = [value for value in container['Config']['Env'] if value.startswith(PILOT_ENV + '=')]
            if container['Image'] != image or values != [PILOT_ENV + '=' + expected_binding]:
                raise Denied('PILOT_BASELINE_CHANGED')
            health = container['State'].get('Health', {}).get('Status')
            if not container['State'].get('Running') or health == 'unhealthy':
                raise Denied('HOST_NOT_HEALTHY')
            if health == 'healthy':
                break
            time.sleep(3)
        else:
            raise Denied('HOST_NOT_HEALTHY')
        with confirmation_step('POST_START_ATTESTATION'):
            self.attest(running_container=container['Id'])
        with confirmation_step('HOST_API_HEALTH'):
            from tools.staging_health import HostHealthFailure
            health = self.backend.health()['state']
            if health != 'HOST_READY':
                raise HostHealthFailure('HEALTH_CONTAINER_ABSENT' if health == 'AWAITING_INITIAL_BOOTSTRAP'
                                        else 'HEALTH_NOT_READY')
        with confirmation_step('CHECKPOINT_INTEGRITY'):
            batch = self.verify_checkpoint(record, recovery=recovery)
        primary = self.profile['stagingGuildId']
        with confirmation_step('PRIMARY_DATA_RETENTION'):
            before = retained_state(batch / f'{primary}.sqlite', primary)
            after = retained_state(self.profile['database'], primary)
            require_retained(before, after)
        with confirmation_step('EVENT_MANAGER_LIFECYCLE'):
            runtime = self.backend.skill()
            release = json.loads(Path(self.profile['releaseFile']).read_bytes())
            require_baseline_skill(release, runtime['version'])
            require_runtime_lifecycle(after, runtime)
        secondary, = set(self.cohort) - {int(primary)}
        with confirmation_step('SECONDARY_DATA_INTEGRITY'):
            with tempfile.TemporaryDirectory(dir=self.profile['workRoot'], prefix='pilot-health-') as root:
                snapshot = Path(root) / 'secondary.sqlite'
                capture_container_tenant(container['Id'], secondary, snapshot)
                if getattr(self, 'active_upgrade', False):
                    require_retained(retained_state(batch / f'{secondary}.sqlite', secondary, require_event_manager=False),
                                     retained_state(snapshot, secondary, require_event_manager=False))
                elif recovery and application_fingerprint(snapshot) != application_fingerprint(batch / f'{secondary}.sqlite'):
                    raise Denied('RECOVERY_REQUIRES_OWNER')

    def confirm_fallback(self, record):
        try:
            self.confirm(self.fallback, self.fallback_override, record, recovery=True)
        except Exception as error:
            try:
                if isinstance(error, ConfirmationFailure):
                    self.save(record, 'ROLLING_BACK', rollbackFailedCheck=error.stage,
                              rollbackHealthFailure=error.detail or None)
            finally:
                self.compose('stop', '--timeout', '30')
            raise

    def prove_rollback_compatibility(self, record):
        """Snapshot the quiesced exact candidate or baseline, compare both DBs.

        A mismatch or unavailable stopped image holds the existing recovery latch.
        No restoration into live paths, and no Gateway or credentialed reader.
        """
        from tools.staging_tenant_rehearsal import retain_quiesced, require_unchanged_dual_checkpoint, verify_activation_checkpoint
        original = self.verify_checkpoint(record, recovery=True)
        stopped = self.inspect()
        if not stopped or stopped['Image'] not in {self.candidate, self.baseline}:
            raise Denied('RECOVERY_REQUIRES_OWNER')
        candidate = stopped['Image'] == self.candidate
        captured = retain_quiesced(self.backend, stopped['Id'], stopped['Image'], candidate=candidate)
        # Bind the newly captured stopped image and its exact admission override.
        binding = {'baselineHostCommit': self.policy['tenantPilotActivation' if candidate else 'hostPromotion']['commit'],
                   'baselineImage': stopped['Image'],
                   'startupOverrideSha256': digest(self.candidate_override if candidate else self.fallback_override),
                   'baselineAdmission': 'PILOT_ACTIVE', 'quiesced': True}
        stamp = int(time.time())
        verify_activation_checkpoint(self.profile['backupRoot'], captured, self.policy['tenantPilot'], binding,
                                     verified_at=stamp, now=stamp)
        observed = Path(self.profile['backupRoot']) / captured['backupBatch']
        require_unchanged_dual_checkpoint(original, observed, self.cohort)
        return {'state': 'DUAL_LOGICAL_CONTENT_UNCHANGED', 'backupBatch': captured['backupBatch'],
                'backupReceiptSha256': captured['backupReceiptSha256']}

    def rollback(self, record):
        current = self.inspect()
        if (record['phase'] == 'STOPPING' and current and current['Id'] == record['baselineContainer']
                and current['Image'] == self.baseline and current['State'].get('Running')
                and self.backend.health()['state'] == 'HOST_READY'):
            return self.save(record, 'PILOT_STOP_CANCELLED')
        permitted = (self.baseline, self.candidate, self.fallback, getattr(self, 'original_fallback', self.fallback))
        if current and current['Image'] not in permitted:
            raise Denied('RECOVERY_REQUIRES_OWNER')
        if (record['phase'] == 'ROLLING_BACK' and 'checkpoint' in record and current
                and current['Image'] == self.fallback and current['State'].get('Running')):
            if getattr(self, 'active_upgrade', False) and record.get('candidateMayHaveStarted') is True:
                if record.get('rollbackCompatibility', {}).get('state') != 'DUAL_LOGICAL_CONTENT_UNCHANGED':
                    self.compose('stop', '--timeout', '30')
                    raise Denied('RECOVERY_REQUIRES_OWNER')
            self.confirm_fallback(record)
            return self.save(record, 'PILOT_ROLLED_BACK')
        if current and current['State'].get('Running'):
            self.compose('stop', '--timeout', '30')
        if getattr(self, 'active_upgrade', False) and (record.get('candidateMayHaveStarted') is True or
                                    record['phase'] in {'STARTING', 'VERIFYING'} or
                                    (current and current['Image'] == self.candidate)):
            record = self.save(record, 'ROLLING_BACK', candidateMayHaveStarted=True)
            try:
                proof = self.prove_rollback_compatibility(record)
            except Exception:
                raise Denied('RECOVERY_REQUIRES_OWNER') from None
            record = self.save(record, 'ROLLING_BACK', rollbackCompatibility=proof)
        self.attest()
        if 'checkpoint' not in record:
            record = self.save(record, 'ROLLING_BACK', checkpoint=self.checkpoint(record))
        else:
            record = self.save(record, 'ROLLING_BACK')
        self.verify_checkpoint(record, recovery=True)
        self.compose('up', '--detach', '--no-build', '--pull', 'never', override=self.fallback_override)
        self.confirm_fallback(record)
        return self.save(record, 'PILOT_ROLLED_BACK')

    def run(self, *, recovery_only=False):
        from tools.staging_tenant_rehearsal import rehearsal_binding, verify_activation_checkpoint
        from .pilot_activation import preparation_state

        record = self.load()
        if recovery_only and (not record or 'checkpoint' not in record
                              or record['phase'] in {'PILOT_ACTIVE', 'PILOT_STOP_CANCELLED'}):
            raise Denied('RECOVERY_REQUIRES_OWNER')
        if 'primaryRecoveryApiRepair' in self.policy:
            if not recovery_only:
                raise Denied('RECOVERY_REQUIRES_OWNER')
            from .recovery_api_repair import prepare
            from tools.staging_tenant_rehearsal import startup_override
            self.verify_checkpoint(record, recovery=True)
            proof = prepare(self.backend, record)
            if record.get('recoveryApiRepair') != proof:
                record = self.save(record, record['phase'], recoveryApiRepair=proof)
            self.recovery_api_repair = proof
            self.original_fallback, self.fallback = self.fallback, proof['image']
            self.fallback_override = startup_override(self.profile, self.policy, self.fallback, primary_only=True)
        if record:
            if recovery_only and record['phase'] == 'PILOT_ROLLED_BACK':
                # A crash or legacy controller can leave the journal terminal
                # while the latch is still held. Re-prove all recovery gates.
                try:
                    record = self.rollback(record)
                except Exception:
                    raise Denied('RECOVERY_REQUIRES_OWNER') from None
            elif record['phase'] in TERMINAL:
                image = {'PILOT_ACTIVE': self.candidate, 'PILOT_ROLLED_BACK': self.fallback,
                         'PILOT_STOP_CANCELLED': self.baseline}[record['phase']]
                current = self.backend.inspect()
                if current['Image'] != image or self.backend.health()['state'] != 'HOST_READY':
                    raise Denied('RECOVERY_REQUIRES_OWNER')
            else:
                try:
                    record = self.rollback(record)
                except Exception:
                    raise Denied('RECOVERY_REQUIRES_OWNER') from None
        else:
            pin = self.policy['tenantPilotActivation']
            state = preparation_state(self.prepared, pin, int(time.time()))
            if state != 'FRESH':
                raise Denied('PILOT_PREVIEW_EXPIRED' if state == 'EXPIRED' else 'PILOT_CHECKPOINT_REJECTED')
            self.attest()
            current = self.backend.inspect()
            if current['Image'] != self.baseline or self.backend.health()['state'] != 'HOST_READY':
                raise Denied('PILOT_BASELINE_CHANGED')
            preview = self.prepared['preview']
            verify_activation_checkpoint(self.profile['backupRoot'], preview, self.policy['tenantPilot'],
                                         rehearsal_binding(self.profile, self.policy, current),
                                         verified_at=preview['verifiedAt'], now=int(time.time()))
            if preparation_state(self.prepared, pin, int(time.time())) != 'FRESH':
                raise Denied('PILOT_PREVIEW_EXPIRED')
            record = self.save({'binding': self.binding, 'baselineContainer': current['Id'],
                                'activationRun': self.run_id}, 'STOPPING')
            try:
                # Legacy workers require a singleton proof. Its in-window
                # cache predating Servertest must not survive this transition.
                Path(self.profile['verificationFile']).unlink(missing_ok=True)
                self.compose('stop', '--timeout', '30')
                record = self.save(record, 'CHECKPOINTED', checkpoint=self.checkpoint(record))
                self.verify_checkpoint(record)
                self.attest()
                if preparation_state(self.prepared, pin, int(time.time())) != 'FRESH':
                    raise Denied('PILOT_PREVIEW_EXPIRED')
                record = self.save(record, 'STARTING', **({'candidateMayHaveStarted': True} if getattr(self, 'active_upgrade', False) else {}))
                self.compose('up', '--detach', '--no-build', '--pull', 'never', override=self.candidate_override)
                record = self.save(record, 'VERIFYING')
                self.confirm(self.candidate, self.candidate_override, record)
                record = self.save(record, 'PILOT_ACTIVE')
            except Exception as error:
                try:
                    record = self.load()
                    code = error.code if isinstance(error, Denied) else 'AUTOMATION_OPERATION_FAILED'
                    checks = {'failedCheck': error.stage} if isinstance(error, ConfirmationFailure) else {}
                    record = self.save(record, record['phase'], failedAt=record['phase'], failure=code, **checks)
                    record = self.rollback(record)
                except Exception:
                    raise Denied('RECOVERY_REQUIRES_OWNER') from None
        result = {'state': record['phase'], 'activationRun': record['activationRun'],
                  'activationAuthorized': record['phase'] == 'PILOT_ACTIVE'}
        if recovery_only:
            if record['phase'] != 'PILOT_ROLLED_BACK' or self.load() != record:
                raise Denied('RECOVERY_REQUIRES_OWNER')
            result['recoveryVerified'] = True
            if hasattr(self, 'recovery_api_repair'):
                result.update(recoveryHostCommit=self.recovery_api_repair['commit'],
                              recoveryImage=self.recovery_api_repair['image'])
        if 'checkpoint' in record:
            result['backupBatch'] = record['checkpoint']['backupBatch']
            result['backupReceiptSha256'] = record['checkpoint']['backupReceiptSha256']
        if 'failure' in record:
            result.update(failedAt=record['failedAt'], failure=record['failure'])
        return result
