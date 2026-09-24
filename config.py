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
# Public user IDs, centrally defined; explicit positive environment IDs override.
THIRD_PARTY_BOTS = {
    "dealgecko": 1550051214035517450,
    "jockie": 411916947773587456,
    "pancake": 239631525350604801,
}
DEALGECKO_BOT_ID = discord_id("DEALGECKO_BOT_ID") or THIRD_PARTY_BOTS["dealgecko"]
JOCKIE_MUSIC_BOT_ID = discord_id("JOCKIE_MUSIC_BOT_ID") or THIRD_PARTY_BOTS["jockie"]
PANCAKE_BOT_ID = discord_id("PANCAKE_BOT_ID") or THIRD_PARTY_BOTS["pancake"]

# No approved automatic provider access: legacy opt-in cannot enable scraping.
GOCDKEYS_AUTOMATIC_SUPPORTED = False
GOCDKEYS_ENABLED = os.getenv("GOCDKEYS_ENABLED", "false").lower() == "true"
GOCDKEYS_REFERRAL_CODE = os.getenv("GOCDKEYS_REFERRAL_CODE", "kas66b").strip()
SUPPORTED_DEAL_SOURCES = ('instant-gaming', 'dealgecko')  # Identities reuse existing bot configuration.

STREAMER_HUB_ENABLED = os.getenv("STREAMER_HUB_ENABLED", "false").lower() == "true"
STREAMER_ROLE_SELECTION_ENABLED = os.getenv("STREAMER_ROLE_SELECTION_ENABLED", "false").lower() == "true"
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = discord_id("GUILD_ID")
CHOOSE_GAMES_CHANNEL_ID = discord_id("CHOOSE_GAMES_CHANNEL_ID")
GAME_SUGGESTIONS_CHANNEL_ID = discord_id("GAME_SUGGESTIONS_CHANNEL_ID")
INTRODUCTIONS_CHANNEL_ID = discord_id("INTRODUCTIONS_CHANNEL_ID")


def validate_startup():
    if not TOKEN or not TOKEN.strip() or TOKEN.strip() == "YOUR_TOKEN_HERE":
        raise ConfigurationError("DISCORD_TOKEN is required. Configure it in your environment or private .env file.")
    if STREAMER_HUB_ENABLED and not TWITCH_CLIENT_ID:
        raise ConfigurationError("TWITCH_CLIENT_ID is required when STREAMER_HUB_ENABLED is true.")
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
