from pathlib import Path
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT
DB_PATH = PROJECT / "gamerhq.db"
SEED_PATH = PROJECT / "data" / "games_seed.json"

# Cleaned catalog: one display category per game, with more distinctive emojis.
UPDATES = {
    "Minecraft": {"emoji": "⛏️", "display_group": "⭐ All-Time Classics"},
    "World of Warcraft": {"emoji": "🌍", "display_group": "⭐ All-Time Classics"},
    "Counter-Strike 2": {"emoji": "💣", "display_group": "⭐ All-Time Classics"},
    "GTA Online": {"emoji": "🚗", "display_group": "⭐ All-Time Classics"},
    "Fortnite": {"emoji": "🪂", "display_group": "⭐ All-Time Classics"},
    "Rocket League": {"emoji": "🚀", "display_group": "⭐ All-Time Classics"},
    "Hearthstone": {"emoji": "🃏", "display_group": "⭐ All-Time Classics"},
    "Warcraft III": {"emoji": "⚔️", "display_group": "⭐ All-Time Classics"},
    "Dota 2": {"emoji": "🛡️", "display_group": "⭐ All-Time Classics"},

    "Overwatch 2": {"emoji": "🤖", "display_group": "🔥 Popular"},
    "Valorant": {"emoji": "🎯", "display_group": "🔥 Popular"},
    "Marvel Rivals": {"emoji": "🦸", "display_group": "🔥 Popular"},
    "Apex Legends": {"emoji": "🔺", "display_group": "🔥 Popular"},
    "Palworld": {"emoji": "🐾", "display_group": "🔥 Popular"},
    "Helldivers 2": {"emoji": "🚀", "display_group": "🔥 Popular"},
    "Rainbow Six Siege": {"emoji": "🛡️", "display_group": "🔥 Popular"},
    "Call of Duty: Warzone": {"emoji": "🪖", "display_group": "🔥 Popular"},
    "Dead by Daylight": {"emoji": "🩸", "display_group": "🔥 Popular"},
    "Wuthering Waves": {"emoji": "🌊", "display_group": "🔥 Popular"},

    "Brawl Stars": {"emoji": "⭐", "display_group": "📱 Mobile Games"},
    "Clash Royale": {"emoji": "👑", "display_group": "📱 Mobile Games"},
    "Clash of Clans": {"emoji": "🏰", "display_group": "📱 Mobile Games"},
    "Pokémon GO": {"emoji": "🔴", "display_group": "📱 Mobile Games"},
    "League of Legends: Wild Rift": {"emoji": "🏯", "display_group": "📱 Mobile Games"},
    "Mobile Legends: Bang Bang": {"emoji": "💠", "display_group": "📱 Mobile Games"},
    "Call of Duty: Mobile": {"emoji": "📲", "display_group": "📱 Mobile Games"},

    "Among Us": {"emoji": "👨‍🚀", "display_group": "🎉 Party & Social"},
    "Fall Guys": {"emoji": "🫘", "display_group": "🎉 Party & Social"},
    "Party Animals": {"emoji": "🐾", "display_group": "🎉 Party & Social"},
    "Lethal Company": {"emoji": "👻", "display_group": "🎉 Party & Social"},
    "Phasmophobia": {"emoji": "🔦", "display_group": "🎉 Party & Social"},

    "THE FINALS": {"emoji": "💥", "display_group": "🔫 Shooter"},
    "Battlefield": {"emoji": "🎖️", "display_group": "🔫 Shooter"},
    "Escape from Tarkov": {"emoji": "🎒", "display_group": "🔫 Shooter"},
    "Hunt: Showdown 1896": {"emoji": "🤠", "display_group": "🔫 Shooter"},
    "PUBG: Battlegrounds": {"emoji": "🪂", "display_group": "🔫 Shooter"},

    "Final Fantasy XIV": {"emoji": "🌟", "display_group": "🌍 MMO & RPG"},
    "Guild Wars 2": {"emoji": "🐉", "display_group": "🌍 MMO & RPG"},
    "Diablo IV": {"emoji": "😈", "display_group": "🌍 MMO & RPG"},
    "Path of Exile 2": {"emoji": "💀", "display_group": "🌍 MMO & RPG"},
    "Monster Hunter Wilds": {"emoji": "🐲", "display_group": "🌍 MMO & RPG"},

    "Rust": {"emoji": "🛠️", "display_group": "🏗️ Survival & Sandbox"},
    "ARK: Survival Ascended": {"emoji": "🦖", "display_group": "🏗️ Survival & Sandbox"},
    "Valheim": {"emoji": "🪓", "display_group": "🏗️ Survival & Sandbox"},
    "Terraria": {"emoji": "🌳", "display_group": "🏗️ Survival & Sandbox"},
    "Project Zomboid": {"emoji": "🧟", "display_group": "🏗️ Survival & Sandbox"},

    "StarCraft II": {"emoji": "👽", "display_group": "⚔️ Strategy & Card"},
    "Age of Empires II: Definitive Edition": {"emoji": "🏹", "display_group": "⚔️ Strategy & Card"},
    "Age of Empires IV": {"emoji": "🏰", "display_group": "⚔️ Strategy & Card"},
    "Civilization VII": {"emoji": "🌐", "display_group": "⚔️ Strategy & Card"},
    "Teamfight Tactics": {"emoji": "♟️", "display_group": "⚔️ Strategy & Card"},

    "League of Legends": {"emoji": "🏆", "display_group": "⭐ All-Time Classics"},
    "SMITE 2": {"emoji": "⚡", "display_group": "🏆 MOBA"},
    "Heroes of the Storm": {"emoji": "🌀", "display_group": "🏆 MOBA"},

    "EA Sports FC": {"emoji": "⚽", "display_group": "⚽ Sports & Racing"},
    "Forza Horizon": {"emoji": "🏎️", "display_group": "⚽ Sports & Racing"},
    "F1": {"emoji": "🏁", "display_group": "⚽ Sports & Racing"},

    "Tekken 8": {"emoji": "🥊", "display_group": "🥊 Fighting"},
    "Street Fighter 6": {"emoji": "🥋", "display_group": "🥊 Fighting"},
    "Brawlhalla": {"emoji": "⚔️", "display_group": "🥊 Fighting"},
}

def load_seed():
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return data

def save_seed(data):
    SEED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    if not SEED_PATH.exists():
        raise SystemExit(f"Missing seed file: {SEED_PATH}")
    if not DB_PATH.exists():
        raise SystemExit(f"Missing database: {DB_PATH}")

    data = load_seed()
    games = data["games"]

    missing = []
    for game in games:
        upd = UPDATES.get(game["name"])
        if not upd:
            missing.append(game["name"])
            continue
        game["emoji"] = upd["emoji"]
        game["display_group"] = upd["display_group"]

    # Ensure the catalog still has all expected games updated.
    if missing:
        print("WARNING: No explicit cleanup mapping for:")
        for name in missing:
            print(" -", name)

    save_seed(data)

    conn = sqlite3.connect(DB_PATH)
    try:
        for name, upd in UPDATES.items():
            conn.execute(
                "UPDATE games SET emoji=?, display_group=? WHERE name=?",
                (upd["emoji"], upd["display_group"], name),
            )
        conn.commit()
    finally:
        conn.close()

    print("Catalog cleanup complete.")
    print("Updated seed file and gamerhq.db.")
    print("Next: restart the bot and run /game overview to refresh the message.")

if __name__ == "__main__":
    main()
