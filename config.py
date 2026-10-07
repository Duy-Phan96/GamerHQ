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
AMAZON_BOT_ID = discord_id("AMAZON_BOT_ID")
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


def skill_ids(name):
    values = tuple(value.strip() for value in os.getenv(name, "").split(",") if value.strip())
    if len(set(values)) != len(values):
        raise ConfigurationError(f"{name} must not contain duplicate Skill IDs.")
    import re
    pattern = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    if any(not pattern.fullmatch(value) for value in values):
        raise ConfigurationError(f"{name} must contain lowercase kebab-case Skill IDs.")
    return values


EXTERNAL_SKILLS = skill_ids("GAMERHQ_EXTERNAL_SKILLS")

GAMERHQ_WEB_API_ENABLED = os.getenv("GAMERHQ_WEB_API_ENABLED", "false").lower() == "true"
GAMERHQ_WEB_API_HOST = os.getenv("GAMERHQ_WEB_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
_web_api_port = os.getenv("GAMERHQ_WEB_API_PORT", "8080").strip()
if not _web_api_port.isdigit() or not 1 <= int(_web_api_port) <= 65535:
    raise ConfigurationError("GAMERHQ_WEB_API_PORT must be an integer between 1 and 65535.")
GAMERHQ_WEB_API_PORT = int(_web_api_port)
GAMERHQ_WEB_API_SECRET = os.getenv("GAMERHQ_WEB_API_SECRET", "").strip()
# Optional host encryption key for per-Skill secrets. When unset, the
# secrets.skill capability is unavailable and Skills requiring it fail closed.
GAMERHQ_SKILL_SECRET_KEY = os.getenv("GAMERHQ_SKILL_SECRET_KEY", "").strip()

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
    if GAMERHQ_WEB_API_ENABLED and len(GAMERHQ_WEB_API_SECRET) < 32:
        raise ConfigurationError(
            "GAMERHQ_WEB_API_SECRET must be at least 32 characters when the web API is enabled."
        )


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
GAME_CHANNEL_MEMBER_THRESHOLD = 10
GAME_CHANNEL_SOFT_LIMIT = 20
POPULAR_GAMES_COUNT = 25
