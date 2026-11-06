from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from tools.staging_automation import web
from tools.staging_automation.bridge import execute
from tools.staging_automation.policy import Denied


URL = 'https://productionresultssa4.blob.core.windows.net/archive.zip?sig=test'


@pytest.fixture
def pin():
    return {
        **json.loads(Path('deploy/staging/web-candidate.json').read_text()),
        'hostname': 'staging.synthetic.net',
        'workflowSha256': 'e' * 64,
    }


@pytest.mark.parametrize('url', [
    'http://productionresultssa4.blob.core.windows.net/archive.zip?sig=test',
    'https://productionresultssa4.blob.core.windows.net@127.0.0.1/archive.zip?sig=test',
    'https://productionresultssa4.blob.core.windows.net.invalid/archive.zip?sig=test',
    'https://productionresultssa4.blob.core.windows.net/archive.zip',
    'https://127.0.0.1/archive.zip?sig=test',
])
def test_artifact_origin_is_restricted(url):
    with pytest.raises(Denied, match='WEB_ARTIFACT_REJECTED'):
        web.artifact_url(url)


def test_artifact_requires_pinned_archive_source_and_checksums(tmp_path, monkeypatch, pin):
    files = {
        'IMAGE_ID': pin['imageId'].encode() + b'\n',
        'SOURCE_COMMIT': pin['commit'].encode() + b'\n',
        'gamerhq-web.tar.gz': b'synthetic image archive',
    }
    archive = io.BytesIO()
    with ZipFile(archive, 'w') as bundle:
        for name, contents in files.items():
            bundle.writestr(name, contents)
        bundle.writestr('SHA256SUMS', '\n'.join(f'{sha256(body).hexdigest()}  {name}' for name, body in files.items()))
    blob = archive.getvalue()
    monkeypatch.setattr(web, 'build_opener', lambda redirect: SimpleNamespace(open=lambda *_args, **_kwargs: io.BytesIO(blob)))
    with pytest.raises(Denied, match='WEB_ARTIFACT_REJECTED'):
        web.download(pin, URL, tmp_path)
    (tmp_path / 'artifact.zip').unlink()
    pin['archiveSha256'] = sha256(blob).hexdigest()
    assert web.download(pin, URL, tmp_path).read_bytes() == files['gamerhq-web.tar.gz']


def test_web_needs_enrollment_and_rejects_caller_control_fields(pin):
    state = {'enabled': True, 'bootstrapVerified': True}
    with pytest.raises(Denied, match='WEB_NOT_ENROLLED'):
        execute('challenge', {'operation': 'web-deploy'}, {}, state, None, lambda _: None)
    with pytest.raises(Denied, match='PAYLOAD_REJECTED'):
        execute('web-deploy', {'token': 'synthetic', 'challenge': 'synthetic', 'port': 443},
                {'webDeployment': pin}, state, None, lambda _: None)


def test_unhealthy_candidate_restores_running_web_and_cannot_be_rolled_back_to(monkeypatch, pin):
    first, second = web.SLOTS
    state = {'webActive': first}
    rows = {first: {'running': True}, second: {'running': False}}
    deployment = web.WebDeployment(None, pin, state, lambda _: None)
    monkeypatch.setattr(deployment, 'slots', lambda: rows)
    monkeypatch.setattr(deployment, 'stop', lambda row: row.update(running=False))

    def start(row):
        row['running'] = True
        if row is rows[second]:
            raise Denied('WEB_HEALTH_FAILED')

    monkeypatch.setattr(deployment, 'start', start)
    with pytest.raises(Denied, match='WEB_HEALTH_FAILED'):
        deployment.select(second, rows)
    assert rows[first]['running'] and not rows[second]['running']
    assert state['webActive'] == first
    with pytest.raises(Denied, match='WEB_ROLLBACK_UNAVAILABLE'):
        deployment.rollback()


@pytest.mark.skipif(os.environ.get('GAMERHQ_WEB_CONTROLLER_DOCKER_CI') != 'true'
                    or os.environ.get('GITHUB_ACTIONS') != 'true', reason='requires disposable Actions Docker daemon')
def test_disposable_docker_switch_preserves_host_and_restores_previous_web(tmp_path, pin):
    def docker(*args, stdin=None):
        return subprocess.run(['docker', *args], input=stdin, text=True, capture_output=True, check=True, timeout=90).stdout.strip()

    backend = SimpleNamespace(run=lambda args, timeout=30, stdin=None: docker(*args[1:], stdin=stdin),
                              profile={'workRoot': str(tmp_path)})
    state = {}
    deployment = web.WebDeployment(backend, pin, state, lambda _: None)
    (tmp_path / 'server.js').write_text('''
require('http').createServer((req,res) => res.end(JSON.stringify({
  service:'gamerhq-web', status:'ok', version:process.env.GAMERHQ_WEB_VERSION
}))).listen(3000, '0.0.0.0');
''')
    (tmp_path / 'Dockerfile').write_text('FROM node:24-alpine\nWORKDIR /app\nCOPY server.js .\n'
        'RUN mkdir scripts && touch scripts/production-preflight.mjs\n')
    docker('build', '-t', 'gamerhq-web-staging-fixture', str(tmp_path))
    image = docker('image', 'inspect', '--format', '{{.Id}}', 'gamerhq-web-staging-fixture')
    pin['imageId'] = image
    docker('network', 'create', web.NETWORK)
    try:
        sentinel = docker('run', '-d', '--name', 'gamerhq-staging-bot', '--network', web.NETWORK,
                          'gamerhq-web-staging-fixture', 'sleep', '300')
        identity = docker('inspect', '--format', '{{.Id}} {{.State.StartedAt}}', sentinel)
        first, second = web.SLOTS
        for name in (first, second):
            deployment.create(name, {'GAMERHQ_WEB_VERSION': 'ci-' + pin['commit']})
            assert deployment.select(name, deployment.slots())['webCommit'] == pin['commit']
            pin['commit'] = 'b' * 40
        assert deployment.rollback()['webCommit'] != pin['commit']
        assert docker('inspect', '--format', '{{.Id}} {{.State.StartedAt}}', sentinel) == identity
        assert docker('port', first, '3000/tcp') == web.PORT

        # Exercise the reviewed proxy route over certificate-verified TLS. The
        # local CA and high port are only for this disposable runner.
        template = Path('deploy/staging/web.Caddyfile').read_text()
        edge = tmp_path / 'Caddyfile'
        hostname = pin['hostname']
        edge.write_text('{\n\tauto_https disable_redirects\n}\n' +
                        template.replace('{$GAMERHQ_WEB_STAGING_HOSTNAME} {', f'{hostname}:14443 {{\n\ttls internal'))
        docker('run', '-d', '--name', 'gamerhq-staging-web-edge-ci', '--network', 'host',
               '--volume', str(edge) + ':/etc/caddy/Caddyfile:ro', 'caddy:2-alpine')
        certificate = tmp_path / 'edge-ca.pem'
        for _ in range(20):
            try:
                certificate.write_text(docker('exec', 'gamerhq-staging-web-edge-ci', 'cat',
                                              '/data/caddy/pki/authorities/local/root.crt'))
                break
            except subprocess.CalledProcessError:
                time.sleep(1)
        else:
            pytest.fail('Disposable TLS edge did not create its local CA.')
        curl = ['curl', '--silent', '--show-error', '--noproxy', '*', '--cacert', str(certificate),
                '--resolve', f'{hostname}:14443:127.0.0.1', '--max-time', '10']
        origin = f'https://{hostname}:14443'
        result = subprocess.check_output([*curl, '--fail', origin + '/'], text=True)
        assert json.loads(result)['service'] == 'gamerhq-web'
        result = subprocess.check_output([*curl, '--output', '/dev/null', '--write-out', '%{http_code}',
                                          origin + '/api/health'], text=True)
        assert result == '404'
    finally:
        subprocess.run(['docker', 'rm', '-f', *web.SLOTS, 'gamerhq-staging-bot', 'gamerhq-staging-web-edge-ci'],
                       capture_output=True, timeout=30)
        docker('network', 'rm', web.NETWORK)
