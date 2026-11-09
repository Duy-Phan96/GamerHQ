"""Offline test of the owner read-only promotion receipt and its output scope."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3

from tools import staging_host_promotion_diagnostics as diagnostic


def ledger(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE skill_domain_authority (guild_id, domain, skill_id);
            CREATE TABLE skill_guild_state (guild_id, skill_id, installed, configured, enabled, version);
            CREATE TABLE skill_deployments
                (id, skill_id, state, created_at, updated_at, previous_image, evidence_json);
            INSERT INTO skill_domain_authority VALUES(12, 'event-manager', 'event-manager');
            INSERT INTO skill_guild_state VALUES(12, 'event-manager', 1, 1, 1, '0.1.2');
            INSERT INTO skill_guild_state VALUES(34, 'event-manager', 1, 1, 1, '9.9.9');
        """)


def record(path, ident, state, time, evidence):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO skill_deployments VALUES(?, 'event-manager', ?, ?, ?, ?, ?)",
                     (ident, state, time, time, 'private-image-id', json.dumps(evidence)))


def test_pre_apply_blocker_is_separate_from_candidate_attempts(tmp_path):
    db = tmp_path / 'stage.sqlite'
    ledger(db)
    record(db, 'a' * 32, 'RECOVERY_REQUIRED', diagnostic.RUN_STARTED - 60,
           {'hostSourceCommit': diagnostic.BASELINE, 'private': 'sensitive'})
    result = diagnostic.evidence_from_database(db, tmp_path, 12, os.getuid())
    assert result['eventManagerAuthority'] and result['integrityPass']
    assert result['skillVersion'] == '0.1.2'
    assert result['attemptsSinceFailedRun'] == []
    assert result['preexistingHostBlockers'][0]['state'] == 'RECOVERY_REQUIRED'
    assert 'sensitive' not in str(result) and 'private-image-id' not in str(result)
    assert not db.with_name(db.name + '-wal').exists()


def test_candidate_backup_and_restore_evidence_are_separately_verified(tmp_path):
    db = tmp_path / 'stage.sqlite'
    backup = tmp_path / ('b' * 32 + '.sqlite')
    ledger(db)
    ledger(backup)
    backup.chmod(0o600)
    checksum = hashlib.sha256(backup.read_bytes()).hexdigest()
    record(db, 'b' * 32, 'RECOVERY_REQUIRED', diagnostic.RUN_STARTED + 10,
           {'hostSourceCommit': diagnostic.TARGET, 'probe': {'healthy': True},
            'backup': {'sha256': checksum, 'verification': {'restoreProbe': 'PASS'}}})
    result = diagnostic.evidence_from_database(db, tmp_path, 12, os.getuid())
    attempt = result['attemptsSinceFailedRun'][0]
    assert attempt['previousImageRecorded'] and attempt['offlineProbePassed']
    assert attempt['backup'] == {'recorded': True, 'filePresent': True, 'checksumMatches': True,
                                 'integrityPass': True, 'restoreProbeRecorded': True}
    assert checksum not in str(result) and 'b' * 32 not in str(result)


def test_backup_symlink_never_supplies_evidence(tmp_path):
    db = tmp_path / 'db.sqlite'
    ledger(db)
    alias = tmp_path / ('c' * 32 + '.sqlite')
    alias.symlink_to(db)
    evidence = diagnostic.receipt_for_backup(tmp_path, 'c' * 32, {}, os.getuid())
    assert evidence['inspection'] == 'UNAVAILABLE'
    assert evidence['integrityPass'] is None
