"""Disposable hosted-runner enrollment and real SSH capability-boundary test."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0: raise PermissionError('DISPOSABLE_CI_ONLY')
    from tools.enroll_staging_automation import enroll
    account = pwd.getpwnam('gamerhq-staging-bridge')
    root = Path('/opt/gamerhq-staging'); config = Path('/etc/gamerhq-staging')
    profile_path = config / 'settings/profile.json'; profile = json.loads(profile_path.read_text())
    profile['workerEnabled'] = True
    profile_path.write_text(json.dumps(profile)); os.chown(profile_path, account.pw_uid, account.pw_gid); profile_path.chmod(0o600)
    originals = {str(p): p.read_bytes() for p in (profile_path, config / 'policy.json', Path('/usr/local/libexec/gamerhq-staging-entry'), Path(account.pw_dir) / '.ssh/authorized_keys')}
    database = root / 'data/gamerhq.db'; database.write_bytes(b'synthetic existing staging database'); os.chown(database, account.pw_uid, account.pw_gid)
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='automation-boundary-') as tmp:
        tmp = Path(tmp)
        for name in ('client', 'host'):
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(tmp / name)], check=True)
        public = ' '.join((tmp / 'client.pub').read_text().split()[:2])
        for _ in range(2):
            receipt = enroll(args.commit, public, 'ENROLL RESTRICTED STAGING AUTOMATION')
            assert receipt['state'] == 'ENROLLED_INACTIVE' and receipt['legacyWorkerCommit'] == profile['hostSourceCommit']
            assert not any(receipt[k] for k in ('automationEnabled', 'gatewayStarted', 'productionChanged', 'existingCredentialsChanged', 'legacySshEntryChanged', 'sandboxChanged', 'workerSourceChanged'))
        for name, body in originals.items():
            current = Path(name).read_bytes()
            if name.endswith('authorized_keys'): assert current.startswith(body) and current.count(public.encode()) == 1
            else: assert current == body
        host = ' '.join((tmp / 'host.pub').read_text().split()[:2]); known = tmp / 'known_hosts'; known.write_text('[127.0.0.1]:22225 ' + host + '\n')
        sshd = tmp / 'sshd_config'
        sshd.write_text('Port 22225\nListenAddress 127.0.0.1\nHostKey '+str(tmp / 'host')+'\nPidFile '+str(tmp / 'pid')+'\nUsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nAllowUsers gamerhq-staging-bridge\nStrictModes yes\nPermitUserRC no\nDisableForwarding yes\n')
        server = subprocess.Popen(['/usr/sbin/sshd', '-D', '-f', str(sshd)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(30):
                try:
                    with socket.create_connection(('127.0.0.1', 22225), timeout=1): break
                except OSError: time.sleep(.1)
            def call(op, payload=None, prefix='gamerhq-staging-automation-v1'):
                req = {'version': 1, 'phase': 'automation', 'operation': op}
                command = prefix + ' ' + base64.b64encode(json.dumps(req).encode()).decode()
                return subprocess.run(['ssh', '-F', '/dev/null', '-T', '-p', '22225', '-i', str(tmp / 'client'), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+str(known), 'gamerhq-staging-bridge@127.0.0.1', command], input=json.dumps(payload) if payload else '', capture_output=True, text=True, timeout=90)
            status = call('status'); assert status.returncode == 0, status.stderr
            value = json.loads(status.stdout); assert value['state'] == 'NOT_ACTIVATED' and value['controllerCommit'] == args.commit
            assert call('bootstrap').returncode != 0
            assert call('credentials').returncode != 0
            assert call('status', prefix='gamerhq-staging-v1').returncode != 0
            challenge = json.loads(call('challenge', {'operation': 'activate'}).stdout)
            assert call('activate', {'token': 'invalid-jwt', 'challenge': challenge['challenge']}).returncode != 0
            assert json.loads(call('status').stdout)['state'] == 'NOT_ACTIVATED'
        finally: server.terminate(); server.wait(timeout=10)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
    # Verify actual Docker normalization, not only a hand-written inspect fixture.
    # This is a disposable sleep container, never the Discord application.
    child = '''import os,sys,json,subprocess
sys.path[:0]=['/opt/gamerhq-staging/automation-controller','/opt/gamerhq-staging/deployment-worker']
from tools.staging_dispatch import environment
os.environ.clear();os.environ.update(environment())
from staging_automation.backend import HostBackend
p=json.load(open('/etc/gamerhq-staging/settings/profile.json'))
def run(args,check=True):return subprocess.run(args,capture_output=True,text=True,check=check,timeout=180)
run(['docker','network','create','gamerhq-staging-private'],False)
run(['docker','pull','python:3.12-slim'])
try:
 run(['docker','run','--detach','--name','gamerhq-staging-bot','--label','com.docker.compose.project=gamerhq-staging','--label','com.docker.compose.service=gamerhq-staging','--user','10001:10001','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','512m','--cpus','1','--pids-limit','128','--restart','no','--network','gamerhq-staging-private','--volume','/opt/gamerhq-staging/data:/app/runtime/data:Z','--volume','/opt/gamerhq-staging/backups:/app/backups:Z','python:3.12-slim','python','-c','import time;time.sleep(120)'])
 assert HostBackend(p,{}).inspect()['Name']=='/gamerhq-staging-bot'
finally:run(['docker','rm','--force','gamerhq-staging-bot'],False)
print('NORMALIZED_STAGING_LIMITS_PASS')
'''
    launch = ['runuser', '-u', account.pw_name, '--', 'env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin', 'HOME='+account.pw_dir,
        'XDG_RUNTIME_DIR=/run/user/'+str(account.pw_uid), 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(account.pw_uid)+'/bus',
        'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet', str(root/'deployment-worker/.venv/bin/python'), '-I', '-B', '-']
    normalized = subprocess.run(launch, input=child, capture_output=True, text=True, timeout=240)
    if normalized.returncode or normalized.stdout.strip() != 'NORMALIZED_STAGING_LIMITS_PASS':
        raise RuntimeError('Disposable Docker normalization failed: '+normalized.stderr[-2000:])
    print(json.dumps({'enrollmentRetry': 'PASS', 'realScopedSsh': 'PASS', 'legacyBootstrapDenied': True, 'credentialChangeDenied': True,
        'unsignedActivationDenied': True, 'retainedDatabase': True, 'existingProfileAndPolicyUnchanged': True, 'normalizedContainerLimits': 'PASS', 'gatewayStarted': False, 'productionChanged': False}))


if __name__ == '__main__': main()
