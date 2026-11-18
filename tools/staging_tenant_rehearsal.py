"""Exercise backup and restore for every declared guild in disposable storage."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile

from database.tenants import GuildTenantStore
from tools.backup_database import backup_database


_RETAINED_BATCH_LIMIT = 14
_MINIMUM_FREE_BYTES = 1024**3
_TENANT_DATABASE_LIMIT = 256 * 1024**2
_CONTAINER_SNAPSHOT_LIMIT = 16 * 1024**2
PREVIEW_MAX_AGE = 3600  # Same lifetime as live Staging verification.


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_activation_checkpoint(backup_root, preview, binding, expected_baseline, *, verified_at, now):
    """Re-open the exact retained preview and rehash both private snapshots."""
    from tools.backup_database import verify

    if type(verified_at) is not int or not 0 <= now - verified_at <= PREVIEW_MAX_AGE:
        raise ValueError("PILOT_PREVIEW_EXPIRED")
    batch = preview.get('backupBatch', '')
    if not isinstance(batch, str) or not re.fullmatch(r'tenant-pilot-[a-z0-9_]{8}', batch):
        raise ValueError("PILOT_CHECKPOINT_REJECTED")
    if any(preview.get(key) != value for key, value in expected_baseline.items()):
        raise ValueError("PILOT_BASELINE_CHANGED")
    folder = Path(backup_root) / batch
    own_uid = os.geteuid() if os.name == 'posix' else None

    def private(path, *, directory=False):
        info = path.lstat()
        if directory:
            correct = stat.S_ISDIR(info.st_mode)
        else:
            correct = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        if not correct or (own_uid is not None and (info.st_uid != own_uid or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600))):
            raise ValueError("PILOT_CHECKPOINT_REJECTED")
        return info

    private(folder, directory=True)
    manifest = folder / 'verified.json'
    if private(manifest).st_size > 16_384:
        raise ValueError("PILOT_CHECKPOINT_REJECTED")
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != preview.get('backupReceiptSha256'):
        raise ValueError("PILOT_CHECKPOINT_REJECTED")
    receipt = json.loads(raw)
    if (receipt.get('schemaVersion') != 1 or receipt.get('binding') != expected_baseline
            or receipt.get('rollbackRehearsal') != 'PRIMARY_ONLY_PASS'):
        raise ValueError("PILOT_CHECKPOINT_REJECTED")
    rows = receipt.get('tenants', ())
    if not isinstance(rows, list) or len(rows) != 2 or {str(row.get('guildId')) for row in rows} != set(binding['guildIds']):
        raise ValueError("PILOT_CHECKPOINT_REJECTED")
    for row in rows:
        guild = str(row['guildId'])
        snapshot = folder / f'{guild}.sqlite'
        size = private(snapshot).st_size
        limit = _TENANT_DATABASE_LIMIT if guild == binding['guildIds'][0] else _CONTAINER_SNAPSHOT_LIMIT
        if not 0 < size <= limit or row.get('restore') != 'PASS' or _digest(snapshot) != row.get('backupSha256'):
            raise ValueError("PILOT_CHECKPOINT_REJECTED")
        verify(snapshot)
    return receipt


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


def retain(store, backup_root, scratch_directory, *, binding=None, secondary_capture=None, before_seal=None):
    """Keep private backup files and a receipt only after every restore passes."""
    from tools.staging_configuration import atomic_json

    backup_root = Path(backup_root)
    if sum(1 for _ in backup_root.glob("tenant-pilot-*")) >= _RETAINED_BATCH_LIMIT:
        raise PermissionError("Pilot backup retention limit reached. Review the retained batches.")
    logical_bytes = 0
    for guild_id in (store.primary_guild, *sorted(store.additional_guilds)):
        if secondary_capture is not None and guild_id != store.primary_guild:
            logical_bytes += _CONTAINER_SNAPSHOT_LIMIT
            continue
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
        entries = _capture_and_probe(store, batch, Path(scratch), secondary_capture=secondary_capture)
    if before_seal is not None:
        before_seal()
    receipt = batch / "verified.json"
    atomic_json(receipt, {"schemaVersion": 1, "tenants": entries, "binding": binding,
                          "rollbackRehearsal": "PRIMARY_ONLY_PASS"})
    return {"backupBatch": batch.name, "backupReceiptSha256": _digest(receipt),
            "tenantRestores": entries, "rollbackRehearsal": "PRIMARY_ONLY_PASS"}


def _capture_and_probe(store, source, restored, *, secondary_capture=None):
    guilds = (store.primary_guild, *sorted(store.additional_guilds))
    clone_store = GuildTenantStore(primary_guild=store.primary_guild,
                                   primary_database=restored / f"{store.primary_guild}.sqlite",
                                   tenant_directory=restored, additional_guilds=store.additional_guilds)
    evidence = []
    for guild_id in guilds:
        snapshot = source / f"{guild_id}.sqlite"
        if secondary_capture is not None and guild_id != store.primary_guild:
            secondary_capture(guild_id, snapshot)
        else:
            with store.scope(guild_id) as tenant:
                backup_database(snapshot, source=tenant.database)
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


def snapshot_bytes(directory, guild_id, limit, output):
    """Stdlib-only program executed as the existing Staging container user."""
    from contextlib import closing
    import os
    from pathlib import Path
    import sqlite3
    import stat

    name = str(guild_id)
    if not 0 < guild_id < 2**63:
        raise ValueError("Invalid guild ID.")
    directory = Path(directory)
    path = directory / (name + ".sqlite")
    info = path.lstat()
    if directory.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError("Invalid tenant storage path.")
    if os.name == "posix" and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()):
        raise PermissionError("Invalid tenant storage ownership or mode.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
        with closing(sqlite3.connect(":memory:")) as target:
            page_count = source.execute("PRAGMA page_count").fetchone()[0]
            page_size = source.execute("PRAGMA page_size").fetchone()[0]
            if page_count * page_size > limit:
                raise ValueError("Tenant backup requires an owner-reviewed size budget.")

            def check_progress(_, __, total):
                if total * page_size > limit:
                    raise ValueError("Tenant grew beyond the backup size budget.")

            source.backup(target, pages=128, progress=check_progress)
            if target.execute("SELECT guild_id, ready FROM gamerhq_tenant_identity").fetchall() != [(guild_id, 1)]:
                raise PermissionError("Unexpected tenant identity.")
            if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Tenant snapshot failed integrity verification.")
            payload = target.serialize()
            if not 0 < len(payload) <= limit:
                raise ValueError("Tenant snapshot size is invalid.")
            output.write(payload)


def capture_container_tenant(container, guild_id, destination, *, run=subprocess.run):
    """Export only the pinned secondary DB into a new worker-private file."""
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise PermissionError("Approved Staging container identity is required.")
    _capture_tenant(["docker", "exec", "--user", "10001:10001", container, "python"], guild_id, destination, run)


def _capture_tenant(command, guild_id, destination, run):
    from inspect import getsource
    from tools.backup_database import verify

    program = getsource(snapshot_bytes) + (
        "\nimport sys\nsnapshot_bytes('/app/runtime/data/tenants', int(sys.argv[1]), "
        f"{_CONTAINER_SNAPSHOT_LIMIT}, sys.stdout.buffer)\n"
    )
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            run([*command, "-I", "-B", "-c", program, str(guild_id)],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL, timeout=90, check=True)
            output.flush()
            os.fsync(output.fileno())
        if not 0 < destination.stat().st_size <= _CONTAINER_SNAPSHOT_LIMIT:
            raise ValueError("Tenant snapshot size is invalid.")
        verify(destination)
    except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error, KeyboardInterrupt):
        destination.unlink(missing_ok=True)
        raise


def retain_quiesced(backend, baseline_container, baseline_image, *, run=subprocess.run):
    """Retain fresh dual snapshots while the approved Host remains stopped.

    The caller holds the staging operation lock and owns the stopped-service
    recovery. The secondary reader has only a read-only data bind and no
    credentials or Gateway access.
    """
    from hosts.gamerhq.skill_deployment_docker import IMAGE
    from tools.staging_automation.policy import Denied

    profile = {**backend.profile, "tenantPilot": backend.policy["tenantPilot"]}
    cohort = approved_binding(profile, backend.policy)
    if not IMAGE.fullmatch(baseline_image) or not re.fullmatch(r"[0-9a-f]{64}", baseline_container):
        raise Denied("PILOT_BASELINE_CHANGED")

    def verify_stopped():
        container = backend.inspect()
        state = container.get("State", {})
        if (container["Id"] != baseline_container or container["Image"] != baseline_image
                or state.get("Running") is not False or state.get("Status") != "exited"):
            raise Denied("PILOT_BASELINE_NOT_QUIESCED")
        binding = rehearsal_binding(profile, backend.policy, container)
        if binding["baselineHostCommit"] != backend.policy["hostPromotion"]["commit"]:
            raise Denied("PILOT_BASELINE_CHANGED")
        return binding

    binding = verify_stopped()
    backend.storage_budget()
    source = Path(profile["database"]).resolve(strict=True)
    if source != Path(profile["database"]) or (source.parent / "tenants").is_symlink() or "," in str(source.parent):
        raise Denied("PILOT_TENANT_PATH_REJECTED")
    primary = int(profile["stagingGuildId"])
    store = GuildTenantStore(primary_guild=primary, primary_database=source,
                             tenant_directory=source.parent / "tenants",
                             additional_guilds=tuple(guild for guild in cohort if guild != primary))
    reader = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
              "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--user", "10001:10001",
              "--memory", "128m", "--cpus", "0.5", "--pids-limit", "32",
              "--mount", f"type=bind,src={source.parent},dst=/app/runtime/data,readonly",
              "--entrypoint", "python", baseline_image]

    def capture(guild_id, destination):
        verify_stopped()
        try:
            _capture_tenant(reader, guild_id, destination, run)
        except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error):
            raise Denied("PILOT_SECONDARY_BACKUP_REJECTED") from None

    result = retain(store, profile["backupRoot"], profile["workRoot"], binding={**binding, "quiesced": True},
                    secondary_capture=capture, before_seal=verify_stopped)
    return {**result, "quiesced": True}


def startup_override(profile, policy, image, *, primary_only=False):
    """Build the exact pilot override for the existing isolated Compose service."""
    from hosts.gamerhq.skill_deployment_docker import IMAGE
    from services.tenant_routing import PILOT_ENV, pilot_environment, pilot_routes

    cohort = approved_binding(profile, policy)
    if not cohort or not IMAGE.fullmatch(image) or profile.get("service") != "gamerhq-staging":
        raise PermissionError("Protected Staging pilot startup is unavailable.")
    value = pilot_environment(profile, cohort, primary_only=primary_only)
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
