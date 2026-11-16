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
    guilds = (store.primary_guild, *sorted(store.additional_guilds))
    with tempfile.TemporaryDirectory(prefix="tenant-restore-", dir=scratch_directory) as scratch:
        source = Path(scratch) / "backups"
        restored = Path(scratch) / "restore"
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
