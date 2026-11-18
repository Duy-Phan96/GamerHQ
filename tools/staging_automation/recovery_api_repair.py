"""Owner-enrolled, one-file Host API repair for the existing held transaction."""
import hashlib

from .policy import Denied

PIN = {
    'commit': '8f450533ec0c9589b6fa375e34bd951f91dff419',
    'archiveSha256': 'b84ed81d4371112634200021f24b43f436ac8113c4ecf6d273cd95daeca573e8',
    'apiSha256': '17def9fc201fe4f7c9a4bd554a73e0fd9d2c6724eeb73e97056abf10d6c054ec',
    'parentCommit': 'ca6bc4ac5604987dcab9b5310b7af2b9c53cf652',
    'parentImage': 'sha256:78e394acafa55e9bbf9810530b69beaa2ef9d1f3fccfa7c736669f85db0d0e02',
}
PATH = 'hosts/gamerhq/web_api.py'


def validate_pin(policy):
    if (policy.get('primaryRecoveryApiRepair') != PIN
            or policy.get('tenantPilotCutover', {}).get('fallback', {}).get('commit') != PIN['parentCommit']):
        raise Denied('PILOT_CHECKPOINT_REJECTED')


def verify_source(git):
    if (git('diff', '--name-only', PIN['parentCommit'], PIN['commit']).decode().splitlines() != [PATH]
            or hashlib.sha256(git('show', PIN['commit'] + ':' + PATH)).hexdigest() != PIN['apiSha256']):
        raise Denied('IMMUTABLE_RELEASE_CHANGED')


def prepare(backend, record):
    from hosts.gamerhq.skill_deployment_docker import IMAGE
    from .host_promotion import build_reviewed_overlay, source_commit

    validate_pin(backend.policy)
    if (record.get('activationRun') != '44283427505'
            or record.get('binding', {}).get('fallbackImage') != PIN['parentImage']
            or record.get('phase') not in {'ROLLING_BACK', 'PILOT_ROLLED_BACK'}):
        raise Denied('RECOVERY_REQUIRES_OWNER')
    proof = record.get('recoveryApiRepair')
    if proof is None:
        if (record.get('rollbackHealthFailure') != {'reason': 'HEALTH_HTTP_REJECTED', 'httpStatus': 403}
                or record['phase'] != 'ROLLING_BACK'):
            raise Denied('RECOVERY_REQUIRES_OWNER')
        current = backend.inspect()
        if current['Image'] != PIN['parentImage'] or current['State'].get('Running') is not False:
            raise Denied('RECOVERY_REQUIRES_OWNER')
        backend.storage_budget()
        image = build_reviewed_overlay(backend, PIN['parentImage'], PIN['commit'], PIN['archiveSha256'],
                                       PIN['parentCommit'], verify_source)
        proof = {'image': image, 'commit': PIN['commit'], 'apiSha256': PIN['apiSha256']}
    if (not isinstance(proof, dict) or set(proof) != {'image', 'commit', 'apiSha256'}
            or proof['commit'] != PIN['commit'] or proof['apiSha256'] != PIN['apiSha256']
            or not isinstance(proof['image'], str) or not IMAGE.fullmatch(proof['image'])
            or proof['image'] == PIN['parentImage'] or source_commit(backend, proof['image']) != PIN['commit']):
        raise Denied('RECOVERY_REQUIRES_OWNER')
    # The immutable image must contain precisely the reviewed transport change.
    program = "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('/app/" + PATH + "').read_bytes()).hexdigest())"
    found = backend.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
                         '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--user', '10001:10001',
                         '--memory', '128m', '--cpus', '0.5', '--pids-limit', '32', '--entrypoint', 'python',
                         proof['image'], '-I', '-B', '-c', program])
    if found != PIN['apiSha256']:
        raise Denied('IMMUTABLE_RELEASE_CHANGED')
    return proof
