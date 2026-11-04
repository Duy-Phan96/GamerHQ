"""Bounded public worker evidence. Never retain subprocess output or credentials."""
import json
import re
import subprocess

CODES = frozenset({'BUILDX_STATE_NOT_WRITABLE','DOCKER_BUILD_FAILED','DOCKER_COMMAND_FAILED',
    'DOCKER_TIMEOUT','DOCKER_COMMAND_UNAVAILABLE','DOCKER_MEMORY_EXHAUSTED','DOCKER_STORAGE_FULL',
    'DOCKER_REGISTRY_REJECTED','DOCKER_NETWORK_FAILED','PREPARATION_REJECTED','WORKER_COMMAND_FAILED',
    'WORKER_OUTPUT_LIMIT','STAGING_PROFILE_REJECTED','STAGING_DAEMON_REJECTED','STAGING_BASE_IMAGE_MISSING',
    'STAGING_COMPOSE_CONFIG_REJECTED','STAGING_STORAGE_MISSING','STAGING_STORAGE_PROBE_FAILED',
    'STAGING_DATA_ACCESS_REJECTED','STAGING_DATABASE_ACCESS_REJECTED','STAGING_BACKUPS_ACCESS_REJECTED',
    'STAGING_ARTIFACT_REJECTED','STAGING_RECORD_PERSISTENCE_FAILED'})
STEPS = frozenset({'docker-build','docker-command','prepare','worker-command','profile','daemon','base-image',
    'compose-config','storage-probe','artifact','record'})

def decode_evidence(text):
    if not isinstance(text,str) or len(text.encode()) > 65536:
        return None
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except (ValueError,TypeError):
            continue
        if not isinstance(value,dict) or set(value)-{'stagingFailure','step','exitCode','auditId'}:
            continue
        if not isinstance(value.get('stagingFailure'),str) or not isinstance(value.get('step'),str):
            continue
        if value['stagingFailure'] not in CODES or value['step'] not in STEPS:
            continue
        if 'exitCode' in value and (type(value['exitCode']) is not int or not -255 <= value['exitCode'] <= 255):
            continue
        if 'auditId' in value and (not isinstance(value['auditId'],str) or not re.fullmatch('[0-9a-f]{32}',value['auditId'])):
            continue
        return value
    return None

class StagingFailure(RuntimeError):
    def __init__(self,code,step,*,exit_code=None,audit_id=None):
        value={'stagingFailure':code,'step':step}
        if exit_code is not None:value['exitCode']=exit_code
        if audit_id is not None:value['auditId']=audit_id
        encoded=json.dumps(value,separators=(',',':'))
        if decode_evidence(encoded) is None:
            raise ValueError('INVALID_PUBLIC_WORKER_EVIDENCE')
        self.evidence=value
        super().__init__(encoded)

class VerificationFailure(StagingFailure,ValueError):
    """Preserve validation-error compatibility while publishing only safe codes."""

def verified_check(code,step,call):
    try:return call()
    except StagingFailure:raise
    except Exception as error:
        status=getattr(error,'returncode',None)
        if type(status) is not int or not -255 <= status <= 255:status=None
        raise VerificationFailure(code,step,exit_code=status) from None

def docker_failure(error,step='docker-command'):
    if isinstance(error,subprocess.TimeoutExpired):
        return StagingFailure('DOCKER_TIMEOUT',step)
    if isinstance(error,OSError):
        return StagingFailure('DOCKER_COMMAND_UNAVAILABLE',step)
    text=(getattr(error,'stderr','') or '').lower()
    code='DOCKER_BUILD_FAILED' if step=='docker-build' else 'DOCKER_COMMAND_FAILED'
    if 'buildx' in text and any(s in text for s in ('read-only file system','permission denied')):
        code='BUILDX_STATE_NOT_WRITABLE'
    elif 'no space left on device' in text:code='DOCKER_STORAGE_FULL'
    elif any(s in text for s in ('out of memory','cannot allocate memory','oom-kill')):code='DOCKER_MEMORY_EXHAUSTED'
    elif any(s in text for s in ('unauthorized','toomanyrequests','pull access denied')):code='DOCKER_REGISTRY_REJECTED'
    elif any(s in text for s in ('temporary failure in name resolution','network is unreachable','tls handshake timeout')):code='DOCKER_NETWORK_FAILED'
    return StagingFailure(code,step,exit_code=getattr(error,'returncode',None))
