"""Offline, whole-database import. Never merges divergent installations."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from unittest.mock import patch

from tools.backup_database import backup_database, verify


def validate_source(path):
    verify(path)
    from database import db
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, db.SCHEMA_VERSION):
            raise ValueError('Unsupported source schema version')
        for table, required in {'settings': {'key', 'value'},
                                'games': {'id', 'name', 'role_id', 'category_id', 'active'}}.items():
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            if not required <= columns:
                raise ValueError('Source is not a compatible GamerHQ runtime database')
    return version


def migrate_copy(source, destination):
    from database import db
    validate_source(source)
    backup_database(destination, source=source)
    with patch.object(db, 'DB_PATH', Path(destination)):
        db.init_db()  # Existing additive migrations only; never re-seed/reset owner state.
    verify(destination)
    with closing(sqlite3.connect(destination)) as conn:
        required = set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', db.SCHEMA))
        actual = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= actual or conn.execute('PRAGMA user_version').fetchone()[0] != db.SCHEMA_VERSION:
            raise ValueError('Migrated schema is incomplete')
        # Validate all canonical columns as well, not just table names.
        with closing(sqlite3.connect(':memory:')) as reference:
            reference.executescript(db.SCHEMA)
            for table in required:
                expected = {r[1] for r in reference.execute(f'PRAGMA table_info({table})')}
                present = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
                if not expected <= present:
                    raise ValueError('Migrated schema has incompatible columns')


def import_database(source, target, *, backup=None, apply=False, bots_stopped=False):
    source, target = Path(source).absolute(), Path(target).absolute()
    if source.is_symlink() or target.is_symlink() or source.resolve() == target.resolve():
        raise ValueError('Source and target must be distinct regular paths without symlinks')
    if source.exists() and target.exists() and os.path.samefile(source, target):
        raise ValueError('Source and target must not be aliases of the same file')
    if not target.parent.is_dir():
        raise ValueError('Target directory must already exist')
    if apply and (not bots_stopped or backup is None):
        raise ValueError('Apply requires --bots-stopped and a new --backup path')
    if apply and not target.is_file():
        raise ValueError('Apply requires an existing production DB to back up; first-install transfer is documented separately')
    with tempfile.TemporaryDirectory(prefix='gamerhq-import-', dir=target.parent) as folder:
        staged = Path(folder) / 'migrated.db'
        migrate_copy(source, staged)
        if not apply:
            return 'Compatibility/migrations passed on a temporary copy; source and target unchanged.'
        backup = Path(backup).absolute()
        if backup.resolve() in (source.resolve(), target.resolve()) or backup.exists():
            raise ValueError('Backup must be a new, separate file')
        # Refuse leftover SQLite sidecars rather than deleting possibly active state.
        if any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Production SQLite sidecars exist; confirm shutdown/checkpoint privately before import')
        validate_source(target)
        before = target.stat()
        backup_database(backup, source=target)
        after = target.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Target changed during backup; import refused')
        if any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Target acquired SQLite sidecars during backup; import refused')
        os.chmod(staged, 0o600)
        with staged.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(staged, target)
        return 'Imported verified runtime snapshot; previous production DB retained in the requested backup. No Discord connection made.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--bots-stopped', action='store_true')
    args = parser.parse_args()
    try:
        print(import_database(args.source, args.target, backup=args.backup, apply=args.apply, bots_stopped=args.bots_stopped))
    except (OSError, ValueError, sqlite3.Error):
        print('Import refused: check paths, compatibility, integrity, shutdown and exclusive backup destination. No private values printed.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
