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
            from tools.staging_automation.pilot_cutover import PilotCutover
            result = PilotCutover(backend, packet['prepared'], packet['image'], packet['runId']).run()
        else:
            raise Denied('OPERATION_NOT_ALLOWED')
    except Denied as error:
        result = {'denied': error.code if error.code in ERROR_CODES else 'AUTOMATION_OPERATION_FAILED'}
    except Exception as error:
        evidence = decode_evidence(str(error))
        code = evidence.get('stagingFailure') if evidence else None
        result = {'denied': 'HOST_' + code if code in CODES else 'AUTOMATION_OPERATION_FAILED'}
    print(json.dumps(result))
