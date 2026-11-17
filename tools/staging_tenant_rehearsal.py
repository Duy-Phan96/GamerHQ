"""Exercise backup and restore for every declared guild in disposable storage."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from database.tenants import GuildTenantStore
from tools.backup_database import backup_database


_RETAINED_BATCH_LIMIT = 14
_MINIMUM_FREE_BYTES = 1024**3
_TENANT_DATABASE_LIMIT = 256 * 1024**2


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


def retain(store, backup_root, scratch_directory, *, binding=None):
    """Keep private backup files and a receipt only after every restore passes."""
    from tools.staging_configuration import atomic_json

    backup_root = Path(backup_root)
    if sum(1 for _ in backup_root.glob("tenant-pilot-*")) >= _RETAINED_BATCH_LIMIT:
        raise PermissionError("Pilot backup retention limit reached. Review the retained batches.")
    logical_bytes = 0
    for guild_id in (store.primary_guild, *sorted(store.additional_guilds)):
        with store.scope(guild_id) as tenant, closing(sqlite3.connect(tenant.database.as_uri() + "?mode=ro", uri=True)) as conn:
            size = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
            if size > _TENANT_DATABASE_LIMIT:
                raise PermissionError("Pilot database exceeds the existing Staging size budget.")
            logical_bytes += size
    # Allow for the retained batch and the simultaneous scratch restore even
    # when both roots use the same filesystem. Never delete a batch implicitly.
    if min(shutil.disk_usage(root).free for root in (backup_root, scratch_directory)) < _MINIMUM_FREE_BYTES + 2 * logical_bytes:
        raise PermissionError("Pilot backup and restore exceed the available Staging storage budget.")
    batch = Path(tempfile.mkdtemp(prefix="tenant-pilot-", dir=backup_root))
    with tempfile.TemporaryDirectory(prefix="tenant-restore-", dir=scratch_directory) as scratch:
        entries = _capture_and_probe(store, batch, Path(scratch))
    receipt = batch / "verified.json"
    atomic_json(receipt, {"schemaVersion": 1, "tenants": entries, "binding": binding,
                          "rollbackRehearsal": "PRIMARY_ONLY_PASS"})
    return {"backupBatch": batch.name, "backupReceiptSha256": _digest(receipt),
            "tenantRestores": entries, "rollbackRehearsal": "PRIMARY_ONLY_PASS"}


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
    primary_only = GuildTenantStore(primary_guild=store.primary_guild,
                                    primary_database=clone_store.primary_database,
                                    tenant_directory=restored)
    with primary_only.scope(store.primary_guild) as primary:
        if _content_digest(primary.database) != _content_digest(source / primary.database.name):
            raise ValueError("Primary-only rollback does not match its verified snapshot.")
    for guild_id in store.additional_guilds:
        try:
            with primary_only.scope(guild_id):
                raise ValueError("Primary-only rollback admitted the secondary guild.")
        except PermissionError:
            with clone_store.scope(guild_id) as retained:
                if _content_digest(retained.database) != _content_digest(source / retained.database.name):
                    raise ValueError("Primary-only rollback changed the retained secondary tenant.")
    return tuple(evidence)


def startup_override(profile, policy, image):
    """Build the exact pilot override for the existing isolated Compose service."""
    from hosts.gamerhq.skill_deployment_docker import IMAGE
    from services.tenant_routing import PILOT_ENV, pilot_environment, pilot_routes

    cohort = approved_binding(profile, policy)
    if not cohort or not IMAGE.fullmatch(image) or profile.get("service") != "gamerhq-staging":
        raise PermissionError("Protected Staging pilot startup is unavailable.")
    value = pilot_environment(profile, cohort)
    pilot_routes(value, primary=int(profile["stagingGuildId"]), database=profile["database"], get_guild=lambda _: None)
    return {"services": {profile["service"]: {"image": image, "environment": {PILOT_ENV: value}}}}


def rehearsal_binding(profile, policy, container):
    image = container["Image"]
    host_commit = container["Config"]["Labels"]["org.opencontainers.image.revision"]
    override = startup_override(profile, policy, image)
    encoded = json.dumps(override, sort_keys=True, separators=(",", ":")).encode()
    return {"baselineHostCommit": host_commit, "baselineImage": image,
            "startupOverrideSha256": hashlib.sha256(encoded).hexdigest()}


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
