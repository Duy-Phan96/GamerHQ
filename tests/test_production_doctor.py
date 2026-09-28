import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tools import production_doctor as doctor, production_preflight as preflight
from tools.instance_lock import instance_lock
from tests import test_production as fixtures


class DoctorTests(unittest.TestCase):
    setUp = fixtures.ProductionTests.setUp

    def test_report_is_read_only_and_does_not_expose_values(self):
        env = self.root / '.env'
        env.write_text('DISCORD_TOKEN=private-token-sentinel\nGUILD_ID=123456789876543210\nTWITCH_CLIENT_ID=private-client-sentinel\n', encoding='utf-8')
        before = self.path.read_bytes()
        text = '\n'.join(doctor.report(env, environ={}, db_path=self.path, backup_dir=self.root))
        for secret in ('private-token-sentinel', '123456789876543210', 'private-client-sentinel'):
            self.assertNotIn(secret, text)
        self.assertIn('DISCORD_TOKEN: configured', text)
        self.assertIn('Schema/migrations: current', text)
        self.assertEqual(before, self.path.read_bytes())

    def test_duplicate_keys_rejected_with_only_names_and_lines(self):
        env = self.root / '.env'
        env.write_text('INSTANT_GAMING_BOT_ID=private-one\nINSTANT_GAMING_BOT_ID=private-two\n', encoding='utf-8')
        _, issues = doctor.environment_file(env)
        self.assertEqual(issues, ['Duplicate environment key: INSTANT_GAMING_BOT_ID; lines: 1, 2'])
        with self.assertRaisesRegex(ValueError, 'Duplicate environment key') as error:
            preflight.configuration(env, {})
        self.assertNotIn('private-one', str(error.exception))
        self.assertNotIn('private-two', str(error.exception))

    def test_multiline_values_are_not_mistaken_for_keys(self):
        env = self.root / '.env'
        env.write_text('DISCORD_TOKEN="first\nGUILD_ID=inside-value\nlast"\nGUILD_ID=123\n', encoding='utf-8')
        _, issues = doctor.environment_file(env)
        self.assertEqual(issues, [])

    def test_duplicate_line_numbers_include_blank_lines_and_comments(self):
        _, issues = doctor.environment_text('# settings\n\nGUILD_ID=first\n\n  GUILD_ID=second\n')
        self.assertEqual(issues, ['Duplicate environment key: GUILD_ID; lines: 3, 5'])

    def test_missing_database_not_created_and_corruption_not_printed(self):
        env = self.root / '.env'
        env.touch()
        missing = self.root / 'absent' / 'database.db'
        text = '\n'.join(doctor.report(env, environ={}, db_path=missing))
        self.assertFalse(missing.parent.exists())
        self.assertIn('DB exists: False', text)
        self.path.write_bytes(b'private-corruption-sentinel')
        text = '\n'.join(doctor.report(env, environ={}, db_path=self.path))
        self.assertNotIn('private-corruption-sentinel', text)
        self.assertIn('inspection: unavailable or failed', text)

    def test_docker_output_whitelist_excludes_secrets_and_errors(self):
        result = subprocess.CompletedProcess([], 0, json.dumps([dict(
            Config={'Env': ['DISCORD_TOKEN=never-print']}, State={'Health': {'Status': 'healthy'}},
            HostConfig={'RestartPolicy': {'Name': 'unless-stopped'}}, Mounts=[{'Destination': '/app/runtime/data'}])]), '')
        with patch.object(doctor.subprocess, 'run', return_value=result):
            output = '\n'.join(doctor.docker_report())
        self.assertNotIn('never-print', output)
        self.assertIn('Container health: healthy', output)
        with patch.object(doctor.subprocess, 'run', side_effect=OSError('private-error')):
            self.assertNotIn('private-error', '\n'.join(doctor.docker_report()))

    def test_schema_file_has_unique_keys_and_comments(self):
        text = (doctor.ROOT / '.env.example').read_text(encoding='utf-8')
        _, issues = doctor.environment_file(doctor.ROOT / '.env.example')
        self.assertEqual(issues, [])
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line and not line.startswith('#'):
                self.assertTrue(lines[i-1].startswith('#'), line.split('=')[0])

    def test_single_instance_lock_rejects_second_holder_and_releases(self):
        with instance_lock(self.path):
            with self.assertRaises(RuntimeError):
                with instance_lock(self.path): pass
        with instance_lock(self.path): pass

    def test_scripts_and_schema_are_canonical(self):
        root = doctor.ROOT
        update = (root / 'scripts/update.sh').read_text()
        for required in ('git pull --ff-only origin main', 'tools.production_doctor --env-stdin-only', 'tools.production_preflight', '--wait'):
            self.assertIn(required, update)
        self.assertNotIn('/server repair', update)
        self.assertTrue((root / 'scripts/backup.sh').exists())
        self.assertIn('!.env.example', (root / '.dockerignore').read_text())
