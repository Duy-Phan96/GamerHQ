from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from database import db
from tools.backup_database import backup_database
from tools.staging_automation import catalog_preview
from tools.staging_automation.bridge import execute
from tools.staging_automation.policy import Denied


@pytest.mark.skipif(os.name != "posix", reason="Controller checks Unix file ownership and modes.")
def test_catalog_preview_uses_newest_eligible_backup_and_returns_only_counts(tmp_path, monkeypatch):
    live = tmp_path / "staging.db"
    monkeypatch.setattr(db, "DB_PATH", live)
    monkeypatch.setattr(db, "SEED_PATH", Path("data/games_seed.json"))
    monkeypatch.setattr(catalog_preview, "MANIFEST", Path("data/game_catalog_expansion.json"))
    db.init_db()
    db.seed_catalog()
    with db.connect() as conn:
        conn.execute("UPDATE games SET role_id=71, channel_id=81 WHERE name='GTA Online'")
        conn.execute("INSERT INTO skill_domain_authority VALUES(1,'event-manager','event-manager','a','b',1)")

    records = []
    for index in (1, 2, 3):
        ident = f"{index:032x}"
        snapshot = backup_database(tmp_path / (ident + ".sqlite"), source=live)
        if index == 3:
            with closing(sqlite3.connect(snapshot)) as conn, conn:
                conn.execute("UPDATE skill_domain_authority SET guild_id=2")
        os.utime(snapshot, (1000 + index, 1000 + index))
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest() if index != 2 else "0" * 64
        records.append((ident, 1000, 1010, json.dumps({"backup": {"sha256": digest}})))
    profile = {"environment": "staging", "stagingGuildId": "1", "productionGuildId": "2",
               "backupRoot": str(tmp_path), "deploymentUid": os.getuid()}
    report = catalog_preview.preview(profile, records=lambda _: records, now=1010)
    assert report["backupAgeSeconds"] == 9
    assert report["titles"] == 70
    assert (report["existing"], report["proposedInserts"], report["ambiguous"]) == (38, 32, 0)
    assert (report["existingMappedRoles"], report["existingMappedChannels"]) == (1, 1)
    assert "role_id" not in report and "channel_id" not in report and "review_token" not in report
    assert len(db.get_all_games()) == 60
    with pytest.raises(Denied, match="CATALOG_VERIFIED_BACKUP_UNAVAILABLE"):
        catalog_preview.preview(profile, records=lambda _: records[1:], now=1010)
    with pytest.raises(Denied, match="CATALOG_STAGING_SOURCE_REJECTED"):
        catalog_preview.preview({**profile, "environment": "production"}, records=lambda _: [], now=1010)


def test_catalog_preview_requires_enrollment_and_rejects_caller_paths():
    state = {"enabled": True, "bootstrapVerified": True}
    with pytest.raises(Denied, match="CATALOG_PREVIEW_NOT_ENROLLED"):
        execute("challenge", {"operation": "catalog-preview"}, {}, state, None, lambda _: None)
    with pytest.raises(Denied, match="PAYLOAD_REJECTED"):
        execute("catalog-preview", {"token": "token", "challenge": "challenge", "path": "other.db"},
                {"catalogPreviewWorkflowSha256": "a" * 64}, state, None, lambda _: None)
