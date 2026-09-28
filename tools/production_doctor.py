"""Read-only, shareable production diagnostics. Never render environment values."""
import argparse
import ast
from contextlib import closing
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from dotenv.parser import parse_stream

ROOT = Path(__file__).resolve().parents[1]


def environment_file(path):
    """Parse once, including multiline values; report only safe key names/line numbers."""
    if not path.is_file():
        return {}, ['Environment file: missing (process environment may be used)']
    return environment_text(path.read_text(encoding='utf-8-sig'))


def environment_text(text):
    values, positions, issues = {}, {}, []
    for entry in parse_stream(io.StringIO(text)):
        # python-dotenv includes leading blank lines in the original span.
        leading = entry.original.string[:len(entry.original.string) - len(entry.original.string.lstrip())]
        line = entry.original.line + leading.count('\n')
        if entry.error:
            issues.append(f'Invalid environment syntax at line {entry.original.line}')
        elif entry.key:
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*', entry.key):
                issues.append(f'Invalid environment key at line {entry.original.line}')
                continue
            positions.setdefault(entry.key, []).append(line)
            values[entry.key] = entry.value
    for key, lines in positions.items():
        if len(lines) > 1:
            issues.append(f'Duplicate environment key: {key}; lines: ' + ', '.join(map(str, lines)))
    return values, issues


def report(env_file, *, environ=None, db_path=None, backup_dir=None, docker=False):
    values, issues = environment_file(env_file)
    values.update(os.environ if environ is None else environ)
    schema, _ = environment_file(ROOT / '.env.example')
    lines = ['GamerHQ Production Doctor — read-only; no secret values', *issues]
    for key in schema:
        raw = str(values.get(key) or '').strip()
        if key.endswith('_ENABLED'):
            status = 'enabled' if raw.lower() == 'true' else 'disabled' if raw.lower() in ('', 'false') else 'invalid'
        else:
            status = 'configured' if raw and raw != '0' else 'missing'
        lines.append(f'{key}: {status}')
    path = Path(db_path or values.get('GAMERHQ_DB_PATH') or ROOT / 'gamerhq.db').expanduser()
    if not path.is_absolute():
        path = ROOT / path
    # Paths are intentionally reported, never contents, tokens, IDs or exception text.
    lines.extend([f'DB path: {path}', f'DB exists: {path.is_file()}',
                  f'DB writable: {path.is_file() and os.access(path, os.W_OK)}'])
    backups = Path(backup_dir or path.parent.parent / 'backups')
    lines.append(f'Backup directory writable: {backups.is_dir() and os.access(backups, os.W_OK)}')
    try:
        files = [p for p in backups.glob('*.db') if p.is_file()]
        lines.append(f'Newest backup age (hours): {max(0, int((time.time() - max(p.stat().st_mtime for p in files)) / 3600))}' if files else 'Recent backup: unavailable')
        # Read schema literals without importing config or running migrations.
        constants = {}
        for node in ast.parse((ROOT / 'database/db.py').read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('PRAGMA query_only=ON')
            valid = conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            with closing(sqlite3.connect(':memory:')) as expected:
                expected.executescript(constants['SCHEMA'])
                current = version == constants['SCHEMA_VERSION']
                for (table,) in expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    needed = {r[1] for r in expected.execute(f'PRAGMA table_info("{table}")')}
                    present = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                    current = current and needed <= present
            lines.extend([f'DB integrity: {"ok" if valid else "failed"}', f'Schema/migrations: {"current" if current else "needs review"}'])
    except (OSError, sqlite3.Error, ValueError, KeyError):
        lines.append('Database/backup inspection: unavailable or failed; no changes made')
    if docker:
        lines.extend(docker_report())
    else:
        lines.append('Docker: not inspected (use --docker on the VPS host)')
    return lines


def docker_report():
    try:
        result = subprocess.run(['docker', 'inspect', 'gamerhq-bot'], capture_output=True, text=True, timeout=15, check=True)
        container = json.loads(result.stdout)[0]
        health = container.get('State', {}).get('Health', {}).get('Status')
        policy = container.get('HostConfig', {}).get('RestartPolicy', {}).get('Name')
        mounts = {m.get('Destination') for m in container.get('Mounts', [])}
        return [f'Container health: {health if health in ("healthy", "unhealthy", "starting") else "unavailable"}',
                f'Restart policy: {"unless-stopped" if policy == "unless-stopped" else "needs review"}',
                f'Persistent data mount /app/runtime/data: {"configured" if "/app/runtime/data" in mounts else "missing"}',
                f'Backup mount /app/backups: {"configured" if "/app/backups" in mounts else "missing"}']
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        return ['Docker: unavailable; no container was started or changed']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--db-path', type=Path)
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--docker', action='store_true')
    parser.add_argument('--env-only', action='store_true', help='Validate file syntax/duplicate keys; prints no values')
    parser.add_argument('--env-stdin-only', action='store_true', help='Validate private env file supplied on stdin, without printing values')
    args = parser.parse_args()
    try:
        if args.env_only or args.env_stdin_only:
            _, issues = environment_text(sys.stdin.read()) if args.env_stdin_only else environment_file(args.env_file)
            print('\n'.join(issues) if issues else 'Environment file syntax and keys: OK')
            return int(bool(issues))
        print('\n'.join(report(args.env_file, db_path=args.db_path, backup_dir=args.backup_dir, docker=args.docker)))
        return 0
    except (OSError, ValueError):
        print('Doctor could not inspect configuration; no values are printed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
