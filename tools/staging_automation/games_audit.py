"""Opt-in primary-Staging Games observer; closed until root-owned pin enrollment."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .policy import Denied
from tools.game_audit_execution import FILES, LAUNCH, REASONS, output, packet


def validate_pin(value):
    if (not isinstance(value, dict) or set(value) != {'sourceCommit', 'sourceSha256', 'workflowSha256'}
            or not re.fullmatch('[0-9a-f]{40}', str(value.get('sourceCommit')))
            or not re.fullmatch('[0-9a-f]{64}', str(value.get('workflowSha256')))
            or not isinstance(value.get('sourceSha256'), dict) or set(value['sourceSha256']) != set(FILES)
            or any(not re.fullmatch('[0-9a-f]{64}', str(v)) for v in value['sourceSha256'].values())):
        raise Denied('GAMES_AUDIT_NOT_ENROLLED')


def run(backend):
    from .pilot_cutover import journal_status
    pin = backend.policy.get('gamesAudit')
    validate_pin(pin)
    if (backend.operation_lock is None or journal_status(backend.profile).get('phase') != 'PILOT_ACTIVE'
            or backend.health()['state'] != 'HOST_READY'):
        raise Denied('GAMES_AUDIT_BASELINE_REJECTED')
    current = backend.inspect()
    expected = backend.policy['tenantPilotActivation']['commit']
    if current['Config'].get('Labels', {}).get('org.opencontainers.image.revision') != expected:
        raise Denied('GAMES_AUDIT_BASELINE_REJECTED')
    # Primary only. The secondary allowlist and tenant DB are never enumerated.
    binding = {'environment': 'staging', 'guildId': int(backend.profile['stagingGuildId']),
               'applicationId': int(backend.policy['tenantPilot']['applicationId']),
               'database': '/app/runtime/data/gamerhq.db'}
    if str(binding['guildId']) == str(backend.profile['productionGuildId']):
        raise Denied('GAMES_AUDIT_BASELINE_REJECTED')
    root = Path(__file__).resolve().parents[1] / 'games-audit-source'
    try:
        sources = {name: (root / name).read_text(encoding='utf-8') for name in FILES}
        sealed = packet(sources, pin['sourceSha256'], binding, source_commit=pin['sourceCommit'])
    except (OSError, ValueError):
        raise Denied('GAMES_AUDIT_SOURCE_REJECTED') from None
    raw = backend.run(['docker', 'exec', '--interactive', '--user', '10001:10001', current['Id'],
                       'python', '-I', '-B', '-c', LAUNCH], timeout=180, stdin=json.dumps(sealed))
    try:
        result = output(raw)
        if result.get('state') == 'GAMES_AUDIT_COLLECTED' and any(result.get(k) != v for k, v in binding.items() if k != 'database'):
            raise ValueError()
    except ValueError:
        raise Denied('GAMES_AUDIT_RESPONSE_REJECTED') from None
    after = backend.inspect()
    if (after['Id'] != current['Id'] or after['Image'] != current['Image']
            or backend.health()['state'] != 'HOST_READY'):
        raise Denied('GAMES_AUDIT_BASELINE_REJECTED')
    if result.get('observerCommit') != pin['sourceCommit'] or result.get('observerSourceSha256') != pin['sourceSha256']:
        raise Denied('GAMES_AUDIT_RESPONSE_REJECTED')
    # Detailed rows never enter a public Actions log or artifact. Like existing
    # audit logs, receipts are private worker metadata, not application DB state.
    work = Path(backend.profile['workRoot'])
    if not work.is_dir() or work.is_symlink() or len(list(work.glob('games-audit-*.json'))) >= 8:
        raise Denied('GAMES_AUDIT_RECEIPT_LIMIT')
    receipt_id = uuid.uuid4().hex
    result.update(hostCommit=expected, image=current['Image'])
    fd = os.open(work / ('games-audit-' + receipt_id + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=True, sort_keys=True)
        stream.write('\n')
    counts = result.get('counts', {})
    if (not isinstance(counts, dict) or set(counts) - {'READY', 'NOT_SET_UP', 'HIDDEN', 'SETUP_NEEDED', 'REVIEW', 'UNVERIFIED'}
            or any(type(v) is not int or v < 0 for v in counts.values())):
        raise Denied('GAMES_AUDIT_RESPONSE_REJECTED')
    safe_error = {}
    if result['state'] == 'GAMES_AUDIT_UNAVAILABLE':
        safe_error['reason'] = result.get('reason') if result.get('reason') in REASONS else 'RUNTIME_OR_SCHEMA_UNAVAILABLE'
        if type(result.get('httpStatus')) is int and 100 <= result['httpStatus'] <= 599:
            safe_error['httpStatus'] = result['httpStatus']
    return {**safe_error, 'state': result['state'], 'status': 'PASS' if result['state'] == 'GAMES_AUDIT_COLLECTED' else 'UNVERIFIED',
            'collectionUnavailable': result['state'] == 'GAMES_AUDIT_UNAVAILABLE',
            'counts': counts, 'privateReceiptId': receipt_id, 'privateReceiptStored': True,
            'databaseChanged': False, 'discordResourcesChanged': False, 'gatewayRestarted': False,
            'productionChanged': False}
