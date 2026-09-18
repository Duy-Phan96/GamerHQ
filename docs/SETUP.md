# Local installation

## Windows PowerShell

From the repository root, using Python 3.12 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m tools.test
.\.venv\Scripts\python.exe -m pip check
```

## Linux/macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
.venv/bin/python -m tools.test
.venv/bin/python -m pip check
```

Never overwrite an existing private `.env` when updating. Offline tests do not load it. Set the real variables in `.env` only when you intend to connect your development bot. Use separate development credentials, guild and SQLite path. `GAMERHQ_DB_PATH=runtime/data/gamerhq.db` is a portable local choice; relative paths resolve from this repository, independent of the terminal directory. Existing installs without that setting retain their legacy `gamerhq.db` path.

## Database construction

Schema and additive migrations live in `database/db.py`; the public game-only seed is `data/games_seed.json`. They are initialized automatically on real bot startup. No database download or production snapshot is needed. Tests demonstrate initialization into a new temporary nested directory and verify private tables are empty.

For an explicitly configured development DB only, initialization without Discord is:

```powershell
.\.venv\Scripts\python.exe -c "from database import db; db.init_db(); db.seed_catalog()"
```

This command writes to your configured DB; check `.env` first. It is not part of the credential-free test runner.

## Starting deliberately

After [Discord setup](DISCORD_SETUP.md):

```powershell
.\.venv\Scripts\python.exe bot.py
```

On Linux/macOS use `.venv/bin/python bot.py`. Missing token/guild configuration fails before login; malformed numeric settings name the variable without displaying its contents.

Startup synchronizes guild commands and removes global commands belonging to that application. It also restores known messages/resources. Do not use production credentials for development. Run `/server health` first; owner `/server setup` previews and confirms incremental changes. This repository has no offline simulator for a complete live guild and no deployment step is part of unit tests.

## Existing-server assumptions

Before core repair, create START HERE and COMMUNITY categories. Add the channels the health report expects: rules, announcements, choose-your-games, choose-your-roles, looking-for-group, general, introductions, suggestions, tournaments and giveaways. Configure `CHOOSE_GAMES_CHANNEL_ID` to the existing selector. An existing private STAFF category is required for staff review/log placement. Voice and streamer resources follow their existing feature/setup flows; inspect the health report rather than expecting setup to rebuild all missing resources. Review generated roles and bot hierarchy before member testing.

## Maintenance utilities

`python -m tools.backup_database <PRIVATE_BACKUP_PATH>` creates a verified SQLite backup from the configured DB. `python -m tools.production_preflight --db-path <EXISTING_PRIVATE_DB>` checks an already configured deployment; it is not a fresh-database installer. `tools/cleanup_catalog.py` is retained legacy maintenance source, not a setup step; it changes catalog/database data and must not be run casually. Local ignored root patch scripts and old README variants are not installation dependencies.
