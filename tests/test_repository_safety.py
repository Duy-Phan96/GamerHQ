"""Publication and startup safety checks use only synthetic configuration."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools.repository_audit import secret_findings, private_path
import config
from database import db

class RepositorySafetyTests(unittest.TestCase):
    def test_missing_startup_settings_are_clear(self):
        with patch.object(config,'TOKEN',None):
            with self.assertRaisesRegex(config.ConfigurationError,'DISCORD_TOKEN is required'):config.validate_startup()
        with patch.object(config,'TOKEN','synthetic'),patch.object(config,'GUILD_ID',0):
            with self.assertRaisesRegex(config.ConfigurationError,'GUILD_ID is required'):config.validate_startup()

    def test_bad_id_does_not_echo_value(self):
        value='sensitive-'+'invalid'
        with patch.dict(os.environ,{'GUILD_ID':value}):
            with self.assertRaises(config.ConfigurationError) as error:config.discord_id('GUILD_ID')
        self.assertNotIn(value,str(error.exception));self.assertIn('GUILD_ID',str(error.exception))

    def test_fresh_database_constructed_from_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db,'DB_PATH',Path(directory)/'nested'/'fresh.db'):
                db.init_db();db.seed_catalog();db.init_db()
                with db.connect() as conn:
                    self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                    for table in ('support_tickets','ticket_audit','suggestions','lfg_events'):
                        self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
                    self.assertGreater(conn.execute('SELECT COUNT(*) FROM games').fetchone()[0],0)
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM games WHERE role_id IS NOT NULL').fetchone()[0],0)

    def test_ignore_private_files_but_include_source(self):
        paths=['.env','.env.production','gamerhq.db','data/runtime/private.txt','transcripts/ticket.txt','exports/private.csv','backups/state.db','storage/content.json','.venv/pyvenv.cfg','runtime/log.txt','logs/private.log','secret.key','data/x.sqlite3-wal']
        proc=subprocess.run(['git','check-ignore','--stdin','-z'],input=('\0'.join(paths)+'\0').encode(),capture_output=True)
        self.assertEqual(set(proc.stdout.decode().strip('\0').split('\0')),set(paths))
        for path in ('.env.example','database/db.py','data/games_seed.json','tests/test_tickets.py','requirements.txt'):
            result=subprocess.run(['git','check-ignore',path],capture_output=True)
            self.assertEqual(result.returncode,1,path)

    def test_audit_detects_secret_classes_without_emitting_values(self):
        samples=['DISCORD_TOKEN='+'.'.join(['a'*24,'b'*6,'c'*28]),'https://discord.com/api/webhooks/'+'123/'+'a'*30,'-----BEGIN '+'PRIVATE KEY-----','postgresql://'+'user:credential@host/db']
        for sample in samples:self.assertTrue(secret_findings(sample))
        self.assertFalse(secret_findings('DISCORD_TOKEN=\nGUILD_ID=0\nTOKEN = os.getenv("DISCORD_TOKEN")'))
        self.assertFalse(secret_findings('token = secrets.token_urlsafe(8)'))
        self.assertFalse(secret_findings('https://www.instant-gaming.com/?igr=gamer-0a9671a'))
        self.assertTrue(private_path('transcripts/private.txt'))
        self.assertFalse(private_path('.env.example'))

    def test_staged_secret_is_detected_even_when_working_file_is_clean(self):
        import contextlib
        import io
        from tools import repository_audit as audit
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            subprocess.run(['git','init','-q'],cwd=root,check=True,capture_output=True)
            value='.'.join(['a'*24,'b'*6,'c'*28])
            target=root/'synthetic_config.txt'
            target.write_text('DISCORD_TOKEN='+value,encoding='utf-8')
            subprocess.run(['git','add','synthetic_config.txt'],cwd=root,check=True,capture_output=True)
            target.write_text('DISCORD_TOKEN=',encoding='utf-8')
            output=io.StringIO()
            with patch.object(audit,'ROOT',root),patch.object(sys,'argv',['repository_audit']),contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as result:audit.main()
            self.assertEqual(result.exception.code,1)
            self.assertIn('staged: synthetic_config.txt',output.getvalue())
            self.assertNotIn(value,output.getvalue())

if __name__=='__main__':unittest.main()
