"""Verified SQLite backups; daily retention only deletes explicitly named daily snapshots."""
import argparse
from datetime import datetime, timezone, date
import os
from pathlib import Path
import re
import sqlite3


def verify(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Backup must be an existing regular file, not a symlink')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'").fetchone():
            raise ValueError('Not a GamerHQ database')
    finally:
        connection.close()


def backup_database(destination, *, source=None):
    if source is None:
        from config import DB_PATH
        source = DB_PATH
    source = Path(source).resolve()
    destination = Path(destination).expanduser().absolute()
    if not source.is_file():
        raise FileNotFoundError('Runtime database does not exist')
    if destination.resolve() == source:
        raise ValueError('Source and backup must differ')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation prevents overwrite, including races and dangling links.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source_conn = target_conn = None
    try:
        source_conn = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        target_conn = sqlite3.connect(destination)
        source_conn.backup(target_conn)
        target_conn.close()
        target_conn = None
        verify(destination)
    except Exception:
        if target_conn:
            target_conn.close()
        destination.unlink(missing_ok=True)
        raise
    finally:
        if source_conn:
            source_conn.close()
    return destination


def daily_backup(directory, *, source=None, retain=14, prune=False, today=None):
    if retain < 1:
        raise ValueError('Retention must be positive')
    directory = Path(directory)
    today = today or datetime.now(timezone.utc).date()
    destination = directory / f'gamerhq-daily-{today.isoformat()}.db'
    if not destination.exists():
        backup_database(destination, source=source)
    verify(destination)  # A repeat that day keeps the first verified snapshot.
    candidates = []
    for path in directory.glob('gamerhq-daily-*.db'):
        match = re.fullmatch(r'gamerhq-daily-(\d{4}-\d{2}-\d{2})\.db', path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        try:
            stamp = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if stamp <= today:
            candidates.append(path)
    expired = sorted(candidates, reverse=True)[retain:]
    # Fail closed on invalid files; never remove unknown/user-named snapshots.
    for path in expired:
        verify(path)
    if prune:
        for path in expired:
            path.unlink()
    return destination, expired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', nargs='?', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--daily', type=Path)
    parser.add_argument('--retain', type=int, default=14)
    parser.add_argument('--prune', action='store_true', help='Explicitly delete expired, verified daily snapshots')
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.verify)
            print('SQLite backup integrity and GamerHQ identity verified.')
        elif args.daily:
            _, expired = daily_backup(args.daily, source=args.source, retain=args.retain, prune=args.prune)
            print(f'Daily snapshot verified; {len(expired)} expired daily snapshots {"removed" if args.prune else "retained (dry run)"}.')
        else:
            if args.prune:
                parser.error('--prune requires --daily')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            path = args.destination or Path('/app/backups') / f'gamerhq-{stamp}.db'
            backup_database(path, source=args.source)
            print('Verified SQLite backup created.')
    except (OSError, ValueError, sqlite3.Error):
        print('ERROR: Backup operation failed; check private paths, permissions and database integrity. No credentials are printed.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
