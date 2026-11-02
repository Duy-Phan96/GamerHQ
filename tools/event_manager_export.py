"""Export only Event Manager V1 tables from a database copy, read-only.

Never loads config/dotenv or starts the bot. Output contains private event data;
operators must retain it alongside their protected backup, never in Git or CI.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

TABLES = {"series": "lfg_event_series", "events": "lfg_events", "members": "lfg_event_members",
          "messages": "lfg_event_messages", "voiceNotifications": "lfg_voice_notifications",
          "timeProposals": "lfg_time_proposals"}


def export_snapshot(database: Path):
    database = database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError("Database integrity check failed; restore/repair a copy first.")
        result = {"schemaVersion": "event-manager-v1-export.1"}
        for key, table in TABLES.items():
            # Table names are constants, never request input.
            result[key] = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for series in result["series"]:
            series["weekdays"] = json.loads(series["weekdays_json"])
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-copy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    snapshot = export_snapshot(args.database_copy)
    # Exclusive creation prevents overwriting a prior protected export/backup.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print("Read-only event snapshot created. Keep it private; no cutover performed.")


if __name__ == "__main__":
    main()
