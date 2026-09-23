import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
# Runtime DB must survive code/ZIP updates. Set GAMERHQ_DB_PATH to a path
# outside the deploy/code folder for maximum safety. Existing installs keep the
# historical project-local path when the env var is not set.
_db_env = os.getenv("GAMERHQ_DB_PATH")
DB_PATH = (BASE_DIR / Path(_db_env).expanduser()).resolve() if _db_env else (BASE_DIR / "gamerhq.db")
SEED_PATH = BASE_DIR / "data" / "games_seed.json"

class ConfigurationError(ValueError):
    """Invalid configuration; messages must not include supplied values."""


def discord_id(name):
    raw = os.getenv(name, "0").strip() or "0"
    if not raw.isdigit():
        raise ConfigurationError(f"{name} must be a non-negative Discord ID.")
    return int(raw)


INSTANT_GAMING_BOT_ID = discord_id("INSTANT_GAMING_BOT_ID")
DEALGECKO_BOT_ID = discord_id("DEALGECKO_BOT_ID")
JOCKIE_MUSIC_BOT_ID = discord_id("JOCKIE_MUSIC_BOT_ID")
PANCAKE_BOT_ID = discord_id("PANCAKE_BOT_ID")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = discord_id("GUILD_ID")
CHOOSE_GAMES_CHANNEL_ID = discord_id("CHOOSE_GAMES_CHANNEL_ID")
GAME_SUGGESTIONS_CHANNEL_ID = discord_id("GAME_SUGGESTIONS_CHANNEL_ID")
INTRODUCTIONS_CHANNEL_ID = discord_id("INTRODUCTIONS_CHANNEL_ID")


def validate_startup():
    if not TOKEN or not TOKEN.strip() or TOKEN.strip() == "YOUR_TOKEN_HERE":
        raise ConfigurationError("DISCORD_TOKEN is required. Configure it in your environment or private .env file.")
    if GUILD_ID <= 0:
        raise ConfigurationError("GUILD_ID is required and must be a positive Discord server ID.")


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
