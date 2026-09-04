"""Read-only production configuration and database check."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--db-path", type=Path)
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    errors = []

    token = (values.get("DISCORD_TOKEN") or "").strip()
    guild_id = (values.get("GUILD_ID") or "").strip()
    choose_id = (values.get("CHOOSE_GAMES_CHANNEL_ID") or "").strip()
    if not token or token == "YOUR_TOKEN_HERE":
        errors.append("DISCORD_TOKEN is missing or still a placeholder")
    for name, value in (("GUILD_ID", guild_id), ("CHOOSE_GAMES_CHANNEL_ID", choose_id)):
        if not value.isdigit() or int(value) <= 0:
            errors.append(f"{name} must be a positive Discord ID")

    configured_db = args.db_path or Path(values.get("GAMERHQ_DB_PATH") or "")
    if not str(configured_db):
        errors.append("GAMERHQ_DB_PATH is missing")
    else:
        configured_db = configured_db.expanduser().resolve()
        if not configured_db.is_file():
            errors.append(f"Runtime DB does not exist: {configured_db}")
        else:
            conn = sqlite3.connect(configured_db.as_uri() + "?mode=ro", uri=True)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    errors.append(f"SQLite integrity check failed: {integrity}")
                counts = conn.execute(
                    "SELECT count(*), sum(selectable != 0), sum(area_enabled != 0) FROM games"
                ).fetchone()
            finally:
                conn.close()
            print(f"Database: {configured_db}")
            print(f"Games: {counts[0]} | selectable: {counts[1] or 0} | areas: {counts[2] or 0}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("Production preflight passed. No data was changed.")


if __name__ == "__main__":
    main()
