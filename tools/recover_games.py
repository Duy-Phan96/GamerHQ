"""Field-level games recovery on a staged production copy, never a wholesale import."""
from contextlib import closing, nullcontext
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from tools.backup_database import backup_database, verify
from tools.compare_databases import compare, inspect_database, render
from tools.instance_lock import instance_lock

FIELDS = ('name', 'emoji', 'display_group', 'tags_json', 'aliases_json', 'active', 'selectable', 'role_id')
LEGACY = ('category_id', 'chat_channel_id', 'lfg_channel_id', 'clips_channel_id', 'memes_channel_id', 'create_voice_channel_id')


def merge(source, staged):
    """Known fields only. Preserve target PKs, mapped roles/channels and unknown fields."""
    counts = dict(inserted=0, restored=0, preserved_roles=0, deleted_skipped=0, legacy_hints=0)
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(staged)) as dst:
        src.row_factory = dst.row_factory = sqlite3.Row
        source_cols = {r[1] for r in src.execute('PRAGMA table_info(games)')}
        target_cols = {r[1] for r in dst.execute('PRAGMA table_info(games)')}
        if not set(FIELDS) <= source_cols or not set(FIELDS) <= target_cols:
            raise ValueError('Games recovery requires compatible catalog fields; rehearse the schema migration first')
        dst.execute('BEGIN IMMEDIATE')
        dst.execute('CREATE TABLE IF NOT EXISTS game_legacy_hints (game_id INTEGER PRIMARY KEY, resources_json TEXT NOT NULL)')
        if 'channel_id' not in target_cols:
            dst.execute('ALTER TABLE games ADD COLUMN channel_id INTEGER')
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tombstones = {r[0].casefold() for r in dst.execute('SELECT name_key FROM deleted_games')} if 'deleted_games' in tables else set()
        target = {}
        for row in dst.execute('SELECT * FROM games'):
            key = row['name'].strip().casefold()
            if key in target: raise ValueError('Ambiguous target game names; review privately')
            target[key] = dict(row)
        seen = set()
        for row in src.execute('SELECT * FROM games ORDER BY id'):
            game = dict(row)
            key = game['name'].strip().casefold()
            if key in seen: raise ValueError('Ambiguous source game names; review privately')
            seen.add(key)
            if key in tombstones:
                counts['deleted_skipped'] += 1
                continue
            existing = target.get(key)
            # Never attach a recovered role to a different existing game/integration.
            role_id = existing.get('role_id') if existing and existing.get('role_id') else game['role_id']
            if role_id:
                if dst.execute('SELECT 1 FROM games WHERE role_id=? AND id!=?', (role_id, existing['id'] if existing else -1)).fetchone():
                    raise ValueError('Recovered role conflicts with another game; review privately')
                if 'managed_roles' in tables and dst.execute('SELECT 1 FROM managed_roles WHERE role_id=?', (role_id,)).fetchone():
                    raise ValueError('Recovered role conflicts with an integration/preference role')
            if existing:
                gid = existing['id']
                if existing.get('role_id'):
                    counts['preserved_roles'] += 1
                    # Already adopted production game state wins, including a deliberate hide.
                else:
                    fields = [f for f in FIELDS if f != 'name']
                    dst.execute('UPDATE games SET '+','.join(f'{f}=?' for f in fields)+' WHERE id=?', [game[f] for f in fields]+[gid])
                    counts['restored'] += 1
            else:
                fields = list(FIELDS)
                cursor = dst.execute('INSERT INTO games ('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')', [game[f] for f in fields])
                gid = cursor.lastrowid
                counts['inserted'] += 1
            hints = {f: game[f] for f in LEGACY if game.get(f)}
            if hints:
                dst.execute('INSERT OR IGNORE INTO game_legacy_hints(game_id,resources_json) VALUES(?,?)', (gid, json.dumps(hints)))
                counts['legacy_hints'] += 1
        dst.commit()
    verify(staged)
    return counts


def recover(source, target, *, backup=None, apply=False, bots_stopped=False, confirm_comparison=None):
    source, target = Path(source).absolute(), Path(target).absolute()
    if source.is_symlink() or target.is_symlink() or not source.is_file() or not target.is_file() or os.path.samefile(source, target):
        raise ValueError('Distinct existing regular snapshot files are required')
    if apply and (not bots_stopped or backup is None):
        raise ValueError('Apply requires stopped bots and a new backup path')
    with instance_lock(target) if apply else nullcontext(), tempfile.TemporaryDirectory(prefix='gamerhq-games-', dir=target.parent) as folder:
        report = compare(source, target)
        staged = Path(folder)/'games.db'
        backup_database(staged, source=target)  # production is the base, including unknown tables/schema
        counts = merge(source, staged)
        if compare(source, target)['review_token'] != report['review_token']:
            raise ValueError('Snapshots changed; repeat review using stopped copies')
        output = ('Scope: games. Known catalog fields and roles only; existing production roles/state, channels, settings and schema preserved.\n'
                  + '\n'.join(f'{key}: {value}' for key, value in counts.items())
                  + '\nLegacy area mappings: migration hints only, never canonical.\n'
                  + f'Result selector games: {inspect_database(staged)["counts"]["selector_games"]}\n'
                  + render(report))
        if not apply:
            return output + '\nDRY RUN: source and target unchanged. Review this output before any recovery.'
        if confirm_comparison != report['review_token']:
            raise ValueError('Apply requires the exact reviewed comparison token')
        backup = Path(backup).absolute()
        if backup.exists() or backup.is_symlink() or backup.resolve() in {source.resolve(), target.resolve()}:
            raise ValueError('Backup must be a new separate file')
        def sidecars():
            return any(Path(str(target)+suffix).exists() for suffix in ('-wal','-shm','-journal'))
        if sidecars(): raise ValueError('Target SQLite sidecars exist; confirm shutdown first')
        backup_database(backup, source=target)
        if sidecars() or compare(source, backup)['review_token'] != report['review_token'] or compare(source, target)['review_token'] != report['review_token']:
            raise ValueError('Snapshots changed; backup retained, recovery refused')
        os.chmod(staged, 0o600)
        with staged.open('r+b') as stream: os.fsync(stream.fileno())
        os.replace(staged, target)
        return output + '\nGames recovery applied; previous target retained in backup. No Discord connection made.'
