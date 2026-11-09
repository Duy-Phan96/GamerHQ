"""Preview the pinned catalog against a verified private staging backup."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import time

from .policy import Denied

MAX_SNAPSHOT_BYTES = 256 * 1024**2
MANIFEST = Path(__file__).with_name("catalog_manifest.json")


def backup_records(profile):
    database = Path(profile["database"]).resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        return conn.execute(
            "SELECT id, created_at, updated_at, evidence_json FROM skill_deployments "
            "WHERE skill_id='event-manager' AND state IN ('APPLIED','ROLLED_BACK') "
            "ORDER BY updated_at DESC LIMIT 64"
        ).fetchall()


def candidates(profile, records, now):
    root = Path(profile["backupRoot"]).resolve(strict=True)
    result = []
    for ident, created, updated, evidence in records:
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident):
            continue
        try:
            digest = json.loads(evidence)["backup"]["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            snapshot = root / (ident + ".sqlite")
            info = snapshot.lstat()
        except (OSError, KeyError, TypeError, ValueError):
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077
                or info.st_uid != profile["deploymentUid"] or not 0 < info.st_size <= MAX_SNAPSHOT_BYTES
                or not created <= int(info.st_mtime) <= min(updated + 5, now + 5)
                or any(snapshot.with_name(snapshot.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))):
            continue
        result.append((snapshot, info, digest))
    return sorted(result, key=lambda row: row[1].st_mtime_ns, reverse=True)


def preview(profile, *, records=backup_records, now=None):
    from .catalog_import import manifest, plan
    from database import db

    if profile["environment"] != "staging" or profile["stagingGuildId"] == profile["productionGuildId"]:
        raise Denied("CATALOG_STAGING_SOURCE_REJECTED")
    guild_id = int(profile["stagingGuildId"])
    now = int(time.time() if now is None else now)
    games = manifest(MANIFEST)
    for snapshot, info, expected in candidates(profile, records(profile), now):
        try:
            with snapshot.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    continue
            with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                if (conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                        or conn.execute("PRAGMA user_version").fetchone()[0] != db.SCHEMA_VERSION):
                    continue
                owners = [tuple(row) for row in conn.execute(
                    "SELECT guild_id, skill_id FROM skill_domain_authority WHERE domain='event-manager'"
                )]
                if owners != [(guild_id, "event-manager")]:
                    continue
                report = plan(conn, games, snapshot)
            after = snapshot.lstat()
            if (after.st_ino, after.st_size, after.st_mtime_ns) != (info.st_ino, info.st_size, info.st_mtime_ns):
                continue
        except (OSError, sqlite3.Error, ValueError):
            continue
        return {
            "state": "CATALOG_PREVIEW_VERIFIED",
            "environment": "staging",
            "backupTimestamp": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            "backupAgeSeconds": max(0, now - int(info.st_mtime)),
            "backupIntegrity": "PASS",
            "sourceGuildVerified": True,
            "titles": len(games),
            "existing": len(report["existing"]),
            "proposedInserts": len(report["insert"]),
            "aliasUpdates": len(report["alias_updates"]),
            "ambiguous": len(report["ambiguous"]),
            "retired": len(report["retired"]),
            "existingMappedRoles": sum(bool(row["role_id"]) for row in report["existing"]),
            "existingMappedChannels": sum(bool(row["channel_id"] or row["legacy_chat_channel_id"]) for row in report["existing"]),
            "databaseChanged": False,
            "discordResourcesChanged": False,
        }
    raise Denied("CATALOG_VERIFIED_BACKUP_UNAVAILABLE")
