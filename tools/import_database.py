"""Offline recovery: whole-store replacement or production-preserving games scope."""
import argparse
from contextlib import closing, nullcontext
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from unittest.mock import patch

from tools.backup_database import backup_database, verify
from tools.compare_databases import compare, render, inspect_database
from tools.instance_lock import instance_lock


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


def import_database(source, target, *, backup=None, apply=False, bots_stopped=False, confirm_comparison=None, scope='all'):
    if scope == 'games':
        from tools.recover_games import recover
        return recover(source, target, backup=backup, apply=apply, bots_stopped=bots_stopped, confirm_comparison=confirm_comparison)
    if scope != 'all':
        raise ValueError('Unknown import scope')
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
    with instance_lock(target) if apply else nullcontext(), tempfile.TemporaryDirectory(prefix='gamerhq-import-', dir=target.parent) as folder:
        validate_source(source)
        source_state = inspect_database(source)
        original = source_state['fingerprint']
        staged = Path(folder) / 'migrated.db'
        migrate_copy(source, staged)
        if inspect_database(source)['fingerprint'] != original:
            raise ValueError('Source changed during migration rehearsal; use a stopped, consistent snapshot')
        if target.is_file():
            validate_source(target)
        report = compare(source, target) if target.is_file() else None
        if not apply:
            staged_state = inspect_database(staged)
            migration = 'required' if (source_state['schema_version'], source_state['schema']) != (staged_state['schema_version'], staged_state['schema']) else 'not required'
            return (f'Compatibility: OK. Schema migration: {migration} (rehearsed on a temporary copy); source and target unchanged.\n'
                    + (render(report) if report else 'Production snapshot unavailable; no replacement permitted.'))
        if report['potential_conflicts'] and confirm_comparison != report['review_token']:
            raise ValueError('Production-only or changed state exists; compare snapshots and confirm the exact review token before replacement')
        backup = Path(backup).absolute()
        if backup.resolve() in (source.resolve(), target.resolve()) or backup.exists():
            raise ValueError('Backup must be a new, separate file')
        # Refuse leftover SQLite sidecars rather than deleting possibly active state.
        if any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Production SQLite sidecars exist; confirm shutdown/checkpoint privately before import')
        validate_source(target)
        before = target.stat()
        backup_database(backup, source=target)
        if compare(source, backup)['review_token'] != report['review_token']:
            raise ValueError('Snapshots changed since comparison; review again. Backup retained')
        after = target.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Target changed during backup; import refused')
        if any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Target acquired SQLite sidecars during backup; import refused')
        if compare(source, target)['review_token'] != report['review_token']:
            raise ValueError('Source or target changed; import refused. Backup retained')
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
    parser.add_argument('--scope', choices=('all', 'games'), default='all', help='Use games for field-level catalog recovery preserving production state')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--dry-run', '--check', action='store_true', help='Explicit non-destructive compatibility and comparison mode (default)')
    parser.add_argument('--confirm-comparison', help='Exact reviewed comparison token; required when replacing divergent state')
    parser.add_argument('--bots-stopped', action='store_true')
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error('--apply cannot be combined with --dry-run/--check')
    try:
        print(import_database(args.source, args.target, backup=args.backup, apply=args.apply,
                              bots_stopped=args.bots_stopped, confirm_comparison=args.confirm_comparison, scope=args.scope))
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        print('Import refused: check compatibility, integrity, shutdown, backup destination and --confirm-comparison for divergent state. Run --dry-run again. No private values printed.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
