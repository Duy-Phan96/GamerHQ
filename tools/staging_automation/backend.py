"""Bounded adapter to the EXISTING installed Host controllers and staging DB."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid
import shutil

from .policy import Denied, release_allowed
from .oidc import github, owner_protected


class HealthDockerDenied(Denied):
    def __init__(self, code, reason):
        super().__init__(code)
        self.reason = reason
        self.status = None


def verify_live_storage(backend, container):
    """Check access as the owning container UID without relabeling live mounts."""
    if (container.get('State', {}).get('Running') is not True
            or container['State'].get('Health', {}).get('Status') != 'healthy'):
        raise Denied('HOST_NOT_HEALTHY')
    backend.run(['docker', 'exec', '--user', '10001:10001', container['Id'], 'python', '-I', '-B', '-c',
                 'import os,sys; sys.exit(not all(os.access(p,os.R_OK|os.W_OK) for p in sys.argv[1:]))',
                 '/app/runtime/data', '/app/runtime/data/gamerhq.db', '/app/backups'])


class HostBackend:
    def __init__(self, profile, policy):
        self.profile, self.policy = profile, policy
        self.operation_lock = None

    def run(self, args, timeout=30, *, stdin=None, pass_fds=()):
        try:
            result = subprocess.run(args, input=stdin, capture_output=True, text=True, check=True, timeout=timeout,
                                    **({'pass_fds': pass_fds} if pass_fds else {}))
            if len(result.stdout.encode()) > 262144: raise ValueError()
            return result.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            from tools.staging_errors import docker_failure
            raise Denied('HOST_' + docker_failure(error).evidence['stagingFailure']) from None
        except Exception: raise Denied('HOST_OPERATION_FAILED') from None

    def inspect(self):
        p = self.profile
        rows = json.loads(self.run(['docker', 'inspect', p['container']]))
        if len(rows) != 1: raise Denied('CONTAINER_IDENTITY_REJECTED')
        c = rows[0]; h = c['HostConfig']; labels = c['Config'].get('Labels', {})
        if c['Name'] != '/gamerhq-staging-bot' or labels.get('com.docker.compose.project') != 'gamerhq-staging' or labels.get('com.docker.compose.service') != 'gamerhq-staging': raise Denied('CONTAINER_IDENTITY_REJECTED')
        if c['Config']['User'] != '10001:10001' or h.get('Privileged') or h.get('CapAdd') or h.get('ReadonlyRootfs') is not True or h.get('CapDrop') != ['ALL']: raise Denied('CONTAINER_PRIVILEGES_REJECTED')
        if not 0 < h.get('Memory', 0) <= 536870912 or not 0 < h.get('NanoCpus', 0) <= 1000000000 or not 0 < h.get('PidsLimit', 0) <= 128: raise Denied('RESOURCE_LIMIT_REQUIRES_OWNER')
        if h.get('RestartPolicy', {}).get('Name') != 'no' or set(c['NetworkSettings']['Networks']) != {'gamerhq-staging-private'}: raise Denied('CONTAINER_NETWORK_REJECTED')
        mounts = {(m['Source'], m['Destination']) for m in c['Mounts'] if m['Type'] != 'tmpfs'}
        expected = {(str(Path(p['database']).parent), '/app/runtime/data'), (p['backupRoot'], '/app/backups')}
        if mounts != expected or len([m for m in c['Mounts'] if m['Type'] != 'tmpfs']) != 2: raise Denied('CONTAINER_MOUNTS_REJECTED')
        return c

    def health(self):
        from hosts.gamerhq.staging_rootless import verify_rootless
        from tools.staging_health import HostHealthFailure, health

        try:
            verify_rootless(self.profile)
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            raise HostHealthFailure('HEALTH_DAEMON_REJECTED') from None
        try:
            found = self.run(['docker', 'ps', '--all', '--filter', 'name=^/gamerhq-staging-bot$', '--format', '{{.ID}}'])
        except Denied as error:
            raise HealthDockerDenied(error.code, 'HEALTH_CONTAINER_LOOKUP_FAILED') from None
        if not found: return {'state': 'AWAITING_INITIAL_BOOTSTRAP', 'gatewayStarted': False}
        try:
            c = self.inspect()
            state = c['State']
            healthy = state.get('Running') is True and state.get('Health', {}).get('Status') == 'healthy'
        except Denied as error:
            raise HealthDockerDenied(error.code, 'HEALTH_CONTAINER_INSPECTION_REJECTED') from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HostHealthFailure('HEALTH_CONTAINER_INSPECTION_REJECTED') from None
        if not healthy:
            raise HealthDockerDenied('HOST_NOT_HEALTHY', 'HEALTH_GATEWAY_HEARTBEAT_REJECTED')
        result = health(self.profile)
        return {'state': result['status'], 'gatewayChecked': result['gatewayChecked'],
            'hostCommit': c['Config'].get('Labels', {}).get('org.opencontainers.image.revision')}

    def fresh_verify(self):
        from hosts.gamerhq.skill_staging import verify_remote
        from tools.skill_staging import write_record
        if 'tenantPilot' in self.profile:
            # Routine deployment paths still snapshot only the primary database.
            raise Denied('OPERATION_NOT_ALLOWED')
        proof = verify_remote(self.profile)
        write_record(self.profile['verificationFile'], proof)

    def verify_tenant_pilot(self, *, expected_image):
        """Check Discord without a Gateway and retain verified private snapshots."""
        return self._pilot_source('preview', expected_image)

    def prepare_tenant_pilot_activation(self, expected_image):
        return self._pilot_source('prepare-activation', expected_image)

    def cutover_tenant_pilot(self, expected_image, prepared, run_id, *, recovery_only=False):
        if self.operation_lock is None:
            raise Denied('CONCURRENT_OPERATION_REJECTED')
        return self._pilot_source('cutover', expected_image, prepared=prepared, runId=run_id, lockFd=self.operation_lock,
                                  recoveryOnly=recovery_only)

    def _pilot_source(self, operation, expected_image, **evidence):
        bundle = Path(__file__).resolve().parents[1] / 'pilot-source'
        if bundle.is_dir():
            python = '/opt/gamerhq-staging/deployment-worker/.venv/bin/python'
            launch = 'import sys; sys.path.insert(0, sys.argv[1]); from tools.staging_tenant_preview_entry import main; main()'
            inherited = {'pass_fds': (self.operation_lock,)} if operation == 'cutover' else {}
            raw = self.run([python, '-I', '-B', '-c', launch, str(bundle)], timeout=420 if operation == 'preview' else 1000,
                           stdin=json.dumps({'profile': self.profile, 'policy': self.policy, 'image': expected_image,
                                             'operation': operation, **evidence}), **inherited)
            result = json.loads(raw)
            if 'denied' in result:
                raise Denied(result['denied'])
            return result
        if operation == 'preview':
            return self._verify_tenant_pilot(expected_image=expected_image)
        if operation == 'cutover':
            raise Denied('OPERATION_NOT_ALLOWED')
        from .pilot_activation import prepare_session
        return prepare_session(self, expected_image)

    def _verify_tenant_pilot(self, *, expected_image):
        from database.tenants import GuildTenantStore
        from hosts.gamerhq.skill_staging import verify_remote
        from services.tenant_routing import GuildCorePermissionsError
        from tools.staging_tenant_rehearsal import approved_binding, capture_container_tenant, rehearsal_binding, retain
        proof_profile = {**self.profile, 'tenantPilot': self.policy.get('tenantPilot')}
        approved = approved_binding(proof_profile, self.policy)
        if approved is None:
            raise Denied('OPERATION_NOT_ALLOWED')
        self.storage_budget()
        container = self.inspect()
        if container['Image'] != expected_image:
            raise Denied('UNKNOWN_IMAGE_REQUIRES_OWNER')
        if self.health()['state'] != 'HOST_READY':
            raise Denied('HOST_NOT_READY')
        verify_live_storage(self, container)
        try:
            proof = verify_remote(proof_profile, approved_pilot=approved, read_only_artifacts=True, probe_storage=False)
        except GuildCorePermissionsError as error:
            return {'state': 'PILOT_CORE_PERMISSIONS_REJECTED', 'guildId': str(error.guild_id),
                    'missingPermissions': error.missing, 'gatewayStarted': False}
        binding = rehearsal_binding(proof_profile, self.policy, container)
        primary = int(self.profile['stagingGuildId'])
        source = Path(self.profile['database']).resolve(strict=True)
        tenant_directory = source.parent / 'tenants'
        if tenant_directory.is_symlink():
            raise Denied('PILOT_TENANT_PATH_REJECTED')
        store = GuildTenantStore(primary_guild=primary, primary_database=source,
                                 tenant_directory=tenant_directory,
                                 additional_guilds=tuple(guild for guild in approved if guild != primary))
        def capture(guild_id, destination):
            try:
                capture_container_tenant(container['Id'], guild_id, destination)
            except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error):
                raise Denied('PILOT_SECONDARY_BACKUP_REJECTED') from None

        result = retain(store, self.profile['backupRoot'], self.profile['workRoot'], binding=binding, secondary_capture=capture)
        current = self.inspect()
        if (current['Id'], current['Image']) != (container['Id'], container['Image']):
            raise Denied('UNKNOWN_IMAGE_REQUIRES_OWNER')
        return {**proof, **binding, **result, 'state': 'PILOT_PREVIEW_VERIFIED'}

    def storage_budget(self):
        if shutil.disk_usage(self.profile['workRoot']).free < 1073741824: raise Denied('DISK_BUDGET_REQUIRES_OWNER')
        with sqlite3.connect(Path(self.profile['database']).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as source:
            size = source.execute('PRAGMA page_count').fetchone()[0] * source.execute('PRAGMA page_size').fetchone()[0]
        if size > 268435456: raise Denied('DATABASE_BUDGET_REQUIRES_OWNER')

    def policy_integrity(self):
        if not owner_protected(github(self.policy, '/environments/staging'), self.policy['ownerId']):
            raise Denied('LEGACY_ENVIRONMENT_NOT_PROTECTED')

    def activate(self, approved_image=None):
        p = self.policy
        first = github(p, '/actions/runs/' + p['bootstrapRun'])
        if first.get('conclusion') != 'success' or first.get('head_sha') != p['bootstrapCommit'] or first.get('head_branch') != 'main' or first.get('event') != 'workflow_dispatch': raise Denied('INITIAL_BOOTSTRAP_NOT_VERIFIED')
        approvals = github(p, '/actions/runs/' + p['bootstrapRun'] + '/approvals')
        if not any(r.get('state') == 'approved' and str(r.get('user', {}).get('id')) == p['ownerId'] and any(e.get('name') == 'deployment-security' for e in r.get('environments', [])) for r in approvals): raise Denied('INITIAL_BOOTSTRAP_NOT_VERIFIED')
        # Close the legacy key's environment before automatic operations are armed.
        if not owner_protected(github(p, '/environments/staging'), p['ownerId']): raise Denied('LEGACY_ENVIRONMENT_NOT_PROTECTED')
        self.fresh_verify()
        from hosts.gamerhq.skill_staging import artifact_fingerprint
        proof = json.loads((Path(self.profile['workRoot']) / 'initial-candidate.json').read_text())
        current = self.inspect()
        if approved_image:
            if current['Image'] != approved_image: raise Denied('UNKNOWN_IMAGE_REQUIRES_OWNER')
        elif proof.get('healthy') is not True or proof.get('gatewayStarted') is not False or proof.get('image') != current['Image'] or proof.get('fingerprint') != artifact_fingerprint(self.profile): raise Denied('INITIAL_IMAGE_NOT_VERIFIED')
        return current['Image']

    def prepare(self, candidate):
        self.storage_budget()
        from tools.staging_configuration import atomic_json
        from hosts.gamerhq.skill_release_artifacts import fetch_release
        from hosts.gamerhq.skill_staging import verify_offline
        from tools.skill_staging import write_record, backend_for
        current = json.loads(Path(self.profile['releaseFile']).read_text())
        candidate = candidate or current
        release_allowed(candidate, current, self.profile)
        # Published tag -> immutable reviewed commit -> publisher SHA256SUMS -> bytes.
        # The caller cannot substitute a file, URL, publisher, capability or series.
        fetch_release(candidate, artifact_root=self.profile['artifactRoot'], trusted_repositories=self.profile['trustedRepositories'], token=os.environ.get(self.profile['releaseTokenEnv']))
        sdk = json.loads(Path(self.profile['sdkReleaseFile']).read_text())
        fetch_release(sdk, artifact_root=self.profile['artifactRoot'], trusted_repositories=self.profile['trustedRepositories'], token=os.environ.get(self.profile['releaseTokenEnv']))
        atomic_json(Path(self.profile['releaseFile']), candidate)
        write_record(self.profile['offlineVerificationFile'], verify_offline(self.profile))
        filename = candidate['distribution'].replace('-', '_') + '-' + candidate['version'] + '-py3-none-any.whl'
        proof = backend_for(self.profile, None).stage(release=candidate, wheel=Path(self.profile['artifactRoot']) / filename, deployment_id='automation-' + uuid.uuid4().hex)
        if proof.get('healthy') is not True or proof.get('gatewayStarted') is not False or proof.get('isolated') is not True or proof.get('artifactSha256') != candidate['sha256'] or proof.get('runtimeApi') != '1' or not set(proof.get('capabilities', [])) <= set(candidate['capabilities']): raise Denied('OFFLINE_PROBE_FAILED')
        return {'state': 'PREPARED', 'version': candidate['version']}

    def skill(self):
        from tools.skill_staging import host_request
        return host_request(self.profile, f"/api/v1/servers/{self.profile['stagingGuildId']}/skills/event-manager")

    def install(self):
        self.storage_budget()
        self.fresh_verify()
        if self.health()['state'] != 'HOST_READY': raise Denied('HOST_NOT_READY')
        if self.skill().get('state', {}).get('installed'): return {'state': 'ALREADY_INSTALLED'}
        release = json.loads(Path(self.profile['releaseFile']).read_text())
        if self.skill().get('version') != release['version']: raise Denied('PACKAGE_UPDATE_REQUIRED')
        # SQLite's backup API takes a consistent copy, never a second authority.
        target = Path(self.profile['backupRoot']) / ('automation-install-' + uuid.uuid4().hex + '.db')
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
        try:
            with sqlite3.connect(Path(self.profile['database']).resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(target) as backup: source.backup(backup)
            from tools.skill_staging import host_request
            host_request(self.profile, f"/api/v1/servers/{self.profile['stagingGuildId']}/skills/event-manager/install", payload={})
        except Exception: raise Denied('RECOVERY_REQUIRES_OWNER') from None
        return {'state': 'INSTALLED'}

    def update(self):
        self.storage_budget()
        from database import db
        from hosts.gamerhq.skill_artifacts import UpdatePolicy
        from .deployment import SkillUpdateController
        from tools.skill_staging import backend_for
        from tools.skill_deployment_worker import health_probe
        self.fresh_verify()
        current = self.skill()['version']; release = json.loads(Path(self.profile['releaseFile']).read_text())
        if current == release['version']: return {'state': 'NO_UPDATE'}
        old = {**release, 'version': current}
        release_allowed(release, old, self.profile)
        db.DB_PATH = Path(self.profile['database']).resolve(strict=True)
        backend = backend_for(self.profile, lambda version: health_probe(self.profile, {**release, 'version': version}))
        controller = SkillUpdateController(backend, backup_root=self.profile['backupRoot'])
        filename = release['distribution'].replace('-', '_') + '-' + release['version'] + '-py3-none-any.whl'
        settings = {**self.profile.get('updatePolicy', {}), 'automatic_compatible': True}
        staged = controller.stage(release=release, wheel=Path(self.profile['artifactRoot']) / filename, current_version=current,
            current_capabilities=self.profile['approvedCapabilities']['event-manager'], policy=UpdatePolicy(**settings))
        if staged['state'] != 'READY': raise Denied('DEPLOYMENT_NOT_READY')
        try: applied = controller.apply(staged['id'], approval='APPROVE ' + staged['id'])
        except Exception:
            if controller.get(staged['id'])['state'] == 'RECOVERY_REQUIRED': raise Denied('RECOVERY_REQUIRES_OWNER') from None
            raise Denied('UPDATE_FAILED') from None
        return {'state': applied['state'], 'deploymentId': applied['id'], 'image': self.inspect()['Image']}

    def restart(self, expected_image, recover=False):
        current = self.inspect()
        if current['Image'] != expected_image: raise Denied('UNKNOWN_IMAGE_REQUIRES_OWNER')
        if recover and current['State'].get('Running') and current['State'].get('Health', {}).get('Status') == 'healthy': return {'state': 'ALREADY_HEALTHY'}
        self.fresh_verify()
        # Never creates an absent container, changes image, restores data or bootstraps.
        self.run(['docker', 'restart', '--time', '30', 'gamerhq-staging-bot'], timeout=90)
        for _ in range(12):
            try:
                if self.health()['state'] == 'HOST_READY': return {'state': 'RECOVERED' if recover else 'RESTARTED'}
            except Exception: pass
            time.sleep(5)
        raise Denied('RESTART_HEALTH_FAILED')

    def history(self):
        from database import db
        from hosts.gamerhq.skill_update_controller import deployment_history
        db.DB_PATH = Path(self.profile['database']).resolve(strict=True)
        return {'state': 'HISTORY', 'deployments': [{k: r[k] for k in ('id', 'version', 'state', 'updated_at')} for r in deployment_history(skill_id='event-manager')]}

    def smoke(self):
        if self.health()['state'] != 'HOST_READY': raise Denied('HOST_NOT_READY')
        skill = self.skill()
        return {'state': 'SMOKE_PASS', 'skillVersion': skill['version'], 'installed': bool(skill.get('state', {}).get('installed')),
            'hostCommit': self.inspect()['Config'].get('Labels', {}).get('org.opencontainers.image.revision')}
