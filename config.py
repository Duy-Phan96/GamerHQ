import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
# Runtime DB must survive code/ZIP updates. Set GAMERHQ_DB_PATH to a path
# outside the deploy/code folder for maximum safety. Existing installs keep the
# historical project-local path when the env var is not set.
_db_env = os.getenv("GAMERHQ_DB_PATH")
DB_PATH = Path(_db_env).expanduser().resolve() if _db_env else (BASE_DIR / "gamerhq.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SEED_PATH = BASE_DIR / "data" / "games_seed.json"

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
CHOOSE_GAMES_CHANNEL_ID = int(os.getenv("CHOOSE_GAMES_CHANNEL_ID", "0") or 0)
GAME_SUGGESTIONS_CHANNEL_ID = int(os.getenv("GAME_SUGGESTIONS_CHANNEL_ID", "0") or 0)
INTRODUCTIONS_CHANNEL_ID = int(os.getenv("INTRODUCTIONS_CHANNEL_ID", "0") or 0)

DISPLAY_GROUP_ORDER = [
    "⭐ All-Time Classics",
    "🔥 Popular",
    "📱 Mobile Games",
    "🎉 Party & Social",
    "🔫 Shooter",
    "🌍 MMO & RPG",
    "🏗️ Survival & Sandbox",
    "⚔️ Strategy & Card",
    "🏆 MOBA",
    "⚽ Sports & Racing",
    "🥊 Fighting",
]
