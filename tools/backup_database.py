"""Create a consistent SQLite backup without stopping GamerHQ."""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH


def backup_database(destination: Path) -> Path:
    source = DB_PATH.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Runtime database does not exist: {source}")
    destination = destination.expanduser().resolve()
    if destination == source:
        raise ValueError("Backup destination must differ from the runtime database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite an existing backup: {destination}")

    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(target_conn)
        result = target_conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
    except Exception:
        target_conn.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        target_conn.close()
    finally:
        source_conn.close()
    return destination


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path,
                        default=Path("/app/backups") / f"gamerhq-{stamp}.db")
    args = parser.parse_args()
    path = backup_database(args.destination)
    print(f"Verified SQLite backup created: {path}")


if __name__ == "__main__":
    main()
