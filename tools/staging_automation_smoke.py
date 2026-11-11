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
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0: raise PermissionError('DISPOSABLE_CI_ONLY')
    from tools.repair_staging_ssh_routing import repair, REPAIRED
    from tools.repair_staging_automation_access import repair as repair_access
    enrolled_commit = 'a1b50573850921e478a2f838e72868853bde6dd7'
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
        old_installer = subprocess.check_output(['git', 'show', enrolled_commit + ':tools/enroll_staging_automation.py'])
        installer = tmp / 'original-enrollment.py'; installer.write_bytes(old_installer)
        for _ in range(2):
            # Reproduce the exact published owner command, including umask 077.
            result = subprocess.run(['python3', str(installer), '--commit', enrolled_commit, '--public-key', public,
                '--approval', 'ENROLL RESTRICTED STAGING AUTOMATION'], capture_output=True, text=True, check=True, umask=0o077)
            receipt = json.loads(result.stdout)
            assert receipt['state'] == 'ENROLLED_INACTIVE' and receipt['legacyWorkerCommit'] == profile['hostSourceCommit']
            assert not any(receipt[k] for k in ('automationEnabled', 'gatewayStarted', 'productionChanged', 'existingCredentialsChanged', 'legacySshEntryChanged', 'sandboxChanged', 'workerSourceChanged'))
        for name, body in originals.items():
            current = Path(name).read_bytes()
            if name.endswith('authorized_keys'): assert current.startswith(body) and current.count(public.encode()) == 1
            else: assert current == body
        # Existing installations may have restrictive code directories; model
        # that state explicitly, independent of runner ACLs/pre-existing modes.
        for directory in (root / 'automation-controller', root / 'automation-controller/staging_automation'):
            directory.chmod(0o700)
        host = ' '.join((tmp / 'host.pub').read_text().split()[:2]); known = tmp / 'known_hosts'; known.write_text('[127.0.0.1]:22225 ' + host + '\n')
        sshd = tmp / 'sshd_config'
        sshd.write_text('Port 22225\nListenAddress 127.0.0.1\nHostKey '+str(tmp / 'host')+'\nPidFile '+str(tmp / 'pid')+'\nUsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nAllowUsers gamerhq-staging-bridge\nStrictModes yes\nPermitUserRC no\nDisableForwarding yes\nInclude /etc/ssh/sshd_config.d/90-gamerhq-staging.conf\n')
        server = subprocess.Popen(['/usr/sbin/sshd', '-D', '-f', str(sshd)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(30):
                try:
                    with socket.create_connection(('127.0.0.1', 22225), timeout=1): break
                except OSError: time.sleep(.1)
            def call(op, payload=None, prefix='gamerhq-staging-automation-v1', key=None):
                req = {'version': 1, 'phase': 'automation', 'operation': op}
                command = prefix + ' ' + base64.b64encode(json.dumps(req).encode()).decode()
                return subprocess.run(['ssh', '-F', '/dev/null', '-T', '-p', '22225', '-i', str(key or tmp / 'client'), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+str(known), 'gamerhq-staging-bridge@127.0.0.1', command], input=json.dumps(payload) if payload else '', capture_output=True, text=True, timeout=90)
            failed = call('status')
            assert failed.returncode != 0 and 'Unsupported or unavailable isolated staging request' in failed.stderr
            for _ in range(2):
                repaired = repair(args.commit, 'REPAIR AUTHENTICATED STAGING SSH ROUTING')
                assert repaired['forceCommandRetained'] and not repaired['automationEnabled']
            assert Path('/etc/ssh/sshd_config.d/90-gamerhq-staging.conf').read_bytes() == REPAIRED
            server.send_signal(__import__('signal').SIGHUP)
            time.sleep(.5)
            denied = call('status')
            denied_evidence = {'exitCode': denied.returncode,
                'stderrPermissionDenied': 'Permission denied' in denied.stderr,
                'stdoutPermissionDenied': 'Permission denied' in denied.stdout,
                'stderrControllerPath': 'automation-controller/entry.py' in denied.stderr,
                'stdoutControllerPath': 'automation-controller/entry.py' in denied.stdout,
                'legacyEntryRejected': 'Unsupported or unavailable isolated staging request' in denied.stderr,
                'routerRejected': 'SSH_AUTHENTICATED_ROUTE_REJECTED' in denied.stderr}
            denied_evidence['controllerMode'] = oct((root / 'automation-controller').stat().st_mode & 0o777)
            denied_evidence['packageMode'] = oct((root / 'automation-controller/staging_automation').stat().st_mode & 0o777)
            assert denied_evidence['controllerMode'] == '0o700', denied_evidence
            assert denied_evidence['packageMode'] == '0o700', denied_evidence
            # systemd versions differ in forwarding the child's stderr. Check
            # failed access then successful access after changing only modes.
            assert denied.returncode == 2, denied_evidence
            for _ in range(2):
                fixed = repair_access(args.commit, 'RESTORE REVIEWED STAGING CODE ACCESS')
                assert fixed['directoryModes'] == ['0o750', '0o750'] and fixed['restrictedSandboxStatus'] == 'NOT_ACTIVATED'
            status = call('status'); assert status.returncode == 0, status.stderr
            value = json.loads(status.stdout); assert value['state'] == 'NOT_ACTIVATED' and value['controllerCommit'] == enrolled_commit
            assert call('bootstrap').returncode != 0
            assert call('credentials').returncode != 0
            assert call('status', prefix='gamerhq-staging-v1').returncode != 0
            assert call('status', key='/tmp/staging-legacy-ci-key').returncode != 0
            challenge = json.loads(call('challenge', {'operation': 'activate'}).stdout)
            assert call('activate', {'token': 'invalid-jwt', 'challenge': challenge['challenge']}).returncode != 0
            assert json.loads(call('status').stdout)['state'] == 'NOT_ACTIVATED'
            # Install the reviewed extension under the same restrictive umask,
            # retaining keys, state, legacy worker, DB and sandbox. No apply.
            from tools.extend_staging_host_promotion import (ACTIVE_CONFIGURATION_CONTROLLER,
                ACTIVE_MANAGED_MESSAGES_CONTROLLER, ACTIVE_STRUCTURE_CONTROLLER, BULK_STRUCTURE_HOST,
                CATALOG_CONTROLLER, CONFIGURATION_HOST,
                FAILED_CONFIGURATION_CONTROLLER, HOST, MANAGED_MESSAGES_HOST, OLD, STRUCTURE_HOST, WEB_BASE, extension)
            from urllib.request import urlopen

            def install_pin(commit, digest, approval, *flags):
                installer = tmp / (commit + '.py')
                with urlopen('https://raw.githubusercontent.com/Duy-Phan96/GamerHQ/' + commit +
                             '/tools/extend_staging_host_promotion.py', timeout=20) as source:
                    body = source.read(65537)
                assert len(body) <= 65536 and hashlib.sha256(body).hexdigest() == digest
                installer.write_bytes(body)
                result = subprocess.run([sys.executable, str(installer), '--commit', commit, '--approval', approval, *flags],
                    check=False, timeout=420, capture_output=True, text=True, umask=0o077)
                assert result.returncode == 0, result.stderr
                return json.loads(result.stdout)
            lock_info = (root / 'worker/operation.lock').lstat()
            print(json.dumps({'extensionLock': {'uid': lock_info.st_uid, 'gid': lock_info.st_gid,
                'mode': oct(lock_info.st_mode & 0o777), 'links': lock_info.st_nlink,
                'expectedUid': account.pw_uid, 'expectedGid': account.pw_gid}}), flush=True)
            first = install_pin('135bbf604926d6b1794503d17821ad00f1c09555',
                '232a6cbe47a6300516a002c2d792a655c4a0f6040cc6c4bb80dc63c4c4e85f3e',
                'INSTALL REVIEWED STAGING HOST PROMOTION')
            assert first['state'] == 'INSTALLED'
            state_path = root / 'worker/automation-state.json'
            inactive_bytes = state_path.read_bytes()
            active = json.loads(inactive_bytes); active.update(enabled=True, bootstrapVerified=True, approvedImage='sha256:'+'a'*64)
            state_path.write_text(json.dumps(active)); os.chown(state_path, account.pw_uid, account.pw_gid)
            previous_pin = install_pin(OLD, 'b05e832e126c9f2a0964a920a84b9f1c4d254de4a1741e1a4307a8449efacc2a',
                'REPLACE REVIEWED STAGING HOST PIN')
            assert previous_pin['state'] == 'PIN_INSTALLED'
            installed = json.loads(call('status').stdout)
            assert installed['controllerCommit'] == OLD
            assert installed['hostPromotionCommit'] == '17858f47480e914956dd241b9f37a328b4c6ac2b'
            current_pin = install_pin(WEB_BASE, '8943f7508ebc13dade0a7e64d39b4aa654a117fdf56375fe7773e86561e955c6',
                'REPLACE REVIEWED STAGING HOST PIN')
            assert current_pin['approvedHostCommit'] == HOST
            catalog = install_pin(CATALOG_CONTROLLER, 'cd61eabcb22960ee5bfa78cd68617505c099a7cfd2660656c3ed332530aa863a',
                'ENROLL STAGING CATALOG PREVIEW', '--catalog-preview')
            assert catalog['state'] == 'CATALOG_PREVIEW_ENROLLED'
            assert json.loads(call('status').stdout)['controllerCommit'] == CATALOG_CONTROLLER
            policy_path = config / 'automation-policy.json'
            catalog_policy = json.loads(policy_path.read_bytes())
            first_configuration = install_pin(FAILED_CONFIGURATION_CONTROLLER,
                'de77d78f0abc0968ca2e76d0b1bfa9e4820b2e79b32332ef1559c8d8999db019',
                'ENROLL STAGING CONFIGURATION PREVIEW', '--configuration-preview')
            assert first_configuration['state'] == 'PIN_INSTALLED'
            assert json.loads(call('status').stdout)['controllerCommit'] == FAILED_CONFIGURATION_CONTROLLER
            configured_policy = json.loads(policy_path.read_bytes())
            held = json.loads(state_path.read_bytes())
            held['recoveryRequired'] = True
            state_path.write_text(json.dumps(held))
            active_bytes = state_path.read_bytes()
            for _ in range(2):
                extended = install_pin(ACTIVE_CONFIGURATION_CONTROLLER,
                    '7f4cabf6cd4d2f1c5319ec18948e5dfdf568d56d4b68d1a5ec43d094c981b1a5',
                    'INSTALL STAGING PROMOTION FIX WITH RECOVERY HELD', '--configuration-preview-recovery')
                assert extended['state'] == 'FIX_INSTALLED_RECOVERY_HELD' and extended['recoveryRequired'] is True
                assert extended['approvedHostCommit'] == CONFIGURATION_HOST and extended['promotionPinChanged'] is False
                assert extended['catalogPreviewPolicyPreserved'] is True
                assert (root / 'worker/operation.lock').stat().st_mode & 0o777 == 0o600
                assert state_path.read_bytes() == active_bytes
            extended_status = json.loads(call('status').stdout)
            assert extended_status['controllerCommit'] == ACTIVE_CONFIGURATION_CONTROLLER
            assert extended_status['hostPromotionCommit'] == CONFIGURATION_HOST
            assert extended_status['state'] == 'ACTIVE' and extended_status['recoveryRequired'] is True
            installed_policy = json.loads(policy_path.read_bytes())
            assert installed_policy == {**configured_policy, 'controllerCommit': ACTIVE_CONFIGURATION_CONTROLLER}
            assert {k: v for k, v in installed_policy.items() if k not in {'controllerCommit', 'hostPromotion'}} == {
                k: v for k, v in catalog_policy.items() if k not in {'controllerCommit', 'hostPromotion'}}
            state_path.write_text(json.dumps({**held, 'recoveryRequired': False}))
            profile_path.write_text(json.dumps({**profile, 'applicationHostCommit': CONFIGURATION_HOST,
                                                'baseImage': held['approvedImage']}))
            managed_pin = install_pin(ACTIVE_MANAGED_MESSAGES_CONTROLLER,
                '4c34bc4b5983ada9933ba1b726a22c772397c9cc5ad4b4835c03245397ea2586',
                'ENROLL STAGING MANAGED MESSAGE PREVIEW', '--managed-message-preview')
            assert managed_pin['approvedHostCommit'] == MANAGED_MESSAGES_HOST
            installed_policy = json.loads(policy_path.read_bytes())
            profile_path.write_text(json.dumps({**profile, 'applicationHostCommit': MANAGED_MESSAGES_HOST,
                                                'baseImage': held['approvedImage']}))
            state_path.write_text(json.dumps({**held, 'recoveryRequired': False}))
            structure_pin = install_pin(ACTIVE_STRUCTURE_CONTROLLER,
                '7e75d62657693f04f068bd3aec13751a49a3aa1b19c1d0278560178d716ed919',
                'ENROLL STAGING STRUCTURE PHASES', '--structure-phases')
            assert structure_pin['approvedHostCommit'] == STRUCTURE_HOST
            installed_policy = json.loads(policy_path.read_bytes())
            profile_path.write_text(json.dumps({**profile, 'applicationHostCommit': STRUCTURE_HOST,
                                                'baseImage': held['approvedImage']}))
            state_path.write_text(json.dumps(held))
            approval = 'ENROLL STAGING BULK STRUCTURE SETUP'
            old_entry = (root / 'automation-controller/entry.py').read_bytes()
            held_state = state_path.read_bytes()
            try:
                extension(args.commit, approval, bulk_structure=True)
            except ValueError as error:
                assert str(error) == 'REVIEWED_RECOVERY_REQUIRED'
            else:
                raise AssertionError('Enrollment ignored the recovery hold')
            assert json.loads(policy_path.read_bytes()) == installed_policy
            assert (root / 'automation-controller/entry.py').read_bytes() == old_entry
            assert state_path.read_bytes() == held_state
            # Reproduce the verified post-promotion baseline with disposable metadata.
            state_path.write_text(json.dumps({**held, 'recoveryRequired': False}))
            live_state = state_path.read_bytes()
            staged_profile = profile_path.read_bytes()
            for _ in range(2):
                previous = os.umask(0o077)
                try: result = extension(args.commit, approval, bulk_structure=True)
                finally: os.umask(previous)
                assert result['approvedHostCommit'] == BULK_STRUCTURE_HOST
                assert result['previousHostCommit'] == STRUCTURE_HOST
                assert result['state'] == 'PIN_INSTALLED' and result['hostPromotionRequiresProtectedOwnerReview']
            assert profile_path.read_bytes() == staged_profile and state_path.read_bytes() == live_state
            enrolled = json.loads(call('status').stdout)
            assert enrolled['controllerCommit'] == args.commit and enrolled['hostPromotionCommit'] == BULK_STRUCTURE_HOST
            assert enrolled['recoveryRequired'] is False
            new_policy = json.loads(policy_path.read_bytes())
            assert new_policy == {**installed_policy, 'controllerCommit': args.commit,
                                  'hostPromotion': new_policy['hostPromotion']}
            profile_path.write_bytes(originals[str(profile_path)])
            catalog_challenge = json.loads(call('challenge', {'operation': 'catalog-preview'}).stdout)
            assert call('catalog-preview', {'token': 'invalid-jwt', 'challenge': catalog_challenge['challenge']}).returncode != 0
            assert call('host-promote', {'token': 'invalid-jwt', 'challenge': 'invalid'}).returncode != 0
            state_path.write_bytes(inactive_bytes)
        finally: server.terminate(); server.wait(timeout=10)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
    # Verify actual Docker normalization, not only a hand-written inspect fixture.
    # This is a disposable sleep container, never the Discord application.
    child = '''import os,sys,json,subprocess
sys.path[:0]=['/opt/gamerhq-staging/automation-controller/revisions/REVIEWED_EXTENSION','/opt/gamerhq-staging/deployment-worker']
from tools.staging_dispatch import environment
os.environ.clear();os.environ.update(environment())
from staging_automation.backend import HostBackend
from staging_automation.host_promotion import COMMIT
from staging_automation.deployment import SkillUpdateController
import inspect
assert 'host_source_commit' in inspect.signature(SkillUpdateController.stage).parameters
assert 'from .deployment import SkillUpdateController' in inspect.getsource(HostBackend.update)
assert COMMIT=='REVIEWED_HOST'
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
    child = child.replace('REVIEWED_EXTENSION', args.commit).replace('REVIEWED_HOST', BULK_STRUCTURE_HOST)
    launch = ['runuser', '-u', account.pw_name, '--', 'env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin', 'HOME='+account.pw_dir,
        'XDG_RUNTIME_DIR=/run/user/'+str(account.pw_uid), 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(account.pw_uid)+'/bus',
        'systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet', str(root/'deployment-worker/.venv/bin/python'), '-I', '-B', '-']
    normalized = subprocess.run(launch, input=child, capture_output=True, text=True, timeout=240)
    if normalized.returncode or normalized.stdout.strip() != 'NORMALIZED_STAGING_LIMITS_PASS':
        raise RuntimeError('Disposable Docker normalization failed: '+normalized.stderr[-2000:])
    print(json.dumps({'enrollmentRetry': 'PASS', 'realScopedSsh': 'PASS', 'legacyBootstrapDenied': True, 'credentialChangeDenied': True,
        'unsignedActivationDenied': True, 'retainedDatabase': True, 'existingProfileAndPolicyUnchanged': True, 'catalogPreviewPolicyPreserved': True,
        'recoveryLatchPreserved': True, 'heldEnrollmentDenied': True, 'bulkStructurePinEnrollment': 'PASS',
        'normalizedContainerLimits': 'PASS',
        'accountForceCommandFailureReproduced': True, 'authenticatedKeyRouting': 'PASS', 'crossIdentityRoutingDenied': True,
        'restrictiveDirectoryAccessFailureReproduced': True, 'ownerInstallerUmask': '077', 'directoryAccessRepair': 'PASS',
        'gatewayStarted': False, 'productionChanged': False}))


if __name__ == '__main__': main()
