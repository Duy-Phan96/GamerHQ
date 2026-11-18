"""Durable single-container Staging cutover, called under the operation lock."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .policy import Denied


TERMINAL = {'PILOT_ACTIVE', 'PILOT_ROLLED_BACK', 'PILOT_STOP_CANCELLED'}
PENDING = {'STOPPING', 'CHECKPOINTED', 'STARTING', 'VERIFYING', 'ROLLING_BACK'}


def journal_path(profile):
    return Path(profile['workRoot']) / 'tenant-pilot-cutover.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class PilotCutover:
    def __init__(self, backend, prepared, approved_image, run_id):
        from hosts.gamerhq.skill_deployment_docker import IMAGE
        from tools.staging_tenant_rehearsal import approved_binding, startup_override

        self.backend = backend
        self.policy = backend.policy
        self.profile = {**backend.profile, 'tenantPilot': self.policy['tenantPilot']}
        self.cohort = approved_binding(self.profile, self.policy)
        self.prepared = prepared
        self.run_id = run_id
        self.path = journal_path(self.profile)
        self.baseline = approved_image
        self.candidate = prepared.get('candidateImage')
        self.fallback = prepared.get('fallbackImage')
        if (not all(isinstance(v, str) and IMAGE.fullmatch(v) for v in (self.baseline, self.candidate, self.fallback))
                or len({self.baseline, self.candidate, self.fallback}) != 3):
            raise Denied('PILOT_CHECKPOINT_REJECTED')
        self.candidate_override = startup_override(self.profile, self.policy, self.candidate)
        self.fallback_override = startup_override(self.profile, self.policy, self.fallback, primary_only=True)
        if (prepared.get('startupOverrideSha256') != digest(self.candidate_override)
                or prepared.get('fallbackStartupOverrideSha256') != digest(self.fallback_override)):
            raise Denied('PILOT_CHECKPOINT_REJECTED')
        self.binding = {
            'preparedSha256': digest(prepared), 'baselineImage': self.baseline,
            'candidateImage': self.candidate, 'fallbackImage': self.fallback,
        }

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
            path = self.path.with_name('tenant-pilot-compose.json')
            write_record(path, override)
            cmd.extend(['--file', str(path)])
        self.backend.run([*cmd, *args, self.profile['service']], timeout=90)

    def attest(self):
        from hosts.gamerhq.skill_staging import verify_remote
        from .host_promotion import source_commit

        pin = self.policy['tenantPilotActivation']
        fallback = self.policy['tenantPilotCutover']['fallback']
        if (self.prepared.get('candidateCommit') != pin['commit']
                or self.prepared.get('fallbackCommit') != fallback['commit']
                or self.prepared.get('containerRollbackRehearsal') != 'PASS'
                or self.prepared.get('fallbackRollbackRehearsal') != 'PASS'
                or source_commit(self.backend, self.candidate) != pin['commit']
                or source_commit(self.backend, self.fallback) != fallback['commit']):
            raise Denied('PILOT_CHECKPOINT_REJECTED')
        return verify_remote(self.profile, approved_pilot=self.cohort, read_only_artifacts=True)

    def checkpoint(self, record):
        from tools.staging_tenant_rehearsal import retain_quiesced, startup_override

        captured = retain_quiesced(self.backend, record['baselineContainer'], self.baseline)
        binding = {'baselineHostCommit': self.policy['hostPromotion']['commit'], 'baselineImage': self.baseline,
                   'startupOverrideSha256': digest(startup_override(self.profile, self.policy, self.baseline)),
                   'quiesced': True}
        return {**captured, **binding, 'capturedAt': int(time.time())}

    def verify_checkpoint(self, record, *, recovery=False):
        from tools.staging_tenant_rehearsal import startup_override, verify_activation_checkpoint

        checkpoint = record['checkpoint']
        stamp = checkpoint['capturedAt']
        binding = {'baselineHostCommit': self.policy['hostPromotion']['commit'], 'baselineImage': self.baseline,
                   'startupOverrideSha256': digest(startup_override(self.profile, self.policy, self.baseline)),
                   'quiesced': True}
        verify_activation_checkpoint(self.profile['backupRoot'], checkpoint, self.policy['tenantPilot'], binding,
                                     verified_at=stamp, now=stamp if recovery else int(time.time()))
        return Path(self.profile['backupRoot']) / checkpoint['backupBatch']

    def confirm(self, image, override, record, *, recovery=False):
        from services.tenant_routing import PILOT_ENV
        from tools.staging_tenant_rehearsal import capture_container_tenant
        from .host_promotion import require_baseline_skill, require_retained, require_runtime_lifecycle, retained_state

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
        self.attest()
        if self.backend.health()['state'] != 'HOST_READY':
            raise Denied('HOST_NOT_READY')
        batch = self.verify_checkpoint(record, recovery=recovery)
        primary = self.profile['stagingGuildId']
        before = retained_state(batch / f'{primary}.sqlite', primary)
        after = retained_state(self.profile['database'], primary)
        require_retained(before, after)
        runtime = self.backend.skill()
        release = json.loads(Path(self.profile['releaseFile']).read_bytes())
        require_baseline_skill(release, runtime['version'])
        require_runtime_lifecycle(after, runtime)
        secondary, = set(self.cohort) - {int(primary)}
        with tempfile.TemporaryDirectory(dir=self.profile['workRoot'], prefix='pilot-health-') as root:
            capture_container_tenant(container['Id'], secondary, Path(root) / 'secondary.sqlite')

    def rollback(self, record):
        current = self.inspect()
        if (record['phase'] == 'STOPPING' and current and current['Id'] == record['baselineContainer']
                and current['Image'] == self.baseline and current['State'].get('Running')
                and self.backend.health()['state'] == 'HOST_READY'):
            return self.save(record, 'PILOT_STOP_CANCELLED')
        if current and current['Image'] not in (self.baseline, self.candidate, self.fallback):
            raise Denied('RECOVERY_REQUIRES_OWNER')
        if (record['phase'] == 'ROLLING_BACK' and 'checkpoint' in record and current
                and current['Image'] == self.fallback and current['State'].get('Running')):
            try:
                self.confirm(self.fallback, self.fallback_override, record, recovery=True)
            except Exception:
                self.compose('stop', '--timeout', '30')
                raise
            return self.save(record, 'PILOT_ROLLED_BACK')
        if current and current['State'].get('Running'):
            self.compose('stop', '--timeout', '30')
        self.attest()
        if 'checkpoint' not in record:
            record = self.save(record, 'ROLLING_BACK', checkpoint=self.checkpoint(record))
        else:
            record = self.save(record, 'ROLLING_BACK')
        self.verify_checkpoint(record, recovery=True)
        self.compose('up', '--detach', '--no-build', '--pull', 'never', override=self.fallback_override)
        try:
            self.confirm(self.fallback, self.fallback_override, record, recovery=True)
        except Exception:
            self.compose('stop', '--timeout', '30')
            raise
        return self.save(record, 'PILOT_ROLLED_BACK')

    def run(self):
        from tools.staging_tenant_rehearsal import rehearsal_binding, verify_activation_checkpoint
        from .pilot_activation import preparation_state

        record = self.load()
        if record:
            if record['phase'] in TERMINAL:
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
                record = self.save(record, 'STARTING')
                self.compose('up', '--detach', '--no-build', '--pull', 'never', override=self.candidate_override)
                record = self.save(record, 'VERIFYING')
                self.confirm(self.candidate, self.candidate_override, record)
                record = self.save(record, 'PILOT_ACTIVE')
            except Exception as error:
                try:
                    record = self.load()
                    code = error.code if isinstance(error, Denied) else 'AUTOMATION_OPERATION_FAILED'
                    record = self.save(record, record['phase'], failedAt=record['phase'], failure=code)
                    record = self.rollback(record)
                except Exception:
                    raise Denied('RECOVERY_REQUIRES_OWNER') from None
        result = {'state': record['phase'], 'activationRun': record['activationRun'],
                  'activationAuthorized': record['phase'] == 'PILOT_ACTIVE'}
        if 'checkpoint' in record:
            result['backupBatch'] = record['checkpoint']['backupBatch']
            result['backupReceiptSha256'] = record['checkpoint']['backupReceiptSha256']
        if 'failure' in record:
            result.update(failedAt=record['failedAt'], failure=record['failure'])
        return result
