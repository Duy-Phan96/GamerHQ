"""Preview or apply a reviewed game manifest to an existing GamerHQ database."""

import argparse
from collections import defaultdict
from contextlib import closing, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata

from tools.backup_database import backup_database
from tools.instance_lock import instance_lock


def name_key(title):
    title = unicodedata.normalize("NFKC", title).casefold()
    title = title.replace("'", "").replace("’", "")
    return " ".join("".join(char if char.isalnum() or char in "+#" else " " for char in title).split())


def strings(value):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("Expected a list of nonempty strings.")
    return value


def manifest(path):
    from config import DISPLAY_GROUP_ORDER

    source = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(source, dict) or set(source) != {"games"} or not isinstance(source["games"], list):
        raise ValueError("Expected a JSON object with a games array.")
    games = []
    for row in source["games"]:
        if not isinstance(row, dict) or set(row) - {"name", "aliases", "emoji", "display_group", "tags", "review_note", "existing_name"}:
            raise ValueError("Unexpected game manifest fields.")
        name, emoji, group = row.get("name"), row.get("emoji", "🎮"), row.get("display_group")
        if not isinstance(name, str) or not any(char.isalnum() for char in name_key(name)) or not isinstance(emoji, str) or not emoji.strip():
            raise ValueError("Each game needs a valid name and emoji.")
        if group is not None and group not in DISPLAY_GROUP_ORDER:
            raise ValueError("Use an existing game display group.")
        aliases = strings(row.get("aliases", []))
        if any(not any(char.isalnum() for char in name_key(alias)) for alias in aliases):
            raise ValueError("Each alias needs a letter or number.")
        note = row.get("review_note")
        if note is not None and (not isinstance(note, str) or not note.strip()):
            raise ValueError("A review note must be nonempty text.")
        existing_name = row.get("existing_name")
        if existing_name is not None and (not isinstance(existing_name, str) or not name_key(existing_name)):
            raise ValueError("An existing target needs a canonical name.")
        games.append({"existing_name": existing_name, "review_note": note, "name": name.strip(), "emoji": emoji, "display_group": group,
                      "tags": strings(row.get("tags", [])), "aliases": aliases})
    return games


def title_keys(name, aliases):
    return {name_key(value) for value in (name, *aliases)} - {""}


def plan(conn, games, target):
    existing = [dict(row) for row in conn.execute("SELECT * FROM games ORDER BY id")]
    deleted = sorted(row[0] for row in conn.execute("SELECT name_key FROM deleted_games"))
    tombstones = {name_key(name) for name in deleted}
    canonical_by_key = defaultdict(set)
    existing_by_key = defaultdict(set)
    for row in existing:
        canonical_by_key[name_key(row["name"])].add(row["id"])
        for key in title_keys(row["name"], strings(json.loads(row["aliases_json"]))):
            existing_by_key[key].add(row["id"])
    existing_by_id = {row["id"]: row for row in existing}

    keys = [title_keys(row["name"], [*row["aliases"], row["existing_name"] or ""]) for row in games]
    input_by_key = defaultdict(set)
    for position, row_keys in enumerate(keys):
        for key in row_keys:
            input_by_key[key].add(position)

    report = {"existing": [], "insert": [], "ambiguous": [], "retired": [], "alias_updates": []}
    for position, (row, row_keys) in enumerate(zip(games, keys)):
        title = row["name"]
        matched = sorted(set().union(*(existing_by_key[key] for key in row_keys)))
        collisions = sorted(set().union(*(input_by_key[key] for key in row_keys)) - {position})
        target_name = row["existing_name"]
        target_ids = canonical_by_key[name_key(target_name)] if target_name else set()
        target_mismatch = target_name and (len(target_ids) != 1 or set(matched) != target_ids)
        if row["review_note"] or collisions or len(matched) > 1 or target_mismatch:
            report["ambiguous"].append({"title": title, "existing_ids": matched,
                                        "manifest_titles": [games[i]["name"] for i in collisions],
                                        "review_note": row["review_note"], "expected_name": target_name})
        elif row_keys & tombstones and not target_name:
            report["retired"].append(title)
        elif matched:
            match = existing_by_id[matched[0]]
            report["existing"].append({"title": title, "id": match["id"], "name": match["name"],
                                       "role_id": match["role_id"], "channel_id": match["channel_id"],
                                       "legacy_chat_channel_id": match["chat_channel_id"]})
            if target_name:
                aliases = json.loads(match["aliases_json"])
                known = {value.lower() for value in (match["name"], *aliases)}
                additions = []
                for alias in (title, *row["aliases"]):
                    key = alias.lower()
                    if key not in known:
                        additions.append(alias)
                        known.add(key)
                if additions:
                    report["alias_updates"].append({"id": match["id"], "name": match["name"],
                                                    "add": additions, "aliases": aliases + additions})
        else:
            report["insert"].append({key: value for key, value in row.items() if key not in {"review_note", "existing_name"}})

    snapshot = json.dumps([str(target), games, existing, deleted], sort_keys=True, separators=(",", ":"))
    report["review_token"] = hashlib.sha256(snapshot.encode()).hexdigest()
    return report


def import_catalog(database, source, *, apply=False, review_token=None, backup=None):
    from database import db

    target = Path(database)
    if target.is_symlink() or not target.is_file():
        raise ValueError("Select an existing GamerHQ database without symlinks.")
    if apply and (not review_token or backup is None):
        raise ValueError("Apply requires the exact preview token and a new backup path.")
    if backup is not None and Path(backup).resolve() in {target.resolve(), Path(source).resolve()}:
        raise ValueError("Backup must be a separate file.")
    games = manifest(source)
    mode = "rw" if apply else "ro"
    with instance_lock(target) if apply else nullcontext():
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=" + mode, uri=True)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            if conn.execute("PRAGMA user_version").fetchone()[0] != db.SCHEMA_VERSION:
                raise ValueError("Use a database on the current GamerHQ schema.")
            report = plan(conn, games, target.resolve())
            if not apply:
                return report
            if report["review_token"] != review_token:
                raise ValueError("Catalog or manifest changed. Generate a fresh preview.")
            if report["ambiguous"]:
                raise ValueError("Resolve ambiguous titles before applying.")
            if any(row["display_group"] is None for row in report["insert"]):
                raise ValueError("Choose an existing display group for every new title and preview again.")
            if report["insert"] or report["alias_updates"]:
                backup_database(backup, source=target)
                conn.executemany("UPDATE games SET aliases_json=? WHERE id=?",
                                 [(json.dumps(row["aliases"], ensure_ascii=False), row["id"])
                                  for row in report["alias_updates"]])
            if report["insert"]:
                conn.executemany(
                    "INSERT INTO games (name, emoji, display_group, tags_json, aliases_json) VALUES (?, ?, ?, ?, ?)",
                    [(row["name"], row["emoji"], row["display_group"], json.dumps(row["tags"], ensure_ascii=False),
                      json.dumps(row["aliases"], ensure_ascii=False)) for row in report["insert"]],
                )
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--review-token")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        result = import_catalog(args.database, args.manifest, apply=args.apply,
                                review_token=args.review_token, backup=args.backup)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except (OSError, sqlite3.Error, RuntimeError):
        parser.exit(1, "Catalog import failed. Check the private database, backup path, and instance lock.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

