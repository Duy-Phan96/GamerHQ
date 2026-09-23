"""Credential-safe offline preflight; database migration is rehearsed on a copy."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from dotenv import dotenv_values


def configuration(env_file=None, environ=None):
    values = dict(dotenv_values(env_file)) if env_file and env_file.is_file() else {}
    values.update(os.environ if environ is None else environ)
    return values


def check(values, *, allow_new=False, db_path=None, backup_dir=None):
    errors = []
    notices = []
    token = str(values.get('DISCORD_TOKEN') or '').strip()
    if len(token) < 30 or any(word in token.lower() for word in ('your_token', 'placeholder', 'changeme', 'example')):
        errors.append('DISCORD_TOKEN is missing or an obvious placeholder')
    for name in ('GUILD_ID', 'CHOOSE_GAMES_CHANNEL_ID', 'GAME_SUGGESTIONS_CHANNEL_ID', 'INTRODUCTIONS_CHANNEL_ID', 'INSTANT_GAMING_BOT_ID', 'DEALGECKO_BOT_ID', 'JOCKIE_MUSIC_BOT_ID', 'PANCAKE_BOT_ID'):
        raw = str(values.get(name) or '0').strip()
        if not raw.isdigit() or int(raw) < (1 if name in ('GUILD_ID', 'CHOOSE_GAMES_CHANNEL_ID') else 0):
            errors.append(f'{name} is not a valid Discord ID')
    raw = db_path or values.get('GAMERHQ_DB_PATH')
    if not raw:
        errors.append('GAMERHQ_DB_PATH is required for production')
    if errors:
        return errors, notices
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    for directory in (path.parent, Path(backup_dir) if backup_dir else path.parent):
        try:
            if not directory.is_dir():
                raise OSError()
            with tempfile.TemporaryFile(dir=directory) as probe:
                probe.write(b'write-probe')
                probe.flush()
        except OSError:
            errors.append('A runtime/backup directory is missing or not writable by this user')
    if path.is_symlink():
        errors.append('Database path must not be a symbolic link')
    if path.exists() and (not path.is_file() or not os.access(path, os.W_OK)):
        errors.append('Existing database is not a writable regular file')
    if not path.exists() and not allow_new:
        errors.append('Runtime database is missing; import the existing DB or explicitly use --allow-new')
    if errors:
        return errors, notices
    try:
        # Production /tmp is deliberately small; rehearse beside persistent data.
        with tempfile.TemporaryDirectory(prefix='gamerhq-preflight-', dir=path.parent) as temporary:
            rehearsal = Path(temporary) / 'schema.db'
            if path.exists():
                source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
                try:
                    if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise sqlite3.DatabaseError()
                    if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'").fetchone():
                        raise sqlite3.DatabaseError()
                    with closing(sqlite3.connect(rehearsal)) as target:
                        source.backup(target)
                finally:
                    source.close()
            # Actual additive application migrations, on temporary SQLite only.
            with patch.dict(os.environ, {k: str(v) for k, v in values.items() if v is not None}), patch('dotenv.load_dotenv', return_value=False):
                from database import db
                with patch.object(db, 'DB_PATH', rehearsal):
                    db.init_db()
                    db.seed_catalog()
                    with db.connect() as conn:
                        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise sqlite3.DatabaseError()
            notices.append('Schema initialization/additive migrations rehearsed successfully on a temporary copy.')
            if not path.exists():
                notices.append('New database explicitly allowed; startup will initialize it. No live DB was created here.')
    except (sqlite3.Error, OSError, ValueError):
        errors.append('Database integrity or migration rehearsal failed; inspect private configuration/storage before startup')
    return errors, notices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--db-path', type=Path)
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--allow-new', action='store_true')
    args = parser.parse_args()
    try:
        errors, notices = check(configuration(args.env_file), allow_new=args.allow_new, db_path=args.db_path, backup_dir=args.backup_dir)
    except (OSError, ValueError):
        errors, notices = ['Configuration could not be read; no values are printed'], []
    for notice in notices:
        print(notice)
    for error in errors:
        print('ERROR: ' + error)
    if errors:
        return 1
    print('Production preflight passed. Live database unchanged; no Discord connection was made.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
