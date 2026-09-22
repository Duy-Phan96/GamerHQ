"""Run all offline tests with temporary storage and no loaded local credentials."""
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@contextmanager
def offline_environment():
    with tempfile.TemporaryDirectory(prefix='gamerhq-tests-') as directory:
        clean={'GAMERHQ_DB_PATH':str(Path(directory)/'test.db'),'GUILD_ID':'0',
               'INSTANT_GAMING_BOT_ID':'0','CHOOSE_GAMES_CHANNEL_ID':'0','GAME_SUGGESTIONS_CHANNEL_ID':'0','INTRODUCTIONS_CHANNEL_ID':'0'}
        # dotenv is disabled before importing application modules. Discord requests
        # fail immediately if an accidental integration call escapes a test mock.
        with patch.dict(os.environ,clean), patch('dotenv.load_dotenv',return_value=False), \
             patch('discord.http.HTTPClient.request',side_effect=AssertionError('Live Discord requests are forbidden in offline tests')):
            os.environ.pop('DISCORD_TOKEN',None)
            yield


def main():
    root=Path(__file__).resolve().parents[1]
    with offline_environment():
        suite=unittest.defaultTestLoader.discover(str(root/'tests'))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1



if __name__=='__main__':raise SystemExit(main())
