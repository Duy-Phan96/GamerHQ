"""Deploy only a reviewed Web image to the existing staging rootless daemon."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import build_opener
from zipfile import ZipFile

from .oidc import NoRedirects
from .policy import Denied


REPOSITORY = 'Duy-Phan96/gamerhq-web'
NETWORK = 'gamerhq-staging-private'
SLOTS = ('gamerhq-web-staging-a', 'gamerhq-web-staging-b')
PORT = '127.0.0.1:13000'
MAX_ARCHIVE = 160 * 1024**2
MIN_RAM_MIB = 384
MIN_DISK_MIB = 2048
OAUTH_FILE = Path('/etc/gamerhq-staging/web-oauth.json')
LABEL = 'io.gamerhq.staging.web.commit'


def validate_pin(pin):
    fields = {'commit', 'imageId', 'archiveSha256', 'artifactId', 'runId', 'workflowSha256', 'hostname'}
    if not isinstance(pin, dict) or set(pin) != fields:
        raise Denied('WEB_POLICY_REJECTED')
    for key, pattern in {
        'commit': '[0-9a-f]{40}', 'imageId': 'sha256:[0-9a-f]{64}', 'archiveSha256': '[0-9a-f]{64}',
        'workflowSha256': '[0-9a-f]{64}', 'artifactId': '[1-9][0-9]{0,19}', 'runId': '[1-9][0-9]{0,19}',
        'hostname': r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}',
    }.items():
        if not isinstance(pin[key], str) or not re.fullmatch(pattern, pin[key]):
            raise Denied('WEB_POLICY_REJECTED')
    if pin['hostname'].endswith(('.local', '.localhost', '.test', '.invalid', '.example')):
        raise Denied('WEB_POLICY_REJECTED')


def artifact_url(url):
    if not isinstance(url, str) or len(url) > 3000 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise Denied('WEB_ARTIFACT_REJECTED')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not re.fullmatch(r'productionresults[a-z0-9]+\.blob\.core\.windows\.net', parsed.netloc)
            or not parsed.path.startswith('/') or not parsed.query or parsed.fragment):
        raise Denied('WEB_ARTIFACT_REJECTED')
    return url


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def download(pin, url, root):
    archive = root / 'artifact.zip'
    total = 0
    deadline = time.monotonic() + 180
    with build_opener(NoRedirects()).open(artifact_url(url), timeout=15) as source, archive.open('xb') as output:
        for chunk in iter(lambda: source.read(1024**2), b''):
            total += len(chunk)
            if total > MAX_ARCHIVE or time.monotonic() > deadline:
                raise Denied('WEB_ARTIFACT_REJECTED')
            output.write(chunk)
    if digest(archive) != pin['archiveSha256']:
        raise Denied('WEB_ARTIFACT_REJECTED')
    with ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if (len(entries) != 4 or {entry.filename for entry in entries} !=
                {'IMAGE_ID', 'SOURCE_COMMIT', 'SHA256SUMS', 'gamerhq-web.tar.gz'}
                or any(entry.file_size > (MAX_ARCHIVE if entry.filename.endswith('.gz') else 1024) for entry in entries)):
            raise Denied('WEB_ARTIFACT_REJECTED')
        if (bundle.read('IMAGE_ID').decode().strip() != pin['imageId']
                or bundle.read('SOURCE_COMMIT').decode().strip() != pin['commit']):
            raise Denied('WEB_ARTIFACT_REJECTED')
        image = root / 'image.tar.gz'
        with bundle.open('gamerhq-web.tar.gz') as source, image.open('xb') as output:
            shutil.copyfileobj(source, output)
        entries = [line.split() for line in bundle.read('SHA256SUMS').decode().splitlines()]
        checksums = {name: checksum for checksum, name in entries}
        expected = {name: hashlib.sha256(bundle.read(name)).hexdigest() for name in ('IMAGE_ID', 'SOURCE_COMMIT')}
        expected['gamerhq-web.tar.gz'] = digest(image)
        if len(entries) != 3 or checksums != expected:
            raise Denied('WEB_ARTIFACT_REJECTED')
    return image


def capacity(work_root, docker_root):
    available = next(int(line.split()[1]) // 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    disk = min(shutil.disk_usage(path).free // 1024**2 for path in (work_root, docker_root))
    return {'availableMemoryMiB': available, 'availableDiskMiB': disk,
            'minimumAvailableMemoryMiB': MIN_RAM_MIB, 'minimumAvailableDiskMiB': MIN_DISK_MIB,
            'capacityReady': available >= MIN_RAM_MIB and disk >= MIN_DISK_MIB}


def oauth_environment(pin, commit):
    fd = os.open(OAUTH_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o037 or info.st_nlink != 1 or info.st_size > 4096:
            raise Denied('WEB_CONFIGURATION_REJECTED')
        settings = json.load(source)
    if (set(settings) != {'discordClientId', 'discordClientSecret', 'sessionSecret'}
            or not re.fullmatch(r'[0-9]{17,20}', settings['discordClientId'])
            or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', settings['discordClientSecret'])
            or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', settings['sessionSecret'])):
        raise Denied('WEB_CONFIGURATION_REJECTED')
    secret = os.environ.get('GAMERHQ_STAGING_WEB_API_SECRET', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret):
        raise Denied('WEB_CONFIGURATION_REJECTED')
    return {
        'NODE_ENV': 'production',
        'DISCORD_CLIENT_ID': settings['discordClientId'],
        'DISCORD_CLIENT_SECRET': settings['discordClientSecret'],
        'SESSION_SECRET': settings['sessionSecret'],
        'DISCORD_REDIRECT_URI': 'https://' + pin['hostname'] + '/api/auth/discord/callback',
        'GAMERHQ_HOST_API_URL': 'http://gamerhq-staging:8080',
        'GAMERHQ_HOST_API_SECRET': secret,
        'GAMERHQ_WEB_VERSION': 'ci-' + commit,
    }


class WebDeployment:
    def __init__(self, backend, pin, state, save):
        self.backend, self.pin, self.state, self.save = backend, pin, state, save

    def docker(self, *args, timeout=30):
        return self.backend.run(['docker', *args], timeout=timeout)

    def slots(self):
        rows = {}
        for name in SLOTS:
            container = self.docker('container', 'ls', '--all', '--no-trunc', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}')
            if not container:
                continue
            if not re.fullmatch(r'[0-9a-f]{64}', container):
                raise Denied('WEB_CONTAINER_REJECTED')
            item, = json.loads(self.docker('container', 'inspect', container))
            config, limits = item['Config'], item['HostConfig']
            commit = config.get('Labels', {}).get(LABEL, '')
            if (item['Name'] != '/' + name or item['Id'] != container or not re.fullmatch(r'[0-9a-f]{40}', commit)
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', item['Image']) or config['User'] != '1001:1001'
                    or not limits['ReadonlyRootfs'] or limits['Privileged'] or limits.get('CapAdd')
                    or limits.get('CapDrop') != ['ALL'] or item['Mounts'] or limits['NetworkMode'] != NETWORK
                    or limits.get('SecurityOpt') != ['no-new-privileges:true']
                    or limits.get('RestartPolicy', {}).get('Name') != 'no'
                    or set(item['NetworkSettings']['Networks']) != {NETWORK}
                    or limits['PortBindings'] != {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '13000'}]}
                    or limits['Memory'] != 256 * 1024**2 or limits['MemorySwap'] != limits['Memory']
                    or limits['NanoCpus'] != 500000000 or limits['PidsLimit'] != 96):
                raise Denied('WEB_CONTAINER_REJECTED')
            rows[name] = {'id': container, 'imageId': item['Image'], 'commit': commit, 'running': item['State']['Running']}
        return rows

    def budget(self):
        from hosts.gamerhq.staging_rootless import verify_rootless
        info = verify_rootless(self.backend.profile)
        return capacity(self.backend.profile['workRoot'], info['DockerRootDir'])

    def probe(self, row, attempts=1):
        for attempt in range(attempts):
            try:
                with build_opener(NoRedirects()).open('http://' + PORT + '/api/health', timeout=3) as response:
                    health = json.loads(response.read(4096))
                if (health.get('service'), health.get('status'), health.get('version')) != ('gamerhq-web', 'ok', 'ci-' + row['commit']):
                    raise Denied('WEB_HEALTH_FAILED')
                self.docker('exec', row['id'], 'node', 'scripts/production-preflight.mjs', timeout=20)
                return
            except Exception:
                if attempt + 1 < attempts:
                    time.sleep(2)
        raise Denied('WEB_HEALTH_FAILED')

    def create(self, name):
        env = oauth_environment(self.pin, self.pin['commit'])
        self.backend.run(['docker', 'create', '--name', name, '--label', LABEL + '=' + self.pin['commit'],
                    '--pull=never', '--network', NETWORK, '--publish', PORT + ':3000', '--user', '1001:1001',
                    '--init', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m,mode=1777',
                    '--tmpfs', '/app/.next/cache:rw,noexec,nosuid,size=32m,uid=1001,gid=1001,mode=0700',
                    '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--memory=256m',
                    '--memory-swap=256m', '--cpus=0.5', '--pids-limit=96', '--restart=no',
                    '--log-driver=json-file', '--log-opt=max-size=5m', '--log-opt=max-file=2',
                    '--env-file', '/dev/stdin', '--entrypoint', 'node', self.pin['imageId'], 'server.js'],
                    stdin=''.join(key + '=' + value + '\n' for key, value in env.items()))

    def stop(self, row):
        self.docker('stop', '--time', '10', row['id'])

    def start(self, row):
        self.docker('start', row['id'])
        self.probe(row, attempts=10)

    def select(self, target, rows):
        current = self.state.get('webActive')
        previous = rows.get(current)
        candidate = rows[target]
        try:
            for name, row in rows.items():
                if name != target and row['running']:
                    self.stop(row)
            if candidate['running']:
                self.probe(candidate, attempts=10)
            else:
                self.start(candidate)
        except Exception:
            try:
                fresh = self.slots()
                if fresh[target]['running'] and (target != current or not candidate['running']):
                    self.stop(fresh[target])
                if previous and current != target and not fresh[current]['running']:
                    self.start(previous)
            except Exception:
                raise Denied('WEB_RECOVERY_REQUIRED') from None
            raise Denied('WEB_HEALTH_FAILED') from None
        self.state['webActive'] = target
        if current is not None and current != target:
            self.state['webPrevious'] = current
        self.save(self.state)
        return {'state': 'WEB_HEALTHY', 'webCommit': candidate['commit'], 'imageId': candidate['imageId'],
                'rollbackAvailable': self.state.get('webPrevious') in rows}

    def deploy(self, url):
        rows = self.slots()
        current = self.state.get('webActive')
        if (current is not None and current not in rows) or any(name != current and row['running'] for name, row in rows.items()):
            raise Denied('WEB_RECOVERY_REQUIRED')
        if current and rows[current]['imageId'] == self.pin['imageId'] and rows[current]['commit'] == self.pin['commit']:
            return self.select(current, rows)
        if not self.budget()['capacityReady']:
            raise Denied('WEB_CAPACITY_REJECTED')
        target = next(name for name in SLOTS if name != current)
        with tempfile.TemporaryDirectory(prefix='web-deploy-', dir=self.backend.profile['workRoot']) as temp:
            root = Path(temp)
            image = download(self.pin, url, root)
            self.docker('image', 'load', '--input', str(image), timeout=180)
            if self.docker('image', 'inspect', '--format', '{{.Id}}', self.pin['imageId']) != self.pin['imageId']:
                raise Denied('WEB_ARTIFACT_REJECTED')
            if target in rows:
                self.state.pop('webPrevious', None)
                self.save(self.state)
                self.docker('container', 'rm', rows[target]['id'])
            self.create(target)
        rows = self.slots()
        if rows[target]['imageId'] != self.pin['imageId'] or rows[target]['commit'] != self.pin['commit']:
            raise Denied('WEB_CONTAINER_REJECTED')
        return self.select(target, rows)

    def rollback(self):
        rows = self.slots()
        current = self.state.get('webActive')
        if current not in rows:
            # A failed first deploy has no previous Web container to restore.
            raise Denied('WEB_ROLLBACK_UNAVAILABLE')
        if not rows[current]['running']:
            return self.select(current, rows)
        other = self.state.get('webPrevious')
        if other not in rows or other == current:
            raise Denied('WEB_ROLLBACK_UNAVAILABLE')
        return self.select(other, rows)

    def health(self):
        report = self.budget()
        rows = self.slots()
        row = rows.get(self.state.get('webActive'))
        if row is None or not row['running']:
            return {'state': 'WEB_NOT_RUNNING', **report}
        try:
            self.probe(row)
        except Denied:
            return {'state': 'WEB_UNHEALTHY', **report}
        return {'state': 'WEB_HEALTHY', 'webCommit': row['commit'], 'imageId': row['imageId'],
                'rollbackAvailable': self.state.get('webPrevious') in rows, **report}
