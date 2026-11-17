"""Exercise backup and restore for every declared guild in disposable storage."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from database.tenants import GuildTenantStore
from tools.backup_database import backup_database


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _content_digest(path):
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        for line in conn.iterdump():
            digest.update((line + "\n").encode())
    return digest.hexdigest()


def rehearse(store, scratch_directory):
    """Return integrity evidence for an isolated restore, keeping the sources untouched."""
    with tempfile.TemporaryDirectory(prefix="tenant-restore-", dir=scratch_directory) as scratch:
        return _capture_and_probe(store, Path(scratch) / "backups", Path(scratch) / "restore")


def retain(store, backup_root, scratch_directory):
    """Keep private backup files and a receipt only after every restore passes."""
    from tools.staging_configuration import atomic_json

    batch = Path(tempfile.mkdtemp(prefix="tenant-pilot-", dir=backup_root))
    with tempfile.TemporaryDirectory(prefix="tenant-restore-", dir=scratch_directory) as scratch:
        entries = _capture_and_probe(store, batch, Path(scratch))
    atomic_json(batch / "verified.json", {"schemaVersion": 1, "tenants": entries})
    return {"backupBatch": batch.name, "tenantRestores": entries}


def _capture_and_probe(store, source, restored):
    guilds = (store.primary_guild, *sorted(store.additional_guilds))
    clone_store = GuildTenantStore(primary_guild=store.primary_guild,
                                   primary_database=restored / f"{store.primary_guild}.sqlite",
                                   tenant_directory=restored, additional_guilds=store.additional_guilds)
    evidence = []
    for guild_id in guilds:
        with store.scope(guild_id) as tenant:
            snapshot = backup_database(source / f"{guild_id}.sqlite", source=tenant.database)
        expected = _digest(snapshot)
        clone = backup_database(restored / snapshot.name, source=snapshot)
        with clone_store.scope(guild_id):
            if _digest(snapshot) != expected or _content_digest(snapshot) != _content_digest(clone):
                raise ValueError("Tenant restore differs from its verified snapshot.")
        evidence.append({"guildId": guild_id, "backupSha256": expected, "restore": "PASS"})
    return tuple(evidence)


def approved_binding(profile, policy):
    """Read the exact owner-pinned cohort from protected controller policy."""
    pin = policy.get("tenantPilot")
    if pin is None and "tenantPilot" not in profile:
        return None
    if (profile.get("environment") != "staging" or not isinstance(pin, dict)
            or set(pin) != {"applicationId", "guildIds"} or profile.get("tenantPilot") != pin
            or pin["applicationId"] != profile["stagingBotId"]):
        raise PermissionError("Protected pilot binding does not match staging.")
    ids = pin["guildIds"]
    if (not isinstance(ids, list) or len(ids) != 2
            or any(type(guild_id) is not str or not guild_id.isascii() or not guild_id.isdecimal()
                   or not 0 < int(guild_id) < 2**63 or str(int(guild_id)) != guild_id for guild_id in ids)
            or len(set(ids)) != 2 or profile["stagingGuildId"] not in ids or profile["productionGuildId"] in ids
            or profile["productionBotId"] == pin["applicationId"]):
        raise PermissionError("Protected pilot must name exactly two non-production guilds.")
    return tuple(int(guild_id) for guild_id in ids)
