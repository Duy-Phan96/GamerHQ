"""Disposable Linux CI only: real rootless daemon, SSH, ACL and isolation checks.

Never run on the VPS. Only synthetic fixtures and a new localhost SSH server.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from .staging_bridge_protocol import encode


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
        raise PermissionError('Only a disposable root GitHub Actions runner is supported.')
    state = json.loads(Path('/var/lib/gamerhq-staging-bootstrap/state.json').read_text())
    uid = state['uid']
    def user(code):
        return run(['runuser', '-u', 'gamerhq-staging-bridge', '--', 'env',
            'HOME=/var/lib/gamerhq-staging-bridge', f'XDG_RUNTIME_DIR=/run/user/{uid}', f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',
            'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet', '--unit=staging-ci-' + uuid.uuid4().hex,
            '/opt/gamerhq-staging/deployment-worker/.venv/bin/python', '-I', '-B', '-c',
            "import sys; sys.path.insert(0,'/opt/gamerhq-staging/deployment-worker'); " + code])
    with tempfile.TemporaryDirectory(prefix='staging-sshd-ci-') as directory:
        root = Path(directory)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(root / 'host')], check=True)
        key = ' '.join((root / 'host.pub').read_text().split()[:2])
        (root / 'known_hosts').write_text('[127.0.0.1]:22222 ' + key + '\n')
        config = root / 'sshd_config'
        config.write_text(f'Port 22222\nListenAddress 127.0.0.1\nHostKey {root}/host\nPidFile {root}/pid\n'
            'UsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\n'
            'PermitRootLogin no\nAllowUsers gamerhq-staging-bridge\nStrictModes yes\nLogLevel ERROR\nPermitUserRC no\nDisableForwarding yes\n')
        server = subprocess.Popen(['/usr/sbin/sshd', '-D', '-f', str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            base = ['ssh', '-F', '/dev/null', '-T', '-p', '22222', '-i', str(args.key), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(root / 'known_hosts'), 'gamerhq-staging-bridge@127.0.0.1']
            inspect = encode({'version':1,'phase':'prepare','operation':'inspect'})
            result = run(base + [inspect], input='')
            if result.returncode:
                # Only a blank-credential disposable fixture opts into diagnostics.
                diagnostic = user("from tools.staging_dispatch import main; import io; sys.stdin=io.TextIOWrapper(io.BytesIO(b'{\"request\":{\"version\":1,\"phase\":\"prepare\",\"operation\":\"inspect\"},\"configuration\":null}')); main()")
                print('Disposable namespace diagnostic:', diagnostic.stdout, diagnostic.stderr)
                raise RuntimeError('Restricted rootless SSH inspection failed: ' + result.stderr[-1500:])
            assert json.loads(result.stdout)['productionChanged'] is False
            for rejected in ('id', 'bash', 'gamerhq-staging-v1 e30=', encode({'version':1,'phase':'prepare','operation':'build'})):
                assert run(base + [rejected], input='').returncode != 0
            assert run(['runuser','-u','gamerhq-staging-bridge','--','sudo','-n','/bin/true']).returncode != 0
            assert run(['id','-nG','gamerhq-staging-bridge']).stdout.strip() == 'gamerhq-staging-bridge'
            assert not Path('/etc/sudoers.d/gamerhq-staging-bridge').exists()
            isolated = user("import os; from tools.staging_dispatch import environment; os.environ.clear(); os.environ.update(environment()); "
                "from pathlib import Path; from hosts.gamerhq.staging_rootless import verify_rootless; import json,subprocess; "
                "p=json.loads(Path('/etc/gamerhq-staging/settings/profile.json').read_text()); verify_rootless(p); "
                "assert not os.access('/opt/gamerhq/data/production-fixture',os.R_OK); "
                "assert not os.access('/run/docker.sock',os.R_OK); "
                "assert 'gamerhq-production-fixture' not in subprocess.check_output(['docker','ps','-a','--format','{{.Names}}'],text=True); "
                "print('ROOTLESS_PRODUCTION_ISOLATION_PASS')")
            assert isolated.returncode == 0, isolated.stderr
            release = {'repository':'Duy-Phan96/gamerhq-skill-event-manager','distribution':'gamerhq-skill-event-manager','version':'0.1.0',
                'reviewedCommit':'a'*40,'sha256':'b'*64,'sourceReviewed':True,'skillId':'event-manager','runtimeApi':'1',
                'capabilities':['storage.skill','storage.atomic'],'migrationMode':'backward-compatible'}
            sdk = {k:release[k] for k in ('version','reviewedCommit','sha256','sourceReviewed')}
            sdk.update(repository='Duy-Phan96/GamerHQ', distribution='gamerhq-skill-sdk', version='0.3.0')
            payload = {k:str(i) for i,k in enumerate(('stagingBotId','stagingGuildId','productionBotId','productionGuildId','categoryId','voiceCategoryId'),1)}
            payload.update(discordToken='synthetic-staging-token-for-ci',releaseToken='synthetic-read-only-artifact-token',sdkRelease=sdk,skillRelease=release)
            initial = encode({'version':1,'phase':'configure','operation':'initial','approval':'CONFIGURE ISOLATED STAGING'})
            configured = run(base + [initial], input=json.dumps(payload))
            assert configured.returncode == 0, configured.stderr
            health = encode({'version':1,'phase':'configure','operation':'health'})
            healthy = run(base + [health], input='')
            assert healthy.returncode == 0 and json.loads(healthy.stdout)['status'] == 'READY_OFFLINE'
            acl = user("import os,subprocess; from tools.staging_dispatch import environment; os.environ.clear(); os.environ.update(environment()); "
                "subprocess.run(['docker','run','--rm','--network','none','--user','10001:10001','--volume','/opt/gamerhq-staging/data:/state',"
                "'python:3.12-slim','python','-c',\"open('/state/access-probe','w').write('synthetic-stage-data')\"],check=True); "
                "from pathlib import Path; assert Path('/opt/gamerhq-staging/data/access-probe').read_text()=='synthetic-stage-data'; print('ROOTLESS_SHARED_STORAGE_PASS')")
            assert acl.returncode == 0, acl.stderr
            profile = json.loads(Path('/etc/gamerhq-staging/settings/profile.json').read_text())
            assert profile['workerEnabled'] is False and not Path(profile['database']).exists()
            assert run(['docker','inspect','--format','{{.State.Running}}','gamerhq-production-fixture']).stdout.strip() == 'true'
            assert run(['docker','inspect','--format','{{.Id}}','gamerhq-production-fixture']).stdout.strip() == Path('/tmp/production-fixture-id').read_text().strip()
            audit = run(['journalctl','-t','gamerhq-staging','--since','-10min','--no-pager','--output=cat']).stdout
            assert 'READY_OFFLINE' in audit and payload['discordToken'] not in audit and payload['releaseToken'] not in audit
            revoke = encode({'version':1,'phase':'configure','operation':'revoke','approval':'REVOKE STAGING ACCESS'})
            assert run(base+[revoke],input='').returncode == 0
            assert run(base+[inspect],input='').returncode != 0
            print('PASS: immutable resumable bootstrap; rootless Docker; production file/socket/container isolation; no sudo; SSH shell rejection; stdin-only configuration; shared UID-10001 storage ACL; watchdog audit; durable revocation. No staging database or Gateway created.')
        finally:
            server.terminate(); server.wait(timeout=10)


if __name__ == '__main__':
    main()
