"""Run the enrolled preview with reviewed imports, leaving the legacy worker intact."""
import json
import os
import sys


def main():
    from tools.staging_automation.backend import HostBackend
    from tools.staging_automation.policy import Denied, ERROR_CODES
    from tools.staging_errors import CODES, decode_evidence

    if os.geteuid() == 0:
        raise PermissionError('The preview runs in the existing worker sandbox.')
    packet = json.loads(sys.stdin.buffer.read(32768))
    try:
        backend = HostBackend(packet['profile'], packet['policy'])
        if packet.get('operation', 'preview') == 'preview':
            result = backend._verify_tenant_pilot(expected_image=packet['image'])
        elif packet['operation'] == 'prepare-activation':
            from tools.staging_automation.pilot_activation import prepare_session
            result = prepare_session(backend, packet['image'])
        elif packet['operation'] == 'cutover':
            import fcntl
            from pathlib import Path
            import stat
            from tools.staging_automation.pilot_cutover import PilotCutover
            # Reusing the inherited locked open-file description also keeps
            # Compose in this operation if its parent is interrupted.
            backend.operation_lock = packet['lockFd']
            info = os.fstat(backend.operation_lock)
            expected = (Path(backend.profile['workRoot']) / 'operation.lock').lstat()
            if (not os.path.samestat(info, expected) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise Denied('CONCURRENT_OPERATION_REJECTED')
            fcntl.flock(backend.operation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = PilotCutover(backend, packet['prepared'], packet['image'], packet['runId']).run(
                recovery_only=packet['recoveryOnly'])
        else:
            raise Denied('OPERATION_NOT_ALLOWED')
    except Denied as error:
        result = {'denied': error.code if error.code in ERROR_CODES else 'AUTOMATION_OPERATION_FAILED'}
    except Exception as error:
        evidence = decode_evidence(str(error))
        code = evidence.get('stagingFailure') if evidence else None
        result = {'denied': 'HOST_' + code if code in CODES else 'AUTOMATION_OPERATION_FAILED'}
    print(json.dumps(result))
