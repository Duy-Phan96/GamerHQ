"""Production preparation tests use disposable SQLite and no external services."""
import contextlib
from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from database import db
from tools import backup_database as backup, production_preflight as preflight, container_health


class ProductionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'gamerhq.db'
        with patch.object(db, 'DB_PATH', self.path):
            db.init_db()
        self.values = dict(DISCORD_TOKEN='synthetic-' + 'x' * 40, GUILD_ID='123',
                           CHOOSE_GAMES_CHANNEL_ID='456', GAMERHQ_DB_PATH=str(self.path))

    def test_preflight_existing_and_no_mutation(self):
        before = self.path.read_bytes()
        errors, notices = preflight.check(self.values, backup_dir=self.root)
        self.assertEqual(errors, [])
        self.assertTrue(notices)
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_database_requires_explicit_flag_and_is_not_created(self):
        self.path.unlink()
        self.assertTrue(preflight.check(self.values)[0])
        self.assertEqual(preflight.check(self.values, allow_new=True)[0], [])
        self.assertFalse(self.path.exists())

    def test_configuration_environment_overrides_env_file(self):
        env = self.root / '.env'
        env.write_text('GUILD_ID=100\nCHOOSE_GAMES_CHANNEL_ID=200\n', encoding='utf-8')
        values = preflight.configuration(env, {'GUILD_ID': '999'})
        self.assertEqual(values['GUILD_ID'], '999')
        self.assertEqual(values['CHOOSE_GAMES_CHANNEL_ID'], '200')

    def test_preflight_redacts_bad_values_and_placeholders(self):
        for token in ('', 'YOUR_TOKEN_HERE', 'placeholder-' + 'x' * 50):
            values = dict(self.values, DISCORD_TOKEN=token, GUILD_ID='private-invalid-id')
            errors, _ = preflight.check(values)
            self.assertTrue(errors)
            self.assertNotIn('private-invalid-id', str(errors))
            if token:
                self.assertNotIn(token, str(errors))

    def test_missing_and_unwritable_directories_fail(self):
        self.assertTrue(preflight.check(self.values, backup_dir=self.root / 'missing')[0])
        with patch('tools.production_preflight.tempfile.TemporaryFile', side_effect=PermissionError):
            self.assertTrue(preflight.check(self.values)[0])

    def test_corrupt_database_fails_without_printing_content(self):
        self.path.write_bytes(b'private corrupted contents')
        errors, _ = preflight.check(self.values)
        self.assertTrue(errors)
        self.assertNotIn('private corrupted contents', str(errors))

    def test_preflight_rehearses_additive_schema_on_copy(self):
        with contextlib.closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('DROP TABLE managed_message_content')
        self.assertFalse(preflight.check(self.values)[0])
        with contextlib.closing(sqlite3.connect(self.path)) as conn, conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='managed_message_content'").fetchone())

    def test_consistent_wal_backup_and_no_overwrite(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute("INSERT INTO settings VALUES ('synthetic','committed')")
            connection.commit()
            target = backup.backup_database(self.root / 'saved.db', source=self.path)
            backup.verify(target)
            with contextlib.closing(sqlite3.connect(target)) as result:
                self.assertEqual(result.execute('SELECT value FROM settings').fetchone()[0], 'committed')
            with self.assertRaises(FileExistsError):
                backup.backup_database(target, source=self.path)
            with self.assertRaises(ValueError):
                backup.backup_database(self.path, source=self.path)
        finally:
            connection.close()

    def test_retention_is_opt_in_and_only_daily_namespace(self):
        folder = self.root / 'backups'
        today = date(2026, 9, 22)
        for offset in range(16):
            backup.daily_backup(folder, source=self.path, today=today-timedelta(days=offset))
        manual = backup.backup_database(folder / 'manual-keep.db', source=self.path)
        unrelated = folder / 'gamerhq-daily-not-a-date.db'
        unrelated.write_bytes(b'untouched')
        _, expired = backup.daily_backup(folder, source=self.path, today=today)
        self.assertEqual(len(expired), 2)
        self.assertTrue(all(p.exists() for p in expired))
        backup.daily_backup(folder, source=self.path, today=today, prune=True)
        self.assertFalse(any(p.exists() for p in expired))
        self.assertTrue(manual.exists())
        self.assertEqual(unrelated.read_bytes(), b'untouched')
        self.assertEqual(len(list(folder.glob('gamerhq-daily-2026-*.db'))), 14)

    def test_invalid_snapshot_blocks_retention(self):
        folder = self.root / 'backups'
        folder.mkdir()
        damaged = folder / 'gamerhq-daily-2020-01-01.db'
        damaged.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.Error):
            backup.daily_backup(folder, source=self.path, retain=1, prune=True)
        self.assertTrue(damaged.exists())

    def test_backup_restore_to_new_path_preserves_state(self):
        snapshot = backup.backup_database(self.root / 'snapshot.db', source=self.path)
        restored = backup.backup_database(self.root / 'restored.db', source=snapshot)
        backup.verify(restored)
        self.assertEqual(snapshot.read_bytes(), restored.read_bytes())

    @patch('tools.container_health.os.kill')
    def test_health_is_local_fresh_gateway_readiness(self, probe):
        path = self.root / 'heartbeat.json'
        self.assertFalse(container_health.healthy(path))
        for stamp, ready, expected in ((100, True, True), (0, True, False), (100, False, False), (120, True, False)):
            path.write_text(json.dumps(dict(time=stamp, ready=ready, pid=os.getpid())))
            self.assertEqual(container_health.healthy(path, now=110), expected)
        probe.assert_called_with(os.getpid(), 0)
        probe.side_effect = ProcessLookupError
        self.assertFalse(container_health.healthy(path, now=110))

    def test_compose_production_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / 'compose.yaml').read_text(encoding='utf-8')
        self.assertNotIn('ports:', text)
        self.assertNotIn('/var/run/docker.sock', text)
        self.assertNotIn('./:/app', text)
        for expected in ('restart: unless-stopped', '../.env', '../data:/app/runtime/data:z', '../backups:/app/backups:z', 'max-size:', 'max-file:'):
            self.assertIn(expected, text)
        dockerfile = (root / 'Dockerfile').read_text()
        self.assertIn('USER gamerhq', dockerfile)
        self.assertIn('requirements.lock', dockerfile)
        self.assertNotIn('COPY . ', dockerfile)


if __name__ == '__main__':
    unittest.main()
