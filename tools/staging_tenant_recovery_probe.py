"""Bounded, data-free diagnostics for the secondary database inside Staging."""
import io
import os
from pathlib import Path
import sqlite3
import stat

from tools.provision_staging_pilot_tenant import provision
from tools.staging_tenant_rehearsal import _CONTAINER_SNAPSHOT_LIMIT, snapshot_bytes


def inspect_secondary(directory, guild_id):
    """Return only public file metadata and a bounded capture result."""
    target = directory / f'{guild_id}.sqlite'
    try:
        folder = directory.lstat()
    except FileNotFoundError:
        return {'state': 'MISSING', 'step': 'tenant-directory'}
    except OSError:
        return {'state': 'INACCESSIBLE', 'step': 'tenant-directory'}
    if not stat.S_ISDIR(folder.st_mode):
        return {'state': 'UNSAFE_PATH', 'step': 'tenant-directory'}
    if os.name == 'posix' and (folder.st_uid != os.geteuid() or folder.st_gid != os.getegid() or stat.S_IMODE(folder.st_mode) != 0o700):
        return {'state': 'UNSAFE_MODE_OR_OWNER', 'step': 'tenant-directory'}
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {'state': 'MISSING', 'step': 'tenant-file'}
    except OSError:
        return {'state': 'INACCESSIBLE', 'step': 'tenant-file'}
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return {'state': 'UNSAFE_PATH', 'step': 'tenant-file'}
    facts = {'mode': format(stat.S_IMODE(info.st_mode), '04o'), 'uid': info.st_uid, 'gid': info.st_gid}
    if os.name == 'posix' and (facts['mode'] != '0600' or info.st_uid != os.geteuid() or info.st_gid != os.getegid()):
        return {**facts, 'state': 'UNSAFE_MODE_OR_OWNER', 'step': 'tenant-file'}
    try:
        snapshot_bytes(directory, guild_id, _CONTAINER_SNAPSHOT_LIMIT, io.BytesIO())
    except PermissionError:
        return {**facts, 'state': 'TENANT_IDENTITY_OR_ACCESS_REJECTED', 'step': 'sqlite-snapshot'}
    except sqlite3.Error:
        return {**facts, 'state': 'SQLITE_REJECTED', 'step': 'sqlite-snapshot'}
    except ValueError:
        return {**facts, 'state': 'SIZE_OR_INTEGRITY_REJECTED', 'step': 'sqlite-snapshot'}
    except OSError:
        return {**facts, 'state': 'INACCESSIBLE', 'step': 'sqlite-snapshot'}
    return {**facts, 'state': 'SNAPSHOT_READY', 'step': 'sqlite-snapshot'}


def recover(pin, primary, secondary, database):
    directory = Path(database).parent / 'tenants'
    before = inspect_secondary(directory, secondary)
    if before['state'] != 'MISSING':
        return {'before': before, 'after': before, 'created': False}
    try:
        result = provision(pin, primary, database)
    except (OSError, sqlite3.Error, ValueError):
        return {'before': before, 'after': {'state': 'PROVISIONING_REJECTED', 'step': 'atomic-provision'}, 'created': False}
    return {'before': before, 'after': inspect_secondary(directory, secondary), 'created': result['created']}
