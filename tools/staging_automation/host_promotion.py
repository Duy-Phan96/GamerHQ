"""Owner-pinned Host overlay using the existing staged deployment controller.

Never replaces the trusted worker, its dependencies, SSH policy or credentials.
The current immutable image supplies unchanged dependencies and the installed
Skill. Only exact, checksum-bound Git application source is overlaid.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import tarfile
import tempfile
from contextlib import closing

from .deployment import SkillUpdateController, application_fingerprint
from .policy import Denied

COMMIT = '17858f47480e914956dd241b9f37a328b4c6ac2b'
BASELINE = '6a2c74a234d693abe70f5f69dfbb41dbce38bc75'
CI_RUN = '42013244636'
REPOSITORY = 'https://github.com/Duy-Phan96/GamerHQ.git'
DIRECTORIES = ('cogs', 'skill_runtime', 'hosts', 'skills', 'services', 'database', 'tools')
FILES = ('bot.py', 'config.py', 'release_info.py', 'VERSION', 'CHANGELOG.md', '.env.example', 'data/games_seed.json')
COMPATIBILITY = ('Dockerfile', 'requirements.txt', 'requirements.lock', 'requirements-skills.lock', 'pyproject.toml', 'database/db.py')
REVIEWED_HOST_CHANGES = frozenset({
    'hosts/gamerhq/skill_resources.py',
    'hosts/gamerhq/skill_update_controller.py',
    'hosts/gamerhq/web_platform.py',
})


def verify_compatibility(git):
    for path in (*COMPATIBILITY, 'database', 'skill_runtime', 'skills', 'services'):
        if git('rev-parse', BASELINE + ':' + path) != git('rev-parse', COMMIT + ':' + path):
            raise Denied('MIGRATION_REQUIRES_OWNER')
    changed = git('diff', '--no-renames', '--name-only', '-z', BASELINE, COMMIT, '--', 'hosts')
    if frozenset(changed.decode().rstrip('\0').split('\0')) != REVIEWED_HOST_CHANGES:
        raise Denied('MIGRATION_REQUIRES_OWNER')


def extract_application(archive, destination):
    """No links, special files or runtime/configuration mounts in the build context."""
    total = 0
    with tarfile.open(archive) as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name:
                raise Denied('PAYLOAD_REJECTED')
            selected = member.name in FILES or path.parts[0] in DIRECTORIES
            if not selected:
                continue
            if member.isdir():
                continue
            if not member.isfile():
                raise Denied('PAYLOAD_REJECTED')
            total += member.size
            if total > 32 * 1024 * 1024:
                raise Denied('REQUEST_LIMIT')
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as incoming, target.open('xb') as output:
                output.write(incoming.read())


def retained_state(path, guild):
    """Private comparison only; return no participant/configuration values publicly."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise Denied('RECOVERY_REQUIRES_OWNER')
        authority = conn.execute("SELECT * FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'", (guild,)).fetchall()
        installed = conn.execute("SELECT installed,configured,enabled,version FROM skill_guild_state WHERE guild_id=? AND skill_id='event-manager'", (guild,)).fetchone()
        if len(authority) != 1 or authority[0][2] != 'event-manager' or not installed or installed[0] != 1 or installed[3] != '0.1.0':
            raise Denied('RECOVERY_REQUIRES_OWNER')
        rows = {}
        for key, raw in conn.execute("SELECT storage_key,value_json FROM skill_storage WHERE guild_id=? AND skill_id='event-manager'", (guild,)):
            value = json.loads(raw)
            if key.startswith('event.v2:'):
                value = {k: v for k, v in value.items() if k != 'revision'}
            elif key.startswith('occurrence.v2:'):
                # Status/revision can naturally advance at startup; preserve
                # all membership, attendance, configuration and identity fields.
                value = {k: v for k, v in value.items() if k not in ('revision', 'status', 'voice')}
            rows[key] = value
        mappings = {}
        for key, raw in conn.execute("SELECT resource_key,state_json FROM skill_managed_resources "
                "WHERE guild_id=? AND skill_id='event-manager' AND kind='text'", (guild,)):
            value = json.loads(raw)
            if value.get('channelId'):
                mappings[key] = {field: value[field] for field in ('channelId', 'messageId', 'marker')
                                 if value.get(field) is not None}
        return {'authority': authority, 'installed': installed, 'rows': rows, 'mappings': mappings}


def require_retained(before, after):
    if (before['authority'] != after['authority'] or before['installed'] != after['installed']
            or any(after['rows'].get(k) != v for k, v in before['rows'].items())
            or any(after['mappings'].get(key, {}).get(field) != value
                   for key, mapping in before['mappings'].items() for field, value in mapping.items())):
        raise Denied('RECOVERY_REQUIRES_OWNER')


class PromotionDocker:
    """Reuse the existing Docker adapter with stronger Host/backup health gates."""
    def __init__(self, backend, docker, commit):
        self.backend, self.docker, self.commit = backend, docker, commit
        self.before = None

    def __getattr__(self, name):
        return getattr(self.docker, name)

    def verify_backup(self, backup):
        self.before = retained_state(backup, self.backend.profile['stagingGuildId'])
        with tempfile.TemporaryDirectory(dir=self.backend.profile['workRoot'], prefix='host-restore-probe-') as tmp:
            clone = Path(tmp) / 'restore.sqlite'
            with closing(sqlite3.connect(Path(backup).resolve().as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(clone)) as target:
                source.backup(target)
            require_retained(self.before, retained_state(clone, self.backend.profile['stagingGuildId']))
            if application_fingerprint(clone) != application_fingerprint(backup):
                raise Denied('RECOVERY_REQUIRES_OWNER')
        return {'integrity': 'PASS', 'restoreProbe': 'PASS', 'skillAuthority': 'event-manager'}

    def healthy(self, *, expected_version):
        if not self.docker.healthy(expected_version=expected_version):
            return False
        # Rollback uses the unchanged previous image; require retention then too.
        require_retained(self.before, retained_state(self.backend.profile['database'], self.backend.profile['stagingGuildId']))
        return self.backend.health()['state'] == 'HOST_READY'


def source_commit(backend, image):
    # No Gateway, mounts, credentials, network or Docker socket in this probe.
    return backend.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--user', '10001:10001', '--memory', '128m', '--cpus', '0.5', '--pids-limit', '32',
        '--entrypoint', 'python', image, '-c', "from pathlib import Path; print(Path('/app/BUILD_COMMIT').read_text().strip())"])


def build_overlay(backend, current):
    policy, p = backend.policy['hostPromotion'], backend.profile
    if policy['commit'] != COMMIT or source_commit(backend, current) != BASELINE:
        raise Denied('IMMUTABLE_RELEASE_CHANGED')
    with tempfile.TemporaryDirectory(dir=p['workRoot'], prefix='host-source-') as directory:
        root = Path(directory)
        repo = root / 'repository'; repo.mkdir()
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
        def git(*args):
            result = subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-C', str(repo), *args], env=env, capture_output=True, check=True, timeout=180)
            if len(result.stdout) > 262144:
                raise Denied('PUBLIC_METADATA_LIMIT')
            return result.stdout
        git('init', '--quiet'); git('remote', 'add', 'origin', REPOSITORY)
        for commit in (COMMIT, BASELINE):
            git('fetch', '--quiet', '--depth=1', 'origin', commit)
        if git('rev-parse', COMMIT).decode().strip() != COMMIT:
            raise Denied('IMMUTABLE_RELEASE_CHANGED')
        verify_compatibility(git)
        archive = root / 'source.tar'
        git('archive', '--format=tar', '--output=' + str(archive), COMMIT)
        if archive.stat().st_size > 32 * 1024 * 1024 or hashlib.sha256(archive.read_bytes()).hexdigest() != policy['archiveSha256']:
            raise Denied('IMMUTABLE_RELEASE_CHANGED')
        context = root / 'context'; context.mkdir()
        extract_application(archive, context)
        alias = 'gamerhq-host-promotion-base:' + current.split(':')[-1]
        backend.run(['docker', 'tag', current, alias])
        if backend.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', alias]) != current:
            raise Denied('IMMUTABLE_RELEASE_CHANGED')
        # Full application tree from the exact commit; installed wheels and all
        # dependencies stay in the approved current image, outside this overlay.
        dockerfile = f'FROM {alias}\nUSER root\n'
        for path in FILES:
            dockerfile += f'COPY --chown=10001:10001 {path} /app/{path}\n'
        for path in DIRECTORIES:
            dockerfile += f'COPY --chown=10001:10001 {path}/ /app/{path}/\n'
        dockerfile += f'RUN printf \'%s\\n\' {COMMIT} > /app/BUILD_COMMIT\nLABEL org.opencontainers.image.revision={COMMIT}\nUSER 10001:10001\n'
        (context / 'Dockerfile').write_text(dockerfile, encoding='utf-8')
        identity = root / 'image-id'
        backend.run(['docker', 'build', '--network', 'none', '--iidfile', str(identity), str(context)], timeout=600)
        from hosts.gamerhq.skill_deployment_docker import IMAGE
        image = identity.read_text().strip()
        if not IMAGE.fullmatch(image) or backend.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', alias]) != current or source_commit(backend, image) != COMMIT:
            raise Denied('IMMUTABLE_RELEASE_CHANGED')
        return image


def promote(backend, *, expected_image):
    from database import db
    from tools.skill_staging import backend_for
    from tools.skill_deployment_worker import health_probe
    from hosts.gamerhq.skill_staging import verify_local_artifacts
    from tools.staging_configuration import atomic_json
    from .oidc import github
    if 'hostPromotion' not in backend.policy:
        raise Denied('OPERATION_NOT_ALLOWED')
    p = backend.profile
    backend.storage_budget(); backend.fresh_verify()
    current = backend.inspect()['Image']
    if current != expected_image:
        raise Denied('UNKNOWN_IMAGE_REQUIRES_OWNER')
    if backend.health()['state'] != 'HOST_READY':
        raise Denied('HOST_NOT_READY')
    retained_state(p['database'], p['stagingGuildId'])
    verify_local_artifacts(p)
    release = json.loads(Path(p['releaseFile']).read_text())
    if release['version'] != '0.1.0' or backend.skill()['version'] != '0.1.0':
        raise Denied('VERSION_REQUIRES_OWNER')
    # Source trust includes private-wheel CI in the reviewed Host PR. Verify
    # public Host CI live without assuming private GitHub token permissions.
    run = github(backend.policy, '/actions/runs/' + CI_RUN)
    if run.get('head_sha') != COMMIT or run.get('conclusion') != 'success':
        raise Denied('OFFLINE_PROBE_FAILED')
    if source_commit(backend, current) == COMMIT:
        if p.get('applicationHostCommit') != COMMIT or p['baseImage'] != current:
            raise Denied('RECOVERY_REQUIRES_OWNER')
        return {'state': 'ALREADY_CURRENT', 'hostCommit': COMMIT, 'skillVersion': '0.1.0', 'image': current, 'health': 'HOST_READY'}
    db.DB_PATH = Path(p['database']).resolve(strict=True)
    with db.connect() as conn:
        pending = conn.execute("SELECT evidence_json FROM skill_deployments WHERE state IN ('APPLYING','RECOVERY_REQUIRED')").fetchall()
        if any(json.loads(row[0]).get('hostSourceCommit') for row in pending):
            raise Denied('RECOVERY_REQUIRES_OWNER')
    try:
        overlay = build_overlay(backend, current)
    except Denied:
        raise
    except Exception:
        raise Denied('HOST_SOURCE_BUILD_FAILED') from None
    candidate = {**p, 'baseImage': overlay}
    docker = backend_for(candidate, lambda version: health_probe(p, {**release, 'version': version}))
    deployed = PromotionDocker(backend, docker, COMMIT)
    controller = SkillUpdateController(deployed, backup_root=p['backupRoot'])
    wheel = Path(p['artifactRoot']) / (release['distribution'].replace('-', '_') + '-0.1.0-py3-none-any.whl')
    ready = controller.stage(release=release, wheel=wheel, current_version='0.1.0', current_capabilities=p['approvedCapabilities']['event-manager'],
        manual_review=True, host_source_commit=COMMIT)
    if source_commit(backend, ready['image']) != COMMIT:
        raise Denied('IMMUTABLE_RELEASE_CHANGED')
    try:
        applied = controller.apply(ready['id'], approval='APPROVE ' + ready['id'])
        if backend.inspect()['Image'] != applied['image'] or source_commit(backend, applied['image']) != COMMIT:
            raise Denied('RECOVERY_REQUIRES_OWNER')
        # Future Skill updates inherit this accepted Host, never the old UI.
        updated = {**p, 'baseImage': applied['image'], 'applicationHostCommit': COMMIT}
        from tools.staging_dispatch import PROFILE
        atomic_json(PROFILE, updated)
        backend.profile = updated
        backend.fresh_verify()
    except Exception:
        if controller.get(ready['id'])['state'] == 'ROLLED_BACK':
            raise Denied('UPDATE_FAILED') from None
        raise Denied('RECOVERY_REQUIRES_OWNER') from None
    return {'state': 'APPLIED', 'hostCommit': COMMIT, 'skillVersion': '0.1.0', 'health': 'HOST_READY',
        'deploymentId': ready['id'], 'backupVerified': True, 'restoreProbe': 'PASS', 'authorityPreserved': True, 'image': applied['image']}
